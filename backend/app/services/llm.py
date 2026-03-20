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
    'audio': ['V3-music_analysis_task.txt', 'V3-music_analysis_task_retry.txt'],
    'action_verbs': ['V3-action_verbs_task.txt', 'V3-action_verbs_task_retry.txt'],
    'scene_building': ['V3-scene_building_task.txt', 'V3-scene_building_task_retry.txt'],
    'text_analysis': ['V3-text_analysis_task.txt', 'V3-text_analysis_task_retry.txt'],
    'text_with_music': ['V3-text_analysis_task.txt', 'V3-text_analysis_task_retry.txt'],
    'fusion': ['V3-production_analysis_task.txt', 'V3-production_analysis_task_retry.txt'],
    'director_final': ['director_final_task.txt', 'director_final_task_retry.txt'],
}
DEFAULT_SYSTEM_PROMPT_FALLBACK = (
    '你是有声书后期分析助手。请严格遵守任务提示词中的输入输出约束。'
)


def _normalize_provider_name(provider_value: str | None) -> str:
    value = str(provider_value or '').strip().lower()
    if value in {'', 'default'}:
        return 'default'
    if value in {'deepseek'}:
        return 'deepseek'
    if value in {'qwen', 'qwen-plus', 'dashscope'}:
        return 'qwen'
    return value


def _provider_settings(provider_override: str | None = None) -> dict:
    normalized = _normalize_provider_name(provider_override)
    if normalized == 'qwen':
        return {
            'provider': 'qwen',
            'base_url': settings.qwen_base_url,
            'api_key': settings.qwen_api_key,
            'model': settings.qwen_model,
            'timeout_sec': settings.llm_timeout_sec,
            'temperature': settings.llm_temperature,
        }
    return {
        'provider': settings.llm_provider,
        'base_url': settings.llm_base_url,
        'api_key': settings.llm_api_key,
        'model': settings.llm_model,
        'timeout_sec': settings.llm_timeout_sec,
        'temperature': settings.llm_temperature,
    }


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


def llm_enabled(provider_override: str | None = None) -> bool:
    provider = _provider_settings(provider_override)
    return bool(provider.get('api_key') and provider.get('model') and provider.get('base_url'))


