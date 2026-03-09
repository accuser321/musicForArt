import re

from app.config import settings
from app.services.llm import generate_report, llm_enabled
from app.services.nlp_zh import tokenize_cn
from app.services.sfx_matcher import load_sfx_library, match_sfx_candidates

ACTION_KEYWORDS = {
    '冲': '冲锋',
    '杀': '近战',
    '奔': '奔跑',
    '拔剑': '武器出鞘',
    '破': '破坏',
    '坍塌': '坍塌',
    '嘶吼': '兽吼',
    '低语': '低语',
}

EMOTION_KEYWORDS = {
    '愤怒': '高压',
    '恐惧': '压迫',
    '悲伤': '低落',
    '紧张': '紧张',
    '平静': '平稳',
    '绝望': '低谷',
    '希望': '抬升',
}

SFX_MAP = {
    '冲锋': '群体脚步+战吼',
    '近战': '刀剑碰撞+短促重击',
    '奔跑': '急促脚步+衣物摩擦',
    '武器出鞘': '金属抽离声',
    '破坏': '碎裂+坠落',
    '坍塌': '结构崩塌低频轰鸣',
    '兽吼': '低频兽吼+空间混响',
    '低语': '近讲呼吸+房间底噪',
}


def _split_sentences_with_span(text: str) -> list[dict]:
    out = []
    start = 0
    for m in re.finditer(r'[。！？!?\n]+', text):
        end = m.start()
        raw = text[start:end]
        stripped = raw.strip()
        if stripped:
            left_trim = len(raw) - len(raw.lstrip())
            right_trim = len(raw) - len(raw.rstrip())
            out.append(
                {
                    'text': stripped,
                    'char_start': start + left_trim,
                    'char_end': end - right_trim,
                }
            )
        start = m.end()
    tail = text[start:]
    tail_stripped = tail.strip()
    if tail_stripped:
        left_trim = len(tail) - len(tail.lstrip())
        right_trim = len(tail) - len(tail.rstrip())
        out.append(
            {
                'text': tail_stripped,
                'char_start': start + left_trim,
                'char_end': len(text) - right_trim,
            }
        )
    return out


def _build_rule_report(scenes: list[dict]) -> str:
    lines = [
        '# 文本分析报告（规则版）',
        '',
        f'- 句子数：{len(scenes)}',
        '- 输出：场景动作、情绪曲线、音效建议',
        '',
        '## 场景建议',
    ]

    for scene in scenes:
        lines.append(
            f"- Scene {scene['scene_no']}: 强度 {scene['intensity']} | 动作 {', '.join(scene['actions']) or '无'} | 音效 {', '.join(scene['sfx']) or '无'}"
        )
    return '\n'.join(lines)


def _build_sfx_requirements(scenes: list[dict]) -> tuple[list[dict], list[str]]:
    library = load_sfx_library()
    req_map: dict[str, dict] = {}

    for scene in scenes:
        scene_no = scene.get('scene_no')
        for action in scene.get('actions', []):
            term = action
            rec = req_map.setdefault(
                term,
                {
                    'term': term,
                    'scene_nos': [],
                    'reasons': [],
                    'candidates': [],
                    'download_ready': False,
                    'selected_file': None,
                },
            )
            rec['scene_nos'].append(scene_no)
            rec['reasons'].append(f'场景{scene_no}动作：{action}')

    for term, rec in req_map.items():
        cands = match_sfx_candidates(term, library, top_n=3)
        rec['candidates'] = [
            {
                'file_name': c['file_name'],
                'score': c['score'],
                'canonical': c['canonical'],
            }
            for c in cands
        ]
        if cands:
            rec['download_ready'] = True
            rec['selected_file'] = cands[0]['file_name']
        rec['scene_nos'] = sorted(set(rec['scene_nos']))
        rec['reasons'] = sorted(set(rec['reasons']))

    requirements = sorted(
        req_map.values(),
        key=lambda x: (0 if x['download_ready'] else 1, -len(x['scene_nos']), x['term']),
    )
    quick_download_list = sorted({x['selected_file'] for x in requirements if x.get('selected_file')})
    return requirements, quick_download_list


