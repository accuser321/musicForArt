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


def _load_prompt(name: str) -> str:
    p = PROMPT_DIR / name
    if not p.exists():
        return ''
    return p.read_text(encoding='utf-8').strip()


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
    req_id = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    print(
        f'[LLM START] req={req_id} provider={settings.llm_provider} model={settings.llm_model} timeout={settings.llm_timeout_sec}s',
        file=sys.stderr,
    )

    try:
        with urllib.request.urlopen(req, timeout=settings.llm_timeout_sec) as resp:
            raw = resp.read().decode('utf-8')
            data = json.loads(raw)
            content = data['choices'][0]['message']['content'].strip()
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM OK] req={req_id} elapsed_ms={elapsed} chars={len(content)}', file=sys.stderr)
            return content, {'status': 'ok', 'request_id': req_id, 'elapsed_ms': elapsed}
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode('utf-8', errors='ignore')
        except Exception:
            err_body = ''
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f'[LLM HTTPError] req={req_id} elapsed_ms={elapsed} code={e.code} reason={e.reason} body={err_body[:500]}', file=sys.stderr)
        return None, {'status': 'http_error', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': f'{e.code} {e.reason}', 'body': err_body[:500]}
    except urllib.error.URLError as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f'[LLM URLError] req={req_id} elapsed_ms={elapsed} reason={e.reason}', file=sys.stderr)
        return None, {'status': 'url_error', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': str(e.reason)}
    except TimeoutError:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f'[LLM TimeoutError] req={req_id} elapsed_ms={elapsed} request timed out', file=sys.stderr)
        return None, {'status': 'timeout', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': 'request timed out'}
    except http.client.IncompleteRead:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f'[LLM IncompleteRead] req={req_id} elapsed_ms={elapsed} upstream connection closed unexpectedly', file=sys.stderr)
        return None, {'status': 'incomplete_read', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': 'upstream connection closed unexpectedly'}
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f'[LLM ParseError] req={req_id} elapsed_ms={elapsed} {type(e).__name__}: {e}', file=sys.stderr)
        return None, {'status': 'parse_error', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': f'{type(e).__name__}: {e}'}


def _task_template(task_mode: str, evidence_kind: str | None = None) -> str:
    if evidence_kind == 'director_final':
        return _load_prompt('director_final_task.txt')
    if evidence_kind == 'text_with_music':
        return _load_prompt('text_with_music_task.txt')
    if evidence_kind == 'fusion' and task_mode == 'production':
        return _load_prompt('fusion_production_task.txt')
    mapping = {
        'teaching': 'music_teaching_task.txt',
        'production': 'production_task.txt',
        'concise': 'concise_task.txt',
    }
    return _load_prompt(mapping.get(task_mode, 'production_task.txt'))


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


def _mode_chain(task_mode: str) -> list[str]:
    mode = (task_mode or 'production').strip().lower()
    if mode == 'teaching':
        return ['teaching', 'production', 'concise']
    if mode == 'production':
        return ['production', 'concise']
    if mode == 'concise':
        return ['concise']
    return [mode, 'production', 'concise']


def generate_report(task_mode: str, evidence_payload: dict, debug_prompt: bool = False) -> tuple[dict | None, str | None, dict]:
    system_prompt = _load_prompt('core_system.txt')
    compact = _compact_evidence(evidence_payload)
    evidence_kind = str(compact.get('kind') or '').strip().lower()
    attempts: list[str] = []
    trace: list[dict] = []
    first_unstructured_raw: str | None = None
    first_unstructured_mode: str | None = None

    for mode in _mode_chain(task_mode):
        attempts.append(mode)
        print(f'[LLM MODE] requested={task_mode} trying={mode}', file=sys.stderr)
        task_prompt = _task_template(mode, evidence_kind=evidence_kind)
        user_prompt = (
            f'{task_prompt}\n\n'
            '输出要求：\n'
            '1) 先输出 JSON（可直接解析）。\n'
            '2) 再输出 markdown 字段对应的完整内容。\n\n'
            f'证据 JSON:\n{json.dumps(compact, ensure_ascii=False, indent=2)}'
        )

        raw, call_meta = _chat_completion(system_prompt, user_prompt)
        if debug_prompt:
            trace.append(
                {
                    'mode': mode,
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
                print(f'[LLM CONTRACT INVALID] mode={mode} reason={reason}', file=sys.stderr)
                if debug_prompt and trace:
                    trace[-1]['contract_valid'] = False
                    trace[-1]['contract_reason'] = reason
                continue
            if debug_prompt and trace:
                trace[-1]['contract_valid'] = True
                trace[-1]['contract_reason'] = ''
            markdown = parsed.get('markdown') if isinstance(parsed.get('markdown'), str) else raw
            return parsed, markdown, {
                'requested_mode': task_mode,
                'effective_mode': mode,
                'attempted_modes': attempts,
                'fallback_applied': mode != task_mode,
                'llm_trace': trace if debug_prompt else None,
            }

        if first_unstructured_raw is None:
            first_unstructured_raw = raw
            first_unstructured_mode = mode
            print(f'[LLM WARN] mode={mode} returned non-JSON, fallback continues', file=sys.stderr)

    if first_unstructured_raw is not None:
        return None, first_unstructured_raw, {
            'requested_mode': task_mode,
            'effective_mode': first_unstructured_mode,
            'attempted_modes': attempts,
            'fallback_applied': (first_unstructured_mode or task_mode) != task_mode,
            'llm_trace': trace if debug_prompt else None,
        }

    return None, None, {
        'requested_mode': task_mode,
        'effective_mode': None,
        'attempted_modes': attempts,
        'fallback_applied': False,
        'llm_trace': trace if debug_prompt else None,
    }
