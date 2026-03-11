import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from time import perf_counter
import re
from urllib.parse import quote
import random
import secrets
from functools import wraps

from flask import Flask, jsonify, request, send_file, g
from flask_cors import CORS
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, engine
from app.models import (
    AudioAnalysis,
    AuthCode,
    AuthSession,
    Base,
    DailyUsage,
    DraftReview,
    FusionPlan,
    NarrationAnalysis,
    Project,
    TextAnalysis,
    UserAccount,
    UserOperationLog,
)
from app.services.audio_analysis import analyze_audio_for_audiobook
from app.services.narration import analyze_narration_for_audiobook
from app.services.semantic_graph import expand_term, graph_status, reason_term, upsert_relation
from app.services.semantic_store import get_lexicon, merge_lexicon, save_lexicon
from app.services.sfx_matcher import load_sfx_library, match_sfx_candidates
from app.services.fusion import build_fusion_plan
from app.services.llm import llm_enabled
from app.services.text_analysis import analyze_text_for_audiobook
from app.services.exporter import export_cue_csv, export_sfx_zip
from app.services.reasoning_observability import get_reason_cache, log_reason_event, set_reason_cache
from app.services.ops_draft import generate_lexicon_draft
from app.services.rollout import resolve_semantic_backend
from app.models import ReasoningLog
from app.services.prompt_graph import (
    remove_prompt_field,
    remove_prompt_edge,
    prompt_field_detail,
    prompt_graph_overview,
    prompt_graph_status,
    sync_prompt_graph_to_neo4j,
    upsert_prompt_edge,
    upsert_prompt_field,
)

app = Flask(settings.app_name)
CORS(app)


def _normalize_phone(raw: str) -> str:
    return re.sub(r'[^0-9+]', '', raw or '')


def _get_bearer_token() -> str:
    auth = (request.headers.get('Authorization') or '').strip()
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return ''


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ensure_user(db, phone: str) -> UserAccount:
    row = db.execute(select(UserAccount).where(UserAccount.phone == phone)).scalar_one_or_none()
    if row is None:
        row = UserAccount(phone=phone, is_authorized=0, is_admin=0, daily_limit=3)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _get_session_user(db) -> UserAccount | None:
    token = _get_bearer_token()
    if not token:
        return None
    s = db.execute(select(AuthSession).where(AuthSession.token == token)).scalar_one_or_none()
    if s is None:
        return None
    if s.expires_at < _utc_now():
        db.delete(s)
        db.commit()
        return None
    u = db.execute(select(UserAccount).where(UserAccount.phone == s.phone)).scalar_one_or_none()
    return u


def _ensure_bootstrap_admins(db) -> None:
    phones = [x.strip() for x in (settings.bootstrap_admin_phones or '').split(',') if x.strip()]
    for p in phones:
        phone = _normalize_phone(p)
        if not phone:
            continue
        u = _ensure_user(db, phone)
        changed = False
        if int(u.is_admin or 0) != 1:
            u.is_admin = 1
            changed = True
        if int(u.is_authorized or 0) != 1:
            u.is_authorized = 1
            changed = True
        if changed:
            db.commit()


def _get_user_phone_from_context() -> str:
    u = getattr(g, 'current_user', None)
    if u and getattr(u, 'phone', None):
        return str(u.phone)
    return _normalize_phone(_extract_user_phone())


def _require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with SessionLocal() as db:
            u = _get_session_user(db)
            if u is None:
                return jsonify({'detail': '请先手机号登录'}), 401
            g.current_user = u
        return fn(*args, **kwargs)

    return wrapper


def _require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with SessionLocal() as db:
            _ensure_bootstrap_admins(db)
            u = _get_session_user(db)
            if u is None:
                return jsonify({'detail': '请先手机号登录'}), 401
            if int(u.is_admin or 0) != 1:
                return jsonify({'detail': '需要管理员权限'}), 403
            g.current_user = u
        return fn(*args, **kwargs)

    return wrapper


def _check_feature_access(action: str) -> tuple[UserAccount | None, dict | None, tuple | None]:
    with SessionLocal() as db:
        u = _get_session_user(db)
        if u is None:
            return None, None, (jsonify({'detail': '请先手机号登录后再使用功能'}), 401)

        # 授权用户不限次；非授权用户按日限额
        if int(u.is_authorized or 0) == 1:
            info = {'authorized': True, 'daily_limit': None, 'used_today': None, 'remaining': None}
            return u, info, None

        today = datetime.now().strftime('%Y-%m-%d')
        action_key = 'core_feature'
        row = (
            db.execute(
                select(DailyUsage)
                .where(DailyUsage.phone == u.phone)
                .where(DailyUsage.action == action_key)
                .where(DailyUsage.ymd == today)
            ).scalar_one_or_none()
        )
        if row is None:
            row = DailyUsage(phone=u.phone, action=action_key, ymd=today, used_count=0)
            db.add(row)
            db.commit()
            db.refresh(row)

        limit = int(u.daily_limit or 3)
        if row.used_count >= limit:
            return (
                None,
                None,
                (
                    jsonify(
                        {
                            'detail': '今日调用次数已达上限，请联系管理员授权',
                            'usage': {
                                'authorized': False,
                                'daily_limit': limit,
                                'used_today': int(row.used_count),
                                'remaining': 0,
                            },
                        }
                    ),
                    403,
                ),
            )

        row.used_count += 1
        db.commit()
        info = {
            'authorized': False,
            'daily_limit': limit,
            'used_today': int(row.used_count),
            'remaining': max(0, limit - int(row.used_count)),
        }
        return u, info, None


def _enforce_feature_access(action: str):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            u, usage, err = _check_feature_access(action)
            if err is not None:
                return err
            g.current_user = u
            g.usage_info = usage
            return fn(*args, **kwargs)

        return wrapper

    return deco


def _extract_user_phone() -> str:
    phone = (request.headers.get('X-User-Phone') or '').strip()
    if phone:
        return phone
    for src in (request.args, request.form):
        p = (src.get('user_phone') or '').strip()
        if p:
            return p
    payload = request.get_json(silent=True) or {}
    return (payload.get('user_phone') or '').strip()


def _llm_trace_digest(trace: list | None) -> list[dict]:
    out = []
    for x in (trace or []):
        if not isinstance(x, dict):
            continue
        cm = x.get('call_meta') or {}
        out.append(
            {
                'prompt_file': x.get('prompt_file'),
                'status': cm.get('status'),
                'request_id': cm.get('request_id'),
                'contract_valid': x.get('contract_valid'),
            }
        )
    return out


def _log_user_operation(
    db,
    action: str,
    project_id: int | None,
    req: dict | None = None,
    resp: dict | None = None,
    file_refs: list[str] | None = None,
) -> None:
    row = UserOperationLog(
        project_id=project_id,
        user_phone=_get_user_phone_from_context(),
        action=action,
        input_json=json.dumps(req or {}, ensure_ascii=False),
        output_json=json.dumps(resp or {}, ensure_ascii=False),
        file_refs_json=json.dumps(file_refs or [], ensure_ascii=False),
    )
    db.add(row)
    db.commit()


def _normalize_phone_for_path(phone: str) -> str:
    p = re.sub(r'[^0-9+]', '', phone or '')
    return p or 'anonymous'


def _build_local_storage_path(
    action: str,
    project_id: int,
    original_name: str,
    *,
    suffix_fallback: str = '.bin',
) -> Path:
    root = Path(settings.upload_dir).resolve()
    phone = _normalize_phone_for_path(_extract_user_phone())
    now = datetime.now()
    ext = Path(original_name or '').suffix or suffix_fallback
    safe_action = re.sub(r'[^a-zA-Z0-9_-]', '_', action or 'unknown')
    safe_ext = re.sub(r'[^a-zA-Z0-9.]', '', ext) or suffix_fallback
    out_dir = root / phone / f'{now.year:04d}' / f'{now.month:02d}' / f'{now.day:02d}' / safe_action
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f'project_{project_id}{safe_ext}'


