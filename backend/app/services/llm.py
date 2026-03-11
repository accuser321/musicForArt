import json
import re
import sys
import time
import uuid
import http.client
import urllib.error
import urllib.request
from pathlib import Path

from app.config import settings

PROMPT_DIR = Path(__file__).resolve().parent.parent / 'prompts'
SYSTEM_PROMPT_FILE = ''
PROMPT_CHAIN_BY_KIND = {
    'audio': ['V3-music_analysis_task.txt'],
    'text_analysis': ['V3-text_analysis_task.txt'],
    'text_with_music': ['V3-text_analysis_task.txt'],
    'fusion': ['V3-production_analysis_task.txt'],
    'director_final': ['director_final_task.txt'],
}
DEFAULT_SYSTEM_PROMPT_FALLBACK = (
    '你是有声书后期分析助手。请严格遵守任务提示词中的输入输出约束。'
)


def _load_prompt(name: str) -> str:
    if not name:
        return ''
    p = PROMPT_DIR / name
    if not p.exists() or not p.is_file():
        return ''
    return p.read_text(encoding='utf-8').strip()


def _load_system_prompt() -> tuple[str, str]:
    prompt = _load_prompt(SYSTEM_PROMPT_FILE)
    if prompt:
        return prompt, SYSTEM_PROMPT_FILE
    return DEFAULT_SYSTEM_PROMPT_FALLBACK, '(fallback)'


def llm_enabled() -> bool:
    return bool(settings.llm_api_key and settings.llm_model and settings.llm_base_url)


