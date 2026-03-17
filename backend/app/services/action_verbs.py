import json
import re

from app.config import settings
from app.services.llm import (
    _chat_completion,
    _compact_evidence,
    _extract_json_blob,
    _load_prompt,
    _load_system_prompt,
    llm_enabled,
)

ACTION_PROMPTS_BY_GENRE = {
    '玄幻': 'V3-action_verbs_xuanhuan_task.txt',
    '言情': 'V3-action_verbs_yanqing_task.txt',
    '悬疑': 'V3-action_verbs_xuanyi_task.txt',
    '科幻': 'V3-action_verbs_kehuan_task.txt',
}
ALLOWED_ACTION_PROMPTS = set(ACTION_PROMPTS_BY_GENRE.values())
DEFAULT_ACTION_GENRE = '玄幻'
SINGLE_PASS_SAFE_CHARS = 350
SEGMENT_TARGET_CHARS = 320
SEGMENT_MAX_CHARS = 360


def _normalize_genre(genre: str | None) -> str:
    value = (genre or '').strip()
    return value if value in ACTION_PROMPTS_BY_GENRE else DEFAULT_ACTION_GENRE


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


def _fallback_action_report(text: str, genre: str) -> dict:
    return {
        'title': '人物动作动词提取',
        'genre': genre,
        'rule_summary': [
            '必须是人物发出的动作',
            '放在当前文本里仍然是动词',
            '必须是当下正在发生',
        ],
        'action_candidates': [],
        'qualified_actions': [],
        'key_points': ['本次未拿到结构化动词结果'],
        'risks': ['建议重试或优化提示词'],
        'markdown': f'未能从文本中稳定提取人物动作动词，请结合原文复核。文本长度：{len(text)}字。',
    }


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
    if forced_prompt in ALLOWED_ACTION_PROMPTS:
        prompt = forced_prompt
        normalized_genre = next((k for k, v in ACTION_PROMPTS_BY_GENRE.items() if v == forced_prompt), normalized_genre)
        return normalized_genre, prompt
    return normalized_genre, ACTION_PROMPTS_BY_GENRE[normalized_genre]


def _call_action_verbs_once(
    *,
    segment_text: str,
    genre: str,
    expected_prompt: str,
    segment_no: int,
    start_char: int,
    end_char: int,
) -> dict:
    payload = {
        'kind': 'action_verbs',
        'genre': genre,
        'raw_text': segment_text,
        'sentences': _split_sentences(segment_text),
    }
    compact = _compact_evidence(payload)
    task_prompt = _load_prompt(expected_prompt)
    system_prompt, system_prompt_file = _load_system_prompt()
    user_prompt = (
        f'{task_prompt}\n\n'
        '输出要求：\n'
        '1) 仅输出一个 JSON 对象，不要加代码块标记。\n'
        '2) 不要补充解释性前言或结尾。\n'
        '3) 字段名严格按任务提示词返回。\n\n'
        f'证据 JSON:\n{json.dumps(compact, ensure_ascii=False, indent=2)}'
    )
    try:
        raw_response, call_meta = _chat_completion(system_prompt, user_prompt, evidence_kind='action_verbs')
        report_json = _extract_json_blob(raw_response) if raw_response else None
    except Exception as e:
        raw_response = None
        report_json = None
        call_meta = {'status': 'service_exception', 'error': f'{type(e).__name__}: {e}'}

    trace = [
        {
            'mode': 'raw_action_verbs',
            'prompt_file': expected_prompt,
            'system_prompt_file': system_prompt_file,
            'evidence_kind': 'action_verbs',
            'task_prompt': task_prompt,
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
            'raw_response': raw_response,
            'call_meta': call_meta,
            '_source_path': f'segment_reports[{segment_no - 1}]',
        }
    ]
    if isinstance(report_json, dict):
        report_json['genre'] = genre
        report_json.setdefault('markdown', '')
    else:
        report_json = _fallback_action_report(segment_text, genre)
    return {
        'segment_no': segment_no,
        'start_char': start_char,
        'end_char': end_char,
        'text_length': len(segment_text),
        'report_json': report_json,
        'report_markdown': raw_response or '',
        'llm_trace': trace,
        'effective_prompt_file': expected_prompt,
    }


def _merge_reports(text: str, genre: str, segment_reports: list[dict]) -> dict:
    if not segment_reports:
        return _fallback_action_report(text, genre)

    merged_candidates = []
    seen = set()
    for seg in segment_reports:
        report = seg.get('report_json') or {}
        for row in report.get('action_candidates') or []:
            if not isinstance(row, dict):
                continue
            verb = str(row.get('verb') or '').strip()
            excerpt = str(row.get('sentence_excerpt') or '').strip()
            if not verb or not excerpt:
                continue
            dedupe_key = (verb, excerpt)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged_candidates.append(
                {
                    'candidate_no': len(merged_candidates) + 1,
                    'verb': verb,
                    'sentence_excerpt': excerpt,
                    'reason': str(row.get('reason') or '').strip(),
                }
            )

    key_points = []
    kp_seen = set()
    risks = []
    risk_seen = set()
    rules = None
    title = '人物动作动词提取'
    for seg in segment_reports:
        report = seg.get('report_json') or {}
        if not rules and isinstance(report.get('rule_summary'), list):
            rules = report.get('rule_summary')
        if isinstance(report.get('title'), str) and report.get('title'):
            title = report.get('title')
        for item in report.get('key_points') or []:
            text_item = str(item).strip()
            if text_item and text_item not in kp_seen:
                kp_seen.add(text_item)
                key_points.append(text_item)
        for item in report.get('risks') or []:
            text_item = str(item).strip()
            if text_item and text_item not in risk_seen:
                risk_seen.add(text_item)
                risks.append(text_item)

    merged = {
        'title': title,
        'genre': genre,
        'rule_summary': rules or [
            '必须是人物发出的动作',
            '放在当前文本里仍然是动词',
            '必须是当下正在发生',
        ],
        'action_candidates': merged_candidates,
        'qualified_actions': [
            {
                'verb': row['verb'],
                'sentence_excerpt': row['sentence_excerpt'],
            }
            for row in merged_candidates
        ],
        'key_points': key_points[:8],
        'risks': risks[:8],
        'markdown': '',
    }
    if not merged['key_points']:
        merged['key_points'] = [f'已完成 {len(segment_reports)} 段动作动词合并分析']
    return merged


def analyze_action_verbs(
    text: str,
    genre: str | None = None,
    prompt_file: str | None = None,
    report_mode: str | None = None,
    debug_prompt: bool = True,
) -> dict:
    _ = debug_prompt
    mode = report_mode or settings.report_mode_default
    normalized_genre, expected_prompt = _resolve_prompt(genre, prompt_file)
    sentences = _split_sentences(text)
    segments = _segment_text(sentences, text)
    segment_reports = []
    all_trace = []
    for seg in segments:
        seg_report = _call_action_verbs_once(
            segment_text=seg['text'],
            genre=normalized_genre,
            expected_prompt=expected_prompt,
            segment_no=seg['segment_no'],
            start_char=seg['start_char'],
            end_char=seg['end_char'],
        )
        segment_reports.append(seg_report)
        all_trace.extend(seg_report.get('llm_trace') or [])

    merged_report = _merge_reports(text, normalized_genre, segment_reports)
    actual_prompt = None
    if all_trace:
        actual_prompt = all_trace[0].get('prompt_file')

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
        'analysis_mode': 'llm-action-verbs',
        'llm_structured': True,
        'llm_enabled': llm_enabled(),
        'report_mode': mode,
        'effective_report_mode': expected_prompt,
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
    }