def _is_under_upload_root(abs_path: Path) -> bool:
    root = Path(settings.upload_dir).resolve()
    try:
        abs_path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def _fallback_audio_result_on_error(err: Exception) -> dict:
    err_msg = f'{type(err).__name__}: {err}'
    return {
        'duration_sec': 180.0,
        'bpm': 120.0,
        'tags': ['电影感', '史诗感', '战斗推进'],
        'markers': [
            {'label': '起势段', 'time_sec': 9.0, 'type': 'build'},
            {'label': '高潮一', 'time_sec': 50.4, 'type': 'peak'},
            {'label': '回落一', 'time_sec': 68.4, 'type': 'valley'},
            {'label': '高潮二', 'time_sec': 111.6, 'type': 'peak'},
            {'label': '回落二', 'time_sec': 129.6, 'type': 'valley'},
            {'label': '终局高潮', 'time_sec': 147.6, 'type': 'peak'},
        ],
        'report_markdown': (
            '# 音乐分析报告（降级版）\n\n'
            '- 音频元数据解析异常，系统已自动降级为可执行分析。\n'
            '- 你仍可继续进行文本分析与融合执行单生成。\n'
            f'- 异常信息：{err_msg}\n'
        ),
        'report_json': None,
        'analysis_mode': 'rules-only',
        'llm_structured': False,
        'llm_enabled': llm_enabled(),
        'report_mode': settings.report_mode_default,
        'effective_report_mode': None,
        'llm_fallback_applied': True,
        'llm_attempted_modes': [],
        'custom_prompt_chain': ['V1-music_analysis_task.txt'],
        'llm_trace': [
            {
                'mode': 'core_prompt_chain',
                'prompt_file': None,
                'evidence_kind': 'audio',
                'task_prompt': '',
                'system_prompt': '',
                'user_prompt': '',
                'raw_response': None,
                'call_meta': {'status': 'exception', 'error': err_msg},
                'contract_valid': False,
                'contract_reason': err_msg,
            }
        ],
        'degraded': True,
    }


@app.before_request
def ensure_tables():
    Base.metadata.create_all(bind=engine)


@app.get('/health')
def health():
    key_tail = settings.llm_api_key[-4:] if settings.llm_api_key else ''
    return jsonify(
        {
            'ok': True,
            'env': settings.app_env,
            'llm_enabled': llm_enabled(),
            'llm_provider': settings.llm_provider,
            'llm_model': settings.llm_model,
            'llm_key_tail': key_tail,
            'semantic_backend': settings.semantic_backend,
        }
    )


@app.post(f'{settings.api_prefix}/auth/request-code')
def auth_request_code():
    payload = request.get_json(force=True, silent=True) or {}
    phone = _normalize_phone(payload.get('phone') or '')
    if not phone:
        return jsonify({'detail': 'phone is required'}), 400

    with SessionLocal() as db:
        _ensure_bootstrap_admins(db)
        _ensure_user(db, phone)
        code = f'{random.randint(0, 999999):06d}'
        row = AuthCode(
            phone=phone,
            code=code,
            expires_at=_utc_now() + timedelta(seconds=settings.auth_code_ttl_sec),
            used=0,
        )
        db.add(row)
        db.commit()
    # 当前为MVP，本地直接返回验证码，正式上线改短信网关
    return jsonify({'ok': True, 'phone': phone, 'code': code, 'ttl_sec': settings.auth_code_ttl_sec})


@app.post(f'{settings.api_prefix}/auth/login')
def auth_login():
    payload = request.get_json(force=True, silent=True) or {}
    phone = _normalize_phone(payload.get('phone') or '')
    code = str(payload.get('code') or '').strip()
    if not phone or not code:
        return jsonify({'detail': 'phone and code are required'}), 400

    with SessionLocal() as db:
        _ensure_bootstrap_admins(db)
        c = (
            db.execute(
                select(AuthCode)
                .where(AuthCode.phone == phone)
                .where(AuthCode.code == code)
                .where(AuthCode.used == 0)
                .order_by(AuthCode.id.desc())
            ).scalar_one_or_none()
        )
        if c is None or c.expires_at < _utc_now():
            return jsonify({'detail': '验证码无效或已过期'}), 400
        c.used = 1

        u = _ensure_user(db, phone)
        token = secrets.token_urlsafe(32)
        s = AuthSession(
            phone=phone,
            token=token,
            expires_at=_utc_now() + timedelta(seconds=settings.auth_session_ttl_sec),
        )
        db.add(s)
        db.commit()

        return jsonify(
            {
                'ok': True,
                'token': token,
                'expires_in_sec': settings.auth_session_ttl_sec,
                'user': {
                    'phone': u.phone,
                    'is_admin': bool(int(u.is_admin or 0)),
                    'is_authorized': bool(int(u.is_authorized or 0)),
                    'daily_limit': int(u.daily_limit or 3),
                },
            }
        )


@app.get(f'{settings.api_prefix}/auth/me')
@_require_login
def auth_me():
    u = g.current_user
    usage = None
    if int(u.is_authorized or 0) != 1:
        today = datetime.now().strftime('%Y-%m-%d')
        with SessionLocal() as db:
            row = db.execute(
                select(DailyUsage)
                .where(DailyUsage.phone == u.phone)
                .where(DailyUsage.action == 'core_feature')
                .where(DailyUsage.ymd == today)
            ).scalar_one_or_none()
            used = int(row.used_count or 0) if row else 0
        limit = int(u.daily_limit or 3)
        usage = {'used_today': used, 'remaining': max(0, limit - used), 'daily_limit': limit}
    return jsonify(
        {
            'phone': u.phone,
            'is_admin': bool(int(u.is_admin or 0)),
            'is_authorized': bool(int(u.is_authorized or 0)),
            'daily_limit': int(u.daily_limit or 3),
            'usage': usage,
        }
    )


@app.post(f'{settings.api_prefix}/auth/logout')
@_require_login
def auth_logout():
    token = _get_bearer_token()
    with SessionLocal() as db:
        s = db.execute(select(AuthSession).where(AuthSession.token == token)).scalar_one_or_none()
        if s is not None:
            db.delete(s)
            db.commit()
    return jsonify({'ok': True})