def _extract_json_blob(text: str) -> dict | None:
    if not text:
        return None

    # 1) Prefer fenced JSON blocks
    fenced = re.search(r'```json\s*([\s\S]*?)```', text, flags=re.I)
    candidate = (fenced.group(1) if fenced else text).strip()

    # 2) Fast path: whole text is JSON
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass

    # 3) Robust path: scan from each '{' and let raw_decode find first valid object
    decoder = json.JSONDecoder()
    best_obj = None
    preferred_keys = {
        'title',
        'summary',
        'fit_genres',
        'risk_genres',
        'scene_units',
        'clause_timeline',
        'fit_with_music',
        'action_candidates',
        'qualified_actions',
        'music_entry_plan',
        'fit_verdict',
    }
    for i, ch in enumerate(candidate):
        if ch != '{':
            continue
        try:
            obj, _end = decoder.raw_decode(candidate[i:])
            if isinstance(obj, dict):
                if preferred_keys.intersection(obj.keys()):
                    return obj
                if best_obj is None:
                    best_obj = obj
        except json.JSONDecodeError:
            continue
    return best_obj


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_contract(payload: dict, evidence_kind: str | None) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, 'payload is not object'

    kind = (evidence_kind or '').strip().lower()
    if kind == 'audio':
        required = [
            'title',
            'summary',
            'fit_genres',
            'risk_genres',
            'sections',
            'structure_logic',
            'hit_points',
            'mix_notes',
            'key_points',
        ]
        for k in required:
            if k not in payload:
                return False, f'missing key: {k}'
        if not isinstance(payload.get('title'), str):
            return False, 'title must be string'
        if not isinstance(payload.get('summary'), str):
            return False, 'summary must be string'
        if not isinstance(payload.get('fit_genres'), list):
            return False, 'fit_genres must be list'
        if not isinstance(payload.get('risk_genres'), list):
            return False, 'risk_genres must be list'
        if not isinstance(payload.get('sections'), list):
            return False, 'sections must be list'
        for i, row in enumerate(payload.get('sections') or []):
            if not isinstance(row, dict):
                return False, f'sections[{i}] must be object'
            req = [
                'section_no',
                'label',
                'start_sec',
                'end_sec',
                'energy_level',
                'main_layers',
                'instrument_guess',
                'entry_suggestion',
                'exit_suggestion',
            ]
            for rk in req:
                if rk not in row:
                    return False, f'sections[{i}] missing key: {rk}'
        if not isinstance(payload.get('structure_logic'), dict):
            return False, 'structure_logic must be object'
        sl = payload.get('structure_logic') or {}
        for rk in ['pattern_guess', 'repeat_groups', 'progression_comment']:
            if rk not in sl:
                return False, f'structure_logic missing key: {rk}'
        if not isinstance(payload.get('hit_points'), list):
            return False, 'hit_points must be list'
        for i, row in enumerate(payload.get('hit_points') or []):
            if not isinstance(row, dict):
                return False, f'hit_points[{i}] must be object'
            for rk in ['time_sec', 'type', 'usage']:
                if rk not in row:
                    return False, f'hit_points[{i}] missing key: {rk}'
        if not isinstance(payload.get('mix_notes'), list):
            return False, 'mix_notes must be list'
        if not isinstance(payload.get('key_points'), list):
            return False, 'key_points must be list'
        return True, ''

    if kind == 'action_verbs':
        required = [
            'title',
            'rule_summary',
            'action_candidates',
            'key_points',
            'risks',
        ]
        for k in required:
            if k not in payload:
                return False, f'missing key: {k}'
        if not isinstance(payload.get('rule_summary'), list):
            return False, 'rule_summary must be list'
        if not isinstance(payload.get('action_candidates'), list):
            return False, 'action_candidates must be list'
        for i, row in enumerate(payload.get('action_candidates') or []):
            if not isinstance(row, dict):
                return False, f'action_candidates[{i}] must be object'
            req = [
                'candidate_no',
                'verb',
                'sentence_excerpt',
                'reason',
            ]
            for rk in req:
                if rk not in row:
                    return False, f'action_candidates[{i}] missing key: {rk}'
        if not isinstance(payload.get('key_points'), list):
            return False, 'key_points must be list'
        if not isinstance(payload.get('risks'), list):
            return False, 'risks must be list'
        return True, ''

    if kind == 'scene_building':
        required = [
            'title',
            'scene_items',
            'summary',
            'graph_model',
        ]
        for k in required:
            if k not in payload:
                return False, f'missing key: {k}'
        if not isinstance(payload.get('scene_items'), list):
            return False, 'scene_items must be list'
        if not isinstance(payload.get('summary'), dict):
            return False, 'summary must be object'
        if not isinstance(payload.get('graph_model'), dict):
            return False, 'graph_model must be object'
        for i, row in enumerate(payload.get('scene_items') or []):
            if not isinstance(row, dict):
                return False, f'scene_items[{i}] must be object'
            req = [
                'scene_id',
                'scene_name',
                'node_key',
                'sentence_excerpt',
                'is_scene_change',
                'scene_change_reason',
                'time_terms',
                'location_terms',
                'background_elements',
                'feature_elements',
                'detail_elements',
                'supporting_sfx_terms',
                'detail_sfx_terms',
                'excluded_action_terms',
            ]
            for rk in req:
                if rk not in row:
                    return False, f'scene_items[{i}] missing key: {rk}'
        return True, ''

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


