import json
import re

from app.services.llm import generate_report, llm_enabled
from app.services.scene_graph_manage import resolve_scene_template_from_text

SCENE_PROMPTS_BY_GENRE = {
    '玄幻': 'V3-scene_building_task.txt',
    '言情': 'V3-scene_building_task.txt',
    '悬疑': 'V3-scene_building_task.txt',
    '科幻': 'V3-scene_building_task.txt',
}
ALLOWED_SCENE_PROMPTS = {'V3-scene_building_task.txt', 'V3-scene_building_task_retry.txt'}
DEFAULT_SCENE_GENRE = '玄幻'
SINGLE_PASS_SAFE_CHARS = 420
SEGMENT_TARGET_CHARS = 360
SEGMENT_MAX_CHARS = 420

TIME_KEYWORDS = [
    '清晨', '早上', '早晨', '上午', '中午', '午后', '下午', '傍晚', '黄昏', '晚上', '夜里', '深夜', '凌晨',
]
TIME_REGEX_PATTERNS = [
    r'春(?:天|季|日|夜|末|初)',
    r'夏(?:天|季|日|夜|末|初)',
    r'秋(?:天|季|日|夜|末|初)',
    r'冬(?:天|季|日|夜|末|初)',
]

LOCATION_KEYWORDS = [
    '教室', '操场', '海边', '沙滩', '山林', '树林', '林间', '小路', '客栈', '大堂', '宫廷', '长廊', '街巷', '街道',
    '走廊', '房间', '屋内', '室内', '室外', '海岸', '码头', '医院', '病房', '阁楼', '庭院', '院子', '集市', '酒馆',
    '禁地', '山门', '宗门', '大殿',
]
LOCATION_ENTITY_PATTERNS = [
    r'[\u4e00-\u9fff]{1,10}禁地',
    r'[\u4e00-\u9fff]{1,10}山门',
    r'[\u4e00-\u9fff]{1,10}宗门',
    r'[\u4e00-\u9fff]{1,10}大殿',
]
LOCATION_SUFFIXES = ('禁地', '山门', '宗门', '大殿')
LOCATION_PREFIX_BREAKS = ('我', '了', '的', '在', '到', '入', '闯', '惊', '动')

TIME_INFERENCE_MAP = {
    '清晨': ['清晨环境', '鸟叫'],
    '早上': ['早间环境', '鸟叫'],
    '早晨': ['早间环境', '鸟叫'],
    '上午': ['日间环境'],
    '中午': ['日间环境', '知了'],
    '午后': ['日间环境'],
    '下午': ['日间环境'],
    '傍晚': ['傍晚环境', '归鸟'],
    '黄昏': ['傍晚环境', '归鸟'],
    '晚上': ['夜间环境', '蛙鸣'],
    '夜里': ['夜间环境', '蛙鸣'],
    '深夜': ['深夜环境', '虫鸣'],
    '凌晨': ['凌晨环境', '冷风'],
    '春': ['季节提示:春'],
    '夏': ['季节提示:夏'],
    '秋': ['季节提示:秋'],
    '冬': ['季节提示:冬'],
}