def analyze_text_for_audiobook(text: str, report_mode: str | None = None, audio_context: dict | None = None) -> dict:
    mode = report_mode or settings.report_mode_default
    sentence_items = _split_sentences_with_span(text)
    scenes = []

    for idx, item in enumerate(sentence_items, start=1):
        sentence = item['text']
        actions = []
        emotions = []
        tokens = tokenize_cn(sentence)

        for k, v in ACTION_KEYWORDS.items():
            if k in sentence:
                actions.append(v)

        for k, v in EMOTION_KEYWORDS.items():
            if k in sentence:
                emotions.append(v)

        intensity = min(100, 20 + len(actions) * 20 + len(emotions) * 10)
        sfx = [SFX_MAP[a] for a in actions if a in SFX_MAP]

        scenes.append(
            {
                'scene_no': idx,
                'text': sentence,
                'char_start': item['char_start'],
                'char_end': item['char_end'],
                'tokens': tokens,
                'actions': sorted(set(actions)),
                'emotions': sorted(set(emotions)),
                'intensity': intensity,
                'sfx': sorted(set(sfx)),
            }
        )

    sfx_requirements, quick_download_list = _build_sfx_requirements(scenes)
    report_markdown = _build_rule_report(scenes)
    report_markdown += '\n\n## 音效需求清单\n'
    if not sfx_requirements:
        report_markdown += '- 未识别出明确动作音效需求。\n'
    else:
        for item in sfx_requirements:
            cands = '、'.join([c['file_name'] for c in item['candidates']]) if item['candidates'] else '暂无匹配文件'
            report_markdown += f"- {item['term']}（场景{','.join(str(x) for x in item['scene_nos'])}）→ 候选：{cands}\n"

    report_json = {
        'key_points': [
            '先按场景动作提取音效需求词，再按候选文件快速试听筛选',
            '优先处理 download_ready=true 的词条，先完成可落地版本',
            '对未匹配词条建议后续补充音效素材',
        ]
    }
    analysis_mode = 'rules-only+semantic'
    llm_structured = False
    effective_mode = None
    llm_fallback = False
    llm_attempted_modes: list[str] = []

    # 核心链路：音乐分析文本 + 系统证据 + 用户文本 联合调用 LLM
    if audio_context:
        payload = {
            'kind': 'text_with_music',
            'mode': mode,
            'raw_text': text,
            'music_analysis_text': str(audio_context.get('report_markdown') or ''),
            'audio_context': audio_context,
            'scenes': scenes,
        }
        llm_json, llm_md, llm_meta = generate_report(mode, payload)
        if llm_md:
            report_markdown = llm_md
            analysis_mode = 'llm+rules+music'
            llm_structured = bool(llm_json)
            effective_mode = llm_meta.get('effective_mode')
            llm_fallback = llm_meta.get('fallback_applied', False)
            llm_attempted_modes = llm_meta.get('attempted_modes', [])
            if isinstance(llm_json, dict):
                # 保留模型关键结论，同时拼接本地可执行音效清单
                base_points = llm_json.get('key_points') if isinstance(llm_json.get('key_points'), list) else []
                report_json = dict(llm_json)
                report_json['key_points'] = list(base_points) + report_json.get('key_points_local', [])
        else:
            llm_attempted_modes = llm_meta.get('attempted_modes', [])

    return {
        'scenes': scenes,
        'sfx_requirements': sfx_requirements,
        'quick_download_list': quick_download_list,
        'report_markdown': report_markdown,
        'report_json': report_json,
        'analysis_mode': analysis_mode,
        'llm_structured': llm_structured,
        'llm_enabled': llm_enabled(),
        'report_mode': mode,
        'effective_report_mode': effective_mode,
        'llm_fallback_applied': llm_fallback,
        'llm_attempted_modes': llm_attempted_modes,
        'integration_mode': 'music+text+prompt' if audio_context else 'text-only',
    }