def _extract_json_blob(text: str) -> dict | None:
    if not text:
        return None

    fenced = re.search(r'```json\s*(\{.*?\})\s*```', text, flags=re.S)
    candidate = fenced.group(1) if fenced else text

    first = candidate.find('{')
    last = candidate.rfind('}')
    if first == -1 or last == -1 or last <= first:
        return None

    try:
        return json.loads(candidate[first:last + 1])
    except json.JSONDecodeError:
        return None


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_contract(payload: dict, evidence_kind: str | None) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, 'payload is not object'

    kind = (evidence_kind or '').strip().lower()
    def _validate_sections_and_structure(obj: dict) -> tuple[bool, str]:
        if not isinstance(obj.get('sections'), list):
            return False, 'sections must be list'
        if not isinstance(obj.get('structure_logic'), dict):
            return False, 'structure_logic must be object'
        sl = obj['structure_logic']
        for k in ['pattern_guess', 'repeat_groups', 'evidence']:
            if k not in sl:
                return False, f'structure_logic missing key: {k}'
        for i, row in enumerate(obj['sections']):
            if not isinstance(row, dict):
                return False, f'sections[{i}] must be object'
            req = [
                'section_no',
                'section_type',
                'label',
                'start_sec',
                'end_sec',
                'instruments',
                'new_instruments_vs_prev',
                'energy_level',
                'layer_progression',
            ]
            for rk in req:
                if rk not in row:
                    return False, f'sections[{i}] missing key: {rk}'
        return True, ''

    if kind == 'text_with_music':
        required = [
            'title',
            'fit_verdict',
            'key_points',
            'sections',
            'structure_logic',
            'scene_alignment',
            'production_notes',
            'risks',
            'markdown',
        ]
        for k in required:
            if k not in payload:
                return False, f'missing key: {k}'
        fv = payload.get('fit_verdict')
        if not isinstance(fv, dict):
            return False, 'fit_verdict must be object'
        if not isinstance(fv.get('verdict'), str):
            return False, 'fit_verdict.verdict must be string'
        if not _is_num(fv.get('score')):
            return False, 'fit_verdict.score must be number'
        if not isinstance(fv.get('reasons'), list):
            return False, 'fit_verdict.reasons must be list'
        sa = payload.get('scene_alignment')
        if not isinstance(sa, list):
            return False, 'scene_alignment must be list'
        for i, row in enumerate(sa):
            if not isinstance(row, dict):
                return False, f'scene_alignment[{i}] must be object'
            row_required = [
                'scene_no',
                'text_start_char',
                'text_end_char',
                'text_excerpt',
                'music_start_sec',
                'music_end_sec',
                'entry_reason',
                'dialogue_music_ratio',
                'sfx',
            ]
            for rk in row_required:
                if rk not in row:
                    return False, f'scene_alignment[{i}] missing key: {rk}'
        ok, reason = _validate_sections_and_structure(payload)
        if not ok:
            return False, reason
        return True, ''

    if kind == 'text_analysis':
        required = [
            'title',
            'text_theme',
            'fit_with_music',
            'scene_units',
            'clause_timeline',
            'emotion_curve',
            'sfx_requirements',
            'key_points',
            'risks',
            'markdown',
        ]
        for k in required:
            if k not in payload:
                return False, f'missing key: {k}'
        fm = payload.get('fit_with_music')
        if not isinstance(fm, dict):
            return False, 'fit_with_music must be object'
        if not isinstance(fm.get('verdict'), str):
            return False, 'fit_with_music.verdict must be string'
        if not _is_num(fm.get('score')):
            return False, 'fit_with_music.score must be number'
        if not isinstance(fm.get('reasons'), list):
            return False, 'fit_with_music.reasons must be list'

        su = payload.get('scene_units')
        if not isinstance(su, list):
            return False, 'scene_units must be list'
        for i, row in enumerate(su):
            if not isinstance(row, dict):
                return False, f'scene_units[{i}] must be object'
            row_required = [
                'scene_no',
                'text_start_char',
                'text_end_char',
                'text_excerpt',
                'emotion',
                'emotion_change',
                'action_tags',
                'intensity',
                'music_need',
                'entry_hint',
                'exit_hint',
                'sfx_terms',
            ]
            for rk in row_required:
                if rk not in row:
                    return False, f'scene_units[{i}] missing key: {rk}'
        cl = payload.get('clause_timeline')
        if not isinstance(cl, list):
            return False, 'clause_timeline must be list'
        for i, row in enumerate(cl):
            if not isinstance(row, dict):
                return False, f'clause_timeline[{i}] must be object'
            row_required = [
                'clause_no',
                'text_start_char',
                'text_end_char',
                'text',
                'punct',
                'start_sec',
                'end_sec',
            ]
            for rk in row_required:
                if rk not in row:
                    return False, f'clause_timeline[{i}] missing key: {rk}'
        if not isinstance(payload.get('emotion_curve'), list):
            return False, 'emotion_curve must be list'
        if not isinstance(payload.get('sfx_requirements'), list):
            return False, 'sfx_requirements must be list'
        if not isinstance(payload.get('key_points'), list):
            return False, 'key_points must be list'
        if not isinstance(payload.get('risks'), list):
            return False, 'risks must be list'
        return True, ''

    if kind == 'fusion':
        required = [
            'title',
            'fit_verdict',
            'key_points',
            'sections',
            'structure_logic',
            'music_entry_plan',
            'hit_points',
            'risks',
            'export_hints',
            'markdown',
        ]
        for k in required:
            if k not in payload:
                return False, f'missing key: {k}'
        fv = payload.get('fit_verdict')
        if not isinstance(fv, dict):
            return False, 'fit_verdict must be object'
        if not isinstance(payload.get('music_entry_plan'), list):
            return False, 'music_entry_plan must be list'
        for i, row in enumerate(payload.get('music_entry_plan') or []):
            if not isinstance(row, dict):
                return False, f'music_entry_plan[{i}] must be object'
            req = [
                'scene_no',
                'text_start_char',
                'text_end_char',
                'text_excerpt',
                'music_start_sec',
                'music_end_sec',
                'entry_reason',
                'dialogue_music_ratio',
                'sfx',
            ]
            for rk in req:
                if rk not in row:
                    return False, f'music_entry_plan[{i}] missing key: {rk}'
        ok, reason = _validate_sections_and_structure(payload)
        if not ok:
            return False, reason
        return True, ''

    if kind == 'director_final':
        required = [
            'title',
            'fit_verdict',
            'sections',
            'structure_logic',
            'timeline_direction',
            'key_points',
            'risks',
            'action_list',
            'markdown',
        ]
        for k in required:
            if k not in payload:
                return False, f'missing key: {k}'
        fv = payload.get('fit_verdict')
        if not isinstance(fv, dict) or not _is_num(fv.get('score')):
            return False, 'fit_verdict invalid'
        if not isinstance(payload.get('timeline_direction'), list):
            return False, 'timeline_direction must be list'
        ok, reason = _validate_sections_and_structure(payload)
        if not ok:
            return False, reason
        return True, ''

    return True, ''