SCENE_TEMPLATE_MAP = {
    '教室': {
        'scene_name': '教室',
        'template': '教室类',
        'background': ['室内底噪'],
        'feature': ['读书声', '粉笔声'],
        'detail': ['翻书声', '桌椅轻响'],
        'location_inference': ['室内', '教学环境'],
    },
    '操场': {
        'scene_name': '操场',
        'template': '操场类',
        'background': ['空气流动'],
        'feature': ['风声', '树叶声'],
        'detail': ['远处人声', '脚步声'],
        'location_inference': ['室外', '开阔空间'],
    },
    '海边': {
        'scene_name': '海边',
        'template': '海边类',
        'background': ['空气流动'],
        'feature': ['海风', '海浪', '海鸥'],
        'detail': ['孩子嬉闹声', '远处轮船声', '汽笛声'],
        'location_inference': ['室外', '开阔空间', '海岸环境'],
    },
    '沙滩': {
        'scene_name': '海边',
        'template': '海边类',
        'background': ['空气流动'],
        'feature': ['海风', '海浪', '海鸥'],
        'detail': ['孩子嬉闹声', '远处轮船声', '汽笛声'],
        'location_inference': ['室外', '开阔空间', '海岸环境'],
    },
    '山林': {
        'scene_name': '山林',
        'template': '夜林类',
        'background': ['空气流动'],
        'feature': ['夜风', '树叶声', '虫鸣'],
        'detail': ['枯枝轻响', '远处鸟鸣'],
        'location_inference': ['室外', '自然环境'],
    },
    '树林': {
        'scene_name': '山林',
        'template': '夜林类',
        'background': ['空气流动'],
        'feature': ['夜风', '树叶声', '虫鸣'],
        'detail': ['枯枝轻响', '远处鸟鸣'],
        'location_inference': ['室外', '自然环境'],
    },
    '林间': {
        'scene_name': '山林',
        'template': '夜林类',
        'background': ['空气流动'],
        'feature': ['夜风', '树叶声', '虫鸣'],
        'detail': ['枯枝轻响', '远处鸟鸣'],
        'location_inference': ['室外', '自然环境'],
    },
    '客栈': {
        'scene_name': '客栈',
        'template': '客栈类',
        'background': ['室内底噪'],
        'feature': ['杯盏轻碰', '人群低语', '木门声'],
        'detail': ['脚步声', '桌椅轻响'],
        'location_inference': ['室内', '公共空间'],
    },
    '街巷': {
        'scene_name': '街巷',
        'template': '街巷类',
        'background': ['空气流动'],
        'feature': ['风声', '零星人声'],
        'detail': ['脚步声', '远处车马声'],
        'location_inference': ['室外', '街道环境'],
    },
    '长廊': {
        'scene_name': '长廊',
        'template': '长廊类',
        'background': ['室内底噪'],
        'feature': ['脚步回响', '衣料摩擦'],
        'detail': ['远处门响'],
        'location_inference': ['室内', '狭长空间'],
    },
}

ACTION_EXCLUDE_HINTS = [
    '拍手', '拍拍手', '跺脚', '跺跺脚', '踢石头', '狠狠地踢', '狠狠踢', '推门', '挥手', '掐诀', '掐决',
]


def _normalize_genre(genre: str | None) -> str:
    value = (genre or '').strip()
    return value if value in SCENE_PROMPTS_BY_GENRE else DEFAULT_SCENE_GENRE


def _split_sentences(text: str) -> list[dict]:
    out = []
    start = 0
    for m in re.finditer(r'[。！？!?]+', text):
        end = m.start()
        raw = text[start:end]
        stripped = raw.strip()
        if stripped:
            left_trim = len(raw) - len(raw.lstrip())
            right_trim = len(raw) - len(raw.rstrip())
            out.append({'text': stripped, 'char_start': start + left_trim, 'char_end': end - right_trim})
        start = m.end()
    tail = text[start:]
    tail_stripped = tail.strip()
    if tail_stripped:
        left_trim = len(tail) - len(tail.lstrip())
        right_trim = len(tail) - len(tail.rstrip())
        out.append({'text': tail_stripped, 'char_start': start + left_trim, 'char_end': len(text) - right_trim})
    return out


def _merge_unique(items: list[str] | None) -> list[str]:
    seen = set()
    out = []
    for item in items or []:
        value = str(item or '').strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _normalize_location_entity(value: str) -> str:
    text = str(value or '').strip('“”"\'，。！？!？；：、 ')
    if not text:
        return ''
    for suffix in LOCATION_SUFFIXES:
        if text.endswith(suffix):
            cut = -1
            for marker in LOCATION_PREFIX_BREAKS:
                idx = text.rfind(marker)
                if idx > cut:
                    cut = idx
            if cut >= 0 and cut + 1 < len(text):
                trimmed = text[cut + 1 :].strip()
                if trimmed.endswith(suffix):
                    text = trimmed
            break
    return text