def _normalize_contract_payload(payload: dict, evidence_kind: str | None) -> dict:
    if not isinstance(payload, dict):
        return payload
    kind = (evidence_kind or '').strip()
    normalized = dict(payload)
    if kind == 'audio':
        if not str(normalized.get('title') or '').strip():
            normalized['title'] = '音乐分析结果'
        if not str(normalized.get('summary') or '').strip():
            normalized['summary'] = '基于时长、锚点与层次信息生成的音乐分析结果。'
        if not isinstance(normalized.get('fit_genres'), list):
            normalized['fit_genres'] = []
        if not isinstance(normalized.get('risk_genres'), list):
            normalized['risk_genres'] = []
        if not isinstance(normalized.get('sections'), list):
            normalized['sections'] = []
        if not isinstance(normalized.get('structure_logic'), dict):
            normalized['structure_logic'] = {
                'pattern_guess': '未明确识别',
                'repeat_groups': [],
                'progression_comment': '本次结果缺少稳定结构说明，建议结合音乐锚点复核。',
            }
        if not isinstance(normalized.get('hit_points'), list):
            normalized['hit_points'] = []
        if not isinstance(normalized.get('mix_notes'), list):
            normalized['mix_notes'] = []
        if not isinstance(normalized.get('key_points'), list):
            normalized['key_points'] = []
        if 'markdown' not in normalized or not isinstance(normalized.get('markdown'), str):
            normalized['markdown'] = ''
    if kind == 'action_verbs':
        if not str(normalized.get('title') or '').strip():
            normalized['title'] = '人物动作动词提取'
        if not isinstance(normalized.get('rule_summary'), list) or not [str(x).strip() for x in (normalized.get('rule_summary') or []) if str(x).strip()]:
            normalized['rule_summary'] = [
                '必须是人物发出的动作',
                '放在当前文本里仍然是动词',
                '必须是当下正在发生',
            ]
        if not isinstance(normalized.get('action_candidates'), list):
            normalized['action_candidates'] = []
        if not isinstance(normalized.get('key_points'), list):
            normalized['key_points'] = []
        if not isinstance(normalized.get('risks'), list):
            normalized['risks'] = []
    if kind == 'text_analysis':
        if not str(normalized.get('title') or '').strip():
            normalized['title'] = '文本分析报告'
        if not str(normalized.get('text_theme') or '').strip():
            normalized['text_theme'] = '文本主情绪与叙事方向待进一步确认。'
        fit = normalized.get('fit_with_music')
        if not isinstance(fit, dict):
            fit = {}
            normalized['fit_with_music'] = fit
        if not str(fit.get('verdict') or '').strip():
            fit['verdict'] = '部分适配'
        if not _is_num(fit.get('score')):
            fit['score'] = 60
        if not isinstance(fit.get('reasons'), list):
            fit['reasons'] = []
        if len(fit.get('reasons') or []) < 3:
            base_reasons = [str(x).strip() for x in (fit.get('reasons') or []) if str(x).strip()]
            while len(base_reasons) < 3:
                base_reasons.append('当前按已有文本证据保守判断')
            fit['reasons'] = base_reasons[:3]
        if not isinstance(normalized.get('scene_units'), list):
            normalized['scene_units'] = []
        if not isinstance(normalized.get('clause_timeline'), list):
            normalized['clause_timeline'] = []
        if not isinstance(normalized.get('emotion_curve'), list):
            normalized['emotion_curve'] = []
        if not isinstance(normalized.get('sfx_requirements'), list):
            normalized['sfx_requirements'] = []
        if not isinstance(normalized.get('key_points'), list):
            normalized['key_points'] = []
        if not isinstance(normalized.get('risks'), list):
            normalized['risks'] = []
        if 'markdown' not in normalized or not isinstance(normalized.get('markdown'), str):
            normalized['markdown'] = ''
    if kind == 'scene_building':
        if not str(normalized.get('title') or '').strip():
            normalized['title'] = '场景搭建分析'
    if kind == 'fusion':
        if not str(normalized.get('title') or '').strip():
            normalized['title'] = '后期执行单'
        fit = normalized.get('fit_verdict')
        if not isinstance(fit, dict):
            fit = {}
            normalized['fit_verdict'] = fit
        if not str(fit.get('verdict') or '').strip():
            fit['verdict'] = '部分适配'
        if not _is_num(fit.get('score')):
            fit['score'] = 60
        if not isinstance(fit.get('reasons'), list):
            fit['reasons'] = []
        if not isinstance(normalized.get('key_points'), list):
            normalized['key_points'] = []
        if not isinstance(normalized.get('sections'), list):
            normalized['sections'] = []
        if not isinstance(normalized.get('music_entry_plan'), list):
            normalized['music_entry_plan'] = []
        if not isinstance(normalized.get('hit_points'), list):
            normalized['hit_points'] = []
        if not isinstance(normalized.get('risks'), list):
            normalized['risks'] = []
        if not isinstance(normalized.get('export_hints'), list):
            normalized['export_hints'] = []
        if not isinstance(normalized.get('structure_logic'), dict):
            normalized['structure_logic'] = {
                'pattern_guess': '未明确识别',
                'repeat_groups': [],
                'evidence': [],
            }
        if 'markdown' not in normalized or not isinstance(normalized.get('markdown'), str):
            normalized['markdown'] = ''
    return normalized


def _max_tokens_for_kind(evidence_kind: str) -> int:
    base = int(settings.llm_max_tokens or 1200)
    kind = (evidence_kind or '').strip().lower()
    if kind == 'audio':
        return max(700, min(base, 1500))
    if kind == 'action_verbs':
        return max(700, min(base, 1300))
    if kind == 'text_analysis':
        return max(700, min(base, 1300))
    if kind == 'fusion':
        return max(900, min(base, 1700))
    return max(700, min(base, 1400))


