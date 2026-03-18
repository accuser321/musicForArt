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
from sqlalchemy import inspect, select, text as sql_text

from app.config import settings
from app.db import SessionLocal, engine
from app.models import (
    ActionSupplementAsset,
    ActionGraphInheritanceReview,
    ActionSupplementTask,
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
from app.services.action_verbs import analyze_action_verbs
from app.services.action_sfx_graph import (
    build_asset_scope_label,
    build_asset_variant_display_name,
    build_action_node_key,
    build_action_sfx_recommendation,
    classify_sfx_terms,
    get_action_node_layer_term_items,
    load_global_sfx_label_coverage,
    load_action_node_coverage,
)
from app.services.action_graph_draft import apply_action_graph_draft, generate_action_graph_draft
from app.services.action_graph_neo4j import (
    action_graph_neo4j_status,
    list_action_graph_nodes,
    query_action_graph_node,
    sync_action_graph_to_neo4j,
)
from app.services.action_graph_manage import (
    delete_action_graph_node,
    demote_action_graph_terms_to_genre,
    get_action_graph_node_layers,
    list_action_graph_inheritance_blocks,
    list_action_graph_maintenance_catalog,
    promote_action_graph_terms_to_common,
    remove_action_graph_overlap_terms,
    set_action_graph_inheritance_block,
    update_action_graph_node_layer,
)
from app.services.exporter import export_cue_csv, export_sfx_zip
from app.services.reasoning_observability import get_reason_cache, log_reason_event, set_reason_cache
from app.services.ops_draft import generate_lexicon_draft
from app.services.nlp_zh import analyze_cn_tokens
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

SUPPORTED_GENRES = {'玄幻', '言情', '悬疑', '科幻'}


def _is_composite_sfx_term(term: str, children: dict | None = None) -> bool:
    value = str(term or '').strip()
    if not value:
        return False
    child = children or {}
    composite_terms = [str(x).strip() for x in (child.get('composite_sfx_terms') or []) if str(x).strip()]
    if value in composite_terms:
        return True
    return not any(
        hint in value for hint in (
            '声', '音效', '响', '鸣', '啸', '吼',
            '呼吸', '喘息', '脚步', '步伐', '摩擦', '碰撞',
            '破风', '门轴', '门把', '拖拽', '爆裂', '碎裂',
            '敲击', '拍击', '掌击', '拉拽', '推动', '抓取',
        )
    )


def _sfx_mode_label(term: str, children: dict | None = None) -> str:
    return '组合' if _is_composite_sfx_term(term, children) else '直达'


def _build_sfx_display_name(term: str, genre: str, children: dict | None = None) -> str:
    value = str(term or '').strip()
    if not value:
        return ''
    mode = _sfx_mode_label(value, children)
    genre_part = str(genre or '').strip()
    return f'{value}（{mode}{("-" + genre_part) if genre_part else ""}）'


def _ensure_schema_columns() -> None:
    dialect = engine.dialect.name
    with engine.begin() as conn:
        project_cols = {col['name'] for col in inspect(engine).get_columns('projects')}
        if 'genre' not in project_cols:
            if dialect == 'sqlite':
                conn.execute(sql_text("ALTER TABLE projects ADD COLUMN genre VARCHAR(32) NOT NULL DEFAULT '玄幻'"))
            else:
                conn.execute(sql_text("ALTER TABLE projects ADD COLUMN genre VARCHAR(32) NOT NULL DEFAULT '玄幻'"))

        asset_cols = {col['name'] for col in inspect(engine).get_columns('action_supplement_asset')}
        if 'asset_scope' not in asset_cols:
            conn.execute(sql_text("ALTER TABLE action_supplement_asset ADD COLUMN asset_scope VARCHAR(32) NOT NULL DEFAULT 'genre'"))
        if 'asset_scope_genre' not in asset_cols:
            conn.execute(sql_text("ALTER TABLE action_supplement_asset ADD COLUMN asset_scope_genre VARCHAR(32) NOT NULL DEFAULT ''"))


def _record_inheritance_review_hits(db, project_id: int, hits: list[dict]) -> None:
    for item in hits or []:
        genre = str(item.get('genre') or '').strip()
        verb_head = str(item.get('verb_head') or '').strip()
        if not genre or not verb_head:
            continue
        row = db.execute(
            select(ActionGraphInheritanceReview).where(
                ActionGraphInheritanceReview.genre == genre,
                ActionGraphInheritanceReview.verb_head == verb_head,
            )
        ).scalar_one_or_none()
        excerpt = str(item.get('sentence_excerpt') or '').strip()
        if row is None:
            row = ActionGraphInheritanceReview(
                genre=genre,
                verb_head=verb_head,
                project_id=project_id,
                hit_count=1,
                sample_excerpt=excerpt,
                status='active',
            )
            db.add(row)
        else:
            row.project_id = project_id
            row.hit_count = int(row.hit_count or 0) + 1
            if excerpt:
                row.sample_excerpt = excerpt
            row.status = 'active'


def _normalize_phone(raw: str) -> str:
    phone = re.sub(r'[^0-9]', '', raw or '')
    if phone.startswith('86') and len(phone) == 13:
        phone = phone[2:]
    return phone


def _is_valid_phone(phone: str) -> bool:
    return bool(re.fullmatch(r'1\d{10}', phone or ''))


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
    phone = getattr(g, 'current_user_phone', None)
    if phone:
        return str(phone)
    u = getattr(g, 'current_user', None)
    if isinstance(u, str) and u:
        return u
    return _normalize_phone(_extract_user_phone())


def _require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with SessionLocal() as db:
            u = _get_session_user(db)
            if u is None:
                return jsonify({'detail': '请先手机号登录'}), 401
            g.current_user = u
            g.current_user_phone = u.phone
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
            g.current_user_phone = u.phone
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
            g.current_user_phone = u.phone if u else ''
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
    _ensure_schema_columns()


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
    if not _is_valid_phone(phone):
        return jsonify({'detail': '请输入有效的11位手机号'}), 400

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
    if not _is_valid_phone(phone):
        return jsonify({'detail': '请输入有效的11位手机号'}), 400

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
    if not _is_valid_phone(phone):
        return jsonify({'detail': '请输入有效的11位手机号'}), 400

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


@app.get(f'{settings.api_prefix}/semantic/tokenize')
def semantic_tokenize():
    text = (request.args.get('text') or '').strip()
    if not text:
        return jsonify({'detail': 'text is required'}), 400
    return jsonify(analyze_cn_tokens(text))


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


@app.get(f'{settings.api_prefix}/action-graph/neo4j-status')
@_require_admin
def api_action_graph_neo4j_status():
    return jsonify(action_graph_neo4j_status())


@app.post(f'{settings.api_prefix}/action-graph/neo4j-sync')
@_require_admin
def api_action_graph_neo4j_sync():
    out = sync_action_graph_to_neo4j()
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.get(f'{settings.api_prefix}/action-graph/node')
@_require_admin
def api_action_graph_node():
    node_key = (request.args.get('node_key') or '').strip()
    if not node_key:
        return jsonify({'detail': 'node_key is required'}), 400
    out = query_action_graph_node(node_key)
    layer_info = get_action_graph_node_layers(node_key)
    if not layer_info.get('detail'):
        out['graph_layers'] = layer_info
    if not out.get('detail'):
        genre = str(out.get('genre') or '').strip()
        verb_head = str(out.get('verb_head') or '').strip()
        with SessionLocal() as db:
            rows = (
                db.execute(
                    select(ActionSupplementTask).where(
                        ActionSupplementTask.genre == genre,
                        ActionSupplementTask.target_head == verb_head,
                    )
                ).scalars().all()
                if genre and verb_head
                else []
            )
            row_ids = [row.id for row in rows]
            asset_rows = (
                db.execute(
                    select(ActionSupplementAsset).where(ActionSupplementAsset.supplement_id.in_(row_ids))
                ).scalars().all()
                if row_ids
                else []
            )
            assets_by_supp: dict[int, list[dict]] = {}
            for asset in asset_rows:
                asset_children = {'composite_sfx_terms': [str(x).strip() for x in (out.get('composite_sfx_terms') or []) if str(x).strip()]}
                assets_by_supp.setdefault(int(asset.supplement_id), []).append(
                    {
                        'asset_label': str(asset.asset_label or '').strip(),
                        'asset_scope': str(asset.asset_scope or 'genre').strip().lower() or 'genre',
                        'asset_scope_genre': str(asset.asset_scope_genre or genre or '').strip(),
                        'display_name': build_asset_variant_display_name(
                            str(asset.asset_label or '').strip(),
                            str(asset.asset_scope or 'genre').strip().lower() or 'genre',
                            str(asset.asset_scope_genre or genre or '').strip() or genre,
                        ),
                        'scope_label': build_asset_scope_label(
                            str(asset.asset_scope or 'genre').strip().lower() or 'genre',
                            str(asset.asset_scope_genre or genre or '').strip() or genre,
                        ),
                        'sfx_mode': _sfx_mode_label(str(asset.asset_label or '').strip(), asset_children),
                        'asset_file_path': str(asset.asset_file_path or '').strip(),
                        'file_name': Path(asset.asset_file_path or '').name if asset.asset_file_path else '',
                        'created_at': asset.created_at.isoformat() if asset.created_at else '',
                    }
                )
            status_counter: dict[str, int] = {}
            latest_created_at = ''
            target_terms = _merge_unique_list([str(x).strip() for x in (out.get('sfx_terms') or []) if str(x).strip()])
            global_asset_labels = set()
            for asset in asset_rows:
                label = str(asset.asset_label or '').strip()
                if label:
                    global_asset_labels.add(label)
            covered_terms_set = set()
            supplement_items = []
            for row in rows:
                status_counter[row.status] = status_counter.get(row.status, 0) + 1
                if row.created_at:
                    ts = row.created_at.isoformat()
                    if ts > latest_created_at:
                        latest_created_at = ts
                assets = assets_by_supp.get(int(row.id), [])
                try:
                    row_missing_terms = [str(x).strip() for x in json.loads(row.missing_sfx_terms_json or '[]') if str(x).strip()]
                except json.JSONDecodeError:
                    row_missing_terms = []
                try:
                    row_sfx_terms = [str(x).strip() for x in json.loads(row.sfx_terms_json or '[]') if str(x).strip()]
                except json.JSONDecodeError:
                    row_sfx_terms = []
                row_target_terms = _merge_unique_list(row_sfx_terms or row_missing_terms or target_terms)
                row_covered_terms = [term for term in row_target_terms if term in global_asset_labels]
                row_pending_terms = [term for term in row_target_terms if term not in global_asset_labels]
                row_completion_ratio = round((len(row_covered_terms) / len(row_target_terms)), 4) if row_target_terms else 1.0
                ready_to_notify = (row.status == 'ready_to_notify' or not row_pending_terms) and not row.notified_at
                supplement_items.append(
                    {
                        'id': row.id,
                        'status': row.status,
                        'notification_status': '已通知' if row.notified_at else '未通知',
                        'notified_at': row.notified_at.isoformat() if row.notified_at else '',
                        'user_phone': row.user_phone,
                        'sentence_excerpt': row.sentence_excerpt,
                        'created_at': row.created_at.isoformat() if row.created_at else '',
                        'assets': assets,
                        'target_terms': row_target_terms,
                        'covered_terms': row_covered_terms,
                        'pending_terms': row_pending_terms,
                        'covered_count': len(row_covered_terms),
                        'pending_count': len(row_pending_terms),
                        'completion_ratio': row_completion_ratio,
                        'ready_to_notify': ready_to_notify,
                    }
                )
            for asset in asset_rows:
                label = str(asset.asset_label or '').strip()
                if label:
                    covered_terms_set.add(label)
            covered_terms = [term for term in target_terms if term in covered_terms_set]
            pending_terms = [term for term in target_terms if term not in covered_terms_set]
            ready_to_notify_count = sum(1 for item in supplement_items if item.get('ready_to_notify'))
            notified_count = sum(1 for row in rows if row.notified_at)
            incomplete_count = sum(1 for item in supplement_items if not item.get('ready_to_notify'))
        out['supplement_summary'] = {
            'item_count': len(rows),
            'status_counter': status_counter,
            'latest_created_at': latest_created_at,
            'target_terms': target_terms,
            'covered_terms': covered_terms,
            'pending_terms': pending_terms,
            'covered_count': len(covered_terms),
            'pending_count': len(pending_terms),
            'completion_ratio': round((len(covered_terms) / len(target_terms)), 4) if target_terms else 1.0,
            'ready_to_notify_count': ready_to_notify_count,
            'notified_count': notified_count,
            'incomplete_count': incomplete_count,
            'supplement_items': supplement_items[:20],
        }
        out['business_explanation'] = {
            'display_name_rule': '下载版本：{音效词}（通用） 或 {音效词}（赛道）',
            'semantic_edge_meaning': '语义扩展词用于扩展理解与召回，不等于必须上传素材。',
            'direct_edge_meaning': '直达音效边表示可直接命中的素材标签，适合用户直接下载或运营直接补库。',
            'composite_edge_meaning': '组合音效边表示整体动作音效，适合直接交付给用户作为成品动作音效使用。',
            'operator_hint': '运营补库时，需要先选择上传的是通用版素材还是当前赛道版素材；如果节点含有（组合）标签，则说明该词可作为整体动作音效单独上传。',
        }
    code = 200 if not out.get('detail') else 404
    return jsonify(out), code


@app.get(f'{settings.api_prefix}/action-graph/node-layers')
@_require_admin
def api_action_graph_node_layers():
    node_key = (request.args.get('node_key') or '').strip()
    target_genre = (request.args.get('target_genre') or '').strip()
    out = get_action_graph_node_layers(node_key, target_genre=target_genre)
    code = 200 if not out.get('detail') else 404
    return jsonify(out), code


@app.get(f'{settings.api_prefix}/action-graph/maintenance-catalog')
@_require_admin
def api_action_graph_maintenance_catalog():
    out = list_action_graph_maintenance_catalog()
    return jsonify(out), 200


@app.post(f'{settings.api_prefix}/action-graph/node-layer')
@_require_admin
def api_action_graph_node_layer_update():
    payload = request.get_json(force=True) or {}
    out = update_action_graph_node_layer(
        node_key=str(payload.get('node_key') or '').strip(),
        layer=str(payload.get('layer') or '').strip(),
        semantic_terms=[str(x).strip() for x in (payload.get('semantic_terms') or []) if str(x).strip()],
        sfx_terms=[str(x).strip() for x in (payload.get('sfx_terms') or []) if str(x).strip()],
    )
    if out.get('ok'):
        neo4j_sync = sync_action_graph_to_neo4j()
        out['neo4j_sync'] = neo4j_sync
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.post(f'{settings.api_prefix}/action-graph/promote-to-common')
@_require_admin
def api_action_graph_promote_to_common():
    payload = request.get_json(force=True) or {}
    out = promote_action_graph_terms_to_common(
        node_key=str(payload.get('node_key') or '').strip(),
        semantic_terms=[str(x).strip() for x in (payload.get('semantic_terms') or []) if str(x).strip()],
        sfx_terms=[str(x).strip() for x in (payload.get('sfx_terms') or []) if str(x).strip()],
        remove_from_genre=bool(payload.get('remove_from_genre')),
    )
    if out.get('ok'):
        neo4j_sync = sync_action_graph_to_neo4j()
        out['neo4j_sync'] = neo4j_sync
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.post(f'{settings.api_prefix}/action-graph/demote-to-genre')
@_require_admin
def api_action_graph_demote_to_genre():
    payload = request.get_json(force=True) or {}
    out = demote_action_graph_terms_to_genre(
        node_key=str(payload.get('node_key') or '').strip(),
        semantic_terms=[str(x).strip() for x in (payload.get('semantic_terms') or []) if str(x).strip()],
        sfx_terms=[str(x).strip() for x in (payload.get('sfx_terms') or []) if str(x).strip()],
        target_genre=str(payload.get('target_genre') or '').strip(),
        remove_from_common=bool(payload.get('remove_from_common')),
    )
    if out.get('ok'):
        neo4j_sync = sync_action_graph_to_neo4j()
        out['neo4j_sync'] = neo4j_sync
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.post(f'{settings.api_prefix}/action-graph/remove-overlap')
@_require_admin
def api_action_graph_remove_overlap():
    payload = request.get_json(force=True) or {}
    out = remove_action_graph_overlap_terms(
        node_key=str(payload.get('node_key') or '').strip(),
        semantic_terms=[str(x).strip() for x in (payload.get('semantic_terms') or []) if str(x).strip()],
        sfx_terms=[str(x).strip() for x in (payload.get('sfx_terms') or []) if str(x).strip()],
        remove_from_layer=str(payload.get('remove_from_layer') or '').strip(),
        target_genre=str(payload.get('target_genre') or '').strip(),
    )
    if out.get('ok'):
        neo4j_sync = sync_action_graph_to_neo4j()
        out['neo4j_sync'] = neo4j_sync
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.post(f'{settings.api_prefix}/action-graph/node-delete')
@_require_admin
def api_action_graph_node_delete():
    payload = request.get_json(force=True) or {}
    out = delete_action_graph_node(
        node_key=str(payload.get('node_key') or '').strip(),
        action=str(payload.get('action') or '').strip(),
        target_genre=str(payload.get('target_genre') or '').strip(),
    )
    if out.get('ok'):
        neo4j_sync = sync_action_graph_to_neo4j()
        out['neo4j_sync'] = neo4j_sync
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.get(f'{settings.api_prefix}/action-graph/inheritance-dashboard')
@_require_admin
def api_action_graph_inheritance_dashboard():
    blocked = list_action_graph_inheritance_blocks()
    days = max(1, min(int((request.args.get('days') or '30').strip()), 365))
    cutoff = datetime.now() - timedelta(days=days)
    with SessionLocal() as db:
        rows = db.execute(
            select(ActionGraphInheritanceReview)
            .where(ActionGraphInheritanceReview.updated_at >= cutoff)
            .order_by(ActionGraphInheritanceReview.hit_count.desc(), ActionGraphInheritanceReview.updated_at.desc())
        ).scalars().all()
    blocked_pairs = {(str(item.get('genre') or '').strip(), str(item.get('verb_head') or '').strip()) for item in (blocked.get('items') or [])}
    review_items = []
    for row in rows:
        genre = str(row.genre or '').strip()
        verb_head = str(row.verb_head or '').strip()
        if not genre or not verb_head:
            continue
        review_items.append(
            {
                'id': int(row.id),
                'genre': genre,
                'verb_head': verb_head,
                'hit_count': int(row.hit_count or 0),
                'sample_excerpt': str(row.sample_excerpt or '').strip(),
                'status': str(row.status or 'active').strip() or 'active',
                'updated_at': row.updated_at.isoformat() if row.updated_at else '',
                'is_currently_blocked': (genre, verb_head) in blocked_pairs,
            }
        )
    return jsonify(
        {
            'blocked_pool': blocked,
            'review_pool': {
                'days': days,
                'items': review_items,
                'count': len(review_items),
            },
        }
    ), 200


@app.post(f'{settings.api_prefix}/action-graph/inheritance-block')
@_require_admin
def api_action_graph_inheritance_block():
    payload = request.get_json(force=True) or {}
    genre = str(payload.get('genre') or '').strip()
    verb_head = str(payload.get('verb_head') or '').strip()
    action = str(payload.get('action') or '').strip().lower()
    if action not in {'block', 'restore'}:
        return jsonify({'ok': False, 'detail': 'action must be block or restore'}), 400
    out = set_action_graph_inheritance_block(verb_head=verb_head, genre=genre, blocked=(action == 'block'))
    if out.get('ok'):
        with SessionLocal() as db:
            row = db.execute(
                select(ActionGraphInheritanceReview).where(
                    ActionGraphInheritanceReview.genre == genre,
                    ActionGraphInheritanceReview.verb_head == verb_head,
                )
            ).scalar_one_or_none()
            if row is not None:
                row.status = 'restored' if action == 'restore' else 'blocked'
                db.commit()
        neo4j_sync = sync_action_graph_to_neo4j()
        out['neo4j_sync'] = neo4j_sync
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.get(f'{settings.api_prefix}/action-graph/nodes')
@_require_admin
def api_action_graph_nodes():
    genre = (request.args.get('genre') or '').strip()
    q = (request.args.get('q') or '').strip()
    limit = int((request.args.get('limit') or '50').strip())
    only_with_gap = (request.args.get('only_with_gap') or '').strip().lower() in {'1', 'true', 'yes'}
    sort_by = (request.args.get('sort_by') or 'pending').strip()
    status_filter = (request.args.get('status_filter') or 'all').strip()
    out = list_action_graph_nodes(
        genre=genre,
        q=q,
        limit=limit,
        only_with_gap=only_with_gap,
        sort_by=sort_by,
        status_filter=status_filter,
    )
    code = 200 if not out.get('detail') else 400
    return jsonify(out), code


@app.get(f'{settings.api_prefix}/action-graph/explanation')
def api_action_graph_explanation():
    return jsonify(
        {
            'display_name_rule': '下载版本：{音效词}（通用） 或 {音效词}（赛道）',
            'semantic_edge_meaning': '语义扩展词用于扩展理解与召回，不等于必须上传素材。',
            'direct_edge_meaning': '直达音效边表示可直接命中的素材标签，适合用户直接下载或运营直接补库。',
            'composite_edge_meaning': '组合音效边表示整体动作音效，适合直接交付给用户作为成品动作音效使用。',
            'user_hint': '如果你想快速出结果，可优先选择组合音效；如果你想自己叠加设计层次，可优先选择直达音效。',
            'operator_hint': '运营补库时，需要先选择上传的是通用版素材还是当前赛道版素材；如果节点含有（组合）标签，则说明该词可作为整体动作音效单独上传。',
        }
    )


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
    genre = (payload.get('genre') or '玄幻').strip()
    if not title:
        return jsonify({'detail': 'title is required'}), 400
    if genre not in SUPPORTED_GENRES:
        return jsonify({'detail': 'genre must be one of: 玄幻, 言情, 悬疑, 科幻'}), 400

    with SessionLocal() as db:
        project = Project(title=title, genre=genre)
        db.add(project)
        db.commit()
        db.refresh(project)
        _log_user_operation(
            db=db,
            action='create_project',
            project_id=project.id,
            req={'title': title, 'genre': genre},
            resp={'project_id': project.id, 'genre': genre},
            file_refs=[],
        )
        return jsonify(
            {
                'id': project.id,
                'title': project.title,
                'genre': project.genre,
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
        return jsonify({'id': project.id, 'title': project.title, 'genre': getattr(project, 'genre', '玄幻'), 'created_at': project.created_at.isoformat()})


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


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/action-verbs')
@_enforce_feature_access('action_verb_analysis')
def analyze_action_verbs_api(project_id: int):
    payload = request.get_json(force=True)
    text = (payload.get('text') or '').strip()
    genre = (payload.get('genre') or '').strip()
    prompt_file = (payload.get('prompt_file') or '').strip()
    report_mode = (payload.get('report_mode') or request.args.get('report_mode') or settings.report_mode_default).strip().lower()
    debug_prompt = True
    if not text:
        return jsonify({'detail': 'text is required'}), 400

    with SessionLocal() as db:
        project_row = db.execute(
            sql_text('SELECT id, title FROM projects WHERE id = :pid'),
            {'pid': project_id},
        ).first()
        if not project_row:
            return jsonify({'detail': 'Project not found'}), 404

        effective_genre = genre or '玄幻'
        result = analyze_action_verbs(text, genre=effective_genre, prompt_file=prompt_file, report_mode=report_mode, debug_prompt=debug_prompt)
        _log_user_operation(
            db=db,
            action='action_verb_analysis',
            project_id=project_id,
            req={'report_mode': report_mode, 'text_len': len(text), 'genre': effective_genre, 'prompt_file': prompt_file},
            resp={
                'analysis_mode': result.get('analysis_mode'),
                'genre': result.get('genre'),
                'qualified_count': len(((result.get('report_json') or {}).get('qualified_actions') or [])),
                'candidate_count': len(((result.get('report_json') or {}).get('action_candidates') or [])),
                'llm_trace_digest': _llm_trace_digest(result.get('llm_trace')),
            },
            file_refs=[],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return jsonify(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/action-sfx')
@_enforce_feature_access('action_sfx_graph')
def analyze_action_sfx_api(project_id: int):
    payload = request.get_json(force=True) or {}
    action_report = payload.get('action_report')

    if not isinstance(action_report, dict):
        return jsonify({'detail': 'action_report is required and must be object'}), 400

    with SessionLocal() as db:
        project_row = db.execute(
            sql_text('SELECT id, title FROM projects WHERE id = :pid'),
            {'pid': project_id},
        ).first()
        if not project_row:
            return jsonify({'detail': 'Project not found'}), 404

        result = build_action_sfx_recommendation(project_id=project_id, action_report=action_report)
        _record_inheritance_review_hits(db, project_id, result.get('blocked_inheritance_hits') or [])
        db.commit()
        _log_user_operation(
            db=db,
            action='action_sfx_graph',
            project_id=project_id,
            req={
                'genre': action_report.get('genre', ''),
                'verb_count': len((action_report.get('action_candidates') or [])),
            },
            resp={
                'graph_item_count': len(result.get('graph_items') or []),
                'asset_count': (result.get('summary') or {}).get('asset_count', 0),
            },
            file_refs=[],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return jsonify(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/action-graph-draft')
@_enforce_feature_access('action_graph_draft')
def analyze_action_graph_draft_api(project_id: int):
    payload = request.get_json(force=True) or {}
    action_sfx_result = payload.get('action_sfx_result')

    if not isinstance(action_sfx_result, dict):
        return jsonify({'detail': 'action_sfx_result is required and must be object'}), 400

    with SessionLocal() as db:
        project_row = db.execute(
            sql_text('SELECT id, title FROM projects WHERE id = :pid'),
            {'pid': project_id},
        ).first()
        if not project_row:
            return jsonify({'detail': 'Project not found'}), 404

        result = generate_action_graph_draft(action_sfx_result)
        phone = _extract_user_phone()
        draft_node_keys = {
            build_action_node_key(str(item.get('target_genre') or result.get('genre') or '').strip(), str(item.get('target_head') or item.get('verb') or '').strip())
            for item in (result.get('draft_items') or [])
            if isinstance(item, dict) and str(item.get('target_head') or item.get('verb') or '').strip()
        }
        coverage_by_node = load_action_node_coverage(draft_node_keys)
        global_label_coverage = load_global_sfx_label_coverage()
        for item in result.get('draft_items') or []:
            if not isinstance(item, dict):
                continue
            verb = str(item.get('verb') or '').strip()
            target_head = str(item.get('target_head') or '').strip()
            target_genre = str(item.get('target_genre') or '').strip()
            if not verb:
                continue
            node_key = build_action_node_key(target_genre or str(result.get('genre') or '').strip(), target_head or verb)
            covered_labels = set(str(x).strip() for x in ((coverage_by_node.get(node_key) or {}).get('covered_labels') or []) if str(x).strip())
            covered_labels.update(str(x).strip() for x in (global_label_coverage.get('labels') or []) if str(x).strip())
            sfx_terms = [str(x).strip() for x in (item.get('sfx_terms') or []) if str(x).strip()]
            covered_count = len(set(sfx_terms) & covered_labels)
            effective_status = 'ready_to_notify' if sfx_terms and covered_count >= len(set(sfx_terms)) else ('partial' if covered_count else 'pending')
            existing_rows = db.execute(
                select(ActionSupplementTask).where(
                    ActionSupplementTask.project_id == project_id,
                    ActionSupplementTask.user_phone == phone,
                    ActionSupplementTask.verb == verb,
                    ActionSupplementTask.target_head == target_head,
                    ActionSupplementTask.target_genre == target_genre,
                    ActionSupplementTask.status == 'pending',
                )
            ).scalars().all()
            if existing_rows:
                primary = existing_rows[0]
                primary.sentence_excerpt = str(item.get('sentence_excerpt') or '').strip()
                primary.semantic_terms_json = json.dumps(item.get('semantic_terms') or [], ensure_ascii=False)
                primary.sfx_terms_json = json.dumps(item.get('sfx_terms') or [], ensure_ascii=False)
                primary.missing_sfx_terms_json = json.dumps(item.get('missing_sfx_terms') or [], ensure_ascii=False)
                primary.status = effective_status
                for extra in existing_rows[1:]:
                    extra.status = 'merged_duplicate'
            else:
                db.add(
                    ActionSupplementTask(
                        project_id=project_id,
                        user_phone=phone,
                        genre=str(result.get('genre') or ''),
                        verb=verb,
                        target_head=target_head or verb,
                        target_genre=target_genre,
                        sentence_excerpt=str(item.get('sentence_excerpt') or '').strip(),
                        semantic_terms_json=json.dumps(item.get('semantic_terms') or [], ensure_ascii=False),
                        sfx_terms_json=json.dumps(item.get('sfx_terms') or [], ensure_ascii=False),
                        missing_sfx_terms_json=json.dumps(item.get('missing_sfx_terms') or [], ensure_ascii=False),
                        status=effective_status,
                    )
                )
        db.commit()
        _log_user_operation(
            db=db,
            action='action_supplement_sheet',
            project_id=project_id,
            req={
                'genre': action_sfx_result.get('genre', ''),
                'graph_item_count': len(action_sfx_result.get('graph_items') or []),
            },
            resp={
                'draft_count': result.get('draft_count', 0),
                'draft_items': result.get('draft_items', []),
            },
            file_refs=[],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return jsonify(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/action-graph-draft/apply')
@_enforce_feature_access('action_graph_apply')
def apply_action_graph_draft_api(project_id: int):
    payload = request.get_json(force=True) or {}
    draft_result = payload.get('draft_result')

    if not isinstance(draft_result, dict):
        return jsonify({'detail': 'draft_result is required and must be object'}), 400

    with SessionLocal() as db:
        project_row = db.execute(
            sql_text('SELECT id, title FROM projects WHERE id = :pid'),
            {'pid': project_id},
        ).first()
        if not project_row:
            return jsonify({'detail': 'Project not found'}), 404

        result = apply_action_graph_draft(draft_result)
        neo4j_sync = sync_action_graph_to_neo4j()
        _log_user_operation(
            db=db,
            action='action_graph_apply',
            project_id=project_id,
            req={
                'genre': draft_result.get('genre', ''),
                'draft_count': int(draft_result.get('draft_count') or 0),
            },
            resp={
                'ok': bool(result.get('ok')),
                'genre': result.get('genre', ''),
                'neo4j_sync_ok': bool((neo4j_sync or {}).get('ok')),
            },
            file_refs=[],
        )
        result['neo4j_sync'] = neo4j_sync
        result['usage'] = getattr(g, 'usage_info', None)
        return jsonify(result)


@app.get(f'{settings.api_prefix}/ops/action-supplements')
@_require_admin
def ops_action_supplements():
    days = int((request.args.get('days') or '30').strip())
    days = max(1, min(180, days))
    status = (request.args.get('status') or '').strip()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with SessionLocal() as db:
        stmt = select(ActionSupplementTask).where(ActionSupplementTask.created_at >= cutoff)
        if status in {'pending', 'partial'}:
            stmt = stmt.where(ActionSupplementTask.status == status)
        elif status == 'ready_to_notify':
            stmt = stmt.where(ActionSupplementTask.status == 'ready_to_notify')
        rows = db.execute(stmt.order_by(ActionSupplementTask.created_at.desc()).limit(500)).scalars().all()
        supp_ids = [r.id for r in rows]
        asset_rows = (
            db.execute(
                select(ActionSupplementAsset).where(ActionSupplementAsset.supplement_id.in_(supp_ids))
            ).scalars().all()
            if supp_ids
            else []
        )
    node_keys = {
        build_action_node_key(str(r.target_genre or r.genre or '').strip(), str(r.target_head or r.verb or '').strip())
        for r in rows
        if str(r.target_head or r.verb or '').strip()
    }
    coverage_by_node = load_action_node_coverage(node_keys)
    global_label_coverage = load_global_sfx_label_coverage()

    assets_by_supp: dict[int, list[dict]] = {}
    for a in asset_rows:
        assets_by_supp.setdefault(a.supplement_id, []).append(
            {
                'id': a.id,
                'asset_label': a.asset_label,
                'asset_scope': str(a.asset_scope or 'genre').strip().lower() or 'genre',
                'asset_scope_genre': str(a.asset_scope_genre or '').strip(),
                'asset_file_path': a.asset_file_path,
                'file_name': Path(a.asset_file_path).name if a.asset_file_path else '',
                'created_at': a.created_at.isoformat() if a.created_at else '',
            }
        )

    items = []
    status_counter: dict[str, int] = {}
    notified_count = 0
    total_pending_terms = 0
    total_covered_terms = 0
    merged_count = 0
    unique_users = set()
    for r in rows:
        try:
            semantic_terms = json.loads(r.semantic_terms_json or '[]')
        except json.JSONDecodeError:
            semantic_terms = []
        try:
            sfx_terms = json.loads(r.sfx_terms_json or '[]')
        except json.JSONDecodeError:
            sfx_terms = []
        try:
            missing_sfx_terms = json.loads(r.missing_sfx_terms_json or '[]')
        except json.JSONDecodeError:
            missing_sfx_terms = []
        node_key = build_action_node_key(str(r.target_genre or r.genre or '').strip(), str(r.target_head or r.verb or '').strip())
        coverage = coverage_by_node.get(node_key) or {}
        merged_assets = [dict(x) for x in (coverage.get('assets') or assets_by_supp.get(r.id, []))]
        for term in _merge_unique_list(sfx_terms or missing_sfx_terms):
            merged_assets.extend(dict(x) for x in ((global_label_coverage.get('assets_by_label') or {}).get(term) or []))
        dedup_asset_keys = set()
        normalized_assets = []
        for asset in merged_assets:
            key = (
                f"{str(asset.get('asset_label') or '').strip()}"
                f"|{str(asset.get('asset_scope') or '').strip()}"
                f"|{str(asset.get('asset_scope_genre') or '').strip()}"
                f"|{str(asset.get('asset_file_path') or asset.get('file_path') or '').strip()}"
            )
            if key in dedup_asset_keys:
                continue
            dedup_asset_keys.add(key)
            normalized_assets.append(asset)
        merged_assets = normalized_assets
        merged_labels = _merge_unique_list(list(coverage.get('covered_labels') or []) + list(global_label_coverage.get('labels') or []))
        target_terms = _merge_unique_list(sfx_terms or missing_sfx_terms)
        target_terms_classified = classify_sfx_terms(target_terms)
        pending_terms_classified = classify_sfx_terms([term for term in target_terms if term not in set(merged_labels)])
        covered_terms = [term for term in target_terms if term in set(merged_labels)]
        pending_terms = [term for term in target_terms if term not in set(merged_labels)]
        completion_ratio = round((len(covered_terms) / len(target_terms)), 4) if target_terms else 1.0
        is_merged = not pending_terms
        effective_status = str(r.status or '').strip()
        if effective_status != 'merged_duplicate':
            if is_merged:
                effective_status = 'ready_to_notify'
            elif covered_terms:
                effective_status = 'partial'
            else:
                effective_status = 'pending'
        merged_assets_with_display = []
        for asset in merged_assets:
            if not isinstance(asset, dict):
                continue
            label = str(asset.get('asset_label') or '').strip()
            merged_assets_with_display.append(
                {
                    **asset,
                    'display_name': build_asset_variant_display_name(
                        label,
                        str(asset.get('asset_scope') or 'genre').strip().lower() or 'genre',
                        str(asset.get('asset_scope_genre') or r.genre or '').strip() or r.genre,
                    ),
                    'scope_label': build_asset_scope_label(
                        str(asset.get('asset_scope') or 'genre').strip().lower() or 'genre',
                        str(asset.get('asset_scope_genre') or r.genre or '').strip() or r.genre,
                    ),
                    'sfx_mode': _sfx_mode_label(label, {'composite_sfx_terms': target_terms_classified['composite_terms']}),
                }
            )
        unique_users.add(r.user_phone or '')
        status_counter[effective_status] = status_counter.get(effective_status, 0) + 1
        if r.notified_at:
            notified_count += 1
        if is_merged:
            merged_count += 1
        total_pending_terms += len(pending_terms)
        total_covered_terms += len(covered_terms)
        layer_term_items = get_action_node_layer_term_items(
            str(r.target_genre or r.genre or '').strip(),
            str(r.target_head or r.verb or '').strip(),
        )
        semantic_term_items = list(layer_term_items.get('semantic_term_items') or [])
        direct_term_items = list(layer_term_items.get('direct_sfx_term_items') or [])
        composite_term_items = list(layer_term_items.get('composite_sfx_term_items') or [])
        direct_source_map = {str(item.get('term') or '').strip(): item for item in direct_term_items if str(item.get('term') or '').strip()}
        composite_source_map = {str(item.get('term') or '').strip(): item for item in composite_term_items if str(item.get('term') or '').strip()}
        missing_direct_term_items = [
            dict(direct_source_map.get(str(term).strip()) or {
                'term': str(term).strip(),
                'source': 'unknown',
                'source_label': '未标注',
            })
            for term in pending_terms_classified['direct_terms']
            if str(term).strip()
        ]
        missing_composite_term_items = [
            dict(composite_source_map.get(str(term).strip()) or {
                'term': str(term).strip(),
                'source': 'unknown',
                'source_label': '未标注',
            })
            for term in pending_terms_classified['composite_terms']
            if str(term).strip()
        ]

        items.append(
            {
                'id': r.id,
                'project_id': r.project_id,
                'user_phone': r.user_phone,
                'genre': r.genre,
                'verb': r.verb,
                'parent_node': {
                    'genre': r.genre,
                    'verb_head': r.verb,
                    'node_key': f'{r.genre}::{r.verb}' if r.genre else r.verb,
                },
                'target_head': r.target_head,
                'target_genre': r.target_genre,
                'sentence_excerpt': r.sentence_excerpt,
                'semantic_terms': semantic_terms,
                'sfx_terms': sfx_terms,
                'missing_sfx_terms': missing_sfx_terms,
                'sfx_terms_classified': {
                    'direct_terms': target_terms_classified['direct_terms'],
                    'composite_terms': target_terms_classified['composite_terms'],
                    'display_terms': target_terms_classified['display_terms'],
                },
                'missing_sfx_terms_classified': {
                    'direct_terms': pending_terms_classified['direct_terms'],
                    'composite_terms': pending_terms_classified['composite_terms'],
                    'display_terms': pending_terms_classified['display_terms'],
                },
                'children': {
                    'semantic_terms': semantic_terms,
                    'semantic_term_items': semantic_term_items,
                    'sfx_terms': target_terms,
                    'missing_sfx_terms': pending_terms,
                    'covered_sfx_terms': covered_terms,
                    'direct_sfx_terms': target_terms_classified['direct_terms'],
                    'direct_sfx_term_items': direct_term_items,
                    'composite_sfx_terms': target_terms_classified['composite_terms'],
                    'composite_sfx_term_items': composite_term_items,
                    'display_sfx_terms': target_terms_classified['display_terms'],
                    'missing_direct_sfx_terms': pending_terms_classified['direct_terms'],
                    'missing_direct_sfx_term_items': missing_direct_term_items,
                    'missing_composite_sfx_terms': pending_terms_classified['composite_terms'],
                    'missing_composite_sfx_term_items': missing_composite_term_items,
                    'display_missing_sfx_terms': pending_terms_classified['display_terms'],
                },
                'progress': {
                    'target_count': len(target_terms),
                    'covered_count': len(covered_terms),
                    'pending_count': len(pending_terms),
                    'completion_ratio': completion_ratio,
                },
                'is_merged': is_merged,
                'status': effective_status,
                'notification_status': '已通知' if r.notified_at else '未通知',
                'asset_label': r.asset_label,
                'asset_file_path': r.asset_file_path,
                'merged_assets': merged_assets_with_display,
                'notified_at': r.notified_at.isoformat() if r.notified_at else '',
                'created_at': r.created_at.isoformat() if r.created_at else '',
            }
        )

    if status == 'merged':
        items = [item for item in items if item.get('is_merged')]
    elif status == 'ready_to_notify':
        items = [item for item in items if item.get('status') == 'ready_to_notify' and not item.get('notified_at')]
    elif status == 'notified':
        items = [item for item in items if item.get('notified_at')]

    if status:
        status_counter = {}
        notified_count = 0
        total_pending_terms = 0
        total_covered_terms = 0
        merged_count = 0
        unique_users = set()
        for item in items:
            key = str(item.get('status') or '').strip()
            status_counter[key] = status_counter.get(key, 0) + 1
            if item.get('notified_at'):
                notified_count += 1
            if item.get('is_merged'):
                merged_count += 1
            total_pending_terms += int(((item.get('progress') or {}).get('pending_count')) or 0)
            total_covered_terms += int(((item.get('progress') or {}).get('covered_count')) or 0)
            unique_users.add(item.get('user_phone') or '')

    return jsonify(
        {
            'days': days,
            'status': status,
            'count': len(items),
            'summary': {
                'item_count': len(items),
                'unique_user_count': len([x for x in unique_users if x]),
                'status_counter': status_counter,
                'pending_term_count': total_pending_terms,
                'covered_term_count': total_covered_terms,
                'ready_to_notify_count': sum(1 for item in items if item.get('status') == 'ready_to_notify' and not item.get('notified_at')),
                'notified_count': notified_count,
                'merged_count': merged_count,
            },
            'items': items,
        }
    )


@app.post(f'{settings.api_prefix}/ops/action-supplements/merge')
@_require_admin
def ops_action_supplements_merge():
    item_id = request.form.get('item_id', type=int)
    asset_label = (request.form.get('asset_label') or '').strip()
    asset_scope = (request.form.get('asset_scope') or '').strip().lower()
    notify_after_merge = str(request.form.get('notify_after_merge') or '').strip() in {'1', 'true', 'yes'}
    file = request.files.get('file')
    if not item_id:
        return jsonify({'detail': 'item_id is required'}), 400
    if not asset_label:
        return jsonify({'detail': 'asset_label is required'}), 400
    if asset_scope not in {'common', 'genre'}:
        return jsonify({'detail': 'asset_scope must be common or genre'}), 400
    if file is None or not getattr(file, 'filename', ''):
        return jsonify({'detail': 'file is required'}), 400

    with SessionLocal() as db:
        item = db.execute(select(ActionSupplementTask).where(ActionSupplementTask.id == item_id)).scalar_one_or_none()
        if not item:
            return jsonify({'detail': 'supplement item not found'}), 404

        ext = Path(file.filename).suffix or '.bin'
        safe_label = re.sub(r'[\\\\/:*?\"<>|]+', '_', asset_label).strip() or '未命名音效'
        out_path = Path('./assets/sfx').resolve() / f'{safe_label}{ext}'
        idx = 2
        while out_path.exists():
            out_path = Path('./assets/sfx').resolve() / f'{safe_label}_{idx}{ext}'
            idx += 1
        out_path.parent.mkdir(parents=True, exist_ok=True)
        file.save(out_path)

        current_children = classify_sfx_terms(json.loads(item.sfx_terms_json or '[]') if item.sfx_terms_json else [])
        target_genre = item.target_genre or item.genre or '玄幻'
        target_head = item.target_head or item.verb
        next_sfx_terms = _merge_unique_list(json.loads(item.sfx_terms_json or '[]') + [asset_label])
        draft_graph = {'common': {}, 'genres': {}}
        if asset_scope == 'common':
            draft_graph['common'][target_head] = {
                'semantic_terms': [],
                'sfx_terms': next_sfx_terms,
            }
        else:
            draft_graph['genres'][target_genre] = {
                target_head: {
                    'semantic_terms': json.loads(item.semantic_terms_json or '[]'),
                    'sfx_terms': next_sfx_terms,
                }
            }
        apply_action_graph_draft(
            {
                'genre': target_genre,
                'draft_count': 1,
                'draft_graph': draft_graph,
            }
        )
        neo4j_sync = {'ok': False, 'detail': 'neo4j sync skipped'}
        try:
            neo4j_sync = sync_action_graph_to_neo4j()
        except Exception as exc:
            neo4j_sync = {
                'ok': False,
                'detail': f'neo4j sync failed: {exc}',
            }
        sfx_mode = _sfx_mode_label(asset_label, {'composite_sfx_terms': current_children['composite_terms']})
        display_name = build_asset_variant_display_name(asset_label, asset_scope, target_genre)
        db.add(
            ActionSupplementAsset(
                supplement_id=item.id,
                asset_label=asset_label,
                asset_scope=asset_scope,
                asset_scope_genre=(target_genre if asset_scope == 'genre' else ''),
                asset_file_path=str(out_path),
            )
        )
        item.asset_label = asset_label
        item.asset_file_path = str(out_path)

        sfx_terms = [str(x).strip() for x in json.loads(item.sfx_terms_json or '[]') if str(x).strip()]
        node_key = build_action_node_key(str(item.target_genre or item.genre or '').strip(), str(item.target_head or item.verb or '').strip())
        node_coverage = load_action_node_coverage({node_key}).get(node_key) or {}
        global_label_coverage = load_global_sfx_label_coverage()
        existing_labels = set(str(x).strip() for x in (node_coverage.get('covered_labels') or []) if str(x).strip())
        existing_labels.update(str(x).strip() for x in (global_label_coverage.get('labels') or []) if str(x).strip())
        existing_labels.add(asset_label)
        target_terms = set(sfx_terms or [asset_label])
        covered = len(target_terms & existing_labels)
        fully_covered = covered >= len(target_terms)
        item.status = 'ready_to_notify' if fully_covered else 'partial'
        sibling_rows = db.execute(
            select(ActionSupplementTask).where(
                ActionSupplementTask.target_genre == (item.target_genre or item.genre or ''),
                ActionSupplementTask.target_head == (item.target_head or item.verb or ''),
            )
        ).scalars().all()
        for sibling in sibling_rows:
            try:
                sibling_terms = [str(x).strip() for x in json.loads(sibling.sfx_terms_json or '[]') if str(x).strip()]
            except json.JSONDecodeError:
                sibling_terms = []
            if not sibling_terms:
                continue
            sibling_covered = len(set(sibling_terms) & existing_labels)
            sibling.status = 'ready_to_notify' if sibling_covered >= len(set(sibling_terms)) else ('partial' if sibling_covered else 'pending')
        db.commit()

        _log_user_operation(
            db=db,
            action='action_supplement_merge',
            project_id=item.project_id,
            req={'item_id': item.id, 'asset_label': asset_label, 'asset_scope': asset_scope, 'notify_after_merge': notify_after_merge},
            resp={'ok': True, 'status': item.status, 'neo4j_sync_ok': bool((neo4j_sync or {}).get('ok'))},
            file_refs=[str(out_path)],
        )

        return jsonify(
            {
                'ok': True,
                'item_id': item.id,
                'status': item.status,
                'asset_label': asset_label,
                'asset_scope': asset_scope,
                'asset_scope_label': build_asset_scope_label(asset_scope, target_genre),
                'asset_display_name': display_name,
                'asset_mode': sfx_mode,
                'asset_file_path': str(out_path),
                'covered_term_count': covered,
                'target_term_count': len(target_terms),
                'neo4j_sync': neo4j_sync,
            }
        )


def _merge_unique_list(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        value = str(item).strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


@app.post(f'{settings.api_prefix}/ops/action-supplements/notify')
@_require_admin
def ops_action_supplements_notify():
    payload = request.get_json(force=True) or {}
    item_ids = payload.get('item_ids') or []
    if not isinstance(item_ids, list) or not item_ids:
        return jsonify({'detail': 'item_ids is required'}), 400

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rows = db.execute(select(ActionSupplementTask).where(ActionSupplementTask.id.in_(item_ids))).scalars().all()
        grouped: dict[str, list[ActionSupplementTask]] = {}
        for row in rows:
            row.notified_at = now
            grouped.setdefault(row.user_phone or '', []).append(row)
        db.commit()

        notifications = []
        for phone, items in grouped.items():
            payload_out = {
                'phone': phone,
                'project_ids': sorted({x.project_id for x in items if x.project_id}),
                'verbs': [x.verb for x in items],
                'asset_labels': [x.asset_label for x in items if x.asset_label],
                'message': '您之前提交的音效补充需求已完成补充，欢迎回到系统继续使用。',
            }
            _log_user_operation(
                db=db,
                action='action_supplement_notify',
                project_id=items[0].project_id if items else None,
                req=payload_out,
                resp={'queued': True, 'sms_sent': False},
                file_refs=[],
            )
            notifications.append(payload_out)

    return jsonify({'ok': True, 'notification_count': len(notifications), 'notifications': notifications, 'sms_sent': False})


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


@app.get(f'{settings.api_prefix}/ops/action-sfx-feedback')
@_require_admin
def ops_action_sfx_feedback():
    days = int((request.args.get('days') or '7').strip())
    days = max(1, min(90, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    by_verb: dict[str, int] = {}
    by_label: dict[str, int] = {}
    by_file: dict[str, int] = {}
    by_project: dict[str, int] = {}
    rows_out = []

    with SessionLocal() as db:
        rows = db.execute(
            select(
                UserOperationLog.project_id,
                UserOperationLog.user_phone,
                UserOperationLog.action,
                UserOperationLog.input_json,
                UserOperationLog.file_refs_json,
                UserOperationLog.created_at,
            )
            .where(
                UserOperationLog.created_at >= cutoff,
                UserOperationLog.action == 'action_sfx_asset_download',
            )
            .order_by(UserOperationLog.created_at.desc())
            .limit(300)
        ).all()

    for project_id, user_phone, action, input_json, file_refs_json, created_at in rows:
        try:
            payload = json.loads(input_json or '{}')
        except json.JSONDecodeError:
            payload = {}
        try:
            file_refs = json.loads(file_refs_json or '[]')
        except json.JSONDecodeError:
            file_refs = []

        verb = str(payload.get('verb') or '').strip()
        label = str(payload.get('label') or '').strip()
        file_name = str(payload.get('file_name') or '').strip()
        if not file_name and isinstance(file_refs, list) and file_refs:
            file_name = Path(str(file_refs[0])).name

        if verb:
            by_verb[verb] = by_verb.get(verb, 0) + 1
        if label:
            by_label[label] = by_label.get(label, 0) + 1
        if file_name:
            by_file[file_name] = by_file.get(file_name, 0) + 1
        if project_id is not None:
            key = str(project_id)
            by_project[key] = by_project.get(key, 0) + 1

        rows_out.append(
            {
                'project_id': project_id,
                'user_phone': user_phone,
                'action': action,
                'verb': verb,
                'label': label,
                'file_name': file_name,
                'created_at': created_at.isoformat() if created_at else '',
            }
        )

    top_verbs = [{'verb': k, 'count': v} for k, v in sorted(by_verb.items(), key=lambda x: x[1], reverse=True)[:20]]
    top_labels = [{'label': k, 'count': v} for k, v in sorted(by_label.items(), key=lambda x: x[1], reverse=True)[:20]]
    top_files = [{'file_name': k, 'count': v} for k, v in sorted(by_file.items(), key=lambda x: x[1], reverse=True)[:20]]
    top_projects = [{'project_id': k, 'count': v} for k, v in sorted(by_project.items(), key=lambda x: x[1], reverse=True)[:20]]

    return jsonify(
        {
            'days': days,
            'download_count': len(rows_out),
            'top_verbs': top_verbs,
            'top_labels': top_labels,
            'top_files': top_files,
            'top_projects': top_projects,
            'recent_downloads': rows_out,
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


@app.get(f'{settings.api_prefix}/sfx/file')
@_require_login
def download_sfx_file():
    raw_path = (request.args.get('path') or '').strip()
    project_id_raw = (request.args.get('project_id') or '').strip()
    verb = (request.args.get('verb') or '').strip()
    label = (request.args.get('label') or '').strip()
    if not raw_path:
        return jsonify({'detail': 'path is required'}), 400
    p = Path(raw_path).expanduser()
    abs_p = p.resolve()
    sfx_root = Path('./assets/sfx').resolve()
    try:
        abs_p.relative_to(sfx_root)
    except Exception:
        return jsonify({'detail': 'path must be under assets/sfx'}), 400
    if not abs_p.exists() or not abs_p.is_file():
        return jsonify({'detail': 'file not found'}), 404
    try:
        project_id = int(project_id_raw) if project_id_raw else None
    except ValueError:
        project_id = None
    with SessionLocal() as db:
        _log_user_operation(
            db=db,
            action='action_sfx_asset_download',
            project_id=project_id,
            req={
                'verb': verb,
                'label': label,
                'file_name': abs_p.name,
                'path': str(abs_p),
            },
            resp={'ok': True},
            file_refs=[str(abs_p)],
        )
    return send_file(abs_p, as_attachment=True, download_name=abs_p.name)


if __name__ == '__main__':
    Base.metadata.create_all(bind=engine)
    _ensure_schema_columns()
    app.run(host='0.0.0.0', port=8090, debug=False, use_reloader=False)