def _segment_text(sentences: list[dict], full_text: str) -> list[dict]:
    if not full_text.strip():
        return []
    if len(full_text) <= SINGLE_PASS_SAFE_CHARS:
        return [
            {
                'segment_no': 1,
                'text': full_text.strip(),
                'start_char': 0,
                'end_char': len(full_text),
                'sentence_count': len(sentences),
            }
        ]

    segments = []
    current = []
    current_len = 0

    def flush():
        nonlocal current, current_len
        if not current:
            return
        start_char = current[0]['char_start']
        end_char = current[-1]['char_end']
        chunk_text = full_text[start_char:end_char].strip()
        if chunk_text:
            segments.append(
                {
                    'segment_no': len(segments) + 1,
                    'text': chunk_text,
                    'start_char': start_char,
                    'end_char': end_char,
                    'sentence_count': len(current),
                }
            )
        current = []
        current_len = 0

    if not sentences:
        step = SEGMENT_TARGET_CHARS
        idx = 0
        while idx < len(full_text):
            end = min(len(full_text), idx + step)
            segments.append(
                {
                    'segment_no': len(segments) + 1,
                    'text': full_text[idx:end].strip(),
                    'start_char': idx,
                    'end_char': end,
                    'sentence_count': 1,
                }
            )
            idx = end
        return segments

    for sent in sentences:
        sent_len = len(sent['text'])
        if current and current_len >= SEGMENT_TARGET_CHARS:
            flush()
        if current and current_len + sent_len > SEGMENT_MAX_CHARS:
            flush()
        current.append(sent)
        current_len += sent_len
    flush()
    return segments


def _resolve_prompt(genre: str, prompt_file: str | None) -> tuple[str, str]:
    normalized_genre = _normalize_genre(genre)
    forced_prompt = (prompt_file or '').strip()
    if forced_prompt in ALLOWED_SCENE_PROMPTS:
        return normalized_genre, forced_prompt
    return normalized_genre, SCENE_PROMPTS_BY_GENRE[normalized_genre]


def _extract_time_terms(text: str) -> list[str]:
    found = []
    for term in TIME_KEYWORDS:
        if term in text:
            found.append(term)
    for pattern in TIME_REGEX_PATTERNS:
        for m in re.finditer(pattern, text):
            value = m.group(0)
            if value:
                found.append(value)
    for m in re.finditer(r'\d{4}年\d{1,2}月\d{1,2}日\d{1,2}点(?:\d{1,2}分)?', text):
        found.append(m.group(0))
    return _merge_unique(found)


def _extract_location_terms(text: str) -> list[str]:
    found = []
    for pattern in LOCATION_ENTITY_PATTERNS:
        for m in re.finditer(pattern, text):
            value = _normalize_location_entity(m.group(0) or '')
            if value:
                found.append(value)
    found.extend(term for term in LOCATION_KEYWORDS if term in text)
    merged = _merge_unique(found)
    return sorted(merged, key=lambda x: (-len(x), x))


def _time_inference(time_terms: list[str]) -> list[str]:
    out = []
    for term in time_terms:
        if re.search(r'\d{4}年\d{1,2}月\d{1,2}日\d{1,2}点', term):
            out.extend(['明确时间点', '可推导时代/季节/时段'])
        out.extend(TIME_INFERENCE_MAP.get(term, []))
    return _merge_unique(out)


def _pick_scene_template(location_terms: list[str], genre: str) -> tuple[str, dict]:
    graph_hit = resolve_scene_template_from_text(location_terms, genre)
    if graph_hit:
        return str(graph_hit.get('matched_term') or ''), {
            'scene_name': str(graph_hit.get('scene_name') or (location_terms[0] if location_terms else '未命名场景')).strip(),
            'template': str(graph_hit.get('template') or '').strip(),
            'collection_name': str(graph_hit.get('collection_name') or '').strip(),
            'background': _merge_unique(graph_hit.get('background') or []),
            'feature': _merge_unique(graph_hit.get('feature') or []),
            'detail': _merge_unique(graph_hit.get('detail') or []),
            'supporting_sfx_terms': _merge_unique(graph_hit.get('supporting_sfx_terms') or []),
            'detail_sfx_terms': _merge_unique(graph_hit.get('detail_sfx_terms') or []),
            'location_inference': _merge_unique(graph_hit.get('location_inference') or []),
            'common_hit': bool(graph_hit.get('common_hit')),
            'genre_hit': bool(graph_hit.get('genre_hit')),
        }
    for term in location_terms:
        if term in SCENE_TEMPLATE_MAP:
            return term, SCENE_TEMPLATE_MAP[term]
    return '', {
        'scene_name': location_terms[0] if location_terms else '未命名场景',
        'template': '',
        'collection_name': '',
        'background': [],
        'feature': [],
        'detail': [],
        'supporting_sfx_terms': [],
        'detail_sfx_terms': [],
        'location_inference': [],
        'common_hit': False,
        'genre_hit': False,
    }