def _chat_completion(system_prompt: str, user_prompt: str, evidence_kind: str = '', provider_override: str = '') -> tuple[str | None, dict]:
    provider = _provider_settings(provider_override)
    if not llm_enabled(provider_override):
        return None, {'status': 'disabled', 'error': 'llm not enabled'}

    payload = {
        'model': provider['model'],
        'temperature': provider['temperature'],
        'max_tokens': _max_tokens_for_kind(evidence_kind),
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
    }

    body = json.dumps(payload).encode('utf-8')
    url = provider['base_url'].rstrip('/') + '/chat/completions'
    req = urllib.request.Request(
        url=url,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {provider["api_key"]}',
        },
        method='POST',
    )
    max_attempts = max(1, int(getattr(settings, 'llm_retry_attempts', 2) or 2))
    for attempt in range(1, max_attempts + 1):
        req_id = uuid.uuid4().hex[:8]
        t0 = time.perf_counter()
        print(
            f'[LLM START] req={req_id} attempt={attempt}/{max_attempts} provider={provider["provider"]} model={provider["model"]} timeout={provider["timeout_sec"]}s max_tokens={payload["max_tokens"]}',
            file=sys.stderr,
        )
        try:
            with urllib.request.urlopen(req, timeout=provider['timeout_sec']) as resp:
                raw = resp.read().decode('utf-8')
                data = json.loads(raw)
                content = data['choices'][0]['message']['content'].strip()
                elapsed = int((time.perf_counter() - t0) * 1000)
                print(f'[LLM OK] req={req_id} elapsed_ms={elapsed} chars={len(content)}', file=sys.stderr)
                return content, {
                    'status': 'ok',
                    'request_id': req_id,
                    'elapsed_ms': elapsed,
                    'attempt': attempt,
                    'provider': provider['provider'],
                    'model': provider['model'],
                }
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
            return None, {'status': 'http_error', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': f'{e.code} {e.reason}', 'body': err_body[:500], 'attempt': attempt, 'provider': provider['provider'], 'model': provider['model']}
        except urllib.error.URLError as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM URLError] req={req_id} elapsed_ms={elapsed} reason={e.reason}', file=sys.stderr)
            if attempt < max_attempts:
                time.sleep(0.8)
                continue
            return None, {'status': 'url_error', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': str(e.reason), 'attempt': attempt, 'provider': provider['provider'], 'model': provider['model']}
        except TimeoutError:
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM TimeoutError] req={req_id} elapsed_ms={elapsed} request timed out', file=sys.stderr)
            if attempt < max_attempts:
                time.sleep(0.8)
                continue
            return None, {'status': 'timeout', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': 'request timed out', 'attempt': attempt, 'provider': provider['provider'], 'model': provider['model']}
        except http.client.IncompleteRead:
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM IncompleteRead] req={req_id} elapsed_ms={elapsed} upstream connection closed unexpectedly', file=sys.stderr)
            if attempt < max_attempts:
                time.sleep(0.8)
                continue
            return None, {'status': 'incomplete_read', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': 'upstream connection closed unexpectedly', 'attempt': attempt, 'provider': provider['provider'], 'model': provider['model']}
        except http.client.RemoteDisconnected:
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM RemoteDisconnected] req={req_id} elapsed_ms={elapsed} remote end closed connection without response', file=sys.stderr)
            if attempt < max_attempts:
                time.sleep(0.8)
                continue
            return None, {'status': 'remote_disconnected', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': 'remote end closed connection without response', 'attempt': attempt, 'provider': provider['provider'], 'model': provider['model']}
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM ParseError] req={req_id} elapsed_ms={elapsed} {type(e).__name__}: {e}', file=sys.stderr)
            return None, {'status': 'parse_error', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': f'{type(e).__name__}: {e}', 'attempt': attempt, 'provider': provider['provider'], 'model': provider['model']}
        except Exception as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            print(f'[LLM UnknownError] req={req_id} elapsed_ms={elapsed} {type(e).__name__}: {e}', file=sys.stderr)
            if attempt < max_attempts:
                time.sleep(0.8)
                continue
            return None, {'status': 'unknown_exception', 'request_id': req_id, 'elapsed_ms': elapsed, 'error': f'{type(e).__name__}: {e}', 'attempt': attempt, 'provider': provider['provider'], 'model': provider['model']}
    return None, {'status': 'unknown_error', 'error': 'llm call failed unexpectedly'}


def _compact_evidence(evidence_payload: dict) -> dict:
    def _strip_noise(v):
        if isinstance(v, dict):
            return {k: _strip_noise(val) for k, val in v.items() if k != 'tokens'}
        if isinstance(v, list):
            return [_strip_noise(x) for x in v]
        return v

    data = _strip_noise(dict(evidence_payload))
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
    if kind == 'action_verbs':
        return (
            '长度硬约束（必须执行）：\n'
            '- action_candidates 按 raw_text 字数分档：0-1000字<=20，1001-2000字<=35，2001-3500字<=50，3501-5000字<=70，5000字以上<=90。\n'
            '- key_points<=4，risks<=4。\n'
            '- sentence_excerpt<=28字，reason<=20字。\n'
            '- 只保留满足三条规则的人物动作动词，避免把弱相关动词塞满上限。'
        )
    if kind == 'scene_building':
        return (
            '长度硬约束（必须执行）：\n'
            '- scene_items<=8。\n'
            '- 每个 scene_item 的 background_elements<=4，feature_elements<=5，detail_elements<=6。\n'
            '- supporting_sfx_terms<=5，detail_sfx_terms<=6，excluded_action_terms<=6。\n'
            '- summary 只保留 scene_count / asset_count / gap_count 三项。\n'
            '- 只输出 JSON，不输出额外解释文本。'
        )
    if kind == 'text_analysis':
        return (
            '长度硬约束（必须执行）：\n'
            '- scene_units<=8，clause_timeline<=12，sfx_requirements<=10，emotion_curve<=8。\n'
            '- fit_with_music.reasons 固定 3 条且每条<=22字。\n'
            '- key_points<=4（每条<=22字），risks<=4（每条<=24字）。\n'
            '- title<=20字，text_theme<=50字，text_excerpt<=28字。\n'
            '- 只输出 JSON，不输出额外解释文本。'
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
    provider_override = str(compact.get('llm_provider_override') or compact.get('llm_provider') or '').strip()
    attempts: list[str] = []
    trace: list[dict] = []
    first_unstructured_raw: str | None = None
    first_unstructured_prompt_file: str | None = None
    last_contract_reason: str | None = None
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
        if evidence_kind == 'text_analysis':
            output_req = (
                '输出要求：\n'
                '1) 仅输出一个可解析 JSON 对象，不要输出代码块标记。\n'
                '2) 不要在 JSON 前后添加解释性文字。\n'
                '3) markdown 字段可选；如省略，系统会在本地自动生成可读摘要。\n\n'
            )
        else:
            output_req = (
                '输出要求：\n'
                '1) 先输出 JSON（可直接解析）。\n'
                '2) 再输出 markdown 字段对应的完整内容。\n\n'
            )
        retry_hint = ''
        if last_contract_reason and 'retry' in prompt_file:
            retry_hint = (
                '上一次输出未通过结构校验，请优先修复以下问题：\n'
                f'- {last_contract_reason}\n'
                '- 你必须先补齐缺失字段，再输出最终 JSON。\n\n'
            )
        user_prompt = (
            f'{task_prompt}\n\n'
            f'{retry_hint}'
            f'{output_req}'
            f'{output_limits}\n\n'
            f'证据 JSON:\n{json.dumps(compact, ensure_ascii=False, indent=2)}'
        )

        raw, call_meta = _chat_completion(system_prompt, user_prompt, evidence_kind=evidence_kind, provider_override=provider_override)
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
            parsed = _normalize_contract_payload(parsed, evidence_kind=evidence_kind)
            ok, reason = _validate_contract(parsed, evidence_kind=evidence_kind)
            if not ok:
                print(f'[LLM CONTRACT INVALID] prompt={prompt_file} reason={reason}', file=sys.stderr)
                last_contract_reason = reason
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
                'llm_provider_used': (trace[0].get('call_meta') or {}).get('provider') if trace else '',
                'llm_model_used': (trace[0].get('call_meta') or {}).get('model') if trace else '',
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
            'llm_provider_used': (trace[0].get('call_meta') or {}).get('provider') if trace else '',
            'llm_model_used': (trace[0].get('call_meta') or {}).get('model') if trace else '',
            'llm_trace': trace,
        }

    return None, None, {
        'requested_mode': task_mode,
        'effective_mode': None,
        'attempted_modes': attempts,
        'fallback_applied': False,
        'custom_prompt_chain': prompt_chain,
        'system_prompt_file': system_prompt_file,
        'llm_provider_used': (trace[0].get('call_meta') or {}).get('provider') if trace else '',
        'llm_model_used': (trace[0].get('call_meta') or {}).get('model') if trace else '',
        'llm_trace': trace,
    }