def _chat_completion(system_prompt: str, user_prompt: str) -> tuple[str | None, dict]:
    if not llm_enabled():
        return None, {'status': 'disabled', 'error': 'llm not enabled'}

    payload = {
        'model': settings.llm_model,
        'temperature': settings.llm_temperature,
        'max_tokens': settings.llm_max_tokens,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
    }

    body = json.dumps(payload).encode('utf-8')
    url = settings.llm_base_url.rstrip('/') + '/chat/completions'
    req = urllib.request.Request(
        url=url,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {settings.llm_api_key}',
        },
        method='POST',
    )
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        req_id = uuid.uuid4().hex[:8]
        t0 = time.perf_counter()
        print(
            f'[LLM START] req={req_id} attempt={attempt}/{max_attempts} provider={settings.llm_provider} model={settings.llm_model} timeout={settings.llm_timeout_sec}s',
            file=sys.stderr,
        )
        try:
            with urllib.request.urlopen(req, timeout=settings.llm_timeout_sec) as resp:
                raw = resp.read().decode('utf-8')
                data = json.loads(raw)
                content = data['choices'][0]['message']['content'].strip()
                elapsed = int((time.perf_counter() - t0) * 1000)
                print(f'[LLM OK] req={req_id} elapsed_ms={elapsed} chars={len(content)}', file=sys.stderr)
                return content, {'status': 'ok', 'request_id': req_id, 'elapsed_ms': elapsed, 'attempt': attempt}
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode('utf-8', errors='ignore')
            except Exception:
                err_body = ''
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM HTTPError] req={req_id} elapsed_ms={elapsed} code={e.code} reason={e.reason} body={err_body[:500]}', file=sys.stderr)
            retryable = e.code in {408, 429, 500, 502, 503, 504}
            if retryable and attempt < max_attempts:
                time.sleep(0.8)
                continue
            return None, {'status': 'http_error', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': f'{e.code} {e.reason}', 'body': err_body[:500], 'attempt': attempt}
        except urllib.error.URLError as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM URLError] req={req_id} elapsed_ms={elapsed} reason={e.reason}', file=sys.stderr)
            if attempt < max_attempts:
                time.sleep(0.8)
                continue
            return None, {'status': 'url_error', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': str(e.reason), 'attempt': attempt}
        except TimeoutError:
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM TimeoutError] req={req_id} elapsed_ms={elapsed} request timed out', file=sys.stderr)
            if attempt < max_attempts:
                time.sleep(0.8)
                continue
            return None, {'status': 'timeout', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': 'request timed out', 'attempt': attempt}
        except http.client.IncompleteRead:
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM IncompleteRead] req={req_id} elapsed_ms={elapsed} upstream connection closed unexpectedly', file=sys.stderr)
            if attempt < max_attempts:
                time.sleep(0.8)
                continue
            return None, {'status': 'incomplete_read', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': 'upstream connection closed unexpectedly', 'attempt': attempt}
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM ParseError] req={req_id} elapsed_ms={elapsed} {type(e).__name__}: {e}', file=sys.stderr)
            return None, {'status': 'parse_error', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': f'{type(e).__name__}: {e}', 'attempt': attempt}
    return None, {'status': 'unknown_error', 'error': 'llm call failed unexpectedly'}


def _compact_evidence(evidence_payload: dict) -> dict:
    data = dict(evidence_payload)
    if isinstance(data.get('raw_text'), str) and len(data['raw_text']) > 1500:
        data['raw_text'] = data['raw_text'][:1500] + '...'
    if isinstance(data.get('scenes'), list) and len(data['scenes']) > 20:
        data['scenes'] = data['scenes'][:20]
    if isinstance(data.get('audio_markers'), list) and len(data['audio_markers']) > 20:
        data['audio_markers'] = data['audio_markers'][:20]
    if isinstance(data.get('cues'), list) and len(data['cues']) > 30:
        data['cues'] = data['cues'][:30]
    return data


def _resolve_prompt_chain(evidence_kind: str, task_mode: str) -> list[str]:
    kind = (evidence_kind or '').strip().lower()
    if kind in PROMPT_CHAIN_BY_KIND:
        return list(PROMPT_CHAIN_BY_KIND[kind])
    mode = (task_mode or '').strip().lower()
    if mode == 'teaching':
        return ['music_teaching_task.txt']
    return ['production_task.txt']


def _output_limits_by_kind(evidence_kind: str) -> str:
    kind = (evidence_kind or '').strip().lower()
    if kind == 'audio':
        return (
            '长度硬约束（必须执行）：\n'
            '- sections 最多 6 段；hit_points 最多 6 个。\n'
            '- key_points 最多 6 条，每条 <= 32 个汉字。\n'
            '- mix_notes 最多 6 条，每条 <= 38 个汉字。\n'
            '- summary <= 60 个汉字。\n'
            '- markdown 控制在 450-700 个汉字。\n'
            '- 整体输出禁止冗长重复，避免截断。'
        )
    if kind == 'text_analysis':
        return (
            '长度硬约束（必须执行）：\n'
            '- scene_units<=10，clause_timeline<=18，sfx_requirements<=16。\n'
            '- key_points<=5（每条<=28字），risks<=5（每条<=32字）。\n'
            '- markdown 控制在 280-520 字，避免冗长。\n'
            '- 若长度受限，优先保证 JSON 字段完整，再精简 markdown。'
        )
    if kind == 'fusion':
        return (
            '长度硬约束（必须执行）：\n'
            '- music_entry_plan<=10，sections<=8，hit_points<=8。\n'
            '- key_points/risks/export_hints 各<=6，单条尽量短句。\n'
            '- markdown 控制在 500-800 字，避免重复。'
        )
    return (
        '长度控制要求：\n'
        '- 关键列表项避免冗长，每条尽量一句话。\n'
        '- markdown 保持精炼，避免重复展开。'
    )


def generate_report(task_mode: str, evidence_payload: dict, debug_prompt: bool = False) -> tuple[dict | None, str | None, dict]:
    system_prompt, system_prompt_file = _load_system_prompt()
    compact = _compact_evidence(evidence_payload)
    evidence_kind = str(compact.get('kind') or '').strip().lower()
    attempts: list[str] = []
    trace: list[dict] = []
    first_unstructured_raw: str | None = None
    first_unstructured_prompt_file: str | None = None
    _ = debug_prompt  # 保持接口兼容，当前固定开启追踪

    custom_prompt_files = compact.get('prompt_files')
    if isinstance(custom_prompt_files, list) and custom_prompt_files:
        prompt_chain = [str(x).strip() for x in custom_prompt_files if str(x).strip()]
    else:
        prompt_chain = _resolve_prompt_chain(evidence_kind=evidence_kind, task_mode=task_mode)
    output_limits = _output_limits_by_kind(evidence_kind=evidence_kind)
    for prompt_file in prompt_chain:
        attempts.append(prompt_file)
        print(f'[LLM MODE] requested={task_mode} trying_prompt={prompt_file}', file=sys.stderr)
        task_prompt = _load_prompt(prompt_file)
        user_prompt = (
            f'{task_prompt}\n\n'
            '输出要求：\n'
            '1) 先输出 JSON（可直接解析）。\n'
            '2) 再输出 markdown 字段对应的完整内容。\n\n'
            f'{output_limits}\n\n'
            f'证据 JSON:\n{json.dumps(compact, ensure_ascii=False, indent=2)}'
        )

        raw, call_meta = _chat_completion(system_prompt, user_prompt)
        trace.append(
            {
                'mode': 'core_prompt_chain',
                'prompt_file': prompt_file,
                'system_prompt_file': system_prompt_file,
                'evidence_kind': evidence_kind,
                'task_prompt': task_prompt,
                'system_prompt': system_prompt,
                'user_prompt': user_prompt,
                'raw_response': raw,
                'call_meta': call_meta,
            }
        )
        if not raw:
            continue

        parsed = _extract_json_blob(raw)
        if parsed and isinstance(parsed, dict):
            ok, reason = _validate_contract(parsed, evidence_kind=evidence_kind)
            if not ok:
                print(f'[LLM CONTRACT INVALID] prompt={prompt_file} reason={reason}', file=sys.stderr)
                if trace:
                    trace[-1]['contract_valid'] = False
                    trace[-1]['contract_reason'] = reason
                continue
            if trace:
                trace[-1]['contract_valid'] = True
                trace[-1]['contract_reason'] = ''
            markdown = parsed.get('markdown') if isinstance(parsed.get('markdown'), str) else raw
            return parsed, markdown, {
                'requested_mode': task_mode,
                'effective_mode': prompt_file,
                'attempted_modes': attempts,
                'fallback_applied': len(attempts) > 1,
                'custom_prompt_chain': prompt_chain,
                'system_prompt_file': system_prompt_file,
                'llm_trace': trace,
            }

        if first_unstructured_raw is None:
            first_unstructured_raw = raw
            first_unstructured_prompt_file = prompt_file
            print(f'[LLM WARN] prompt={prompt_file} returned non-JSON, fallback continues', file=sys.stderr)

    if first_unstructured_raw is not None:
        return None, first_unstructured_raw, {
            'requested_mode': task_mode,
            'effective_mode': first_unstructured_prompt_file,
            'attempted_modes': attempts,
            'fallback_applied': True,
            'custom_prompt_chain': prompt_chain,
            'system_prompt_file': system_prompt_file,
            'llm_trace': trace,
        }

    return None, None, {
        'requested_mode': task_mode,
        'effective_mode': None,
        'attempted_modes': attempts,
        'fallback_applied': False,
        'custom_prompt_chain': prompt_chain,
        'system_prompt_file': system_prompt_file,
        'llm_trace': trace,
    }