def _excluded_action_terms(text: str) -> list[str]:
    return _merge_unique([term for term in ACTION_EXCLUDE_HINTS if term in text])


def _build_scene_item(idx: int, sentence: dict, genre: str, prev_signature: tuple | None) -> tuple[dict, tuple]:
    text = str(sentence.get('text') or '').strip()
    time_terms = _extract_time_terms(text)
    location_terms = _extract_location_terms(text)
    template_key, template = _pick_scene_template(location_terms, genre)
    time_inf = _time_inference(time_terms)
    location_inf = _merge_unique(template.get('location_inference') or [])
    background = _merge_unique(template.get('background') or [])
    feature = _merge_unique(template.get('feature') or [])
    detail = _merge_unique(template.get('detail') or [])
    supporting_sfx = _merge_unique((template.get('supporting_sfx_terms') or []) + feature + [term for term in time_inf if not term.endswith('环境') and '季节提示' not in term])
    detail_sfx = _merge_unique((template.get('detail_sfx_terms') or []) + detail)
    fallback_to_prev = prev_signature is not None and not location_terms and not time_terms
    inherited_location_terms = list(prev_signature[0]) if fallback_to_prev else location_terms
    inherited_time_terms = list(prev_signature[1]) if fallback_to_prev else [term for term in time_terms if not re.search(r'\d{4}年', term)]
    scene_name = str(template.get('scene_name') or (inherited_location_terms[0] if inherited_location_terms else '未命名场景')).strip() or '未命名场景'
    if fallback_to_prev and prev_signature:
        scene_name = str(prev_signature[2] or scene_name).strip() or scene_name
    signature = (
        tuple(inherited_location_terms),
        tuple(inherited_time_terms),
        scene_name,
    )
    is_scene_change = prev_signature is None or signature != prev_signature
    if prev_signature is None:
        reason = '首个场景'
    elif fallback_to_prev:
        reason = '未识别到新的时空词，沿用上一场景'
    elif signature != prev_signature:
        reason = '时空信息发生变化，需要更换场景'
    else:
        reason = '场景未发生变化，可沿用上一场景'
    return {
        'scene_id': f'scene_{idx:03d}',
        'scene_name': scene_name,
        'node_key': f'{genre}::{scene_name}' if genre else scene_name,
        'sentence_excerpt': text[:80],
        'is_scene_change': is_scene_change,
        'scene_change_reason': reason,
        'time_terms': _merge_unique(time_terms + inherited_time_terms),
        'location_terms': _merge_unique(location_terms + inherited_location_terms),
        'time_inference': time_inf,
        'location_inference': location_inf,
        'background_elements': background,
        'feature_elements': feature,
        'detail_elements': detail,
        'supporting_sfx_terms': supporting_sfx,
        'detail_sfx_terms': detail_sfx,
        'excluded_action_terms': _excluded_action_terms(text),
        'scene_graph_source': {
            'common_hit': bool(template.get('common_hit')),
            'genre_hit': bool(template.get('genre_hit')),
            'template_hit': str(template.get('template') or '').strip(),
            'collection_name': str(template.get('collection_name') or '').strip(),
            'has_fallback_terms': not bool(template_key),
        },
        'assets': [],
        'missing_scene_sfx_terms': [],
    }, signature


