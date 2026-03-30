import csv
import json
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from app.config import settings
from app.services.sfx_matcher import load_sfx_library, match_sfx_candidates


def _safe_slug(text: str) -> str:
    out = ''.join(c if c.isalnum() or c in ('-', '_') else '_' for c in text.strip())
    return out[:60] or 'project'


def export_cue_csv(project_id: int, project_title: str, cues: list[dict]) -> Path:
    export_dir = Path('./exports').resolve()
    export_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{project_id}_{_safe_slug(project_title)}_cue_{ts}.csv"
    target = export_dir / filename

    headers = [
        'scene_no',
        'target_time_sec',
        'marker_label',
        'dialogue_music_ratio',
        'mix_tip',
        'recommended_sfx',
    ]

    with target.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for cue in cues:
            writer.writerow(
                {
                    'scene_no': cue.get('scene_no'),
                    'target_time_sec': cue.get('target_time_sec'),
                    'marker_label': cue.get('marker_label'),
                    'dialogue_music_ratio': cue.get('dialogue_music_ratio'),
                    'mix_tip': cue.get('mix_tip'),
                    'recommended_sfx': ' | '.join(cue.get('recommended_sfx', [])),
                }
            )

    return target


def export_sfx_zip(project_id: int, project_title: str, cues: list[dict]) -> tuple[Path, dict]:
    export_dir = Path('./exports').resolve()
    export_dir.mkdir(parents=True, exist_ok=True)

    sfx_dir = Path('./assets/sfx').resolve()
    sfx_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_name = f"{project_id}_{_safe_slug(project_title)}_sfx_{ts}.zip"
    zip_path = export_dir / zip_name

    required = set()
    for cue in cues:
        for name in cue.get('recommended_sfx', []):
            if name:
                required.add(name.strip())

    found_files = []
    missing = []
    matched_detail = {}
    library = load_sfx_library()
    selected_files = set()

    with zipfile.ZipFile(zip_path, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            'project_id': project_id,
            'project_title': project_title,
            'required_sfx': sorted(required),
            'found_files': [],
            'missing_sfx': [],
            'generated_at': datetime.now().isoformat(),
        }

        for name in sorted(required):
            cands = match_sfx_candidates(name, library, top_n=3)
            chosen = None
            for c in cands:
                if c['file_name'] not in selected_files:
                    chosen = c
                    break
            if chosen is None and cands:
                chosen = cands[0]

            if chosen is None:
                missing.append(name)
                matched_detail[name] = {'chosen': None, 'candidates': []}
                continue

            selected_files.add(chosen['file_name'])
            matched_path = Path(chosen['file_path'])
            arc_name = f"sfx/{matched_path.name}"
            zf.write(matched_path, arc_name)
            found_files.append(matched_path.name)
            matched_detail[name] = {'chosen': chosen, 'candidates': cands}

        manifest['found_files'] = found_files
        manifest['missing_sfx'] = missing
        manifest['matched_detail'] = matched_detail

        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr(
            'README.txt',
            'This bundle is generated from fusion cues. Matching uses Chinese token/semantic expansion and vector-like similarity.\n',
        )

    return zip_path, {
        'required_count': len(required),
        'found_count': len(found_files),
        'missing_count': len(missing),
        'missing_sfx': missing,
    }


_XLSX_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
_XLSX_COLS = [chr(ord('A') + i) for i in range(17)]
_ACTION_TEMPLATE = Path('./assets/templates/action_asset_template.xlsx').resolve()

_SCENE_KEYWORDS = [
    '天空', '半空', '虚空', '禁地', '山脊', '山麓', '莽山', '结界', '地面', '山谷', '深处', '高空',
]
_FEATURE_KEYWORDS = [
    '紫金葫芦', '葫芦', '光剑', '符宝', '雷石', '火球', '冰刃', '血鼎', '结界', '长虹', '掌印',
]
_DETAIL_KEYWORDS = [
    '巨响', '鲜血', '沟壑', '暴雨', '嗡鸣', '心跳', '巨雷', '吐血', '剑雨', '窟窿', '破空', '风压',
]
_MAGIC_KEYWORDS = [
    '雷诀', '掐诀', '结印', '玄力', '天雷', '血鼎', '符宝', '法阵', '结界', '光剑', '掌印',
]
_EMOTION_KEYWORDS = {
    '惊惧': ['不好', '惊', '胆敢', '不及'],
    '痛苦': ['痛', '鲜血', '吐出', '吐血', '晕死'],
    '紧张': ['不敢分神', '穷追不舍', '迟疑不得', '丝毫不敢'],
    '愤怒': ['大怒', '何方妖孽', '竖子尔敢'],
    '决绝': ['心一横', '怎么甘心', '可不是来当炮灰'],
    '贪婪': ['贪婪地吸收'],
}