@app.get(f'{settings.api_prefix}/admin/users')
@_require_admin
def admin_list_users():
    with SessionLocal() as db:
        rows = db.execute(select(UserAccount).order_by(UserAccount.id.desc()).limit(1000)).scalars().all()
    data = [
        {
            'id': r.id,
            'phone': r.phone,
            'is_authorized': bool(int(r.is_authorized or 0)),
            'is_admin': bool(int(r.is_admin or 0)),
            'daily_limit': int(r.daily_limit or 3),
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'updated_at': r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]
    return jsonify({'count': len(data), 'items': data})


@app.post(f'{settings.api_prefix}/admin/users')
@_require_admin
def admin_upsert_user():
    payload = request.get_json(force=True, silent=True) or {}
    phone = _normalize_phone(payload.get('phone') or '')
    if not phone:
        return jsonify({'detail': 'phone is required'}), 400

    is_authorized = 1 if bool(payload.get('is_authorized', False)) else 0
    is_admin = 1 if bool(payload.get('is_admin', False)) else 0
    try:
        daily_limit = int(payload.get('daily_limit', 3))
    except (TypeError, ValueError):
        return jsonify({'detail': 'daily_limit must be integer'}), 400
    daily_limit = max(1, min(100, daily_limit))

    with SessionLocal() as db:
        row = _ensure_user(db, phone)
        row.is_authorized = is_authorized
        row.is_admin = is_admin
        row.daily_limit = daily_limit
        db.commit()
        db.refresh(row)
    return jsonify(
        {
            'ok': True,
            'item': {
                'phone': row.phone,
                'is_authorized': bool(int(row.is_authorized or 0)),
                'is_admin': bool(int(row.is_admin or 0)),
                'daily_limit': int(row.daily_limit or 3),
            },
        }
    )


@app.get(f'{settings.api_prefix}/semantic/expand')
def semantic_expand():
    term = (request.args.get('term') or '').strip()
    project_id = request.args.get('project_id', type=int)
    if not term:
        return jsonify({'detail': 'term is required'}), 400
    backend = resolve_semantic_backend(project_id=project_id, subject=term)
    ex = expand_term(term, backend_override=backend)
    return jsonify({'term': term, 'expanded_terms': sorted(ex.terms), 'sources': ex.sources, 'effective_backend': backend})


@app.get(f'{settings.api_prefix}/semantic/search-sfx')
def semantic_search_sfx():
    term = (request.args.get('term') or '').strip()
    project_id = request.args.get('project_id', type=int)
    top_n = int((request.args.get('top_n') or '5').strip())
    if not term:
        return jsonify({'detail': 'term is required'}), 400
    t0 = perf_counter()
    backend = resolve_semantic_backend(project_id=project_id, subject=term)
    library = load_sfx_library(backend_override=backend)
    matched = match_sfx_candidates(term, library, top_n=top_n, backend_override=backend)
    duration_ms = int((perf_counter() - t0) * 1000)

    with SessionLocal() as db:
        log_reason_event(
            db=db,
            event_type='search_sfx',
            term=term,
            backend=backend,
            project_id=project_id,
            req={'top_n': top_n},
            resp={'success': True, 'duration_ms': duration_ms, 'match_count': len(matched), 'matches': matched[:10]},
        )

    return jsonify({'term': term, 'top_n': top_n, 'matches': matched, 'effective_backend': backend, 'duration_ms': duration_ms})


@app.get(f'{settings.api_prefix}/graph/status')
def semantic_graph_status():
    return jsonify(graph_status())


@app.get(f'{settings.api_prefix}/prompt-graph/status')
def api_prompt_graph_status():
    return jsonify(prompt_graph_status())


@app.get(f'{settings.api_prefix}/prompt-graph/overview')
def api_prompt_graph_overview():
    return jsonify(prompt_graph_overview())


@app.get(f'{settings.api_prefix}/prompt-graph/field')
def api_prompt_graph_field():
    fid = (request.args.get('field_id') or '').strip()
    if not fid:
        return jsonify({'detail': 'field_id is required'}), 400
    out = prompt_field_detail(fid)
    if out.get('detail') == 'field not found':
        return jsonify(out), 404
    return jsonify(out)


@app.post(f'{settings.api_prefix}/prompt-graph/rebuild')
@_require_admin
def api_prompt_graph_rebuild():
    out = sync_prompt_graph_to_neo4j()
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.post(f'{settings.api_prefix}/prompt-graph/field')
@_require_admin
def api_prompt_graph_upsert_field():
    payload = request.get_json(force=True) or {}
    out = upsert_prompt_field(payload)
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.delete(f'{settings.api_prefix}/prompt-graph/field')
@_require_admin
def api_prompt_graph_delete_field():
    fid = (request.args.get('field_id') or '').strip()
    out = remove_prompt_field(fid)
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.post(f'{settings.api_prefix}/prompt-graph/edge')
@_require_admin
def api_prompt_graph_upsert_edge():
    payload = request.get_json(force=True) or {}
    out = upsert_prompt_edge(payload)
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.delete(f'{settings.api_prefix}/prompt-graph/edge')
@_require_admin
def api_prompt_graph_delete_edge():
    src = (request.args.get('from') or '').strip()
    dst = (request.args.get('to') or '').strip()
    out = remove_prompt_edge(src, dst)
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.post(f'{settings.api_prefix}/graph/edge')
def semantic_graph_upsert_edge():
    payload = request.get_json(force=True)
    head = payload.get('head', '')
    relation = payload.get('relation', '')
    tail = payload.get('tail', '')
    bidirectional = bool(payload.get('bidirectional', False))

    try:
        result = upsert_relation(head=head, relation=relation, tail=tail, bidirectional=bidirectional)
        result['usage'] = getattr(g, 'usage_info', None)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'detail': str(e)}), 400
    except RuntimeError as e:
        return jsonify({'detail': str(e)}), 400
    except Exception as e:
        return jsonify({'detail': f'graph upsert failed: {e}'}), 500


@app.get(f'{settings.api_prefix}/graph/reason')
def semantic_graph_reason():
    term = (request.args.get('term') or '').strip()
    project_id = request.args.get('project_id', type=int)
    limit = int((request.args.get('limit') or '12').strip())
    max_hops = int((request.args.get('max_hops') or str(settings.semantic_neo4j_depth)).strip())
    if not term:
        return jsonify({'detail': 'term is required'}), 400

    limit = max(1, min(50, limit))
    max_hops = max(1, min(4, max_hops))
    backend = resolve_semantic_backend(project_id=project_id, subject=term)

    t0 = perf_counter()
    with SessionLocal() as db:
        cached = get_reason_cache(db, term=term, backend=backend, limit=limit, max_hops=max_hops)
        if cached:
            cached['cache_hit'] = True
            cached['effective_backend'] = backend
            cached['duration_ms'] = int((perf_counter() - t0) * 1000)
            log_reason_event(
                db=db,
                event_type='graph_reason',
                term=term,
                backend=backend,
                project_id=project_id,
                req={'limit': limit, 'max_hops': max_hops, 'cache_hit': True},
                resp={'success': True, 'duration_ms': cached['duration_ms'], 'inferred_count': len(cached.get('inferred', []))},
            )
            return jsonify(cached)

        out = reason_term(term=term, limit=limit, max_hops=max_hops, backend_override=backend)
        out['cache_hit'] = False
        out['effective_backend'] = backend
        out['duration_ms'] = int((perf_counter() - t0) * 1000)

        set_reason_cache(db, term=term, backend=backend, limit=limit, max_hops=max_hops, result=out)
        log_reason_event(
            db=db,
            event_type='graph_reason',
            term=term,
            backend=backend,
            project_id=project_id,
            req={'limit': limit, 'max_hops': max_hops, 'cache_hit': False},
            resp={
                'success': True,
                'duration_ms': out['duration_ms'],
                'inferred_count': len(out.get('inferred', [])),
                'local_count': out.get('local_count'),
                'neo4j_count': out.get('neo4j_count'),
            },
        )

        return jsonify(out)


