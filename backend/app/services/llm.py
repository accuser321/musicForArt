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


def _chat_completion(system_prompt: str, user_prompt: str) -> str | None:
    if not llm_enabled():
        return None

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
            return content
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode('utf-8', errors='ignore')
        except Exception:
            err_body = ''
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f'[LLM HTTPError] req={req_id} elapsed_ms={elapsed} code={e.code} reason={e.reason} body={err_body[:500]}', file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f'[LLM URLError] req={req_id} elapsed_ms={elapsed} reason={e.reason}', file=sys.stderr)
        return None
    except TimeoutError:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f'[LLM TimeoutError] req={req_id} elapsed_ms={elapsed} request timed out', file=sys.stderr)
        return None
    except http.client.IncompleteRead:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f'[LLM IncompleteRead] req={req_id} elapsed_ms={elapsed} upstream connection closed unexpectedly', file=sys.stderr)
        return None
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        elapsed = int((time.perf_counter() - t0) * 1000)
        print(f'[LLM ParseError] req={req_id} elapsed_ms={elapsed} {type(e).__name__}: {e}', file=sys.stderr)
        return None


def _task_template(task_mode: str) -> str:
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


def generate_report(task_mode: str, evidence_payload: dict) -> tuple[dict | None, str | None, dict]:
    system_prompt = _load_prompt('core_system.txt')
    compact = _compact_evidence(evidence_payload)
    attempts: list[str] = []
    first_unstructured_raw: str | None = None
    first_unstructured_mode: str | None = None

    for mode in _mode_chain(task_mode):
        attempts.append(mode)
        print(f'[LLM MODE] requested={task_mode} trying={mode}', file=sys.stderr)
        task_prompt = _task_template(mode)
        user_prompt = (
            f'{task_prompt}\n\n'
            '输出要求：\n'
            '1) 先输出 JSON（可直接解析）。\n'
            '2) 再输出 markdown 字段对应的完整内容。\n\n'
            f'证据 JSON:\n{json.dumps(compact, ensure_ascii=False, indent=2)}'
        )

        raw = _chat_completion(system_prompt, user_prompt)
        if not raw:
            continue

        parsed = _extract_json_blob(raw)
        if parsed and isinstance(parsed, dict):
            markdown = parsed.get('markdown') if isinstance(parsed.get('markdown'), str) else raw
            return parsed, markdown, {
                'requested_mode': task_mode,
                'effective_mode': mode,
                'attempted_modes': attempts,
                'fallback_applied': mode != task_mode,
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
        }

    return None, None, {
        'requested_mode': task_mode,
        'effective_mode': None,
        'attempted_modes': attempts,
        'fallback_applied': False,
    }