def _normalize_match_text(text: str) -> str:
    if not text:
        return ''
    return re.sub(r'[\s\r\n\t“”"‘’\'，。！？、：；,.!?:;—…（）()]', '', text)


def _extract_keywords(text: str, pool: list[str]) -> list[str]:
    if not text:
        return []
    hits: list[str] = []
    for kw in pool:
        if kw in text and kw not in hits:
            hits.append(kw)
    return hits


def _extract_emotions(text: str) -> list[str]:
    if not text:
        return []
    hits: list[str] = []
    for label, kws in _EMOTION_KEYWORDS.items():
        if any(kw in text for kw in kws):
            hits.append(label)
    return hits


def _find_best_clause(sentence_excerpt: str, clause_timeline: list[dict]) -> dict | None:
    target = _normalize_match_text(sentence_excerpt)
    if not target:
        return None
    best = None
    best_score = -1
    for clause in clause_timeline:
        clause_text = _normalize_match_text(str(clause.get('text') or ''))
        if not clause_text:
            continue
        score = 0
        if target == clause_text:
            score = 1000
        elif target in clause_text or clause_text in target:
            score = min(len(target), len(clause_text))
        else:
            common = sum(1 for ch in set(target) if ch in clause_text)
            score = common
        if score > best_score:
            best_score = score
            best = clause
    return best


def _find_best_cue(sentence_excerpt: str, cues: list[dict], clause: dict | None) -> dict | None:
    target = _normalize_match_text(sentence_excerpt)
    best = None
    best_score = -1
    for cue in cues:
        cue_text = _normalize_match_text(str(cue.get('text_excerpt') or ''))
        score = 0
        if target and cue_text:
            if target == cue_text:
                score = 1000
            elif target in cue_text or cue_text in target:
                score = min(len(target), len(cue_text))
            else:
                score = sum(1 for ch in set(target) if ch in cue_text)
        if clause and score < 2:
            cue_time = float(cue.get('target_time_sec') or cue.get('music_segment_start_sec') or 0)
            clause_time = float(clause.get('start_sec') or 0)
            distance = abs(cue_time - clause_time)
            score = max(score, int(100 - min(distance * 10, 99)))
        if score > best_score:
            best_score = score
            best = cue
    return best