@app.get(f'{settings.api_prefix}/graph/metrics')
def semantic_graph_metrics():
    days = int((request.args.get('days') or '7').strip())
    days = max(1, min(90, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with SessionLocal() as db:
        rows = db.execute(
            select(ReasoningLog.event_type, ReasoningLog.backend, ReasoningLog.output_json)
            .where(ReasoningLog.created_at >= cutoff)
        ).all()

    bucket = {}
    for event_type, backend, output_json in rows:
        key = (event_type, backend)
        b = bucket.setdefault(
            key,
            {
                'event_type': event_type,
                'backend': backend,
                'count': 0,
                'success_observed_count': 0,
                'success_count': 0,
                'duration_count': 0,
                'duration_sum_ms': 0,
            },
        )
        b['count'] += 1
        try:
            payload = json.loads(output_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload.get('success'), bool):
            b['success_observed_count'] += 1
            if payload.get('success') is True:
                b['success_count'] += 1
        dur = payload.get('duration_ms')
        if isinstance(dur, int) and dur >= 0:
            b['duration_count'] += 1
            b['duration_sum_ms'] += dur

    metrics = []
    for _, b in bucket.items():
        avg_ms = round(b['duration_sum_ms'] / b['duration_count'], 2) if b['duration_count'] else None
        success_rate = (
            round(b['success_count'] / b['success_observed_count'], 4) if b['success_observed_count'] else None
        )
        metrics.append(
            {
                'event_type': b['event_type'],
                'backend': b['backend'],
                'count': b['count'],
                'success_observed_count': b['success_observed_count'],
                'success_rate': success_rate,
                'avg_duration_ms': avg_ms,
            }
        )
    metrics.sort(key=lambda x: (x['event_type'], x['backend']))
    return jsonify({'days': days, 'metrics': metrics})


@app.get(f'{settings.api_prefix}/semantic/lexicon')
def semantic_get_lexicon():
    data = get_lexicon()
    return jsonify({'size': len(data), 'lexicon': data})


@app.put(f'{settings.api_prefix}/semantic/lexicon')
def semantic_replace_lexicon():
    payload = request.get_json(force=True)
    lexicon = payload.get('lexicon')
    if not isinstance(lexicon, dict):
        return jsonify({'detail': 'lexicon must be an object: {keyword:[synonyms...]}'}), 400
    save_lexicon(lexicon)
    data = get_lexicon()
    return jsonify({'ok': True, 'size': len(data), 'lexicon': data})


@app.patch(f'{settings.api_prefix}/semantic/lexicon')
def semantic_merge_lexicon():
    payload = request.get_json(force=True)
    delta = payload.get('lexicon')
    if not isinstance(delta, dict):
        return jsonify({'detail': 'lexicon must be an object: {keyword:[synonyms...]}'}), 400
    data = merge_lexicon(delta)
    return jsonify({'ok': True, 'size': len(data), 'lexicon': data})


@app.post(f'{settings.api_prefix}/projects')
@_enforce_feature_access('create_project')
def create_project():
    payload = request.get_json(force=True)
    title = (payload.get('title') or '').strip()
    if not title:
        return jsonify({'detail': 'title is required'}), 400

    with SessionLocal() as db:
        project = Project(title=title)
        db.add(project)
        db.commit()
        db.refresh(project)
        _log_user_operation(
            db=db,
            action='create_project',
            project_id=project.id,
            req={'title': title},
            resp={'project_id': project.id},
            file_refs=[],
        )
        return jsonify(
            {
                'id': project.id,
                'title': project.title,
                'created_at': project.created_at.isoformat(),
                'usage': getattr(g, 'usage_info', None),
            }
        )


@app.get(f'{settings.api_prefix}/projects/<int:project_id>')
def get_project(project_id: int):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404
        return jsonify({'id': project.id, 'title': project.title, 'created_at': project.created_at.isoformat()})


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/audio')
@_enforce_feature_access('audio_analysis')
def upload_audio(project_id: int):
    file = request.files.get('file')
    if file is None:
        return jsonify({'detail': 'file is required'}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        save_path = _build_local_storage_path(
            action='audio_analysis',
            project_id=project_id,
            original_name=file.filename or 'audio.bin',
            suffix_fallback='.bin',
        )

        file_bytes = file.read()
        max_bytes = settings.max_upload_mb * 1024 * 1024
        if len(file_bytes) > max_bytes:
            return jsonify({'detail': f'File too large. Max {settings.max_upload_mb}MB'}), 413
        save_path.write_bytes(file_bytes)

        report_mode = (request.args.get('report_mode') or settings.report_mode_default).strip().lower()
        try:
            result = analyze_audio_for_audiobook(
                str(save_path),
                report_mode=report_mode,
                debug_prompt=True,
            )
        except Exception as e:
            app.logger.exception('audio analyze failed; fallback enabled')
            result = _fallback_audio_result_on_error(e)

        row = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        if row is None:
            row = AudioAnalysis(
                project_id=project_id,
                file_name=file.filename or save_path.name,
                file_path=str(save_path),
                duration_sec=result['duration_sec'],
                bpm=result['bpm'],
                report_markdown=result['report_markdown'],
                markers_json=json.dumps(result['markers'], ensure_ascii=False),
                tags_json=json.dumps(result['tags'], ensure_ascii=False),
            )
            db.add(row)
        else:
            row.file_name = file.filename or save_path.name
            row.file_path = str(save_path)
            row.duration_sec = result['duration_sec']
            row.bpm = result['bpm']
            row.report_markdown = result['report_markdown']
            row.markers_json = json.dumps(result['markers'], ensure_ascii=False)
            row.tags_json = json.dumps(result['tags'], ensure_ascii=False)

        db.commit()
        _log_user_operation(
            db=db,
            action='audio_analysis',
            project_id=project_id,
            req={'report_mode': report_mode, 'file_name': file.filename or save_path.name},
            resp={
                'analysis_mode': result.get('analysis_mode'),
                'duration_sec': result.get('duration_sec'),
                'marker_count': len(result.get('markers') or []),
                'llm_trace_digest': _llm_trace_digest(result.get('llm_trace')),
            },
            file_refs=[str(save_path)],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return jsonify(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/text')
@_enforce_feature_access('text_analysis')
def analyze_text(project_id: int):
    payload = request.get_json(force=True)
    text = (payload.get('text') or '').strip()
    report_mode = (payload.get('report_mode') or request.args.get('report_mode') or settings.report_mode_default).strip().lower()
    debug_prompt = True
    if not text:
        return jsonify({'detail': 'text is required'}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        audio_context = None
        if audio is not None:
            audio_context = {
                'duration_sec': audio.duration_sec,
                'bpm': audio.bpm,
                'tags': json.loads(audio.tags_json),
                'markers': json.loads(audio.markers_json),
                'report_markdown': audio.report_markdown,
            }

        result = analyze_text_for_audiobook(
            text, report_mode=report_mode, audio_context=audio_context, debug_prompt=debug_prompt
        )
        row = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()

        if row is None:
            row = TextAnalysis(
                project_id=project_id,
                raw_text=text,
                report_markdown=result['report_markdown'],
                scenes_json=json.dumps(result['scenes'], ensure_ascii=False),
            )
            db.add(row)
        else:
            row.raw_text = text
            row.report_markdown = result['report_markdown']
            row.scenes_json = json.dumps(result['scenes'], ensure_ascii=False)

        db.commit()
        _log_user_operation(
            db=db,
            action='text_analysis',
            project_id=project_id,
            req={'report_mode': report_mode, 'text_len': len(text)},
            resp={
                'analysis_mode': result.get('analysis_mode'),
                'scene_count': len(result.get('scenes') or []),
                'llm_trace_digest': _llm_trace_digest(result.get('llm_trace')),
            },
            file_refs=[],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return jsonify(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/narration')
@_enforce_feature_access('narration_analysis')
def analyze_narration(project_id: int):
    file = request.files.get('file')
    if file is None:
        return jsonify({'detail': 'file is required'}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        text = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()
        if not text:
            return jsonify({'detail': 'Text analysis is required before narration analysis'}), 400

        save_path = _build_local_storage_path(
            action='narration_analysis',
            project_id=project_id,
            original_name=file.filename or 'narration.bin',
            suffix_fallback='.bin',
        )
        save_path.write_bytes(file.read())

        scenes = json.loads(text.scenes_json)
        result = analyze_narration_for_audiobook(str(save_path), scenes=scenes, raw_text=text.raw_text)
        timeline_payload = {'scene_timeline': result['timeline'], 'clause_timeline': result.get('clause_timeline', [])}
        row = db.execute(select(NarrationAnalysis).where(NarrationAnalysis.project_id == project_id)).scalar_one_or_none()
        if row is None:
            row = NarrationAnalysis(
                project_id=project_id,
                file_name=file.filename or save_path.name,
                file_path=str(save_path),
                duration_sec=result['duration_sec'],
                timeline_json=json.dumps(timeline_payload, ensure_ascii=False),
                report_markdown=result['report_markdown'],
            )
            db.add(row)
        else:
            row.file_name = file.filename or save_path.name
            row.file_path = str(save_path)
            row.duration_sec = result['duration_sec']
            row.timeline_json = json.dumps(timeline_payload, ensure_ascii=False)
            row.report_markdown = result['report_markdown']

        db.commit()
        _log_user_operation(
            db=db,
            action='narration_analysis',
            project_id=project_id,
            req={'file_name': file.filename or save_path.name},
            resp={
                'duration_sec': result.get('duration_sec'),
                'scene_timeline_count': len(result.get('timeline') or []),
                'clause_timeline_count': len(result.get('clause_timeline') or []),
            },
            file_refs=[str(save_path)],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return jsonify(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/text-narration')
@_enforce_feature_access('text_narration_analysis')
def analyze_text_narration(project_id: int):
    text = (request.form.get('text') or '').strip()
    file = request.files.get('file')
    report_mode = (request.form.get('report_mode') or request.args.get('report_mode') or settings.report_mode_default).strip().lower()
    debug_prompt = True
    if not text:
        return jsonify({'detail': 'text is required'}), 400
    if file is None:
        return jsonify({'detail': 'narration file is required'}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        audio_context = None
        if audio is not None:
            audio_context = {
                'duration_sec': audio.duration_sec,
                'bpm': audio.bpm,
                'tags': json.loads(audio.tags_json),
                'markers': json.loads(audio.markers_json),
                'report_markdown': audio.report_markdown,
            }

        text_result = analyze_text_for_audiobook(
            text, report_mode=report_mode, audio_context=audio_context, debug_prompt=debug_prompt
        )
        text_row = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()
        if text_row is None:
            text_row = TextAnalysis(
                project_id=project_id,
                raw_text=text,
                report_markdown=text_result['report_markdown'],
                scenes_json=json.dumps(text_result['scenes'], ensure_ascii=False),
            )
            db.add(text_row)
        else:
            text_row.raw_text = text
            text_row.report_markdown = text_result['report_markdown']
            text_row.scenes_json = json.dumps(text_result['scenes'], ensure_ascii=False)

        save_path = _build_local_storage_path(
            action='text_narration_analysis',
            project_id=project_id,
            original_name=file.filename or 'narration.bin',
            suffix_fallback='.bin',
        )
        file_bytes = file.read()
        max_bytes = settings.max_upload_mb * 1024 * 1024
        if len(file_bytes) > max_bytes:
            return jsonify({'detail': f'File too large. Max {settings.max_upload_mb}MB'}), 413
        save_path.write_bytes(file_bytes)

        narration_result = analyze_narration_for_audiobook(str(save_path), scenes=text_result['scenes'], raw_text=text)
        timeline_payload = {
            'scene_timeline': narration_result['timeline'],
            'clause_timeline': narration_result.get('clause_timeline', []),
        }
        narration_row = db.execute(select(NarrationAnalysis).where(NarrationAnalysis.project_id == project_id)).scalar_one_or_none()
        if narration_row is None:
            narration_row = NarrationAnalysis(
                project_id=project_id,
                file_name=file.filename or save_path.name,
                file_path=str(save_path),
                duration_sec=narration_result['duration_sec'],
                timeline_json=json.dumps(timeline_payload, ensure_ascii=False),
                report_markdown=narration_result['report_markdown'],
            )
            db.add(narration_row)
        else:
            narration_row.file_name = file.filename or save_path.name
            narration_row.file_path = str(save_path)
            narration_row.duration_sec = narration_result['duration_sec']
            narration_row.timeline_json = json.dumps(timeline_payload, ensure_ascii=False)
            narration_row.report_markdown = narration_result['report_markdown']

        db.commit()
        response_payload = {
            'analysis_mode': 'text+narration-combined',
            'text': text_result,
            'narration': narration_result,
            'llm_trace': text_result.get('llm_trace') or [],
            'prompt_guard': text_result.get('prompt_guard'),
            'llm_attempted_modes': text_result.get('llm_attempted_modes') or [],
            'effective_report_mode': text_result.get('effective_report_mode'),
        }
        _log_user_operation(
            db=db,
            action='text_narration_analysis',
            project_id=project_id,
            req={'report_mode': report_mode, 'text_len': len(text), 'file_name': file.filename or save_path.name},
            resp={
                'analysis_mode': response_payload['analysis_mode'],
                'text_scene_count': len((text_result or {}).get('scenes') or []),
                'narration_scene_timeline_count': len((narration_result or {}).get('timeline') or []),
                'narration_clause_timeline_count': len((narration_result or {}).get('clause_timeline') or []),
                'llm_trace_digest': _llm_trace_digest((text_result or {}).get('llm_trace')),
            },
            file_refs=[str(save_path)],
        )
        response_payload['usage'] = getattr(g, 'usage_info', None)
        return jsonify(response_payload)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/fusion')
@_enforce_feature_access('fusion_execution')
def build_fusion(project_id: int):
    report_mode = (request.args.get('report_mode') or settings.report_mode_default).strip().lower()
    debug_prompt = True
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        text = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()
        narration = db.execute(select(NarrationAnalysis).where(NarrationAnalysis.project_id == project_id)).scalar_one_or_none()

        if not audio or not text:
            missing = []
            if not audio:
                missing.append('audio')
            if not text:
                missing.append('text')
            return (
                jsonify(
                    {
                        'detail': '生成融合执行单前，需要先完成音乐分析和文本分析',
                        'missing': missing,
                        'hint': '请先完成第2步“音乐分析”和第3步“文本分析”，再执行第4步',
                    }
                ),
                400,
            )

        narration_payload = None
        narration_raw_timeline = None
        if narration:
            parsed_timeline = json.loads(narration.timeline_json)
            narration_raw_timeline = parsed_timeline
            if isinstance(parsed_timeline, dict):
                narration_payload = {
                    'scene_timeline': parsed_timeline.get('scene_timeline') or [],
                    'clause_timeline': parsed_timeline.get('clause_timeline') or [],
                }
            elif isinstance(parsed_timeline, list):
                narration_payload = {'scene_timeline': parsed_timeline, 'clause_timeline': []}

        music_context = {
            'duration_sec': audio.duration_sec,
            'bpm': audio.bpm,
            'tags': json.loads(audio.tags_json),
            'markers': json.loads(audio.markers_json),
            'report_markdown': audio.report_markdown,
        }
        text_context = {
            'raw_text': text.raw_text,
            'scenes': json.loads(text.scenes_json),
            'report_markdown': text.report_markdown,
        }
        if narration_raw_timeline is not None:
            text_context['narration_timeline'] = narration_raw_timeline

        result = build_fusion_plan(
            music_context['markers'],
            text_context['scenes'],
            narration_timeline=narration_payload,
            music_context=music_context,
            text_context=text_context,
            report_mode=report_mode,
            debug_prompt=debug_prompt,
        )
        result['evidence_summary'] = {
            'chain': 'music+text+narration',
            'music': {
                'has_data': True,
                'duration_sec': music_context['duration_sec'],
                'bpm': music_context['bpm'],
                'tag_count': len(music_context['tags']),
                'marker_count': len(music_context['markers']),
            },
            'text': {
                'has_data': True,
                'text_len': len(text_context['raw_text'] or ''),
                'scene_count': len(text_context['scenes']),
            },
            'narration': {
                'has_data': bool(narration),
                'scene_timeline_count': len((narration_payload or {}).get('scene_timeline') or []),
                'clause_timeline_count': len((narration_payload or {}).get('clause_timeline') or []),
            },
        }
        row = db.execute(select(FusionPlan).where(FusionPlan.project_id == project_id)).scalar_one_or_none()

        if row is None:
            row = FusionPlan(
                project_id=project_id,
                cue_sheet_json=json.dumps(result['cues'], ensure_ascii=False),
                report_markdown=result['report_markdown'],
            )
            db.add(row)
        else:
            row.cue_sheet_json = json.dumps(result['cues'], ensure_ascii=False)
            row.report_markdown = result['report_markdown']

        db.commit()
        log_reason_event(
            db=db,
            event_type='fusion_build',
            term='fusion',
            backend='n/a',
            project_id=project_id,
            req={
                'report_mode': report_mode,
                'evidence_sources': {
                    'has_music_analysis': True,
                    'has_text_analysis': True,
                    'has_narration_analysis': bool(narration),
                },
                'music_snapshot': {
                    'duration_sec': music_context['duration_sec'],
                    'bpm': music_context['bpm'],
                    'tags': music_context['tags'],
                    'marker_count': len(music_context['markers']),
                    'report_excerpt': (music_context['report_markdown'] or '')[:600],
                },
                'text_snapshot': {
                    'text_len': len(text_context['raw_text'] or ''),
                    'scene_count': len(text_context['scenes']),
                    'report_excerpt': (text_context['report_markdown'] or '')[:600],
                },
                'narration_snapshot': {
                    'scene_timeline_count': len((narration_payload or {}).get('scene_timeline') or []),
                    'clause_timeline_count': len((narration_payload or {}).get('clause_timeline') or []),
                },
            },
            resp={
                'success': True,
                'cue_count': len(result.get('cues', [])),
                'analysis_mode': result.get('analysis_mode'),
                'effective_report_mode': result.get('effective_report_mode'),
                'llm_attempted_modes': result.get('llm_attempted_modes'),
                'llm_trace_digest': [
                    {
                        'prompt_file': x.get('prompt_file'),
                        'status': (x.get('call_meta') or {}).get('status'),
                        'request_id': (x.get('call_meta') or {}).get('request_id'),
                        'contract_valid': x.get('contract_valid'),
                    }
                    for x in (result.get('llm_trace') or [])
                ],
            },
        )
        _log_user_operation(
            db=db,
            action='fusion_execution',
            project_id=project_id,
            req={'report_mode': report_mode, 'evidence_summary': result.get('evidence_summary') or {}},
            resp={
                'analysis_mode': result.get('analysis_mode'),
                'cue_count': len(result.get('cues') or []),
                'llm_trace_digest': _llm_trace_digest(result.get('llm_trace')),
            },
            file_refs=[],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return jsonify(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/director')
@_enforce_feature_access('director_advise')
def director_advise(project_id: int):
    payload = request.get_json(force=True, silent=True) or {}
    report_mode = (payload.get('report_mode') or request.args.get('report_mode') or 'production').strip().lower()
    debug_prompt = True

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404
        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        text = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()
        fusion = db.execute(select(FusionPlan).where(FusionPlan.project_id == project_id)).scalar_one_or_none()
        narration = db.execute(select(NarrationAnalysis).where(NarrationAnalysis.project_id == project_id)).scalar_one_or_none()
        narration_json = None
        if narration:
            try:
                narration_json = json.loads(narration.timeline_json)
            except json.JSONDecodeError:
                narration_json = None
        if not audio or not text:
            return jsonify({'detail': 'Director advice requires audio + text first'}), 400

        narration_payload = None
        if narration:
            parsed = json.loads(narration.timeline_json)
            narration_payload = parsed if isinstance(parsed, dict) else {'scene_timeline': parsed, 'clause_timeline': []}

        from app.services.llm import generate_report  # local import to avoid circular usage expansion

        evidence = {
            'kind': 'director_final',
            'project_title': project.title,
            'audio_analysis_text': audio.report_markdown,
            'audio_markers': json.loads(audio.markers_json),
            'text_raw': text.raw_text,
            'scenes': json.loads(text.scenes_json),
            'narration': narration_payload or {},
            'fusion_cues': json.loads(fusion.cue_sheet_json) if fusion else [],
        }
        out_json, out_md, meta = generate_report(report_mode, evidence, debug_prompt=debug_prompt)
        return jsonify(
            {
                'report_mode': report_mode,
                'report_json': out_json,
                'report_markdown': out_md,
                'llm_attempted_modes': meta.get('attempted_modes', []),
                'effective_report_mode': meta.get('effective_mode'),
                'llm_trace': meta.get('llm_trace'),
            }
        )


@app.get(f'{settings.api_prefix}/analysis/<int:project_id>/report')
@_enforce_feature_access('view_report')
def get_report(project_id: int):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        text = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()
        fusion = db.execute(select(FusionPlan).where(FusionPlan.project_id == project_id)).scalar_one_or_none()
        narration = db.execute(select(NarrationAnalysis).where(NarrationAnalysis.project_id == project_id)).scalar_one_or_none()
        narration_json = None
        if narration:
            try:
                narration_json = json.loads(narration.timeline_json)
            except json.JSONDecodeError:
                narration_json = None

        return jsonify(
            {
                'project': {'id': project.id, 'title': project.title, 'created_at': project.created_at.isoformat()},
                'audio': None
                if not audio
                else {
                    'duration_sec': audio.duration_sec,
                    'bpm': audio.bpm,
                    'tags': json.loads(audio.tags_json),
                    'markers': json.loads(audio.markers_json),
                    'report_markdown': audio.report_markdown,
                },
                'text': None
                if not text
                else {'scenes': json.loads(text.scenes_json), 'report_markdown': text.report_markdown},
                'narration': None
                if not narration
                else {
                    'duration_sec': narration.duration_sec,
                    'timeline': (
                        narration_json.get('scene_timeline')
                        if isinstance(narration_json, dict)
                        else narration_json
                    ),
                    'clause_timeline': (
                        narration_json.get('clause_timeline', [])
                        if isinstance(narration_json, dict)
                        else []
                    ),
                    'report_markdown': narration.report_markdown,
                },
                'fusion': None
                if not fusion
                else {'cues': json.loads(fusion.cue_sheet_json), 'report_markdown': fusion.report_markdown},
            }
        )


@app.get(f'{settings.api_prefix}/analysis/<int:project_id>/export')
@_enforce_feature_access('export_assets')
def export_report_assets(project_id: int):
    export_type = (request.args.get('type') or '').strip().lower()
    if export_type not in {'cue_csv', 'sfx_zip'}:
        return jsonify({'detail': "type must be one of: cue_csv, sfx_zip"}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        fusion = db.execute(select(FusionPlan).where(FusionPlan.project_id == project_id)).scalar_one_or_none()
        if not fusion:
            return jsonify({'detail': 'Fusion plan not found, generate fusion first'}), 400

        cues = json.loads(fusion.cue_sheet_json)

        if export_type == 'cue_csv':
            out = export_cue_csv(project_id=project.id, project_title=project.title, cues=cues)
            log_reason_event(
                db=db,
                event_type='export_download',
                term='cue_csv',
                backend='n/a',
                project_id=project_id,
                req={'type': 'cue_csv'},
                resp={'success': True, 'cue_count': len(cues)},
            )
            return send_file(out, as_attachment=True, download_name=out.name, mimetype='text/csv')

        out_zip, summary = export_sfx_zip(project_id=project.id, project_title=project.title, cues=cues)
        log_reason_event(
            db=db,
            event_type='export_download',
            term='sfx_zip',
            backend='n/a',
            project_id=project_id,
            req={'type': 'sfx_zip'},
            resp={
                'success': True,
                'required_count': summary.get('required_count'),
                'found_count': summary.get('found_count'),
                'missing_count': summary.get('missing_count'),
                'missing_sfx': summary.get('missing_sfx', []),
            },
        )
        # response headers provide a quick machine-readable summary
        resp = send_file(out_zip, as_attachment=True, download_name=out_zip.name, mimetype='application/zip')
        resp.headers['X-SFX-Required-Count'] = str(summary['required_count'])
        resp.headers['X-SFX-Found-Count'] = str(summary['found_count'])
        resp.headers['X-SFX-Missing-Count'] = str(summary['missing_count'])
        return resp


@app.get(f'{settings.api_prefix}/ops/funnel')
@_require_admin
def ops_funnel():
    days = int((request.args.get('days') or '7').strip())
    days = max(1, min(90, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with SessionLocal() as db:
        rows = db.execute(
            select(ReasoningLog.event_type, ReasoningLog.project_id, ReasoningLog.output_json)
            .where(ReasoningLog.created_at >= cutoff)
            .where(ReasoningLog.project_id.is_not(None))
        ).all()

    step_projects: dict[str, set[int]] = {
        'graph_reason': set(),
        'search_sfx': set(),
        'fusion_build': set(),
        'export_download': set(),
    }
    export_type_count = {'cue_csv': 0, 'sfx_zip': 0}

    for event_type, project_id, output_json in rows:
        if event_type in step_projects:
            step_projects[event_type].add(int(project_id))
        if event_type == 'export_download':
            try:
                payload = json.loads(output_json)
            except json.JSONDecodeError:
                payload = {}
            # payload doesn't include type; infer with counts
            if payload.get('required_count') is not None:
                export_type_count['sfx_zip'] += 1
            else:
                export_type_count['cue_csv'] += 1

    base = len(step_projects['graph_reason']) or 1
    funnel = []
    for step in ('graph_reason', 'search_sfx', 'fusion_build', 'export_download'):
        cnt = len(step_projects[step])
        funnel.append({'step': step, 'project_count': cnt, 'rate_from_graph_reason': round(cnt / base, 4)})

    return jsonify({'days': days, 'funnel': funnel, 'export_type_count': export_type_count})


@app.get(f'{settings.api_prefix}/ops/recommendations')
@_require_admin
def ops_recommendations():
    days = int((request.args.get('days') or '7').strip())
    days = max(1, min(90, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    low_match_terms: dict[str, int] = {}
    missing_sfx_terms: dict[str, int] = {}

    with SessionLocal() as db:
        rows = db.execute(
            select(ReasoningLog.event_type, ReasoningLog.term, ReasoningLog.output_json)
            .where(ReasoningLog.created_at >= cutoff)
        ).all()

    for event_type, term, output_json in rows:
        try:
            payload = json.loads(output_json)
        except json.JSONDecodeError:
            payload = {}

        if event_type == 'search_sfx':
            matches = payload.get('matches') or []
            if len(matches) == 0:
                low_match_terms[term] = low_match_terms.get(term, 0) + 1
        elif event_type == 'export_download':
            for m in payload.get('missing_sfx', []):
                if not m:
                    continue
                missing_sfx_terms[m] = missing_sfx_terms.get(m, 0) + 1

    top_low_match = sorted(low_match_terms.items(), key=lambda x: x[1], reverse=True)[:15]
    top_missing = sorted(missing_sfx_terms.items(), key=lambda x: x[1], reverse=True)[:15]

    return jsonify(
        {
            'days': days,
            'recommendations': {
                'expand_lexicon_for_terms': [{'term': k, 'count': v} for k, v in top_low_match],
                'add_sfx_assets_for_terms': [{'term': k, 'count': v} for k, v in top_missing],
            },
        }
    )


@app.get(f'{settings.api_prefix}/ops/lexicon-draft')
@_require_admin
def ops_lexicon_draft():
    try:
        days = int((request.args.get('days') or '7').strip())
    except ValueError:
        days = 7
    with SessionLocal() as db:
        draft = generate_lexicon_draft(db, days=days)
    return jsonify(draft)


@app.get(f'{settings.api_prefix}/ops/lexicon-review')
@_require_admin
def ops_lexicon_review_list():
    with SessionLocal() as db:
        rows = db.execute(
            select(DraftReview.candidate, DraftReview.target_head, DraftReview.status, DraftReview.note, DraftReview.updated_at)
        ).all()
    out = [
        {
            'candidate': c,
            'target_head': h,
            'status': s,
            'note': n,
            'updated_at': (u.isoformat() if u else None),
        }
        for c, h, s, n, u in rows
    ]
    return jsonify({'count': len(out), 'items': out})


@app.post(f'{settings.api_prefix}/ops/lexicon-review')
@_require_admin
def ops_lexicon_review_upsert():
    payload = request.get_json(force=True)
    items = payload.get('items')
    if not isinstance(items, list):
        return jsonify({'detail': 'items must be a list'}), 400

    valid_status = {'pending', 'approved', 'rejected'}
    upserted = 0
    with SessionLocal() as db:
        for item in items:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get('candidate') or '').strip()
            if not candidate:
                continue
            status = str(item.get('status') or 'pending').strip().lower()
            if status not in valid_status:
                continue
            target_head = str(item.get('target_head') or '').strip()
            note = str(item.get('note') or '').strip()

            exist = db.execute(select(DraftReview).where(DraftReview.candidate == candidate)).scalar_one_or_none()
            if exist is None:
                exist = DraftReview(candidate=candidate, target_head=target_head, status=status, note=note)
                db.add(exist)
            else:
                if target_head:
                    exist.target_head = target_head
                exist.status = status
                exist.note = note
            upserted += 1
        db.commit()

    return jsonify({'ok': True, 'upserted': upserted})


@app.post(f'{settings.api_prefix}/ops/lexicon-draft/apply')
@_require_admin
def ops_lexicon_draft_apply():
    payload = request.get_json(force=True)
    draft_lexicon = payload.get('draft_lexicon')
    draft_items = payload.get('draft_items')
    min_confidence = payload.get('min_confidence')
    only_selected = bool(payload.get('only_selected', False))
    mode = (payload.get('mode') or 'merge').strip().lower()
    dry_run = bool(payload.get('dry_run', False))

    # Alternative input: draft_items (with candidate/target_head/confidence/selected)
    applied_review_items = []
    if draft_lexicon is None and isinstance(draft_items, list):
        threshold = 0.0
        if min_confidence is not None:
            try:
                threshold = max(0.0, min(1.0, float(min_confidence)))
            except (TypeError, ValueError):
                return jsonify({'detail': 'min_confidence must be a number in [0,1]'}), 400
        mapped: dict[str, list[str]] = {}
        for item in draft_items:
            if not isinstance(item, dict):
                continue
            selected = item.get('selected', True)
            if only_selected and not selected:
                continue
            try:
                conf = float(item.get('confidence', 0.0) or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf < threshold:
                continue
            head = str(item.get('target_head') or '').strip()
            cand = str(item.get('candidate') or '').strip()
            if not head or not cand:
                continue
            mapped.setdefault(head, []).append(cand)
            applied_review_items.append({'candidate': cand, 'target_head': head, 'status': 'approved', 'note': 'applied'})
        draft_lexicon = mapped

    if not isinstance(draft_lexicon, dict):
        return jsonify({'detail': 'draft_lexicon must be an object (or provide draft_items)'}), 400
    if mode not in {'merge', 'replace'}:
        return jsonify({'detail': 'mode must be merge or replace'}), 400

    before = get_lexicon()
    if dry_run:
        # preview only
        if mode == 'replace':
            after = {}
            for k, vals in draft_lexicon.items():
                head = str(k).strip()
                if not head:
                    continue
                seen = set()
                uniq = []
                for x in vals:
                    xx = str(x).strip()
                    if xx and xx not in seen:
                        seen.add(xx)
                        uniq.append(xx)
                after[head] = uniq
        else:
            after = dict(before)
            for k, vals in draft_lexicon.items():
                head = str(k).strip()
                if not head:
                    continue
                base = after.get(head, [])
                base += [str(x).strip() for x in vals if str(x).strip()]
                # dedup
                seen = set()
                uniq = []
                for x in base:
                    if x not in seen:
                        seen.add(x)
                        uniq.append(x)
                after[head] = uniq
        return jsonify(
            {
                'ok': True,
                'dry_run': True,
                'before_size': len(before),
                'after_size': len(after),
                'applied_head_count': len(draft_lexicon),
                'applied_item_count': sum(len(v) for v in draft_lexicon.values()),
                'after_lexicon': after,
            }
        )

    if mode == 'merge':
        after = merge_lexicon(draft_lexicon)
    else:
        save_lexicon(draft_lexicon)
        after = get_lexicon()

    if applied_review_items:
        with SessionLocal() as db:
            for item in applied_review_items:
                exist = db.execute(select(DraftReview).where(DraftReview.candidate == item['candidate'])).scalar_one_or_none()
                if exist is None:
                    db.add(
                        DraftReview(
                            candidate=item['candidate'],
                            target_head=item['target_head'],
                            status='approved',
                            note=item['note'],
                        )
                    )
                else:
                    exist.target_head = item['target_head']
                    exist.status = 'approved'
                    exist.note = item['note']
            db.commit()

    return jsonify(
        {
            'ok': True,
            'dry_run': False,
            'mode': mode,
            'before_size': len(before),
            'after_size': len(after),
            'applied_head_count': len(draft_lexicon),
            'applied_item_count': sum(len(v) for v in draft_lexicon.values()),
            'lexicon': after,
        }
    )


@app.get(f'{settings.api_prefix}/ops/user-events')
@_require_admin
def ops_user_events():
    phone = (request.args.get('phone') or '').strip()
    action = (request.args.get('action') or '').strip()
    project_id = request.args.get('project_id', type=int)
    limit = int((request.args.get('limit') or '200').strip())
    limit = max(1, min(1000, limit))
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()

    with SessionLocal() as db:
        stmt = select(UserOperationLog)
        if phone:
            stmt = stmt.where(UserOperationLog.user_phone == phone)
        if action:
            stmt = stmt.where(UserOperationLog.action == action)
        if project_id is not None:
            stmt = stmt.where(UserOperationLog.project_id == project_id)
        if date_from:
            try:
                stmt = stmt.where(UserOperationLog.created_at >= datetime.fromisoformat(date_from))
            except ValueError:
                return jsonify({'detail': 'date_from must be ISO format'}), 400
        if date_to:
            try:
                stmt = stmt.where(UserOperationLog.created_at <= datetime.fromisoformat(date_to))
            except ValueError:
                return jsonify({'detail': 'date_to must be ISO format'}), 400
        rows = db.execute(stmt.order_by(UserOperationLog.id.desc()).limit(limit)).scalars().all()

    items = []
    for r in rows:
        try:
            input_json = json.loads(r.input_json or '{}')
        except json.JSONDecodeError:
            input_json = {}
        try:
            output_json = json.loads(r.output_json or '{}')
        except json.JSONDecodeError:
            output_json = {}
        try:
            file_refs = json.loads(r.file_refs_json or '[]')
        except json.JSONDecodeError:
            file_refs = []
        items.append(
            {
                'id': r.id,
                'project_id': r.project_id,
                'user_phone': r.user_phone,
                'action': r.action,
                'created_at': r.created_at.isoformat() if r.created_at else None,
                'input': input_json,
                'output': output_json,
                'file_refs': file_refs,
            }
        )
    return jsonify({'count': len(items), 'items': items})


@app.get(f'{settings.api_prefix}/ops/project/<int:project_id>/flow-bundle')
@_require_admin
def ops_project_flow_bundle(project_id: int):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404
        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        text = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()
        narration = db.execute(select(NarrationAnalysis).where(NarrationAnalysis.project_id == project_id)).scalar_one_or_none()
        fusion = db.execute(select(FusionPlan).where(FusionPlan.project_id == project_id)).scalar_one_or_none()
        events = db.execute(
            select(UserOperationLog)
            .where(UserOperationLog.project_id == project_id)
            .order_by(UserOperationLog.id.desc())
            .limit(1000)
        ).scalars().all()

    event_items = []
    for e in events:
        try:
            e_in = json.loads(e.input_json or '{}')
        except json.JSONDecodeError:
            e_in = {}
        try:
            e_out = json.loads(e.output_json or '{}')
        except json.JSONDecodeError:
            e_out = {}
        try:
            e_files = json.loads(e.file_refs_json or '[]')
        except json.JSONDecodeError:
            e_files = []
        event_items.append(
            {
                'id': e.id,
                'user_phone': e.user_phone,
                'action': e.action,
                'created_at': e.created_at.isoformat() if e.created_at else None,
                'input_json': e_in,
                'output_json': e_out,
                'file_refs': e_files,
            }
        )

    return jsonify(
        {
            'project': {
                'id': project.id,
                'title': project.title,
                'created_at': project.created_at.isoformat() if project.created_at else None,
            },
            'assets': {
                'audio_file': audio.file_path if audio else None,
                'text_raw': text.raw_text if text else None,
                'narration_file': narration.file_path if narration else None,
                'audio_download_api': (
                    f"{settings.api_prefix}/ops/file?path={quote(audio.file_path, safe='')}" if audio and audio.file_path else None
                ),
                'narration_download_api': (
                    f"{settings.api_prefix}/ops/file?path={quote(narration.file_path, safe='')}" if narration and narration.file_path else None
                ),
            },
            'analysis': {
                'audio_report': audio.report_markdown if audio else None,
                'text_report': text.report_markdown if text else None,
                'narration_report': narration.report_markdown if narration else None,
                'fusion_report': fusion.report_markdown if fusion else None,
            },
            'events': event_items,
        }
    )


@app.get(f'{settings.api_prefix}/ops/file')
@_require_admin
def ops_download_local_file():
    raw_path = (request.args.get('path') or '').strip()
    if not raw_path:
        return jsonify({'detail': 'path is required'}), 400
    p = Path(raw_path).expanduser()
    abs_p = p.resolve() if p.is_absolute() else (Path(settings.upload_dir).resolve() / p).resolve()
    if not _is_under_upload_root(abs_p):
        return jsonify({'detail': 'path must be under UPLOAD_DIR'}), 400
    if not abs_p.exists() or not abs_p.is_file():
        return jsonify({'detail': 'file not found'}), 404
    return send_file(abs_p, as_attachment=True, download_name=abs_p.name)


if __name__ == '__main__':
    Base.metadata.create_all(bind=engine)
    app.run(host='0.0.0.0', port=8090, debug=False, use_reloader=False)