def _fallback_scene_report(text: str, genre: str) -> dict:
    sentences = _split_sentences(text)
    scene_items = []
    prev_signature = None
    for idx, sentence in enumerate(sentences or [{'text': text, 'char_start': 0, 'char_end': len(text)}], start=1):
        item, prev_signature = _build_scene_item(idx, sentence, genre, prev_signature)
        if not scene_items or item['is_scene_change']:
            scene_items.append(item)
        else:
            # merge non-change sentence into previous scene for a cleaner engineering-unit result
            last = scene_items[-1]
            last['sentence_excerpt'] = f"{last['sentence_excerpt']} / {item['sentence_excerpt']}"[:160]
            last['time_terms'] = _merge_unique(last['time_terms'] + item['time_terms'])
            last['location_terms'] = _merge_unique(last['location_terms'] + item['location_terms'])
            last['time_inference'] = _merge_unique(last['time_inference'] + item['time_inference'])
            last['location_inference'] = _merge_unique(last['location_inference'] + item['location_inference'])
            last['background_elements'] = _merge_unique(last['background_elements'] + item['background_elements'])
            last['feature_elements'] = _merge_unique(last['feature_elements'] + item['feature_elements'])
            last['detail_elements'] = _merge_unique(last['detail_elements'] + item['detail_elements'])
            last['supporting_sfx_terms'] = _merge_unique(last['supporting_sfx_terms'] + item['supporting_sfx_terms'])
            last['detail_sfx_terms'] = _merge_unique(last['detail_sfx_terms'] + item['detail_sfx_terms'])
            last['excluded_action_terms'] = _merge_unique(last['excluded_action_terms'] + item['excluded_action_terms'])
    if len(scene_items) >= 2:
        first = scene_items[0]
        second = scene_items[1]
        first_has_anchor = bool((first.get('time_terms') or []) or (first.get('location_terms') or []))
        second_has_anchor = bool((second.get('time_terms') or []) or (second.get('location_terms') or []))
        if not first_has_anchor and second_has_anchor:
            second['sentence_excerpt'] = f"{first.get('sentence_excerpt') or ''} / {second.get('sentence_excerpt') or ''}".strip(' /')[:160]
            scene_items.pop(0)
    return {
        'title': '场景搭建分析',
        'genre': genre,
        'scene_items': scene_items,
        'summary': {
            'scene_count': len(scene_items),
            'asset_count': 0,
            'gap_count': sum(len(_merge_unique((item.get('supporting_sfx_terms') or []) + (item.get('detail_sfx_terms') or []))) for item in scene_items),
        },
        'graph_model': {
            'parent': '赛道+场景主节点',
            'children': ['time_terms', 'location_terms', 'scene_elements', 'scene_sfx_terms', 'missing_scene_sfx_terms'],
            'node_example': f'{genre}::山林夜路' if genre else '山林夜路',
        },
        'key_points': [
            '先识别时间与地点，再判断是否需要更换场景。',
            '优先使用支撑场景音效搭建场景，必要时再补细节音效。',
            '人物动作音效不归入场景搭建模块。',
        ],
        'risks': [],
        'markdown': f'已按场景搭建规则提取 {len(scene_items)} 个场景项。',
    }


def _derive_scene_risks(
    scene_items: list[dict] | None,
    *,
    segmented: bool = False,
    fallback_mode: bool = False,
) -> list[str]:
    items = [item for item in (scene_items or []) if isinstance(item, dict)]
    if not items:
        return []

    risks: list[str] = []
    missing_anchor_count = sum(
        1
        for item in items
        if not (item.get('time_terms') or []) and not (item.get('location_terms') or [])
    )
    if missing_anchor_count:
        risks.append(f'有 {missing_anchor_count} 个场景未识别到明确时间词或地点词，场景判断可能更依赖上下文推断。')

    fallback_count = sum(
        1
        for item in items
        if isinstance(item.get('scene_graph_source'), dict) and item['scene_graph_source'].get('has_fallback_terms')
    )
    if fallback_count:
        risks.append(f'有 {fallback_count} 个场景包含保底推断，建议优先复核这些场景的名称、模板和图谱来源。')

    weak_support_count = sum(1 for item in items if len(item.get('supporting_sfx_terms') or []) < 2)
    if weak_support_count:
        risks.append(f'有 {weak_support_count} 个场景的支撑场景音效少于 2 项，当前推荐可能不足以稳定支撑场景识别。')

    if segmented:
        risks.append('文本较长，当前结果由多段分析后合并得到，建议结合原文复核场景切换点。')

    if fallback_mode:
        risks.append('本次结果主要依据规则与图谱推断生成，建议重点复核场景名称、切换点与音效分层。')

    return risks[:10]