def _format_sec_range(start_sec: float | int | None, end_sec: float | int | None) -> str:
    if start_sec is None or end_sec is None:
        return ''
    def _fmt(v: float | int) -> str:
        m = int(float(v) // 60)
        s = int(round(float(v) % 60))
        return f'{m:02d}:{s:02d}'
    return f'{_fmt(start_sec)}-{_fmt(end_sec)}'


def _music_usage_text(cue: dict | None, audio_tags: list[str]) -> str:
    parts: list[str] = []
    if cue:
        marker = (cue.get('marker_label') or '').strip()
        mix_tip = (cue.get('mix_tip') or '').strip()
        if marker:
            parts.append(f'参考节点：{marker}')
        if mix_tip:
            parts.append(mix_tip)
    tags = [str(x).strip() for x in audio_tags if str(x).strip()]
    if tags:
        parts.append(f'音乐特征：{" / ".join(tags[:3])}')
    return '；'.join(parts)


def _feature_usage_text(verb: str, cue: dict | None, magic_terms: list[str], detail_terms: list[str]) -> str:
    suggestions: list[str] = []
    if magic_terms:
        suggestions.append(f'围绕{" / ".join(magic_terms[:2])}强化能量与法术反馈')
    if detail_terms:
        suggestions.append(f'可补充{" / ".join(detail_terms[:2])}等质感细节')
    rec_sfx = cue.get('recommended_sfx', []) if cue else []
    if rec_sfx:
        suggestions.append(f'执行单建议：{" / ".join([str(x) for x in rec_sfx[:3]])}')
    if not suggestions and verb:
        suggestions.append(f'建议围绕“{verb}”补充动作层与整体层音效')
    return '；'.join(suggestions)


def _build_action_asset_rows(
    project_title: str,
    genre: str,
    created_at: datetime,
    audio_tags: list[str],
    clause_timeline: list[dict],
    cues: list[dict],
    action_result: dict,
) -> list[list[str]]:
    report_json = action_result.get('report_json') if isinstance(action_result, dict) else {}
    if not isinstance(report_json, dict):
        report_json = {}
    candidates = report_json.get('action_candidates', [])
    rows: list[list[str]] = []
    export_date = created_at.strftime('%Y-%m-%d')
    for idx, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            continue
        verb = str(item.get('verb') or '').strip()
        excerpt = str(item.get('sentence_excerpt') or '').strip()
        if not verb:
            continue
        clause = _find_best_clause(excerpt, clause_timeline)
        cue = _find_best_cue(excerpt, cues, clause)
        scene_terms = _extract_keywords(excerpt, _SCENE_KEYWORDS)
        feature_terms = _extract_keywords(excerpt, _FEATURE_KEYWORDS)
        detail_terms = _extract_keywords(excerpt, _DETAIL_KEYWORDS)
        magic_terms = _extract_keywords(excerpt, _MAGIC_KEYWORDS)
        emotion_terms = _extract_emotions(excerpt)
        row = [
            export_date,  # A 日期
            project_title,  # B 名称
            f'{genre}·动作分析资产',  # C 类型
            str(idx),  # D 序号
            _format_sec_range(
                clause.get('start_sec') if clause else None,
                clause.get('end_sec') if clause else None,
            ),  # E 分时
            excerpt,  # F 镜头场景
            _music_usage_text(cue, audio_tags),  # G 音乐运用
            '',  # H 背景元素（场景模块二期再补）
            '',  # I 特征元素（场景模块二期再补）
            '',  # J 细节元素（场景模块二期再补）
            verb,  # K 动作
            ' / '.join(emotion_terms),  # L 情绪心理
            ' / '.join(magic_terms),  # M 武器/法术
            _feature_usage_text(verb, cue, magic_terms, detail_terms),  # N 特效运用
            '',  # O 效果器
            '',  # P 其他运用
            '系统自动填充（动作分析一期）',  # Q 备注
        ]
        rows.append(row)
    return rows


def _build_sheet_rows_from_template(template_xml: bytes, data_rows: list[list[str]]) -> bytes:
    ET.register_namespace('', _XLSX_NS)
    ns = {'a': _XLSX_NS}
    root = ET.fromstring(template_xml)
    sheet_data = root.find('a:sheetData', ns)
    if sheet_data is None:
        raise ValueError('sheetData not found in template')

    existing_rows = sheet_data.findall('a:row', ns)
    if len(existing_rows) < 4:
        raise ValueError('template must contain at least 4 rows')
    header_rows = [deepcopy(existing_rows[i]) for i in range(3)]
    template_row = existing_rows[3]
    style_map = {}
    for cell in template_row.findall('a:c', ns):
        ref = cell.attrib.get('r', '')
        col = re.sub(r'\d+', '', ref)
        style_map[col] = cell.attrib.get('s')
    new_rows = header_rows
    for offset, values in enumerate(data_rows, start=4):
        row_el = ET.Element(f'{{{_XLSX_NS}}}row', {'r': str(offset), 'spans': '1:17'})
        for idx, col in enumerate(_XLSX_COLS):
            attrs = {'r': f'{col}{offset}'}
            style = style_map.get(col)
            if style:
                attrs['s'] = style
            text = values[idx] if idx < len(values) else ''
            if text:
                attrs['t'] = 'inlineStr'
            cell_el = ET.SubElement(row_el, f'{{{_XLSX_NS}}}c', attrs)
            if text:
                is_el = ET.SubElement(cell_el, f'{{{_XLSX_NS}}}is')
                t_el = ET.SubElement(is_el, f'{{{_XLSX_NS}}}t')
                if text.startswith(' ') or text.endswith(' ') or '\n' in text:
                    t_el.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                t_el.text = text
        new_rows.append(row_el)

    sheet_data.clear()
    for row in new_rows:
        sheet_data.append(row)

    dimension = root.find('a:dimension', ns)
    if dimension is not None:
        last_row = max(4, len(data_rows) + 3)
        dimension.set('ref', f'A1:Q{last_row}')

    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def export_action_asset_xlsx(
    project_id: int,
    project_title: str,
    project_created_at: datetime,
    genre: str,
    audio_tags: list[str],
    clause_timeline: list[dict],
    cues: list[dict],
    action_result: dict,
) -> Path:
    if not _ACTION_TEMPLATE.exists():
        raise FileNotFoundError(f'Action asset template not found: {_ACTION_TEMPLATE}')

    export_dir = Path('./exports').resolve()
    export_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{project_id}_{_safe_slug(project_title)}_action_asset_{ts}.xlsx'
    target = export_dir / filename

    rows = _build_action_asset_rows(
        project_title=project_title,
        genre=genre,
        created_at=project_created_at,
        audio_tags=audio_tags,
        clause_timeline=clause_timeline,
        cues=cues,
        action_result=action_result,
    )

    with zipfile.ZipFile(_ACTION_TEMPLATE, 'r') as zin:
        template_sheet = zin.read('xl/worksheets/sheet1.xml')
        updated_sheet = _build_sheet_rows_from_template(template_sheet, rows)
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = updated_sheet if item.filename == 'xl/worksheets/sheet1.xml' else zin.read(item.filename)
                zout.writestr(item, data)

    return target