def _merge_scene_items(scene_items: list[dict]) -> list[dict]:
    merged_items: list[dict] = []
    for item in scene_items:
        if not isinstance(item, dict):
            continue
        normalized = {
            'scene_id': '',
            'scene_name': str(item.get('scene_name') or '').strip() or '未命名场景',
            'node_key': str(item.get('node_key') or '').strip() or '未命名场景',
            'sentence_excerpt': str(item.get('sentence_excerpt') or '').strip(),
            'is_scene_change': bool(item.get('is_scene_change', True)),
            'scene_change_reason': str(item.get('scene_change_reason') or '').strip(),
            'time_terms': _merge_unique(item.get('time_terms') or []),
            'location_terms': _merge_unique(item.get('location_terms') or []),
            'time_inference': _merge_unique(item.get('time_inference') or []),
            'location_inference': _merge_unique(item.get('location_inference') or []),
            'background_elements': _merge_unique(item.get('background_elements') or []),
            'feature_elements': _merge_unique(item.get('feature_elements') or []),
            'detail_elements': _merge_unique(item.get('detail_elements') or []),
            'supporting_sfx_terms': _merge_unique(item.get('supporting_sfx_terms') or []),
            'detail_sfx_terms': _merge_unique(item.get('detail_sfx_terms') or []),
            'excluded_action_terms': _merge_unique(item.get('excluded_action_terms') or []),
            'scene_graph_source': item.get('scene_graph_source') if isinstance(item.get('scene_graph_source'), dict) else {},
            'assets': [],
            'missing_scene_sfx_terms': _merge_unique(item.get('missing_scene_sfx_terms') or []),
        }
        if merged_items:
            prev = merged_items[-1]
            same_node = normalized['node_key'] == prev['node_key']
            no_change = not normalized['is_scene_change']
            if same_node or no_change:
                prev['sentence_excerpt'] = f"{prev['sentence_excerpt']} / {normalized['sentence_excerpt']}".strip(' /')[:220]
                for key in [
                    'time_terms',
                    'location_terms',
                    'time_inference',
                    'location_inference',
                    'background_elements',
                    'feature_elements',
                    'detail_elements',
                    'supporting_sfx_terms',
                    'detail_sfx_terms',
                    'excluded_action_terms',
                    'missing_scene_sfx_terms',
                ]:
                    prev[key] = _merge_unique((prev.get(key) or []) + (normalized.get(key) or []))
                prev_source = prev.get('scene_graph_source') if isinstance(prev.get('scene_graph_source'), dict) else {}
                curr_source = normalized.get('scene_graph_source') if isinstance(normalized.get('scene_graph_source'), dict) else {}
                prev['scene_graph_source'] = {
                    'common_hit': bool(prev_source.get('common_hit') or curr_source.get('common_hit')),
                    'genre_hit': bool(prev_source.get('genre_hit') or curr_source.get('genre_hit')),
                    'template_hit': str(prev_source.get('template_hit') or curr_source.get('template_hit') or '').strip(),
                    'collection_name': str(prev_source.get('collection_name') or curr_source.get('collection_name') or '').strip(),
                    'has_fallback_terms': bool(prev_source.get('has_fallback_terms') and curr_source.get('has_fallback_terms')),
                }
                continue
        merged_items.append(normalized)

    for idx, item in enumerate(merged_items, start=1):
        item['scene_id'] = f'scene_{idx:03d}'
        if not item.get('node_key'):
            item['node_key'] = f'场景{idx}'
    return merged_items


def _normalize_scene_report(report_json: dict | None, text: str, genre: str) -> dict:
    fallback = _fallback_scene_report(text, genre)
    if not isinstance(report_json, dict):
        return fallback
    merged = dict(fallback)
    merged.update({k: v for k, v in report_json.items() if v is not None})
    merged['title'] = str(merged.get('title') or '场景搭建分析').strip() or '场景搭建分析'
    merged['genre'] = genre
    scene_items = merged.get('scene_items')
    if not isinstance(scene_items, list):
        scene_items = fallback['scene_items']
    normalized_items = []
    for idx, item in enumerate(scene_items, start=1):
        if not isinstance(item, dict):
            continue
        normalized_items.append(
            {
                'scene_id': str(item.get('scene_id') or f'scene_{idx:03d}').strip(),
                'scene_name': str(item.get('scene_name') or '').strip() or f'场景{idx}',
                'node_key': str(item.get('node_key') or '').strip() or f'{genre}::场景{idx}',
                'sentence_excerpt': str(item.get('sentence_excerpt') or '').strip(),
                'is_scene_change': bool(item.get('is_scene_change', idx == 1)),
                'scene_change_reason': str(item.get('scene_change_reason') or '').strip(),
                'time_terms': _merge_unique(item.get('time_terms') or []),
                'location_terms': _merge_unique(item.get('location_terms') or []),
                'time_inference': _merge_unique(item.get('time_inference') or []),
                'location_inference': _merge_unique(item.get('location_inference') or []),
                'background_elements': _merge_unique(item.get('background_elements') or []),
                'feature_elements': _merge_unique(item.get('feature_elements') or []),
                'detail_elements': _merge_unique(item.get('detail_elements') or []),
                'supporting_sfx_terms': _merge_unique(item.get('supporting_sfx_terms') or []),
                'detail_sfx_terms': _merge_unique(item.get('detail_sfx_terms') or []),
                'excluded_action_terms': _merge_unique(item.get('excluded_action_terms') or []),
                'scene_graph_source': item.get('scene_graph_source') if isinstance(item.get('scene_graph_source'), dict) else {},
                'assets': [],
                'missing_scene_sfx_terms': _merge_unique(item.get('missing_scene_sfx_terms') or []),
            }
        )
    if not normalized_items:
        normalized_items = fallback['scene_items']
    merged['scene_items'] = _merge_scene_items(normalized_items)
    merged['summary'] = {
        'scene_count': len(merged['scene_items']),
        'asset_count': int(((merged.get('summary') or {}).get('asset_count') or 0)),
        'gap_count': int(((merged.get('summary') or {}).get('gap_count') or 0)),
    }
    merged['graph_model'] = {
        'parent': '赛道+场景主节点',
        'children': ['time_terms', 'location_terms', 'scene_elements', 'scene_sfx_terms', 'missing_scene_sfx_terms'],
        'node_example': f'{genre}::山林夜路' if genre else '山林夜路',
    }
    if not isinstance(merged.get('key_points'), list):
        merged['key_points'] = fallback['key_points']
    if not isinstance(merged.get('risks'), list):
        merged['risks'] = []
    else:
        merged['risks'] = _merge_unique([str(x).strip() for x in merged.get('risks') or [] if str(x).strip()])
    if not isinstance(merged.get('markdown'), str):
        merged['markdown'] = fallback['markdown']
    return merged


def _call_scene_building_once(
    *,
    segment_text: str,
    genre: str,
    expected_prompt: str,
    segment_no: int,
    start_char: int,
    end_char: int,
    debug_prompt: bool,
    llm_provider_override: str = '',
) -> dict:
    llm_json, llm_md, llm_meta = generate_report(
        'scene_building',
        {
            'kind': 'scene_building',
            'genre': genre,
            'raw_text': segment_text,
            'sentences': _split_sentences(segment_text),
            'llm_provider_override': llm_provider_override,
        },
        debug_prompt=debug_prompt,
    )
    result = _normalize_scene_report(llm_json, segment_text, genre)
    serializable_report = dict(result)
    result['analysis_mode'] = 'llm_structured' if isinstance(llm_json, dict) else 'fallback_rules'
    result['report_json'] = serializable_report
    result['report_markdown'] = llm_md or result.get('markdown') or ''
    result['llm_trace'] = llm_meta.get('llm_trace') or []
    result['effective_prompt_file'] = expected_prompt
    return {
        'segment_no': segment_no,
        'start_char': start_char,
        'end_char': end_char,
        'text_length': len(segment_text),
        'report_json': result.get('report_json') or {},
        'report_markdown': result.get('report_markdown') or '',
        'llm_trace': result.get('llm_trace') or [],
        'effective_prompt_file': expected_prompt,
    }


def _merge_scene_reports(text: str, genre: str, segment_reports: list[dict]) -> dict:
    if not segment_reports:
        return _fallback_scene_report(text, genre)
    merged_items: list[dict] = []
    key_points: list[str] = []
    risks: list[str] = []
    kp_seen = set()
    risk_seen = set()
    markdown_parts: list[str] = []
    title = '场景搭建分析'
    for seg in segment_reports:
        report = seg.get('report_json') or {}
        if isinstance(report.get('title'), str) and report.get('title'):
            title = report.get('title')
        merged_items.extend(report.get('scene_items') or [])
        for item in report.get('key_points') or []:
            value = str(item).strip()
            if value and value not in kp_seen:
                kp_seen.add(value)
                key_points.append(value)
        for item in report.get('risks') or []:
            value = str(item).strip()
            if value and value not in risk_seen:
                risk_seen.add(value)
                risks.append(value)
        md = str(report.get('markdown') or '').strip()
        if md:
            markdown_parts.append(md)
    final_items = _merge_scene_items(merged_items)
    gap_count = sum(len(_merge_unique((item.get('supporting_sfx_terms') or []) + (item.get('detail_sfx_terms') or []))) for item in final_items)
    derived_risks = _derive_scene_risks(final_items, segmented=len(segment_reports) > 1, fallback_mode=False)
    combined_risks = _merge_unique(risks + derived_risks)
    return {
        'title': title or '场景搭建分析',
        'genre': genre,
        'scene_items': final_items,
        'summary': {
            'scene_count': len(final_items),
            'asset_count': 0,
            'gap_count': gap_count,
        },
        'graph_model': {
            'parent': '赛道+场景主节点',
            'children': ['time_terms', 'location_terms', 'scene_elements', 'scene_sfx_terms', 'missing_scene_sfx_terms'],
            'node_example': f'{genre}::山林夜路' if genre else '山林夜路',
        },
        'key_points': key_points[:10] or ['已完成分段场景搭建合并分析'],
        'risks': combined_risks[:10],
        'markdown': '\n'.join(markdown_parts).strip() or f'已完成 {len(segment_reports)} 段场景搭建合并分析。',
    }


def analyze_scene_building(
    text: str,
    *,
    genre: str = DEFAULT_SCENE_GENRE,
    prompt_file: str | None = None,
    debug_prompt: bool = False,
    llm_provider_override: str = '',
) -> dict:
    normalized_genre, expected_prompt = _resolve_prompt(genre, prompt_file)
    if not text.strip():
        return _fallback_scene_report(text, normalized_genre)

    if llm_enabled(llm_provider_override):
        sentences = _split_sentences(text)
        segments = _segment_text(sentences, text)
        segment_reports = []
        all_trace = []
        for seg in segments:
            seg_report = _call_scene_building_once(
                segment_text=seg['text'],
                genre=normalized_genre,
                expected_prompt=expected_prompt,
                segment_no=seg['segment_no'],
                start_char=seg['start_char'],
                end_char=seg['end_char'],
                debug_prompt=debug_prompt,
                llm_provider_override=llm_provider_override,
            )
            segment_reports.append(seg_report)
            all_trace.extend(seg_report.get('llm_trace') or [])
        merged_report = _merge_scene_reports(text, normalized_genre, segment_reports)
        actual_prompt = all_trace[0].get('prompt_file') if all_trace else None
        segmentation = {
            'mode': 'single' if len(segment_reports) <= 1 else 'segmented',
            'single_pass_safe_chars': SINGLE_PASS_SAFE_CHARS,
            'segment_target_chars': SEGMENT_TARGET_CHARS,
            'segment_max_chars': SEGMENT_MAX_CHARS,
            'segment_count': len(segment_reports),
            'total_text_length': len(text),
            'segments': [
                {
                    'segment_no': s['segment_no'],
                    'start_char': s['start_char'],
                    'end_char': s['end_char'],
                    'text_length': s['text_length'],
                }
                for s in segment_reports
            ],
        }
        return {
            'report_json': merged_report,
            'report_markdown': '',
            'analysis_mode': 'llm-scene-building',
            'llm_structured': True,
            'llm_enabled': llm_enabled(llm_provider_override),
            'llm_provider_used': ((all_trace[0].get('call_meta') or {}).get('provider') if all_trace else ''),
            'llm_model_used': ((all_trace[0].get('call_meta') or {}).get('model') if all_trace else ''),
            'llm_fallback_applied': False,
            'llm_attempted_modes': [expected_prompt],
            'llm_trace': all_trace,
            'genre': normalized_genre,
            'effective_prompt_file': expected_prompt,
            'segmentation': segmentation,
            'segment_reports': segment_reports,
            'prompt_guard': {
                'expected': expected_prompt,
                'actual': actual_prompt,
                'passed': actual_prompt == expected_prompt if actual_prompt else False,
            },
            **merged_report,
        }

    result = _fallback_scene_report(text, normalized_genre)
    result['risks'] = _derive_scene_risks(
        result.get('scene_items') or [],
        segmented=False,
        fallback_mode=True,
    )
    serializable_report = dict(result)
    result['analysis_mode'] = 'fallback_rules'
    result['report_json'] = serializable_report
    result['report_markdown'] = result.get('markdown') or ''
    result['llm_trace'] = []
    result['effective_prompt_file'] = expected_prompt
    return result
