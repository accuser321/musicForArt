import json
import hashlib
import base64
import hmac
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from time import perf_counter
import re
from urllib.parse import quote, urlparse, parse_qs, unquote
import random
import secrets
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_file, g
from flask_cors import CORS
from sqlalchemy import func, inspect, or_, select, text as sql_text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal, engine
from app.models import (
    ActionVerbAnalysis,
    ActionFallbackRiskTerm,
    ActionSupplementAsset,
    ActionGraphInheritanceReview,
    ActionSupplementTask,
    AudioAnalysis,
    AuthCode,
    AuthSession,
    Base,
    CopyrightBookAd,
    CreatorShowcase,
    DailyUsage,
    DraftReview,
    FusionPlan,
    InviteCode,
    LeaderboardOverride,
    MusicMatchResult,
    NarrationAnalysis,
    Project,
    RechargeOrder,
    RecruitmentNeed,
    SceneAnalysis,
    SceneSupplementAsset,
    SceneSupplementTask,
    SystemSetting,
    TextAnalysis,
    UserSfxSubmission,
    UserAccount,
    UserOperationLog,
)
from app.services.audio_analysis import analyze_audio_for_audiobook
from app.services.music_match import build_music_match_result
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
    resolve_action_fallback_head,
)
from app.services.scene_building import analyze_scene_building
from app.services.scene_graph_manage import (
    delete_scene_graph_node,
    delete_scene_collection,
    delete_scene_template,
    demote_scene_node_to_genre,
    get_scene_graph_node_layers,
    list_scene_collections,
    list_scene_graph_catalog,
    list_scene_term_suggestions,
    list_scene_templates,
    rename_scene_collection,
    resolve_scene_graph_node,
    promote_scene_node_to_common,
    update_scene_template,
    update_scene_graph_node_layer,
)
from app.services.scene_graph_neo4j import scene_graph_neo4j_status, sync_scene_graph_to_neo4j
from app.services.scene_sfx_graph import build_scene_sfx_recommendation
from app.services.action_graph_draft import apply_action_graph_draft, generate_action_graph_draft
from app.services.action_graph_neo4j import (
    action_graph_neo4j_status,
    list_action_graph_nodes,
    query_action_graph_node,
    sync_action_graph_to_neo4j,
)
from app.services.action_graph_manage import (
    _load_action_graph,
    apply_action_fallback_replacements,
    delete_action_graph_node,
    demote_action_graph_terms_to_genre,
    get_action_graph_node_layers,
    has_action_formal_head,
    list_action_fallback_replacement_rules,
    list_action_graph_inheritance_blocks,
    list_action_graph_maintenance_catalog,
    promote_action_graph_terms_to_common,
    remove_action_fallback_replacement_rule,
    remove_action_graph_overlap_terms,
    set_action_graph_inheritance_block,
    update_action_graph_node_layer,
)
from app.services.exporter import export_action_asset_xlsx, export_cue_csv
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

CREATOR_SHOWCASE_SUMMARY_MAX_CHARS = 120
PROJECT_TITLE_MAX_CHARS = 40
AUTH_CODE_DAILY_LIMIT = 10

SUPPORTED_GENRES = {'玄幻', '言情', '悬疑', '科幻'}
BETA_INVITE_ONLY_KEY = 'beta_invite_only_enabled'
INVITE_SEED_TARGET = 200
LEADERBOARD_LAYOUT_KEY = 'homepage_leaderboard_layout'
FRONTEND_DEBUG_EXPOSE_KEY = 'frontend_debug_expose_enabled'
HOME_LEADERBOARDS_VISIBLE_KEY = 'home_leaderboards_enabled'
ACTION_FALLBACK_RISK_SEEDED_KEY = 'action_fallback_risk_seeded'
REFERRAL_REWARD_SFX_PACK_KEY = 'referral_reward_sfx_pack'
REFERRAL_REWARD_TEXT_PACK_KEY = 'referral_reward_text_pack'
ACTION_SFX_THRESHOLD_KEY = 'action_sfx_effective_threshold'
LEADERBOARD_WINDOWS = {
    '1d': 1,
    '10d': 10,
    '30d': 30,
    '90d': 90,
}
LEADERBOARD_GENRES = ['玄幻', '言情', '科幻', '悬疑']
DEFAULT_ACTION_FALLBACK_RISK_TERMS = [
    ('走', 'danger', '单字泛词，极易误伤其他动作，默认高风险。'),
    ('看', 'danger', '单字泛词，容易跨大量上下文误归并。'),
    ('听', 'danger', '单字泛词，容易误伤非动作语义。'),
    ('说', 'danger', '单字泛词，容易误伤对白相关语义。'),
    ('道', 'danger', '常见口语尾词，容易和大量表达混淆。'),
    ('向', 'danger', '方向词，容易被短语误拆后误归并。'),
    ('来', 'danger', '单字泛词，建议谨慎处理。'),
    ('去', 'danger', '单字泛词，建议谨慎处理。'),
    ('上', 'danger', '方向词，语义过宽。'),
    ('下', 'danger', '方向词，语义过宽。'),
    ('进', 'danger', '单字泛词，跨语境误伤概率高。'),
    ('出', 'danger', '单字泛词，跨语境误伤概率高。'),
    ('拿', 'warn', '常见动作泛词，建议先观察。'),
    ('放', 'warn', '常见动作泛词，建议先观察。'),
    ('推', 'warn', '常见动作泛词，建议结合上下文判断。'),
    ('拉', 'warn', '常见动作泛词，建议结合上下文判断。'),
    ('打', 'warn', '常见动作泛词，语义跨度较大。'),
    ('撞', 'warn', '高频动作词，建议谨慎归并。'),
    ('叫', 'warn', '常见表达词，建议先观察。'),
    ('喊', 'warn', '常见表达词，建议先观察。'),
    ('望', 'warn', '单字词，容易与多种短语混淆。'),
    ('走去', 'warn', '高频泛短语，建议谨慎归并。'),
    ('走来', 'warn', '高频泛短语，建议谨慎归并。'),
    ('说道', 'warn', '口语尾词型短语，容易误收敛。'),
    ('来到', 'warn', '高频泛短语，建议谨慎归并。'),
    ('出去', 'warn', '高频泛短语，建议谨慎归并。'),
    ('进去', 'warn', '高频泛短语，建议谨慎归并。'),
    ('抬手', 'warn', '常见动作起手式，建议结合正式词判断。'),
    ('伸手', 'warn', '常见动作起手式，建议结合正式词判断。'),
]
AUTO_STANDARD_TERM_SUFFIXES = ('道',)
AUDIO_UPLOAD_MAX_MB = 80
ALLOWED_AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'}
ALLOWED_AUDIO_MIME_TYPES = {
    'audio/mpeg',
    'audio/mp3',
    'audio/wav',
    'audio/x-wav',
    'audio/wave',
    'audio/mp4',
    'audio/x-m4a',
    'audio/aac',
    'audio/flac',
    'audio/x-flac',
    'audio/ogg',
    'application/ogg',
}


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
    return '整体' if _is_composite_sfx_term(term, children) else '直达'


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

        action_task_cols = {col['name'] for col in inspect(engine).get_columns('action_supplement_task')}
        if 'task_scope' not in action_task_cols:
            conn.execute(sql_text("ALTER TABLE action_supplement_task ADD COLUMN task_scope VARCHAR(32) NOT NULL DEFAULT 'genre'"))
        if 'task_scope_genre' not in action_task_cols:
            conn.execute(sql_text("ALTER TABLE action_supplement_task ADD COLUMN task_scope_genre VARCHAR(32) NOT NULL DEFAULT ''"))

        user_cols = {col['name'] for col in inspect(engine).get_columns('user_account')}
        if 'uid' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN uid VARCHAR(64) NOT NULL DEFAULT ''"))
        if 'password_hash' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN password_hash VARCHAR(255) NOT NULL DEFAULT ''"))
        if 'ops_role_code' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN ops_role_code VARCHAR(2) NOT NULL DEFAULT '33'"))
        if 'user_tier_code' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN user_tier_code VARCHAR(2) NOT NULL DEFAULT '33'"))
        if 'daily_text_char_limit' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN daily_text_char_limit INTEGER NOT NULL DEFAULT 5000"))
        if 'daily_sfx_download_limit' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN daily_sfx_download_limit INTEGER NOT NULL DEFAULT 100"))
        if 'text_char_pack_balance' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN text_char_pack_balance INTEGER NOT NULL DEFAULT 0"))
        if 'sfx_download_pack_balance' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN sfx_download_pack_balance INTEGER NOT NULL DEFAULT 0"))
        if 'deposit_balance' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN deposit_balance FLOAT NOT NULL DEFAULT 0"))
        if 'invite_activated' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN invite_activated INTEGER NOT NULL DEFAULT 0"))
        if 'invite_code_used' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN invite_code_used VARCHAR(64) NOT NULL DEFAULT ''"))
        if 'referred_by_user_id' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN referred_by_user_id INTEGER"))
        if 'referred_by_phone' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN referred_by_phone VARCHAR(32) NOT NULL DEFAULT ''"))
        if 'referred_by_uid' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN referred_by_uid VARCHAR(64) NOT NULL DEFAULT ''"))
        if 'referral_input' not in user_cols:
            conn.execute(sql_text("ALTER TABLE user_account ADD COLUMN referral_input VARCHAR(64) NOT NULL DEFAULT ''"))
        uid_rows = conn.execute(sql_text("SELECT id, phone, uid FROM user_account")).fetchall() if user_cols else []
        for row_id, phone, uid in uid_rows:
            phone_value = str(phone or '').strip()
            uid_value = str(uid or '').strip()
            if not phone_value:
                continue
            if uid_value == f'auto_{phone_value}':
                conn.execute(
                    sql_text("UPDATE user_account SET uid = '' WHERE id = :id"),
                    {'id': row_id},
                )
        copyright_cols = {row[1] for row in conn.execute(sql_text("PRAGMA table_info(copyright_book_ad)")).fetchall()}
        if copyright_cols and 'user_phone' not in copyright_cols:
            conn.execute(sql_text("ALTER TABLE copyright_book_ad ADD COLUMN user_phone VARCHAR(32) NOT NULL DEFAULT ''"))
        sfx_submission_cols = {col['name'] for col in inspect(engine).get_columns('user_sfx_submission')}
        if 'adopted_file_name' not in sfx_submission_cols:
            conn.execute(sql_text("ALTER TABLE user_sfx_submission ADD COLUMN adopted_file_name VARCHAR(255) NOT NULL DEFAULT ''"))
        if 'adopted_file_path' not in sfx_submission_cols:
            conn.execute(sql_text("ALTER TABLE user_sfx_submission ADD COLUMN adopted_file_path VARCHAR(1024) NOT NULL DEFAULT ''"))
        if 'adopted_source_label' not in sfx_submission_cols:
            conn.execute(sql_text("ALTER TABLE user_sfx_submission ADD COLUMN adopted_source_label VARCHAR(64) NOT NULL DEFAULT ''"))
        if 'adopted_download_count' not in sfx_submission_cols:
            conn.execute(sql_text("ALTER TABLE user_sfx_submission ADD COLUMN adopted_download_count INTEGER NOT NULL DEFAULT 0"))
        # Backfill legacy blank uid values to stable, unique auto uid strings.
        user_rows = conn.execute(sql_text("SELECT id, phone, uid FROM user_account")).fetchall()
        for row in user_rows:
            row_id = int(row[0])
            phone = str(row[1] or '').strip()
            uid = str(row[2] or '').strip()
            if uid:
                continue
            auto_uid = f'auto_{phone or row_id}'
            conn.execute(
                sql_text("UPDATE user_account SET uid = :uid WHERE id = :id"),
                {'uid': auto_uid, 'id': row_id},
            )


def _generate_invite_code() -> str:
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(random.choice(alphabet) for _ in range(8))


def _ensure_system_setting(db, key: str, default_value: str) -> SystemSetting:
    row = db.execute(select(SystemSetting).where(SystemSetting.setting_key == key)).scalar_one_or_none()
    if row is None:
        row = SystemSetting(setting_key=key, setting_value=default_value)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _invite_only_enabled(db) -> bool:
    row = _ensure_system_setting(db, BETA_INVITE_ONLY_KEY, 'true')
    return str(row.setting_value or '').strip().lower() not in {'0', 'false', 'off', 'no'}


def _set_invite_only_enabled(db, enabled: bool) -> bool:
    row = _ensure_system_setting(db, BETA_INVITE_ONLY_KEY, 'true')
    row.setting_value = 'true' if enabled else 'false'
    db.commit()
    return enabled


def _frontend_debug_expose_enabled(db) -> bool:
    row = _ensure_system_setting(db, FRONTEND_DEBUG_EXPOSE_KEY, 'true' if settings.frontend_debug_expose_default else 'false')
    return str(row.setting_value or '').strip().lower() not in {'0', 'false', 'off', 'no'}


def _set_frontend_debug_expose_enabled(db, enabled: bool) -> bool:
    row = _ensure_system_setting(db, FRONTEND_DEBUG_EXPOSE_KEY, 'true' if settings.frontend_debug_expose_default else 'false')
    row.setting_value = 'true' if enabled else 'false'
    db.commit()
    return enabled


def _home_leaderboards_enabled(db) -> bool:
    row = _ensure_system_setting(db, HOME_LEADERBOARDS_VISIBLE_KEY, 'false')
    return str(row.setting_value or '').strip().lower() not in {'0', 'false', 'off', 'no'}


def _set_home_leaderboards_enabled(db, enabled: bool) -> bool:
    row = _ensure_system_setting(db, HOME_LEADERBOARDS_VISIBLE_KEY, 'false')
    row.setting_value = 'true' if enabled else 'false'
    db.commit()
    return enabled


def _get_int_system_setting(db, key: str, default_value: int, *, min_value: int = 0, max_value: int = 10_000_000) -> int:
    row = _ensure_system_setting(db, key, str(int(default_value)))
    try:
        value = int(str(row.setting_value or '').strip() or int(default_value))
    except (TypeError, ValueError):
        value = int(default_value)
    return max(min_value, min(max_value, value))


def _set_int_system_setting(db, key: str, value: int, *, min_value: int = 0, max_value: int = 10_000_000) -> int:
    actual = max(min_value, min(max_value, int(value)))
    row = _ensure_system_setting(db, key, str(actual))
    row.setting_value = str(actual)
    db.commit()
    return actual


def _get_float_system_setting(
    db,
    key: str,
    default_value: float,
    *,
    min_value: float = 0.0,
    max_value: float = 1.0,
) -> float:
    row = _ensure_system_setting(db, key, str(float(default_value)))
    try:
        value = float(str(row.setting_value or '').strip() or float(default_value))
    except (TypeError, ValueError):
        value = float(default_value)
    return max(min_value, min(max_value, value))


def _set_float_system_setting(
    db,
    key: str,
    value: float,
    *,
    min_value: float = 0.0,
    max_value: float = 1.0,
    precision: int = 4,
) -> float:
    actual = max(min_value, min(max_value, float(value)))
    actual = round(actual, precision)
    row = _ensure_system_setting(db, key, str(actual))
    row.setting_value = str(actual)
    db.commit()
    return actual


def _action_sfx_effective_threshold(db) -> float:
    return _get_float_system_setting(db, ACTION_SFX_THRESHOLD_KEY, 0.18, min_value=0.0, max_value=1.0)


def _strip_user_visible_sfx_suffix(name: str) -> str:
    value = str(name or '').strip()
    if not value:
        return ''
    return re.sub(r'（(?:整体|组合|直达)-[^）]+）$', '', value).strip()


def _build_user_visible_sfx_download_name(
    *,
    abs_path: Path,
    display_name: str = '',
    adopted_submission: UserSfxSubmission | None = None,
) -> str:
    ext = abs_path.suffix or '.mp3'
    base_name = _strip_user_visible_sfx_suffix(display_name)
    if not base_name:
        stem = str(abs_path.stem or '').strip()
        if '__' in stem:
            parts = [str(x).strip() for x in stem.split('__') if str(x).strip()]
            if len(parts) >= 4 and parts[-1].startswith('userbetter_'):
                base_name = _strip_user_visible_sfx_suffix(parts[2])
            else:
                base_name = _strip_user_visible_sfx_suffix(parts[0] if parts else stem)
        else:
            base_name = _strip_user_visible_sfx_suffix(stem)
    if adopted_submission is not None and base_name:
        base_name = f'{base_name}（由用户更优推荐）'
    safe_name = str(base_name or abs_path.stem or 'sfx').strip()
    if not safe_name.lower().endswith(ext.lower()):
        safe_name = f'{safe_name}{ext}'
    return safe_name


def _filter_action_sfx_result_by_threshold(result: dict, threshold: float) -> tuple[dict, int]:
    payload = dict(result or {})
    graph_items = []
    filtered_out_count = 0
    for item in list(payload.get('graph_items') or []):
        row = dict(item or {})
        assets = []
        for asset in list(row.get('assets') or []):
            try:
                score = float((asset or {}).get('score', 0.0) or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if score >= threshold:
                assets.append(asset)
            else:
                filtered_out_count += 1
        row['assets'] = assets
        child = dict(row.get('children') or {})
        all_sfx_terms = [str(x).strip() for x in (child.get('sfx_terms') or row.get('sfx_terms') or []) if str(x).strip()]
        retained_labels = {
            str((asset or {}).get('label') or '').strip()
            for asset in assets
            if str((asset or {}).get('label') or '').strip()
        }
        covered_sfx_terms = [term for term in all_sfx_terms if term in retained_labels]
        missing_sfx_terms = [term for term in all_sfx_terms if term not in retained_labels]
        covered_classified = classify_sfx_terms(covered_sfx_terms)
        missing_classified = classify_sfx_terms(missing_sfx_terms)
        child['covered_sfx_terms'] = covered_sfx_terms[:10]
        child['missing_sfx_terms'] = missing_sfx_terms[:10]
        child['missing_direct_sfx_terms'] = missing_classified['direct_terms'][:10]
        child['missing_composite_sfx_terms'] = missing_classified['composite_terms'][:10]
        child['display_missing_sfx_terms'] = missing_classified['display_terms'][:10]
        row['children'] = child
        row['missing_sfx_terms'] = missing_sfx_terms[:10]
        row['missing_sfx_terms_classified'] = {
            'direct_terms': missing_classified['direct_terms'][:10],
            'composite_terms': missing_classified['composite_terms'][:10],
            'display_terms': missing_classified['display_terms'][:10],
        }
        row['covered_sfx_terms_classified'] = {
            'direct_terms': covered_classified['direct_terms'][:10],
            'composite_terms': covered_classified['composite_terms'][:10],
            'display_terms': covered_classified['display_terms'][:10],
        }
        graph_items.append(row)
    payload['graph_items'] = graph_items
    summary = dict(payload.get('summary') or {})
    summary['asset_count'] = sum(len((item or {}).get('assets') or []) for item in graph_items)
    summary['covered_term_count'] = sum(len(((item or {}).get('children') or {}).get('covered_sfx_terms') or []) for item in graph_items)
    summary['effective_threshold'] = round(float(threshold), 4)
    summary['filtered_asset_count'] = int(filtered_out_count)
    payload['summary'] = summary
    return payload, filtered_out_count


def _build_action_sfx_threshold_recommendation(db, *, days: int) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    effective_threshold = _action_sfx_effective_threshold(db)
    search_rows = db.execute(
        select(ReasoningLog.output_json)
        .where(ReasoningLog.event_type == 'search_sfx')
        .where(ReasoningLog.created_at >= cutoff)
    ).all()

    total_searches = 0
    zero_match_count = 0
    weak_match_count = 0
    top_scores: list[float] = []
    for (output_json,) in search_rows:
        try:
            payload = json.loads(output_json)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        matches = payload.get('matches') or []
        total_searches += 1
        if not matches:
            zero_match_count += 1
            continue
        try:
            top_score = float(((matches or [])[0] or {}).get('score', 0.0) or 0.0)
        except (TypeError, ValueError):
            top_score = 0.0
        top_scores.append(top_score)
        if top_score < effective_threshold:
            weak_match_count += 1

    official_assets = [item for item in load_sfx_library() if str(item.source_pool or '') == 'system']
    user_better_assets = [item for item in load_sfx_library() if str(item.source_pool or '') == 'user_better']
    adopted_rows = db.execute(
        select(UserSfxSubmission.reward_applied, UserSfxSubmission.adopted_download_count)
    ).all()
    adopted_submission_count = 0
    adopted_download_total = 0
    for reward_applied, adopted_download_count in adopted_rows:
        if int(reward_applied or 0) != 1:
            continue
        adopted_submission_count += 1
        adopted_download_total += int(adopted_download_count or 0)

    zero_ratio = (zero_match_count / total_searches) if total_searches else 0.0
    weak_ratio = (weak_match_count / total_searches) if total_searches else 0.0
    avg_top_score = (sum(top_scores) / len(top_scores)) if top_scores else 0.0

    suggested = 0.18
    reasons = []

    if weak_ratio >= 0.45:
        suggested += 0.04
        reasons.append('最近周期内低分命中占比偏高，建议抬高阈值，减少“勉强命中”的素材展示。')
    elif weak_ratio >= 0.25:
        suggested += 0.02
        reasons.append('最近周期内存在较多低分命中，建议适度抬高阈值。')
    else:
        reasons.append('低分命中占比可控，当前阈值不需要明显抬高。')

    if zero_ratio >= 0.45:
        suggested -= 0.02
        reasons.append('完全无命中的比例偏高，说明素材覆盖仍有缺口，不宜把阈值抬得过高。')
    elif zero_ratio >= 0.25:
        suggested -= 0.01
        reasons.append('存在一定无命中情况，阈值建议保守调整。')
    else:
        reasons.append('完全无命中的比例较低，阈值有条件维持稍严格。')

    if adopted_submission_count >= 3:
        suggested += 0.01
        reasons.append('已采纳的“由用户更优推荐”素材逐步增加，系统有条件对展示结果更严格。')
    if adopted_download_total >= 10:
        suggested += 0.01
        reasons.append('用户更优推荐素材已有真实下载表现，可适度提高阈值以强化优质结果。')
    if len(official_assets) < 50:
        suggested -= 0.01
        reasons.append('系统官方素材总量仍偏少，为避免过度过滤，建议稍微下调阈值。')

    suggested = round(max(0.12, min(0.35, suggested)), 4)
    if not reasons:
        reasons = ['当前样本量有限，先沿用基础阈值建议。']

    return {
        'effective_threshold': round(float(effective_threshold), 4),
        'suggested_threshold': suggested,
        'evaluation_period_days': int(days),
        'dimensions': {
            'search_sample_count': int(total_searches),
            'zero_match_count': int(zero_match_count),
            'zero_match_ratio': round(float(zero_ratio), 4),
            'weak_match_count': int(weak_match_count),
            'weak_match_ratio': round(float(weak_ratio), 4),
            'avg_top_score': round(float(avg_top_score), 4),
            'official_asset_count': int(len(official_assets)),
            'user_better_asset_count': int(len(user_better_assets)),
            'adopted_submission_count': int(adopted_submission_count),
            'adopted_download_total': int(adopted_download_total),
        },
        'reasons': reasons[:8],
    }


def _referral_reward_settings(db) -> dict:
    return {
        'sfx_download_pack_reward': _get_int_system_setting(db, REFERRAL_REWARD_SFX_PACK_KEY, 15, min_value=0, max_value=100000),
        'text_char_pack_reward': _get_int_system_setting(db, REFERRAL_REWARD_TEXT_PACK_KEY, 5000, min_value=0, max_value=500000),
    }


def _ensure_action_fallback_risk_terms(db) -> None:
    seeded = _ensure_system_setting(db, ACTION_FALLBACK_RISK_SEEDED_KEY, 'false')
    if str(seeded.setting_value or '').strip().lower() in {'1', 'true', 'yes', 'on'}:
        return
    existing = {
        str(row.term or '').strip(): row
        for row in db.execute(select(ActionFallbackRiskTerm)).scalars().all()
        if str(row.term or '').strip()
    }
    changed = False
    for term, level, note in DEFAULT_ACTION_FALLBACK_RISK_TERMS:
        if term in existing:
            continue
        db.add(
            ActionFallbackRiskTerm(
                term=term,
                risk_level=level,
                note=note,
                enabled=1,
            )
        )
        changed = True
    seeded.setting_value = 'true'
    db.commit()


def _list_action_fallback_risk_terms(db) -> list[dict]:
    rows = db.execute(
        select(ActionFallbackRiskTerm).order_by(ActionFallbackRiskTerm.risk_level.desc(), ActionFallbackRiskTerm.term.asc())
    ).scalars().all()
    return [
        {
            'id': row.id,
            'term': str(row.term or '').strip(),
            'risk_level': str(row.risk_level or 'warn').strip() or 'warn',
            'note': str(row.note or '').strip(),
            'enabled': bool(int(row.enabled or 0)),
        }
        for row in rows
    ]


def _merge_unique_terms(items: list[str] | None) -> list[str]:
    seen = set()
    out = []
    for item in items or []:
        value = str(item or '').strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _fallback_risk_lookup(risk_terms: list[dict] | None = None) -> dict[str, str]:
    lookup = {
        str(term or '').strip(): str(level or 'warn').strip() or 'warn'
        for term, level, _note in DEFAULT_ACTION_FALLBACK_RISK_TERMS
        if str(term or '').strip()
    }
    for item in risk_terms or []:
        term = str((item or {}).get('term') or '').strip()
        if not term or not bool((item or {}).get('enabled', True)):
            continue
        lookup[term] = str((item or {}).get('risk_level') or 'warn').strip() or 'warn'
    return lookup


def _is_high_risk_fallback_term(term: str, risk_lookup: dict[str, str] | None = None) -> bool:
    value = str(term or '').strip()
    if not value:
        return False
    lookup = risk_lookup or {}
    level = str(lookup.get(value) or '').strip()
    if level in {'danger', 'warn'}:
        return True
    return len(value) == 1


def _normalized_fallback_term_candidates(term: str) -> list[str]:
    value = str(term or '').strip()
    if not value:
        return []
    out = [value]
    for suffix in AUTO_STANDARD_TERM_SUFFIXES:
        if value.endswith(suffix):
            candidate = value[: -len(suffix)].strip()
            if len(candidate) >= 2 and candidate not in out:
                out.append(candidate)
    return out


def _suggest_action_fallback_source_term(
    fallback_terms: list[str] | None,
    raw_verbs: list[str] | None = None,
    risk_terms: list[dict] | None = None,
) -> str:
    source_terms = _merge_unique_terms([*(fallback_terms or []), *(raw_verbs or [])])
    if not source_terms:
        return ''
    risk_lookup = _fallback_risk_lookup(risk_terms)
    candidates = _merge_unique_terms(
        candidate
        for term in source_terms
        for candidate in _normalized_fallback_term_candidates(term)
    )

    def score(candidate: str) -> tuple[int, int, str]:
        value = str(candidate or '').strip()
        score_value = 0
        if value in (fallback_terms or []):
            score_value += 120
        if value in (raw_verbs or []):
            score_value += 160
        if len(value) >= 2:
            score_value += 260
        else:
            score_value -= 180
        if _is_high_risk_fallback_term(value, risk_lookup):
            score_value -= 120
        else:
            score_value += 90
        for source in source_terms:
            if source == value:
                continue
            if value in _normalized_fallback_term_candidates(source):
                score_value += 180
            elif source.endswith(value) and len(source) > len(value):
                score_value += 60
        score_value += min(len(value), 8) * 8
        return score_value, len(value), value

    ranked = sorted(candidates, key=score, reverse=True)
    return str(ranked[0] or '').strip() or max(source_terms, key=lambda x: (len(x), x))


def _suggest_action_fallback_replacement_terms(
    fallback_terms: list[str] | None,
    raw_verbs: list[str] | None,
    source_term: str,
    risk_terms: list[dict] | None = None,
) -> tuple[list[str], list[str]]:
    source = str(source_term or '').strip()
    risk_lookup = _fallback_risk_lookup(risk_terms)
    source_terms = _merge_unique_terms([*(fallback_terms or []), *(raw_verbs or [])])
    replacements = []
    filtered_out = []
    for term in source_terms:
        value = str(term or '').strip()
        if not value or value == source:
            continue
        if _is_high_risk_fallback_term(value, risk_lookup):
            filtered_out.append(value)
            continue
        replacements.append(value)
    for candidate in _normalized_fallback_term_candidates(source):
        if candidate and candidate != source and candidate not in replacements:
            if _is_high_risk_fallback_term(candidate, risk_lookup):
                if candidate not in filtered_out:
                    filtered_out.append(candidate)
            else:
                replacements.append(candidate)
    return _merge_unique_terms(replacements), _merge_unique_terms(filtered_out)


def _ensure_invite_seed_codes(db, target: int = INVITE_SEED_TARGET) -> None:
    total = int(db.execute(select(func.count()).select_from(InviteCode)).scalar_one() or 0)
    if total >= target:
        _ensure_system_setting(db, BETA_INVITE_ONLY_KEY, 'true')
        return
    existing = {row.code for row in db.execute(select(InviteCode.code)).all()}
    to_add = target - total
    batch = []
    while len(batch) < to_add:
        code = _generate_invite_code()
        if code in existing:
            continue
        existing.add(code)
        batch.append(InviteCode(code=code, used=0, used_by_phone=''))
    if batch:
        db.add_all(batch)
    _ensure_system_setting(db, BETA_INVITE_ONLY_KEY, 'true')
    db.commit()


def _default_leaderboard_layout() -> dict:
    action_order = ['overall', 'common'] + [f'genre_{genre}' for genre in LEADERBOARD_GENRES]
    scene_order = ['overall', 'common'] + [f'genre_{genre}' for genre in LEADERBOARD_GENRES]
    return {
        'domain_order': ['action', 'scene'],
        'domain_titles': {
            'action': '动作音效热度榜',
            'scene': '场景搭建热度榜',
        },
        'board_orders': {
            'action': action_order,
            'scene': scene_order,
        },
    }


def _load_leaderboard_layout(db) -> dict:
    row = _ensure_system_setting(db, LEADERBOARD_LAYOUT_KEY, json.dumps(_default_leaderboard_layout(), ensure_ascii=False))
    try:
        data = json.loads(row.setting_value or '{}')
    except json.JSONDecodeError:
        data = {}
    default = _default_leaderboard_layout()
    merged = {
        'domain_order': data.get('domain_order') if isinstance(data.get('domain_order'), list) else default['domain_order'],
        'domain_titles': {
            **default['domain_titles'],
            **(data.get('domain_titles') if isinstance(data.get('domain_titles'), dict) else {}),
        },
        'board_orders': {
            'action': (
                data.get('board_orders', {}).get('action')
                if isinstance(data.get('board_orders'), dict) and isinstance(data.get('board_orders', {}).get('action'), list)
                else default['board_orders']['action']
            ),
            'scene': (
                data.get('board_orders', {}).get('scene')
                if isinstance(data.get('board_orders'), dict) and isinstance(data.get('board_orders', {}).get('scene'), list)
                else default['board_orders']['scene']
            ),
        },
    }
    return merged


def _save_leaderboard_layout(db, payload: dict | None) -> dict:
    payload = payload or {}
    current = _load_leaderboard_layout(db)
    domain_order = [x for x in (payload.get('domain_order') or current['domain_order']) if x in {'action', 'scene'}]
    if set(domain_order) != {'action', 'scene'}:
        domain_order = current['domain_order']
    domain_titles = {
        'action': str((payload.get('domain_titles') or {}).get('action') or current['domain_titles']['action']).strip() or current['domain_titles']['action'],
        'scene': str((payload.get('domain_titles') or {}).get('scene') or current['domain_titles']['scene']).strip() or current['domain_titles']['scene'],
    }
    board_orders = {}
    for domain in ('action', 'scene'):
        default_keys = [item['board_key'] for item in _leaderboard_board_defs(domain)]
        incoming = []
        raw_orders = payload.get('board_orders') if isinstance(payload.get('board_orders'), dict) else {}
        if isinstance(raw_orders.get(domain), list):
            incoming = [str(x).strip() for x in raw_orders.get(domain) if str(x).strip()]
        seen = set()
        order = []
        for key in incoming + current['board_orders'].get(domain, []) + default_keys:
            value = str(key).strip()
            if value and value in default_keys and value not in seen:
                seen.add(value)
                order.append(value)
        board_orders[domain] = order
    row = _ensure_system_setting(db, LEADERBOARD_LAYOUT_KEY, '')
    row.setting_value = json.dumps(
        {
            'domain_order': domain_order,
            'domain_titles': domain_titles,
            'board_orders': board_orders,
        },
        ensure_ascii=False,
    )
    db.commit()
    return _load_leaderboard_layout(db)


def _download_token_secret() -> bytes:
    raw = str(settings.download_signing_key or '').strip() or str(settings.llm_api_key or '').strip() or 'music-for-art-download-token'
    return raw.encode('utf-8')


def _make_download_token(path: str, *, phone: str = '', ttl_sec: int | None = None) -> str:
    ttl = int(ttl_sec or settings.download_token_ttl_sec or 300)
    exp = int(datetime.now(timezone.utc).timestamp()) + max(30, ttl)
    payload = {
        'path': str(path or '').strip(),
        'phone': str(phone or '').strip(),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).decode('ascii').rstrip('=')
    message = f'{body}.{exp}'
    sig = hmac.new(_download_token_secret(), message.encode('utf-8'), hashlib.sha256).hexdigest()
    return f'{body}.{exp}.{sig}'


def _parse_download_token(token: str) -> dict | None:
    raw = str(token or '').strip()
    if not raw or raw.count('.') < 2:
        return None
    body, exp_raw, sig = raw.rsplit('.', 2)
    try:
        exp = int(exp_raw)
    except ValueError:
        return None
    expected = hmac.new(_download_token_secret(), f'{body}.{exp}'.encode('utf-8'), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    if exp < int(datetime.now(timezone.utc).timestamp()):
        return None
    padded = body + '=' * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8'))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _tokenized_download_api_if_needed(download_api: str, *, phone: str = '') -> str:
    url = str(download_api or '').strip()
    if not url or not settings.require_signed_downloads:
        return url
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    raw_path = qs.get('path', [''])[0]
    path = unquote(str(raw_path or '').strip())
    if not path:
        return url
    token = _make_download_token(path, phone=phone)
    return f"{settings.api_prefix}/sfx/file?token={quote(token, safe='')}"


def _sanitize_user_payload(value, *, debug_enabled: bool, phone: str = ''):
    if isinstance(value, list):
        return [_sanitize_user_payload(item, debug_enabled=debug_enabled, phone=phone) for item in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key in {'file_path', 'asset_file_path', '_source_path'}:
            continue
        if not debug_enabled and key in {'llm_trace', 'debug', 'raw_response'}:
            continue
        if not debug_enabled and key in {'prompt_file', 'effective_prompt_file'}:
            continue
        if key == 'download_api' and isinstance(item, str):
            out[key] = _tokenized_download_api_if_needed(item, phone=phone)
            continue
        out[key] = _sanitize_user_payload(item, debug_enabled=debug_enabled, phone=phone)
    return out


def _user_json_response(payload: dict):
    with SessionLocal() as db:
        debug_enabled = _frontend_debug_expose_enabled(db)
    phone = getattr(g, 'current_user_phone', '') or ''
    sanitized = _sanitize_user_payload(payload, debug_enabled=debug_enabled, phone=phone)
    if isinstance(sanitized, dict):
        sanitized['frontend_debug_expose_enabled'] = bool(debug_enabled)
    return jsonify(sanitized)


def _leaderboard_window(days_raw: str | None = None, window_key_raw: str | None = None) -> tuple[str, int]:
    key = str(window_key_raw or '').strip()
    if key in LEADERBOARD_WINDOWS:
        return key, LEADERBOARD_WINDOWS[key]
    try:
        days = int(str(days_raw or '').strip() or '10')
    except ValueError:
        days = 10
    for window_key, window_days in LEADERBOARD_WINDOWS.items():
        if days == window_days:
            return window_key, window_days
    if days <= 1:
        return '1d', 1
    if days <= 10:
        return '10d', 10
    if days <= 30:
        return '30d', 30
    return '90d', 90


def _leaderboard_board_defs(domain: str) -> list[dict]:
    scope_name = '动作音效' if domain == 'action' else '场景音效'
    board_defs = [
        {'board_key': 'overall', 'title': f'下载最多的{scope_name} Top5', 'scope_label': ''},
        {'board_key': 'common', 'title': '下载最多的通用音效 Top5', 'scope_label': '通用'},
    ]
    board_defs.extend(
        {'board_key': f'genre_{genre}', 'title': f'下载最多的{genre}赛道音效 Top5', 'scope_label': genre}
        for genre in LEADERBOARD_GENRES
    )
    return board_defs


def _leaderboard_source_action(domain: str) -> str:
    return 'scene_sfx_asset_download' if domain == 'scene' else 'action_sfx_asset_download'


def _load_leaderboard_overrides(db, domain: str, window_key: str) -> list[LeaderboardOverride]:
    return db.execute(
        select(LeaderboardOverride)
        .where(
            LeaderboardOverride.domain == domain,
            LeaderboardOverride.window_key == window_key,
        )
    ).scalars().all()


def _build_leaderboard_rows(db, domain: str, days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
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
            UserOperationLog.action.in_(['action_sfx_asset_download', 'scene_sfx_asset_download']),
        )
        .order_by(UserOperationLog.created_at.desc())
        .limit(3000)
    ).all()
    out = []
    for project_id, user_phone, action, input_json, file_refs_json, created_at in rows:
        try:
            payload = json.loads(input_json or '{}')
        except json.JSONDecodeError:
            payload = {}
        try:
            file_refs = json.loads(file_refs_json or '[]')
        except json.JSONDecodeError:
            file_refs = []
        source_domain = str(payload.get('source_domain') or '').strip()
        if not source_domain:
            source_domain = 'scene' if str(action or '').strip() == 'scene_sfx_asset_download' else 'action'
        if source_domain != domain:
            continue
        file_name = str(payload.get('file_name') or '').strip()
        if not file_name and isinstance(file_refs, list) and file_refs:
            file_name = Path(str(file_refs[0])).name
        display_name = str(payload.get('display_name') or payload.get('label') or file_name).strip()
        if not display_name:
            continue
        source_name = str(payload.get('scene_name') or payload.get('verb') or '').strip()
        scope_label = str(payload.get('scope_label') or '').strip()
        genre = str(payload.get('genre') or '').strip()
        item_key = str(payload.get('item_key') or display_name or file_name).strip()
        out.append(
            {
                'project_id': project_id,
                'user_phone': user_phone,
                'created_at': created_at.isoformat() if created_at else '',
                'item_key': item_key,
                'display_name': display_name,
                'subtitle': source_name,
                'scope_label': scope_label,
                'genre': genre,
                'file_name': file_name,
                'action': action,
            }
        )
    return out


def _board_matches(board_key: str, row: dict) -> bool:
    if board_key == 'overall':
        return True
    scope_label = str(row.get('scope_label') or '').strip()
    if board_key == 'common':
        return scope_label == '通用'
    if board_key.startswith('genre_'):
        return scope_label == board_key.replace('genre_', '', 1)
    return False


def _apply_leaderboard_overrides(items: list[dict], overrides: list[LeaderboardOverride], board_key: str) -> list[dict]:
    mapped = {str(item.get('item_key') or ''): {**item} for item in items}
    hidden = set()
    for row in overrides:
        if str(row.board_key or '') != board_key:
            continue
        key = str(row.item_key or '').strip()
        if not key:
            continue
        if not int(row.enabled or 0):
            hidden.add(key)
            continue
        current = mapped.get(
            key,
            {
                'item_key': key,
                'display_name': key,
                'subtitle': '',
                'raw_count': 0,
                'count': 0,
                'scope_label': '',
                'genre': '',
                'has_override': True,
            },
        )
        current['has_override'] = True
        current['display_name'] = str(row.display_name or current.get('display_name') or key).strip() or key
        current['subtitle'] = str(row.subtitle or current.get('subtitle') or '').strip()
        current['count'] = int(row.override_count) if row.override_count is not None else int(current.get('count') or 0)
        current['manual_rank'] = int(row.manual_rank) if row.manual_rank is not None else None
        try:
            meta = json.loads(row.meta_json or '{}')
        except json.JSONDecodeError:
            meta = {}
        if isinstance(meta, dict):
            for key_name in ('scope_label', 'genre', 'file_name'):
                if meta.get(key_name):
                    current[key_name] = meta.get(key_name)
        mapped[key] = current
    result = [item for key, item in mapped.items() if key not in hidden]
    manual_items = [item for item in result if item.get('manual_rank') is not None]
    auto_items = [item for item in result if item.get('manual_rank') is None]
    manual_items.sort(key=lambda item: (int(item.get('manual_rank') or 0), -int(item.get('count') or 0), str(item.get('display_name') or '')))
    auto_items.sort(key=lambda item: (-int(item.get('count') or 0), str(item.get('display_name') or '')))
    return manual_items + auto_items


def _build_leaderboard_domain_payload(db, domain: str, days: int, window_key: str) -> dict:
    rows = _build_leaderboard_rows(db, domain, days)
    board_defs = _leaderboard_board_defs(domain)
    overrides = _load_leaderboard_overrides(db, domain, window_key)
    boards = []
    for board in board_defs:
        counter: dict[str, dict] = {}
        for row in rows:
            if not _board_matches(board['board_key'], row):
                continue
            key = str(row.get('item_key') or '').strip()
            if not key:
                continue
            bucket = counter.setdefault(
                key,
                {
                    'item_key': key,
                    'display_name': row.get('display_name') or key,
                    'subtitle': row.get('subtitle') or '',
                    'scope_label': row.get('scope_label') or '',
                    'genre': row.get('genre') or '',
                    'file_name': row.get('file_name') or '',
                    'count': 0,
                    'raw_count': 0,
                    'source_counter': {},
                    'has_override': False,
                },
            )
            bucket['count'] += 1
            bucket['raw_count'] += 1
            subtitle = str(row.get('subtitle') or '').strip()
            if subtitle:
                bucket['source_counter'][subtitle] = bucket['source_counter'].get(subtitle, 0) + 1
        items = []
        for item in counter.values():
            if item.get('source_counter'):
                top_source = sorted(item['source_counter'].items(), key=lambda x: (-x[1], x[0]))[0][0]
                item['subtitle'] = top_source
            item.pop('source_counter', None)
            items.append(item)
        items.sort(key=lambda item: (-int(item.get('count') or 0), str(item.get('display_name') or '')))
        items = _apply_leaderboard_overrides(items, overrides, board['board_key'])[:5]
        boards.append(
            {
                **board,
                'items': items,
                'download_count': sum(int(item.get('raw_count') or 0) for item in items),
            }
        )
    layout = _load_leaderboard_layout(db)
    board_order = layout.get('board_orders', {}).get(domain, [])
    boards.sort(key=lambda item: board_order.index(item['board_key']) if item['board_key'] in board_order else 999)
    return {
        'domain': domain,
        'title': layout.get('domain_titles', {}).get(domain) or ('动作音效热度榜' if domain == 'action' else '场景搭建热度榜'),
        'boards': boards,
        'download_count': len(rows),
    }

def _normalize_uid(raw: str) -> str:
    value = re.sub(r'[^0-9]', '', str(raw or '').strip())
    return value[:32]


def _is_valid_uid(uid: str) -> bool:
    value = _normalize_uid(uid)
    return bool(value) and len(value) >= 3


def _is_valid_password(raw: str) -> bool:
    return len(str(raw or '')) >= 6


def _hash_password(raw: str) -> str:
    password = str(raw or '')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 120000).hex()
    return f'pbkdf2_sha256${salt}${digest}'


def _verify_password(raw: str, encoded: str) -> bool:
    value = str(encoded or '').strip()
    password = str(raw or '')
    if not value or not password:
        return False
    try:
        algo, salt, digest = value.split('$', 2)
    except ValueError:
        return False
    if algo != 'pbkdf2_sha256':
        return False
    actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 120000).hex()
    return hmac.compare_digest(actual, digest)


def _issue_auth_session(db, phone: str) -> str:
    token = secrets.token_urlsafe(32)
    s = AuthSession(
        phone=phone,
        token=token,
        expires_at=_utc_now() + timedelta(seconds=settings.auth_session_ttl_sec),
    )
    db.add(s)
    return token


def _apply_activation_and_referral_rules(db, u: UserAccount, *, phone: str, invite_code: str, referral_code: str) -> tuple[dict | None, int | None]:
    beta_invite_only = _invite_only_enabled(db)
    is_first_activation = int(getattr(u, 'invite_activated', 0) or 0) != 1
    if beta_invite_only and is_first_activation and int(u.is_admin or 0) != 1:
        if not invite_code:
            return {'detail': '测试期需要邀请码激活后才能使用系统'}, 403
        invite_row = db.execute(
            select(InviteCode).where(InviteCode.code == invite_code, InviteCode.used == 0)
        ).scalar_one_or_none()
        if invite_row is None:
            return {'detail': '邀请码无效或已失效'}, 403
        invite_row.used = 1
        invite_row.used_by_phone = phone
        invite_row.used_at = _utc_now()
        u.invite_activated = 1
        u.invite_code_used = invite_code
        if _user_tier_code(u) == '33':
            u.user_tier_code = '22'
    elif is_first_activation:
        u.invite_activated = 1
        if _user_tier_code(u) not in {'22', '33'}:
            u.user_tier_code = '33'

    text_limit, sfx_limit = _default_limits_for_tier(_user_tier_code(u))
    if int(getattr(u, 'daily_text_char_limit', 0) or 0) <= 0:
        u.daily_text_char_limit = text_limit
    if int(getattr(u, 'daily_sfx_download_limit', 0) or 0) <= 0:
        u.daily_sfx_download_limit = sfx_limit

    if is_first_activation and referral_code and not int(u.referred_by_user_id or 0):
        referrer = _resolve_referrer(db, referral_code, phone)
        if referrer is None:
            return {'detail': '推荐码无效，请输入有效的 UID'}, 400
        u.referred_by_user_id = int(referrer.id)
        u.referred_by_phone = str(referrer.phone or '')
        u.referred_by_uid = str(referrer.uid or '')
        u.referral_input = referral_code
        reward_settings = _referral_reward_settings(db)
        reward_sfx = int(reward_settings.get('sfx_download_pack_reward') or 0)
        reward_text = int(reward_settings.get('text_char_pack_reward') or 0)
        if reward_sfx > 0:
            referrer.sfx_download_pack_balance = int(getattr(referrer, 'sfx_download_pack_balance', 0) or 0) + reward_sfx
        if reward_text > 0:
            referrer.text_char_pack_balance = int(getattr(referrer, 'text_char_pack_balance', 0) or 0) + reward_text
        db.add(
            UserOperationLog(
                project_id=None,
                user_phone=str(referrer.phone or ''),
                action='referral_reward_issued',
                input_json=json.dumps(
                    {
                        'referrer_uid': str(referrer.uid or ''),
                        'new_user_phone': phone,
                        'referral_input': referral_code,
                    },
                    ensure_ascii=False,
                ),
                output_json=json.dumps(
                    {
                        'sfx_download_pack_reward': reward_sfx,
                        'text_char_pack_reward': reward_text,
                    },
                    ensure_ascii=False,
                ),
                file_refs_json='[]',
            )
        )
    return None, None


def _resolve_referrer(db, raw_code: str, current_phone: str) -> UserAccount | None:
    uid = _normalize_uid(raw_code or '')
    if not uid:
        return None
    row = db.execute(select(UserAccount).where(UserAccount.uid == uid).order_by(UserAccount.id.asc())).scalar_one_or_none()
    if row is None:
        return None
    if str(row.phone or '').strip() == str(current_phone or '').strip():
        return None
    return row


def _record_inheritance_review_hits(db, project_id: int, hits: list[dict]) -> None:
    for item in hits or []:
        genre = str(item.get('genre') or '').strip()
        verb_head = str(item.get('verb_head') or '').strip()
        if not genre or not verb_head:
            continue
        rows = db.execute(
            select(ActionGraphInheritanceReview).where(
                ActionGraphInheritanceReview.genre == genre,
                ActionGraphInheritanceReview.verb_head == verb_head,
            )
        ).scalars().all()
        row = rows[0] if rows else None
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
            for duplicate in rows[1:]:
                row.hit_count = int(row.hit_count or 0) + int(duplicate.hit_count or 0)
                if not row.sample_excerpt and duplicate.sample_excerpt:
                    row.sample_excerpt = duplicate.sample_excerpt
                db.delete(duplicate)


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


def _local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def _local_today_ymd() -> str:
    return _local_now().strftime('%Y-%m-%d')


def _local_day_start_utc_naive() -> datetime:
    local_start = _local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    return local_start.astimezone(timezone.utc).replace(tzinfo=None)


def _ensure_user(db, phone: str) -> UserAccount:
    row = db.execute(select(UserAccount).where(UserAccount.phone == phone)).scalar_one_or_none()
    if row is None:
        row = UserAccount(
            phone=phone,
            uid='',
            ops_role_code='33',
            user_tier_code='33',
            is_authorized=0,
            is_admin=0,
            daily_limit=3,
            daily_text_char_limit=5000,
            daily_sfx_download_limit=100,
            invite_activated=0,
            invite_code_used='',
            referred_by_phone='',
            referred_by_uid='',
            referral_input='',
        )
        db.add(row)
        try:
            db.commit()
            db.refresh(row)
        except IntegrityError:
            db.rollback()
            row = db.execute(select(UserAccount).where(UserAccount.phone == phone)).scalar_one_or_none()
            if row is None:
                raise
    elif str(getattr(row, 'uid', '') or '').strip() == f'auto_{phone}':
        row.uid = ''
        try:
            db.commit()
            db.refresh(row)
        except IntegrityError:
            db.rollback()
            row = db.execute(select(UserAccount).where(UserAccount.phone == phone)).scalar_one_or_none()
            if row is None:
                raise
    return row


def _ops_role_code(u: UserAccount | None) -> str:
    return str(getattr(u, 'ops_role_code', '') or '33').zfill(2)


def _user_tier_code(u: UserAccount | None) -> str:
    return str(getattr(u, 'user_tier_code', '') or '33').zfill(2)


def _default_limits_for_tier(tier_code: str) -> tuple[int, int]:
    code = str(tier_code or '').zfill(2)
    if code == '22':
        return 10000, 200
    return 5000, 100


def _daily_usage_row(db, phone: str, action: str, ymd: str, *, create: bool = False) -> DailyUsage | None:
    row = (
        db.execute(
            select(DailyUsage)
            .where(DailyUsage.phone == phone)
            .where(DailyUsage.action == action)
            .where(DailyUsage.ymd == ymd)
        ).scalar_one_or_none()
    )
    if row is None and create:
        row = DailyUsage(phone=phone, action=action, ymd=ymd, used_count=0)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _user_quota_snapshot(db, u: UserAccount) -> dict:
    today = _local_today_ymd()
    text_used = int((_daily_usage_row(db, u.phone, 'text_chars', today, create=False) or DailyUsage(used_count=0)).used_count or 0)
    sfx_used = int((_daily_usage_row(db, u.phone, 'sfx_download', today, create=False) or DailyUsage(used_count=0)).used_count or 0)
    text_limit = int(getattr(u, 'daily_text_char_limit', 0) or 0)
    sfx_limit = int(getattr(u, 'daily_sfx_download_limit', 0) or 0)
    text_pack_balance = int(getattr(u, 'text_char_pack_balance', 0) or 0)
    sfx_pack_balance = int(getattr(u, 'sfx_download_pack_balance', 0) or 0)
    deposit_balance = round(float(getattr(u, 'deposit_balance', 0) or 0), 2)
    return {
        'ymd': today,
        'text_chars_used': text_used,
        'text_chars_limit': text_limit,
        'text_chars_remaining': max(0, text_limit - text_used) if text_limit > 0 else None,
        'text_char_pack_balance': text_pack_balance,
        'text_char_total_available': max(0, text_limit - text_used) + text_pack_balance if text_limit > 0 else text_pack_balance,
        'text_char_effective_limit': max(0, text_limit) + text_pack_balance,
        'sfx_download_used': sfx_used,
        'sfx_download_limit': sfx_limit,
        'sfx_download_remaining': max(0, sfx_limit - sfx_used) if sfx_limit > 0 else None,
        'sfx_download_pack_balance': sfx_pack_balance,
        'sfx_download_total_available': max(0, sfx_limit - sfx_used) + sfx_pack_balance if sfx_limit > 0 else sfx_pack_balance,
        'sfx_download_effective_limit': max(0, sfx_limit) + sfx_pack_balance,
        'deposit_balance': deposit_balance,
    }


def _additional_text_pack_needed(limit: int, used: int, text_len: int) -> int:
    before_over = max(0, used - limit)
    after_over = max(0, used + text_len - limit)
    return max(0, after_over - before_over)


def _additional_sfx_pack_needed(limit: int, used: int, count: int) -> int:
    before_over = max(0, used - limit)
    after_over = max(0, used + count - limit)
    return max(0, after_over - before_over)


def _consume_text_chars_or_error(db, u: UserAccount, text_len: int):
    if int(u.is_authorized or 0) == 1:
        return None
    limit = int(getattr(u, 'daily_text_char_limit', 0) or 0)
    if limit <= 0 or text_len <= 0:
        return None
    today = _local_today_ymd()
    row = _daily_usage_row(db, u.phone, 'text_chars', today, create=True)
    used = int(row.used_count or 0)
    pack_balance = int(getattr(u, 'text_char_pack_balance', 0) or 0)
    grace_ok = _can_consume_text_chars_with_grace(limit, used, text_len)
    extra_pack_needed = _additional_text_pack_needed(limit, used, text_len)
    if not grace_ok and extra_pack_needed > pack_balance:
        return _text_quota_error_response(db, u)
    row.used_count = used + int(text_len)
    if extra_pack_needed > 0:
        u.text_char_pack_balance = max(0, pack_balance - extra_pack_needed)
    db.commit()
    return None


TEXT_QUOTA_ACTIONS = {
    'text_analysis',
    'action_verb_analysis',
    'scene_building_analysis',
    'text_narration_analysis',
}

TEXT_QUOTA_GRACE_CHARS = 1000
LOCAL_TZ = ZoneInfo('Asia/Shanghai')


def _normalize_text_for_quota(text: str) -> str:
    value = re.sub(r'\s+', ' ', str(text or '').strip())
    return value


def _build_text_fingerprint(text: str) -> str:
    normalized = _normalize_text_for_quota(text)
    if not normalized:
        return ''
    return hashlib.sha1(normalized.encode('utf-8')).hexdigest()


def _has_text_fingerprint_logged_today(db, phone: str, text_fingerprint: str) -> bool:
    if not phone or not text_fingerprint:
        return False
    day_start = _local_day_start_utc_naive()
    rows = db.execute(
        select(UserOperationLog).where(
            UserOperationLog.user_phone == phone,
            UserOperationLog.action.in_(TEXT_QUOTA_ACTIONS),
            UserOperationLog.created_at >= day_start,
        )
    ).scalars().all()
    for row in rows:
        try:
            payload = json.loads(row.input_json or '{}')
        except json.JSONDecodeError:
            payload = {}
        if str(payload.get('text_fingerprint') or '').strip() == text_fingerprint:
            return True
    return False


def _ensure_text_chars_available_once_or_error(db, u: UserAccount, text: str):
    text_fingerprint = _build_text_fingerprint(text)
    if text_fingerprint and _has_text_fingerprint_logged_today(db, u.phone, text_fingerprint):
        return None
    return _ensure_text_chars_available_or_error(db, u, len(str(text or '')))


def _consume_text_chars_once_or_error(db, u: UserAccount, text: str):
    text_fingerprint = _build_text_fingerprint(text)
    if text_fingerprint and _has_text_fingerprint_logged_today(db, u.phone, text_fingerprint):
        return None
    return _consume_text_chars_or_error(db, u, len(str(text or '')))


def _text_quota_error_response(db, u: UserAccount):
    return (
        jsonify(
            {
                'detail': f'今日文本分析字符量已达上限，且已购文字包余额不足。请联系管理员或继续充值。系统支持单次最多超额 {TEXT_QUOTA_GRACE_CHARS} 字的缓冲，超出后将暂停使用。',
                'quota': _user_quota_snapshot(db, u),
            }
        ),
        403,
    )


def _can_consume_text_chars_with_grace(limit: int, used: int, text_len: int) -> bool:
    if limit <= 0 or text_len <= 0:
        return True
    if used >= limit:
        return False
    return used + text_len <= limit + TEXT_QUOTA_GRACE_CHARS


def _ensure_text_chars_available_or_error(db, u: UserAccount, text_len: int):
    if int(u.is_authorized or 0) == 1:
        return None
    limit = int(getattr(u, 'daily_text_char_limit', 0) or 0)
    if limit <= 0 or text_len <= 0:
        return None
    today = _local_today_ymd()
    row = _daily_usage_row(db, u.phone, 'text_chars', today, create=True)
    used = int(row.used_count or 0)
    pack_balance = int(getattr(u, 'text_char_pack_balance', 0) or 0)
    grace_ok = _can_consume_text_chars_with_grace(limit, used, text_len)
    extra_pack_needed = _additional_text_pack_needed(limit, used, text_len)
    if not grace_ok and extra_pack_needed > pack_balance:
        return _text_quota_error_response(db, u)
    return None


def _consume_sfx_download_or_error(db, u: UserAccount, count: int = 1):
    if int(u.is_authorized or 0) == 1:
        return None
    limit = int(getattr(u, 'daily_sfx_download_limit', 0) or 0)
    if limit <= 0 or count <= 0:
        return None
    today = _local_today_ymd()
    row = _daily_usage_row(db, u.phone, 'sfx_download', today, create=True)
    used = int(row.used_count or 0)
    pack_balance = int(getattr(u, 'sfx_download_pack_balance', 0) or 0)
    extra_pack_needed = _additional_sfx_pack_needed(limit, used, count)
    if extra_pack_needed > pack_balance:
        return (
            jsonify(
                {
                    'detail': '今日音效下载数已达上限，且已购下载包余额不足，请联系管理员或继续充值',
                    'quota': _user_quota_snapshot(db, u),
                }
            ),
            403,
        )
    row.used_count = used + int(count)
    if extra_pack_needed > 0:
        u.sfx_download_pack_balance = max(0, pack_balance - extra_pack_needed)
    db.commit()
    return None


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
    if u is None:
        return None
    if _invite_only_enabled(db) and _ops_role_code(u) not in {'00', '11'} and int(getattr(u, 'invite_activated', 0) or 0) != 1:
        return None
    return u


def _ensure_bootstrap_admins(db) -> None:
    phones = [x.strip() for x in (settings.bootstrap_admin_phones or '').split(',') if x.strip()]
    for p in phones:
        phone = _normalize_phone(p)
        if not phone:
            continue
        u = _ensure_user(db, phone)
        changed = False
        if _ops_role_code(u) != '00':
            u.ops_role_code = '00'
            changed = True
        if int(u.is_admin or 0) != 1:
            u.is_admin = 1
            changed = True
        if int(u.is_authorized or 0) != 1:
            u.is_authorized = 1
            changed = True
        if _user_tier_code(u) not in {'22', '33'}:
            u.user_tier_code = '33'
            changed = True
        text_limit, sfx_limit = _default_limits_for_tier(_user_tier_code(u))
        if int(getattr(u, 'daily_text_char_limit', 0) or 0) != int(text_limit):
            u.daily_text_char_limit = int(text_limit)
            changed = True
        if int(getattr(u, 'daily_sfx_download_limit', 0) or 0) != int(sfx_limit):
            u.daily_sfx_download_limit = int(sfx_limit)
            changed = True
        if int(getattr(u, 'invite_activated', 0) or 0) != 1:
            u.invite_activated = 1
            changed = True
        if changed:
            db.commit()


def _trigger_action_graph_sync_async() -> dict:
    def _runner():
        try:
            sync_action_graph_to_neo4j()
        except Exception:
            return

    threading.Thread(target=_runner, daemon=True).start()
    return {'ok': True, 'detail': 'neo4j sync queued'}


def _get_user_phone_from_context() -> str:
    phone = getattr(g, 'current_user_phone', None)
    if phone:
        return str(phone)
    u = getattr(g, 'current_user', None)
    if isinstance(u, str) and u:
        return u
    if hasattr(u, 'phone') and getattr(u, 'phone', None):
        return str(u.phone)
    return ''


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
            if _ops_role_code(u) not in {'00', '11'} and int(u.is_admin or 0) != 1:
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

        info = {
            'authorized': bool(int(u.is_authorized or 0)),
            'ops_role_code': _ops_role_code(u),
            'user_tier_code': _user_tier_code(u),
            'quota': _user_quota_snapshot(db, u),
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
    if getattr(g, 'current_user_phone', None):
        return str(g.current_user_phone)
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
                'elapsed_ms': cm.get('elapsed_ms'),
                'provider': cm.get('provider'),
                'model': cm.get('model'),
                'contract_valid': x.get('contract_valid'),
                'quality_valid': x.get('quality_valid'),
                'quality_reason': x.get('quality_reason'),
            }
        )
    return out


def _summarize_llm_audit(payload: dict | None) -> dict:
    data = payload if isinstance(payload, dict) else {}
    trace = data.get('llm_trace')
    if not isinstance(trace, list):
        trace = data.get('llm_trace_digest')
    rows = []
    for item in (trace or []):
        if not isinstance(item, dict):
            continue
        call_meta = item.get('call_meta') if isinstance(item.get('call_meta'), dict) else item
        prompt_file = str(item.get('prompt_file') or '').strip()
        provider = str(call_meta.get('provider') or '').strip()
        model = str(call_meta.get('model') or '').strip()
        status = str(call_meta.get('status') or '').strip()
        request_id = str(call_meta.get('request_id') or '').strip()
        elapsed_ms = call_meta.get('elapsed_ms')
        try:
            elapsed_ms = int(elapsed_ms) if elapsed_ms is not None else None
        except (TypeError, ValueError):
            elapsed_ms = None
        rows.append(
            {
                'prompt_file': prompt_file,
                'provider': provider,
                'model': model,
                'status': status,
                'request_id': request_id,
                'elapsed_ms': elapsed_ms,
                'contract_valid': item.get('contract_valid'),
                'quality_valid': item.get('quality_valid'),
                'quality_reason': str(item.get('quality_reason') or '').strip(),
            }
        )
    llm_calls = len(rows)
    total_elapsed_ms = sum(int(r['elapsed_ms'] or 0) for r in rows if r.get('elapsed_ms') is not None)
    models = []
    seen_models = set()
    for row in rows:
        label = ' / '.join([x for x in [row.get('provider') or '', row.get('model') or ''] if x]).strip(' /')
        if not label or label in seen_models:
            continue
        seen_models.add(label)
        models.append(label)
    overall_duration_ms = data.get('duration_ms')
    try:
        overall_duration_ms = int(overall_duration_ms) if overall_duration_ms is not None else None
    except (TypeError, ValueError):
        overall_duration_ms = None
    if overall_duration_ms is None and total_elapsed_ms:
        overall_duration_ms = total_elapsed_ms
    return {
        'llm_calls': llm_calls,
        'total_elapsed_ms': total_elapsed_ms or None,
        'overall_duration_ms': overall_duration_ms,
        'models': models,
        'calls': rows,
    }


SYSTEM_PRESSURE_ACTION_LABELS = {
    'audio_analysis': '音乐分析',
    'text_analysis': '文本分析',
    'action_verb_analysis': '动作词提取',
    'action_sfx_graph': '动作图谱音效分析',
    'scene_building_analysis': '场景搭建分析',
    'scene_sfx_graph': '场景图谱音效分析',
    'text_narration_analysis': '文本旁白联合分析',
    'fusion_execution': '融合执行单生成',
}
SYSTEM_PRESSURE_ACTIONS = tuple(SYSTEM_PRESSURE_ACTION_LABELS.keys())


def _pressure_percentile(values: list[int], ratio: float) -> int | None:
    seq = sorted(int(v) for v in values if v is not None)
    if not seq:
        return None
    idx = max(0, min(len(seq) - 1, int(round((len(seq) - 1) * ratio))))
    return seq[idx]


def _pressure_estimate_peak(entries: list[dict]) -> int:
    points = []
    for item in entries:
        ended_at = item.get('created_at')
        duration_ms = item.get('duration_ms')
        if not isinstance(ended_at, datetime) or not isinstance(duration_ms, int) or duration_ms <= 0:
            continue
        started_at = ended_at - timedelta(milliseconds=duration_ms)
        points.append((started_at, 1))
        points.append((ended_at, -1))
    if not points:
        return 0
    current = 0
    peak = 0
    for _, delta in sorted(points, key=lambda x: (x[0], x[1])):
        current += delta
        peak = max(peak, current)
    return peak


def _pressure_duration_ms(output_json: dict, llm_audit: dict) -> int | None:
    candidates = []
    for value in [
        output_json.get('duration_ms'),
        llm_audit.get('overall_duration_ms'),
        llm_audit.get('total_elapsed_ms'),
    ]:
        try:
            value = int(value) if value is not None else None
        except (TypeError, ValueError):
            value = None
        if value is not None and value > 0:
            candidates.append(value)
    return candidates[0] if candidates else None


def _build_system_pressure_window(entries: list[dict]) -> dict:
    durations = [int(item['duration_ms']) for item in entries if isinstance(item.get('duration_ms'), int)]
    llm_calls = sum(int(item.get('llm_calls') or 0) for item in entries)
    retry_jobs = sum(1 for item in entries if int(item.get('llm_calls') or 0) > 1)
    llm_failures = sum(int(item.get('llm_failure_count') or 0) for item in entries)
    quality_issues = sum(int(item.get('quality_issue_count') or 0) for item in entries)
    contract_issues = sum(int(item.get('contract_issue_count') or 0) for item in entries)
    return {
        'request_count': len(entries),
        'unique_users': len({item['user_phone'] for item in entries if item.get('user_phone')}),
        'unique_projects': len({item['project_id'] for item in entries if item.get('project_id') is not None}),
        'llm_calls': llm_calls,
        'retry_jobs': retry_jobs,
        'llm_failure_count': llm_failures,
        'quality_issue_count': quality_issues,
        'contract_issue_count': contract_issues,
        'avg_elapsed_ms': int(sum(durations) / len(durations)) if durations else None,
        'p95_elapsed_ms': _pressure_percentile(durations, 0.95),
        'max_elapsed_ms': max(durations) if durations else None,
        'peak_concurrency_est': _pressure_estimate_peak(entries),
    }


def _build_system_pressure_guidance(m10: dict, h1: dict, h24: dict) -> dict:
    peak = max(int(m10.get('peak_concurrency_est') or 0), int(h1.get('peak_concurrency_est') or 0))
    p95_ms = int(h1.get('p95_elapsed_ms') or 0)
    request_10m = int(m10.get('request_count') or 0)
    request_1h = int(h1.get('request_count') or 0)
    llm_failures = int(h1.get('llm_failure_count') or 0)
    quality_issues = int(h1.get('quality_issue_count') or 0)

    suggested_global_slots = max(3, min(10, max(peak + 1, 4 if request_10m >= 8 else 3)))
    suggested_user_running_limit = 1 if peak >= 8 else 2
    suggested_user_queue_limit = max(3, suggested_global_slots)
    suggested_queue_threshold_sec = 180
    suggested_timeout_sec = 240 if p95_ms >= 120000 else 180
    should_queue = bool(request_10m >= suggested_global_slots or peak >= suggested_global_slots - 1 or p95_ms >= 90000)

    level = '平稳'
    if peak >= 6 or p95_ms >= 150000 or llm_failures >= 3:
        level = '高压'
    elif peak >= 3 or p95_ms >= 90000 or request_10m >= 6 or quality_issues >= 2:
        level = '关注'

    reasons = []
    if request_10m:
        reasons.append(f"最近 10 分钟共有 {request_10m} 个重请求进入核心链路。")
    if peak:
        reasons.append(f"按日志耗时倒推，最近高峰重叠并发约为 {peak}。")
    if p95_ms:
        reasons.append(f"最近 1 小时重请求 P95 耗时约 {round(p95_ms / 1000, 1)} 秒。")
    if quality_issues:
        reasons.append(f"最近 1 小时出现 {quality_issues} 次质量异常，说明慢请求之外还要关注结果稳定性。")
    if llm_failures:
        reasons.append(f"最近 1 小时出现 {llm_failures} 次 LLM 调用非 ok，排队与超时治理应一起考虑。")
    if not reasons:
        reasons.append('最近暂无足够的重请求日志，建议先保持当前结构并持续观察。')

    if h24.get('request_count'):
        reasons.append(
            f"最近 24 小时累计 {int(h24.get('request_count') or 0)} 个重请求，可作为后续并发阈值和扩容策略的基线。"
        )

    return {
        'level': level,
        'should_queue': should_queue,
        'suggested_global_slots': suggested_global_slots,
        'suggested_user_running_limit': suggested_user_running_limit,
        'suggested_user_queue_limit': suggested_user_queue_limit,
        'suggested_timeout_sec': suggested_timeout_sec,
        'suggested_queue_threshold_sec': suggested_queue_threshold_sec,
        'reason_lines': reasons,
        'queue_scope': list(SYSTEM_PRESSURE_ACTIONS),
        'current_basis': '基于最近 24 小时 user_operation_log 与 llm_trace_digest 聚合估算',
        'next_step': '建议先对重接口做异步 job 队列与前端轮询，不对轻接口排队。',
        'request_1h': request_1h,
    }


def _log_user_operation(
    db,
    action: str,
    project_id: int | None,
    req: dict | None = None,
    resp: dict | None = None,
    file_refs: list[str] | None = None,
) -> None:
    phone = _get_user_phone_from_context()
    output_payload = dict(resp or {})
    if phone:
        user = db.execute(select(UserAccount).where(UserAccount.phone == phone)).scalar_one_or_none()
        if user is not None:
            quota = _user_quota_snapshot(db, user)
            output_payload['download_usage_snapshot'] = {
                'ymd': quota.get('ymd'),
                'sfx_download_used': int(quota.get('sfx_download_used') or 0),
                'sfx_download_limit': int(quota.get('sfx_download_limit') or 0),
                'sfx_download_remaining': int(quota.get('sfx_download_remaining') or 0),
                'sfx_download_pack_balance': int(quota.get('sfx_download_pack_balance') or 0),
            }
    row = UserOperationLog(
        project_id=project_id,
        user_phone=phone,
        action=action,
        input_json=json.dumps(req or {}, ensure_ascii=False),
        output_json=json.dumps(output_payload, ensure_ascii=False),
        file_refs_json=json.dumps(file_refs or [], ensure_ascii=False),
    )
    db.add(row)
    db.commit()


def _extract_action_fallback_clusters(result: dict | None) -> list[dict]:
    data = result if isinstance(result, dict) else {}
    genre = str(data.get('genre') or '').strip()
    out = []
    for item in (data.get('graph_items') or []):
        if not isinstance(item, dict):
            continue
        children = item.get('children') or {}
        semantic_items = children.get('semantic_term_items') or []
        sfx_items = children.get('sfx_term_items') or []
        fallback_terms = []
        for row in list(semantic_items) + list(sfx_items):
            if not isinstance(row, dict):
                continue
            if str(row.get('source') or '').strip() != 'fallback':
                continue
            term = str(row.get('term') or '').strip()
            if term and term not in fallback_terms:
                fallback_terms.append(term)
        if not fallback_terms:
            continue
        raw_verbs = [str(x).strip() for x in (item.get('raw_verbs') or []) if str(x).strip()]
        source_term = _suggest_action_fallback_source_term(fallback_terms, raw_verbs)
        candidate_replacement_terms, auto_filtered_terms = _suggest_action_fallback_replacement_terms(
            fallback_terms,
            raw_verbs,
            source_term,
        )
        parent = item.get('parent_node') or {}
        out.append(
            {
                'genre': genre,
                'parent_head': str(parent.get('verb_head') or item.get('verb') or '').strip(),
                'fallback_terms': fallback_terms,
                'source_term': str(source_term or '').strip(),
                'sentence_excerpt': str(item.get('sentence_excerpt') or '').strip(),
                'raw_verbs': raw_verbs,
                'candidate_replacement_terms': candidate_replacement_terms,
                'auto_filtered_terms': auto_filtered_terms,
            }
        )
    return out


def _action_fallback_rule_maps() -> dict:
    rules = list_action_fallback_replacement_rules().get('items', [])
    out = {'common': {}, 'genres': {}}
    for item in rules:
        if not isinstance(item, dict):
            continue
        source = str(item.get('source_term') or '').strip()
        replacements = [str(x).strip() for x in (item.get('replacement_terms') or []) if str(x).strip()]
        scope = str(item.get('scope') or 'common').strip()
        genre = str(item.get('genre') or '').strip()
        if not source or not replacements:
            continue
        if scope == 'genre' and genre:
            out['genres'].setdefault(genre, {})[source] = replacements
        else:
            out['common'][source] = replacements
    return out


def _action_fallback_ignored_maps() -> dict:
    graph = _load_action_graph()
    meta = graph.get('_meta') or {}
    raw = meta.get('fallback_ignored_terms') or {}
    out = {'common': set(), 'genres': {}}
    if not isinstance(raw, dict):
        return out
    out['common'] = {str(x).strip() for x in (raw.get('common') or []) if str(x).strip()}
    genres = raw.get('genres') or {}
    if isinstance(genres, dict):
        for genre, items in genres.items():
            genre_key = str(genre or '').strip()
            if not genre_key:
                continue
            out['genres'][genre_key] = {str(x).strip() for x in (items or []) if str(x).strip()}
    return out


def _is_action_fallback_cluster_resolved(cluster: dict | None, rule_maps: dict | None = None) -> bool:
    data = cluster if isinstance(cluster, dict) else {}
    if bool(data.get('reopened_from_rule')):
        return False
    terms = [str(x).strip() for x in (data.get('fallback_terms') or []) if str(x).strip()]
    if not terms:
        return True
    source_term = str(data.get('source_term') or '').strip() or max(terms, key=lambda x: (len(x), x))
    genre = str(data.get('genre') or '').strip()
    maps = rule_maps if isinstance(rule_maps, dict) else _action_fallback_rule_maps()
    ignored_maps = _action_fallback_ignored_maps()
    genre_map = (maps.get('genres') or {}).get(genre, {}) if genre else {}
    common_map = maps.get('common') or {}
    ignored_terms = set(ignored_maps.get('common') or set())
    if genre:
        ignored_terms.update((ignored_maps.get('genres') or {}).get(genre, set()))
    replacements = list(genre_map.get(source_term) or common_map.get(source_term) or [])
    if replacements:
        return True
    return all(term in ignored_terms for term in terms if term != source_term)


def _collect_action_fallback_monitor(days: int = 30, limit: int = 2000) -> dict:
    window_days = max(1, min(int(days or 30), 180))
    row_limit = max(1, min(int(limit or 2000), 5000))
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    with SessionLocal() as db:
        rows = db.execute(
            select(UserOperationLog)
            .where(
                UserOperationLog.action == 'action_sfx_graph',
                UserOperationLog.created_at >= cutoff,
            )
            .order_by(UserOperationLog.created_at.desc())
            .limit(row_limit)
        ).scalars().all()
        risk_terms = _list_action_fallback_risk_terms(db)

    grouped: dict[str, dict] = {}
    for row in rows:
        try:
            output_json = json.loads(row.output_json or '{}')
        except json.JSONDecodeError:
            output_json = {}
        for cluster in (output_json.get('fallback_clusters') or []):
            if not isinstance(cluster, dict):
                continue
            terms = [str(x).strip() for x in (cluster.get('fallback_terms') or []) if str(x).strip()]
            if not terms:
                continue
            genre = str(cluster.get('genre') or '').strip()
            raw_verbs = [str(x).strip() for x in (cluster.get('raw_verbs') or []) if str(x).strip()]
            source_term = str(cluster.get('source_term') or '').strip() or _suggest_action_fallback_source_term(terms, raw_verbs, risk_terms)
            candidate_replacement_terms, auto_filtered_terms = _suggest_action_fallback_replacement_terms(
                terms,
                raw_verbs,
                source_term,
                risk_terms,
            )
            key = f"{genre}||{'/'.join(sorted(terms))}"
            bucket = grouped.setdefault(
                key,
                {
                    'cluster_key': key,
                    'genre': genre,
                    'fallback_terms': sorted(terms),
                    'source_term': source_term,
                    'hit_count': 0,
                    'project_ids': [],
                    'user_phones': [],
                    'parent_heads': [],
                    'raw_verbs': [],
                    'examples': [],
                    'candidate_replacement_terms': [],
                    'auto_filtered_terms': [],
                    'latest_at': '',
                },
            )
            bucket['hit_count'] += 1
            if row.project_id and row.project_id not in bucket['project_ids']:
                bucket['project_ids'].append(row.project_id)
            phone = str(row.user_phone or '').strip()
            if phone and phone not in bucket['user_phones']:
                bucket['user_phones'].append(phone)
            for field, target in (
                ('parent_head', 'parent_heads'),
                ('sentence_excerpt', 'examples'),
            ):
                value = str(cluster.get(field) or '').strip()
                if value and value not in bucket[target]:
                    bucket[target].append(value)
            for raw_verb in raw_verbs:
                if raw_verb not in bucket['raw_verbs']:
                    bucket['raw_verbs'].append(raw_verb)
            for replacement_term in candidate_replacement_terms:
                if replacement_term not in bucket['candidate_replacement_terms']:
                    bucket['candidate_replacement_terms'].append(replacement_term)
            for filtered_term in auto_filtered_terms:
                if filtered_term not in bucket['auto_filtered_terms']:
                    bucket['auto_filtered_terms'].append(filtered_term)
            created_at = row.created_at.isoformat() if row.created_at else ''
            if created_at and created_at > str(bucket.get('latest_at') or ''):
                bucket['latest_at'] = created_at

    graph = _load_action_graph()
    reopen_queue = ((graph.get('_meta') or {}).get('fallback_reopen_queue') or [])
    if isinstance(reopen_queue, list):
        for queued in reopen_queue:
            if not isinstance(queued, dict):
                continue
            source_term = str(queued.get('source_term') or '').strip()
            if not source_term:
                continue
            genre = str(queued.get('genre') or '').strip()
            fallback_terms = [source_term]
            key = f"{genre}||reopen||{source_term}"
            bucket = grouped.setdefault(
                key,
                {
                    'cluster_key': key,
                    'genre': genre,
                    'fallback_terms': fallback_terms,
                    'source_term': source_term,
                    'hit_count': 0,
                    'project_ids': [],
                    'user_phones': [],
                    'parent_heads': [],
                    'raw_verbs': [source_term],
                    'examples': [],
                    'candidate_replacement_terms': [str(x).strip() for x in (queued.get('replacement_terms') or []) if str(x).strip()],
                    'auto_filtered_terms': [],
                    'latest_at': str(queued.get('released_at') or ''),
                    'reopened_from_rule': True,
                },
            )
            bucket['reopened_from_rule'] = True

    rule_maps = _action_fallback_rule_maps()
    items = []
    for bucket in grouped.values():
        enriched = dict(bucket)
        enriched['resolved'] = _is_action_fallback_cluster_resolved(enriched, rule_maps)
        items.append(enriched)
    items.sort(
        key=lambda item: (
            str(item.get('latest_at') or ''),
            int(item.get('hit_count') or 0),
            str(item.get('cluster_key') or ''),
        ),
        reverse=True,
    )
    rules = list_action_fallback_replacement_rules().get('items', [])
    pending_items = [item for item in items if not item.get('resolved')]
    return {
        'days': window_days,
        'count': len(items),
        'pending_count': len(pending_items),
        'items': items,
        'pending_items': pending_items,
        'rules': rules,
        'risk_terms': risk_terms,
    }


def _attach_quota_headers(resp, quota: dict | None):
    snapshot = quota or {}
    resp.headers['X-Quota-Ymd'] = str(snapshot.get('ymd') or '')
    resp.headers['X-Quota-Text-Chars-Used'] = str(snapshot.get('text_chars_used') or 0)
    resp.headers['X-Quota-Text-Chars-Limit'] = str(snapshot.get('text_chars_limit') or 0)
    resp.headers['X-Quota-Text-Chars-Remaining'] = str(snapshot.get('text_chars_remaining') or 0)
    resp.headers['X-Quota-Sfx-Download-Used'] = str(snapshot.get('sfx_download_used') or 0)
    resp.headers['X-Quota-Sfx-Download-Limit'] = str(snapshot.get('sfx_download_limit') or 0)
    resp.headers['X-Quota-Sfx-Download-Remaining'] = str(snapshot.get('sfx_download_remaining') or 0)
    return resp


def _creator_showcase_out(row: CreatorShowcase) -> dict:
    try:
        skills = json.loads(row.skills_json or '[]')
    except json.JSONDecodeError:
        skills = []
    return {
        'id': row.id,
        'user_phone': row.user_phone,
        'title': row.title,
        'genre': row.genre,
        'role_label': row.role_label,
        'summary': row.summary,
        'skills': skills if isinstance(skills, list) else [],
        'sample_link': row.sample_link,
        'sample_file_name': row.sample_file_name,
        'sample_download_api': (
            f"{settings.api_prefix}/ops/file?path={quote(str(row.sample_file_path or ''), safe='')}"
            if str(row.sample_file_path or '').strip()
            else ''
        ),
        'status': row.status,
        'note': row.note,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def _copyright_ad_out(row: CopyrightBookAd) -> dict:
    return {
        'id': row.id,
        'user_phone': getattr(row, 'user_phone', '') or '',
        'title': row.title,
        'genre': row.genre,
        'description': row.description,
        'budget_text': row.budget_text,
        'deposit_amount': float(row.deposit_amount or 0),
        'contact_note': row.contact_note,
        'status': row.status,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def _recruitment_need_out(row: RecruitmentNeed) -> dict:
    return {
        'id': row.id,
        'title': row.title,
        'genre': row.genre,
        'description': row.description,
        'budget_text': row.budget_text,
        'deadline_text': row.deadline_text,
        'contact_note': row.contact_note,
        'status': row.status,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def _recharge_order_out(row: RechargeOrder) -> dict:
    return {
        'id': row.id,
        'user_phone': row.user_phone,
        'order_type': row.order_type,
        'package_name': row.package_name,
        'units': int(row.units or 0),
        'payable_amount': float(row.payable_amount or 0),
        'deposit_offset': float(row.deposit_offset or 0),
        'note': row.note,
        'status': row.status,
        'reviewed_by': row.reviewed_by,
        'reviewed_at': row.reviewed_at.isoformat() if row.reviewed_at else None,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def _user_sfx_submission_out(row: UserSfxSubmission) -> dict:
    status = str(row.status or '').strip()
    status_label = {
        'pending': '待系统采纳',
        'approved': '已被系统采纳',
        'rejected': '未被采纳',
    }.get(status, status or '-')
    return {
        'id': row.id,
        'user_phone': row.user_phone,
        'project_id': row.project_id,
        'genre': row.genre,
        'verb': row.verb,
        'display_term': row.display_term,
        'sentence_excerpt': row.sentence_excerpt,
        'project_text_excerpt': row.project_text_excerpt,
        'note': row.note,
        'file_name': row.file_name,
        'file_path': row.file_path,
        'file_path_display': _display_local_path(str(row.file_path or '')),
        'download_api': (
            f"{settings.api_prefix}/ops/file?path={quote(str(row.file_path or ''), safe='')}"
            if str(row.file_path or '').strip()
            else ''
        ),
        'status': status,
        'status_label': status_label,
        'review_note': row.review_note,
        'reviewed_by': row.reviewed_by,
        'reviewed_at': row.reviewed_at.isoformat() if row.reviewed_at else None,
        'reward_download_delta': int(row.reward_download_delta or 0),
        'reward_applied': bool(int(row.reward_applied or 0)),
        'adopted_download_count': int(row.adopted_download_count or 0),
        'adopted_file_name': row.adopted_file_name,
        'adopted_file_path': row.adopted_file_path,
        'adopted_file_path_display': _display_local_path(str(row.adopted_file_path or '')),
        'adopted_source_label': row.adopted_source_label,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def _normalize_phone_for_path(phone: str) -> str:
    p = re.sub(r'[^0-9+]', '', phone or '')
    return p or 'anonymous'


def _safe_storage_name(value: str, fallback: str = 'unnamed') -> str:
    text = str(value or '').strip()
    if not text:
        return fallback
    text = re.sub(r'[\\/:*?"<>|]+', '_', text)
    text = re.sub(r'\s+', '_', text)
    text = re.sub(r'_+', '_', text).strip('._ ')
    return text or fallback


def _normalize_user_better_sfx_label(display_term: str, verb: str) -> str:
    return str(display_term or '').strip() or str(verb or '').strip()


def _user_better_sfx_dir() -> Path:
    out_dir = Path('./assets/sfx_user').resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _adopt_user_sfx_submission_asset(row: UserSfxSubmission) -> tuple[str, str]:
    raw_path = str(getattr(row, 'file_path', '') or '').strip()
    src = Path(raw_path) if raw_path else None
    if src is None or not src.exists() or not src.is_file():
        raise FileNotFoundError('submission source file not found')
    label = _normalize_user_better_sfx_label(getattr(row, 'display_term', ''), getattr(row, 'verb', ''))
    if not label:
        raise ValueError('display term is required for adoption')
    ext = src.suffix.lower() or '.mp3'
    safe_label = _safe_storage_name(label, 'user_better')
    safe_genre = _safe_storage_name(str(getattr(row, 'genre', '') or ''), 'genre')
    safe_verb = _safe_storage_name(str(getattr(row, 'verb', '') or ''), 'verb')
    out_path = _user_better_sfx_dir() / f'{safe_genre}__{safe_verb}__{safe_label}__userbetter_{int(getattr(row, "id", 0) or 0)}{ext}'
    out_path.write_bytes(src.read_bytes())
    return out_path.name, str(out_path)


def _display_local_path(abs_path: str) -> str:
    value = str(abs_path or '').strip()
    if not value:
        return ''
    try:
        root = Path('/Users/demo/Documents/New project/musicForArt').resolve()
        return str(Path(value).resolve().relative_to(root))
    except Exception:
        return value


def _build_local_storage_path(
    action: str,
    project_id: int,
    original_name: str,
    *,
    suffix_fallback: str = '.bin',
    unique_name: bool = False,
) -> Path:
    root = Path(settings.upload_dir).resolve()
    phone = _normalize_phone_for_path(_extract_user_phone())
    now = datetime.now()
    ext = Path(original_name or '').suffix or suffix_fallback
    safe_action = re.sub(r'[^a-zA-Z0-9_-]', '_', action or 'unknown')
    safe_ext = re.sub(r'[^a-zA-Z0-9.]', '', ext) or suffix_fallback
    out_dir = root / phone / f'{now.year:04d}' / f'{now.month:02d}' / f'{now.day:02d}' / safe_action
    out_dir.mkdir(parents=True, exist_ok=True)
    unique_part = f'_{secrets.token_hex(4)}' if unique_name else ''
    return out_dir / f'project_{project_id}{unique_part}{safe_ext}'


def _read_validated_audio_upload(file, *, label: str = '音频文件', max_mb: int = AUDIO_UPLOAD_MAX_MB) -> tuple[str, bytes]:
    if file is None or not getattr(file, 'filename', ''):
        raise ValueError(f'{label}不能为空')
    original_name = str(getattr(file, 'filename', '') or '').strip()
    if not original_name:
        raise ValueError(f'{label}不能为空')
    if any(sep in original_name for sep in ('/', '\\', '\x00')):
        raise ValueError(f'{label}文件名不合法')
    safe_name = Path(original_name).name.strip()
    ext = (Path(safe_name).suffix or '').lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError('仅支持 mp3 / wav / m4a / aac / flac / ogg 音频文件')
    content_type = str(getattr(file, 'mimetype', '') or getattr(file, 'content_type', '') or '').split(';', 1)[0].strip().lower()
    if content_type and not (content_type.startswith('audio/') or content_type in ALLOWED_AUDIO_MIME_TYPES):
        raise ValueError('仅支持音频格式文件，禁止上传视频、文本或其他非音频内容')
    file_bytes = file.read()
    if not file_bytes:
        raise ValueError(f'{label}不能为空文件')
    max_bytes = int(max_mb) * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise OverflowError(f'{label}大小不能超过 {int(max_mb)}MB')
    return safe_name, file_bytes


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
    with SessionLocal() as db:
        _ensure_bootstrap_admins(db)
        _ensure_invite_seed_codes(db)
        _ensure_action_fallback_risk_terms(db)


@app.get('/health')
def health():
    key_tail = settings.llm_api_key[-4:] if settings.llm_api_key else ''
    qwen_key_tail = settings.qwen_api_key[-4:] if settings.qwen_api_key else ''
    return jsonify(
        {
            'ok': True,
            'env': settings.app_env,
            'llm_enabled': llm_enabled(),
            'llm_provider': settings.llm_provider,
            'llm_model': settings.llm_model,
            'llm_key_tail': key_tail,
            'qwen_enabled': llm_enabled('qwen'),
            'qwen_provider': 'qwen',
            'qwen_model': settings.qwen_model,
            'qwen_key_tail': qwen_key_tail,
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
        _ensure_invite_seed_codes(db)
        day_start = datetime.combine(_utc_now().date(), datetime.min.time())
        sent_today = int(
            db.execute(
                select(func.count())
                .select_from(AuthCode)
                .where(AuthCode.phone == phone)
                .where(AuthCode.created_at >= day_start)
            ).scalar() or 0
        )
        if sent_today >= AUTH_CODE_DAILY_LIMIT:
            return jsonify({
                'detail': f'该手机号今日最多可接收 {AUTH_CODE_DAILY_LIMIT} 次验证码，请明天再试',
                'phone': phone,
                'sent_today': sent_today,
                'daily_limit': AUTH_CODE_DAILY_LIMIT,
            }), 429
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
    with SessionLocal() as db:
        invite_only_enabled = _invite_only_enabled(db)
    return jsonify({
        'ok': True,
        'phone': phone,
        'code': code,
        'ttl_sec': settings.auth_code_ttl_sec,
        'invite_only_enabled': invite_only_enabled,
        'sent_today': sent_today + 1,
        'daily_limit': AUTH_CODE_DAILY_LIMIT,
        'remaining_today': max(0, AUTH_CODE_DAILY_LIMIT - sent_today - 1),
    })


@app.post(f'{settings.api_prefix}/auth/login')
def auth_login():
    payload = request.get_json(force=True, silent=True) or {}
    phone = _normalize_phone(payload.get('phone') or '')
    code = str(payload.get('code') or '').strip()
    password = str(payload.get('password') or '')
    invite_code = str(payload.get('invite_code') or '').strip().upper()
    referral_code = str(payload.get('referral_code') or '').strip()
    if not phone or (not code and not password):
        return jsonify({'detail': 'phone and password/code are required'}), 400
    if not _is_valid_phone(phone):
        return jsonify({'detail': '请输入有效的11位手机号'}), 400

    with SessionLocal() as db:
        _ensure_bootstrap_admins(db)
        _ensure_invite_seed_codes(db)
        beta_invite_only = _invite_only_enabled(db)
        bootstrap_admin_phones = {p.strip() for p in str(settings.bootstrap_admin_phones or '').split(',') if p.strip()}
        is_bootstrap_admin_login = phone in bootstrap_admin_phones and code == '111111'
        is_test_bypass_login = code == '111111'
        u = _ensure_user(db, phone)
        if password:
            if not _verify_password(password, str(getattr(u, 'password_hash', '') or '')):
                return jsonify({'detail': '手机号或密码不正确'}), 400
        else:
            c = (
                db.execute(
                    select(AuthCode)
                    .where(AuthCode.phone == phone)
                    .where(AuthCode.code == code)
                    .where(AuthCode.used == 0)
                    .order_by(AuthCode.id.desc())
                ).scalar_one_or_none()
            )
            if not is_bootstrap_admin_login and not is_test_bypass_login and (c is None or c.expires_at < _utc_now()):
                return jsonify({'detail': '验证码无效或已过期'}), 400
            if c is not None:
                c.used = 1
        if not str(getattr(u, 'password_hash', '') or '').strip() and not (is_bootstrap_admin_login or is_test_bypass_login):
            return jsonify({'detail': '该手机号尚未注册，请先完成注册'}), 400

        error_body, error_code = _apply_activation_and_referral_rules(
            db, u, phone=phone, invite_code=invite_code, referral_code=referral_code
        )
        if error_body is not None:
            return jsonify(error_body), int(error_code or 400)

        token = _issue_auth_session(db, phone)
        db.commit()
        quota = _user_quota_snapshot(db, u)

        return jsonify(
            {
                'ok': True,
                'token': token,
                'expires_in_sec': settings.auth_session_ttl_sec,
                'user': {
                    'id': int(u.id),
                    'phone': u.phone,
                    'uid': str(u.uid or ''),
                    'ops_role_code': _ops_role_code(u),
                    'user_tier_code': _user_tier_code(u),
                    'is_admin': bool(int(u.is_admin or 0)),
                    'is_authorized': bool(int(u.is_authorized or 0)),
                    'daily_limit': int(u.daily_limit or 3),
                    'daily_text_char_limit': int(getattr(u, 'daily_text_char_limit', 0) or 0),
                    'daily_sfx_download_limit': int(getattr(u, 'daily_sfx_download_limit', 0) or 0),
                    'invite_activated': bool(int(getattr(u, 'invite_activated', 0) or 0)),
                    'invite_code_used': str(getattr(u, 'invite_code_used', '') or ''),
                    'referred_by_phone': str(getattr(u, 'referred_by_phone', '') or ''),
                    'referred_by_uid': str(getattr(u, 'referred_by_uid', '') or ''),
                },
                'beta_invite_only_enabled': beta_invite_only,
                'quota': quota,
            }
        )


@app.post(f'{settings.api_prefix}/auth/code-login-or-register')
def auth_code_login_or_register():
    payload = request.get_json(force=True, silent=True) or {}
    phone = _normalize_phone(payload.get('phone') or '')
    code = str(payload.get('code') or '').strip()
    invite_code = str(payload.get('invite_code') or '').strip().upper()
    referral_code = str(payload.get('referral_code') or '').strip()
    uid_input = _normalize_uid(payload.get('uid') or '')
    if not phone or not code:
        return jsonify({'detail': 'phone and code are required'}), 400
    if not _is_valid_phone(phone):
        return jsonify({'detail': '请输入有效的11位手机号'}), 400

    with SessionLocal() as db:
        _ensure_bootstrap_admins(db)
        _ensure_invite_seed_codes(db)
        beta_invite_only = _invite_only_enabled(db)
        is_test_bypass = code == '111111'
        existing_user = db.execute(select(UserAccount).where(UserAccount.phone == phone)).scalar_one_or_none()
        c = (
            db.execute(
                select(AuthCode)
                .where(AuthCode.phone == phone)
                .where(AuthCode.code == code)
                .where(AuthCode.used == 0)
                .order_by(AuthCode.id.desc())
            ).scalar_one_or_none()
        )
        if not is_test_bypass and (c is None or c.expires_at < _utc_now()):
            return jsonify({'detail': '验证码无效或已过期'}), 400

        u = _ensure_user(db, phone)
        created_now = existing_user is None
        if created_now:
            if not uid_input:
                return jsonify({'detail': '首次登录请填写 UID（纯数字）'}), 400
            if not _is_valid_uid(uid_input):
                return jsonify({'detail': 'UID 仅支持纯数字，且至少3位'}), 400
            u.uid = uid_input
            # 用户中心的首次验证码登录即注册；密码字段仅作为“已注册”标志占位。
            u.password_hash = _hash_password(f'code-only:{phone}')

        error_body, error_code = _apply_activation_and_referral_rules(
            db, u, phone=phone, invite_code=invite_code, referral_code=referral_code
        )
        if error_body is not None:
            return jsonify(error_body), int(error_code or 400)

        if c is not None:
            c.used = 1

        token = _issue_auth_session(db, phone)
        db.commit()
        quota = _user_quota_snapshot(db, u)
        return jsonify(
            {
                'ok': True,
                'token': token,
                'expires_in_sec': settings.auth_session_ttl_sec,
                'created_now': bool(created_now),
                'user': {
                    'id': int(u.id),
                    'phone': u.phone,
                    'uid': str(u.uid or ''),
                    'ops_role_code': _ops_role_code(u),
                    'user_tier_code': _user_tier_code(u),
                    'is_admin': bool(int(u.is_admin or 0)),
                    'is_authorized': bool(int(u.is_authorized or 0)),
                    'daily_limit': int(u.daily_limit or 3),
                    'invite_activated': bool(int(getattr(u, 'invite_activated', 0) or 0)),
                    'invite_code_used': str(getattr(u, 'invite_code_used', '') or ''),
                    'referred_by_phone': str(getattr(u, 'referred_by_phone', '') or ''),
                    'referred_by_uid': str(getattr(u, 'referred_by_uid', '') or ''),
                },
                'beta_invite_only_enabled': beta_invite_only,
                'quota': quota,
            }
        )


@app.post(f'{settings.api_prefix}/auth/register')
def auth_register():
    payload = request.get_json(force=True, silent=True) or {}
    phone = _normalize_phone(payload.get('phone') or '')
    code = str(payload.get('code') or '').strip()
    password = str(payload.get('password') or '')
    invite_code = str(payload.get('invite_code') or '').strip().upper()
    referral_code = str(payload.get('referral_code') or '').strip()
    uid_input = _normalize_uid(payload.get('uid') or '')
    if not phone or not code or not password or not uid_input:
        return jsonify({'detail': 'phone, code, password and uid are required'}), 400
    if not _is_valid_phone(phone):
        return jsonify({'detail': '请输入有效的11位手机号'}), 400
    if not _is_valid_uid(uid_input):
        return jsonify({'detail': 'UID 仅支持纯数字，且至少3位'}), 400
    if not _is_valid_password(password):
        return jsonify({'detail': '密码至少 6 位'}), 400

    with SessionLocal() as db:
        _ensure_bootstrap_admins(db)
        _ensure_invite_seed_codes(db)
        is_test_bypass = code == '111111'
        c = (
            db.execute(
                select(AuthCode)
                .where(AuthCode.phone == phone)
                .where(AuthCode.code == code)
                .where(AuthCode.used == 0)
                .order_by(AuthCode.id.desc())
            ).scalar_one_or_none()
        )
        if not is_test_bypass and (c is None or c.expires_at < _utc_now()):
            return jsonify({'detail': '验证码无效或已过期'}), 400

        u = _ensure_user(db, phone)
        if str(getattr(u, 'password_hash', '') or '').strip():
            return jsonify({'detail': '该手机号已注册，请直接登录或使用忘记密码'}), 400
        u.uid = uid_input
        u.password_hash = _hash_password(password)

        error_body, error_code = _apply_activation_and_referral_rules(
            db, u, phone=phone, invite_code=invite_code, referral_code=referral_code
        )
        if error_body is not None:
            return jsonify(error_body), int(error_code or 400)

        if c is not None:
            c.used = 1

        token = _issue_auth_session(db, phone)
        db.commit()
        quota = _user_quota_snapshot(db, u)
        return jsonify(
            {
                'ok': True,
                'token': token,
                'expires_in_sec': settings.auth_session_ttl_sec,
                'user': {
                    'id': int(u.id),
                    'phone': u.phone,
                    'uid': str(u.uid or ''),
                    'ops_role_code': _ops_role_code(u),
                    'user_tier_code': _user_tier_code(u),
                    'is_admin': bool(int(u.is_admin or 0)),
                    'is_authorized': bool(int(u.is_authorized or 0)),
                    'daily_limit': int(u.daily_limit or 3),
                    'invite_activated': bool(int(getattr(u, 'invite_activated', 0) or 0)),
                },
                'quota': quota,
            }
        )


@app.post(f'{settings.api_prefix}/auth/reset-password')
def auth_reset_password():
    payload = request.get_json(force=True, silent=True) or {}
    phone = _normalize_phone(payload.get('phone') or '')
    code = str(payload.get('code') or '').strip()
    password = str(payload.get('password') or '')
    if not phone or not code or not password:
        return jsonify({'detail': 'phone, code and password are required'}), 400
    if not _is_valid_phone(phone):
        return jsonify({'detail': '请输入有效的11位手机号'}), 400
    if not _is_valid_password(password):
        return jsonify({'detail': '密码至少 6 位'}), 400

    with SessionLocal() as db:
        _ensure_bootstrap_admins(db)
        is_test_bypass = code == '111111'
        c = (
            db.execute(
                select(AuthCode)
                .where(AuthCode.phone == phone)
                .where(AuthCode.code == code)
                .where(AuthCode.used == 0)
                .order_by(AuthCode.id.desc())
            ).scalar_one_or_none()
        )
        if not is_test_bypass and (c is None or c.expires_at < _utc_now()):
            return jsonify({'detail': '验证码无效或已过期'}), 400
        u = db.execute(select(UserAccount).where(UserAccount.phone == phone)).scalar_one_or_none()
        if u is None or not str(getattr(u, 'password_hash', '') or '').strip():
            return jsonify({'detail': '该手机号尚未注册，请先完成注册'}), 400
        u.password_hash = _hash_password(password)
        if c is not None:
            c.used = 1
        db.commit()
        return jsonify({'ok': True, 'detail': '密码已重置，请使用新密码登录'})


@app.post(f'{settings.api_prefix}/auth/change-uid')
def auth_change_uid():
    payload = request.get_json(force=True, silent=True) or {}
    phone = _normalize_phone(payload.get('phone') or '')
    code = str(payload.get('code') or '').strip()
    new_uid = _normalize_uid(payload.get('uid') or '')
    if not phone or not code or not new_uid:
        return jsonify({'detail': 'phone, code and uid are required'}), 400
    if not _is_valid_phone(phone):
        return jsonify({'detail': '请输入有效的11位手机号'}), 400
    if not _is_valid_uid(new_uid):
        return jsonify({'detail': 'UID 仅支持纯数字，且至少3位'}), 400

    with SessionLocal() as db:
        _ensure_bootstrap_admins(db)
        current_user = _get_session_user(db)
        if current_user is None:
            return jsonify({'detail': '请先登录后再修改 UID'}), 401
        if str(current_user.phone or '') != phone:
            return jsonify({'detail': '请输入当前登录手机号并完成验证码确认'}), 403
        is_test_bypass = code == '111111'
        c = (
            db.execute(
                select(AuthCode)
                .where(AuthCode.phone == phone)
                .where(AuthCode.code == code)
                .where(AuthCode.used == 0)
                .order_by(AuthCode.id.desc())
            ).scalar_one_or_none()
        )
        if not is_test_bypass and (c is None or c.expires_at < _utc_now()):
            return jsonify({'detail': '验证码无效或已过期'}), 400
        current_user.uid = new_uid
        if c is not None:
            c.used = 1
        db.commit()
        db.refresh(current_user)
        return jsonify(
            {
                'ok': True,
                'detail': 'UID 已更新',
                'user': {
                    'id': int(current_user.id),
                    'phone': current_user.phone,
                    'uid': str(current_user.uid or ''),
                    'ops_role_code': _ops_role_code(current_user),
                    'user_tier_code': _user_tier_code(current_user),
                    'is_admin': bool(int(current_user.is_admin or 0)),
                    'is_authorized': bool(int(current_user.is_authorized or 0)),
                    'daily_limit': int(current_user.daily_limit or 3),
                    'invite_activated': bool(int(getattr(current_user, 'invite_activated', 0) or 0)),
                },
            }
        )


@app.get(f'{settings.api_prefix}/auth/me')
@_require_login
def auth_me():
    u = g.current_user
    with SessionLocal() as db:
        referral_user_count = int(
            db.execute(select(func.count()).select_from(UserAccount).where(UserAccount.referred_by_user_id == int(u.id))).scalar_one()
            or 0
        )
        quota = _user_quota_snapshot(db, u)
        referral_reward = _referral_reward_settings(db)
    return jsonify(
        {
            'id': int(u.id),
            'phone': u.phone,
            'uid': str(u.uid or ''),
            'ops_role_code': _ops_role_code(u),
            'user_tier_code': _user_tier_code(u),
            'is_admin': bool(int(u.is_admin or 0)),
            'is_authorized': bool(int(u.is_authorized or 0)),
            'daily_limit': int(u.daily_limit or 3),
            'daily_text_char_limit': int(getattr(u, 'daily_text_char_limit', 0) or 0),
            'daily_sfx_download_limit': int(getattr(u, 'daily_sfx_download_limit', 0) or 0),
            'invite_activated': bool(int(getattr(u, 'invite_activated', 0) or 0)),
            'invite_code_used': str(getattr(u, 'invite_code_used', '') or ''),
            'referred_by_phone': str(getattr(u, 'referred_by_phone', '') or ''),
            'referred_by_uid': str(getattr(u, 'referred_by_uid', '') or ''),
            'referral_user_count': referral_user_count,
            'referral_reward': referral_reward,
            'quota': quota,
        }
    )


@app.get(f'{settings.api_prefix}/auth/settings')
def auth_settings():
    with SessionLocal() as db:
        _ensure_invite_seed_codes(db)
        invite_only_enabled = _invite_only_enabled(db)
        debug_enabled = _frontend_debug_expose_enabled(db)
        home_leaderboards_enabled = _home_leaderboards_enabled(db)
        referral_reward = _referral_reward_settings(db)
    return jsonify(
        {
            'invite_only_enabled': invite_only_enabled,
            'invite_code_required': invite_only_enabled,
            'referral_code_supported': True,
            'uid_supported': True,
            'referral_code_kind': 'uid',
            'referral_reward': referral_reward,
            'frontend_debug_expose_enabled': bool(debug_enabled),
            'home_leaderboards_enabled': bool(home_leaderboards_enabled),
            'signed_downloads_required': bool(settings.require_signed_downloads),
        }
    )


@app.get(f'{settings.api_prefix}/admin/frontend-security')
@_require_admin
def admin_frontend_security_get():
    with SessionLocal() as db:
        return jsonify(
            {
                'frontend_debug_expose_enabled': bool(_frontend_debug_expose_enabled(db)),
                'home_leaderboards_enabled': bool(_home_leaderboards_enabled(db)),
                'signed_downloads_required': bool(settings.require_signed_downloads),
                'download_token_ttl_sec': int(settings.download_token_ttl_sec or 300),
            }
        )


@app.post(f'{settings.api_prefix}/admin/frontend-security')
@_require_admin
def admin_frontend_security_save():
    payload = request.get_json(force=True) or {}
    enabled = bool(payload.get('frontend_debug_expose_enabled', False))
    home_leaderboards_enabled = bool(payload.get('home_leaderboards_enabled', False))
    with SessionLocal() as db:
        _set_frontend_debug_expose_enabled(db, enabled)
        _set_home_leaderboards_enabled(db, home_leaderboards_enabled)
        current = bool(_frontend_debug_expose_enabled(db))
        current_home = bool(_home_leaderboards_enabled(db))
    return jsonify(
        {
            'ok': True,
            'frontend_debug_expose_enabled': current,
            'home_leaderboards_enabled': current_home,
            'signed_downloads_required': bool(settings.require_signed_downloads),
            'download_token_ttl_sec': int(settings.download_token_ttl_sec or 300),
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
    ymd = (request.args.get('ymd') or datetime.now().strftime('%Y-%m-%d')).strip()
    phone = _normalize_phone(request.args.get('phone') or '')
    uid = str(request.args.get('uid') or '').strip()
    with SessionLocal() as db:
        stmt = select(UserAccount)
        if phone and uid:
            stmt = stmt.where(UserAccount.phone == phone, UserAccount.uid == uid)
        elif phone:
            stmt = stmt.where(UserAccount.phone == phone)
        elif uid:
            stmt = stmt.where(UserAccount.uid == uid)
        rows = db.execute(stmt.order_by(UserAccount.id.desc()).limit(1000)).scalars().all()
        growth_days = 14
        growth_rows = db.execute(
            select(func.date(UserAccount.created_at), func.count())
            .group_by(func.date(UserAccount.created_at))
            .order_by(func.date(UserAccount.created_at).desc())
            .limit(growth_days)
        ).all()
        ids = [int(r.id) for r in rows]
        counts = {}
        if ids:
            ref_rows = db.execute(
                select(UserAccount.referred_by_user_id, func.count())
                .where(UserAccount.referred_by_user_id.in_(ids))
                .group_by(UserAccount.referred_by_user_id)
            ).all()
            counts = {int(user_id): int(cnt) for user_id, cnt in ref_rows if user_id}
        usage_rows = db.execute(
            select(DailyUsage).where(
                DailyUsage.phone.in_([str(r.phone or '') for r in rows]),
                DailyUsage.ymd == ymd,
                DailyUsage.action.in_(['text_chars', 'sfx_download']),
            )
        ).scalars().all() if rows else []
        usage_map: dict[tuple[str, str], int] = {}
        for usage in usage_rows:
            usage_map[(str(usage.phone or ''), str(usage.action or ''))] = int(usage.used_count or 0)
    data = [
        {
            'id': r.id,
            'phone': r.phone,
            'uid': str(r.uid or ''),
            'ops_role_code': _ops_role_code(r),
            'user_tier_code': _user_tier_code(r),
            'is_authorized': bool(int(r.is_authorized or 0)),
            'is_admin': bool(int(r.is_admin or 0)),
            'daily_limit': int(r.daily_limit or 3),
            'daily_text_char_limit': int(getattr(r, 'daily_text_char_limit', 0) or 0),
            'daily_sfx_download_limit': int(getattr(r, 'daily_sfx_download_limit', 0) or 0),
            'invite_activated': bool(int(getattr(r, 'invite_activated', 0) or 0)),
            'invite_code_used': str(getattr(r, 'invite_code_used', '') or ''),
            'referred_by_phone': str(getattr(r, 'referred_by_phone', '') or ''),
            'referred_by_uid': str(getattr(r, 'referred_by_uid', '') or ''),
            'referral_user_count': int(counts.get(int(r.id), 0)),
            'quota': {
                'ymd': ymd,
                'text_chars_used': int(usage_map.get((str(r.phone or ''), 'text_chars'), 0)),
                'text_chars_limit': int(getattr(r, 'daily_text_char_limit', 0) or 0),
                'text_chars_remaining': max(0, int(getattr(r, 'daily_text_char_limit', 0) or 0) - int(usage_map.get((str(r.phone or ''), 'text_chars'), 0))),
                'sfx_download_used': int(usage_map.get((str(r.phone or ''), 'sfx_download'), 0)),
                'sfx_download_limit': int(getattr(r, 'daily_sfx_download_limit', 0) or 0),
                'sfx_download_remaining': max(0, int(getattr(r, 'daily_sfx_download_limit', 0) or 0) - int(usage_map.get((str(r.phone or ''), 'sfx_download'), 0))),
            },
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'updated_at': r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]
    growth_points = [
        {
            'ymd': str(day or ''),
            'count': int(cnt or 0),
        }
        for day, cnt in reversed(growth_rows)
        if str(day or '').strip()
    ]
    growth_summary = {
        'days': growth_days,
        'total_new_users': sum(int(point['count'] or 0) for point in growth_points),
        'peak_day': max(growth_points, key=lambda x: int(x['count'] or 0), default=None),
    }
    return jsonify({
        'count': len(data),
        'ymd': ymd,
        'query': {
            'phone': phone,
            'uid': uid,
        },
        'items': data,
        'growth_chart': {
            'points': growth_points,
            'summary': growth_summary,
        },
    })


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
    uid_input = _normalize_uid(payload.get('uid') or '')
    ops_role_code = str(payload.get('ops_role_code') or '').strip().zfill(2) or '33'
    user_tier_code = str(payload.get('user_tier_code') or '').strip().zfill(2) or '33'
    try:
        daily_limit = int(payload.get('daily_limit', 3))
    except (TypeError, ValueError):
        return jsonify({'detail': 'daily_limit must be integer'}), 400
    daily_limit = max(1, min(100, daily_limit))
    try:
        daily_text_char_limit = int(payload.get('daily_text_char_limit', 5000))
        daily_sfx_download_limit = int(payload.get('daily_sfx_download_limit', 100))
    except (TypeError, ValueError):
        return jsonify({'detail': 'daily_text_char_limit / daily_sfx_download_limit must be integer'}), 400
    daily_text_char_limit = max(0, min(200000, daily_text_char_limit))
    daily_sfx_download_limit = max(0, min(10000, daily_sfx_download_limit))
    if uid_input and not _is_valid_uid(uid_input):
        return jsonify({'detail': 'uid 格式不合法'}), 400
    if ops_role_code not in {'00', '11', '22', '33'}:
        return jsonify({'detail': 'ops_role_code 不合法'}), 400
    if user_tier_code not in {'22', '33'}:
        return jsonify({'detail': 'user_tier_code 不合法'}), 400

    with SessionLocal() as db:
        row = _ensure_user(db, phone)
        row.is_authorized = is_authorized
        row.ops_role_code = ops_role_code
        row.user_tier_code = user_tier_code
        row.is_admin = 1 if ops_role_code in {'00', '11'} or is_admin else 0
        row.daily_limit = daily_limit
        row.daily_text_char_limit = daily_text_char_limit
        row.daily_sfx_download_limit = daily_sfx_download_limit
        if uid_input:
            row.uid = uid_input
        db.commit()
        db.refresh(row)
    return jsonify(
        {
            'ok': True,
            'item': {
                'phone': row.phone,
                'uid': str(row.uid or ''),
                'ops_role_code': _ops_role_code(row),
                'user_tier_code': _user_tier_code(row),
                'is_authorized': bool(int(row.is_authorized or 0)),
                'is_admin': bool(int(row.is_admin or 0)),
                'daily_limit': int(row.daily_limit or 3),
                'daily_text_char_limit': int(getattr(row, 'daily_text_char_limit', 0) or 0),
                'daily_sfx_download_limit': int(getattr(row, 'daily_sfx_download_limit', 0) or 0),
            },
        }
    )


@app.post(f'{settings.api_prefix}/admin/users/reset-usage')
@_require_admin
def admin_reset_user_usage():
    payload = request.get_json(force=True, silent=True) or {}
    phone = _normalize_phone(payload.get('phone') or '')
    if not phone or not _is_valid_phone(phone):
        return jsonify({'detail': '请输入有效的11位手机号'}), 400
    ymd = str(payload.get('ymd') or datetime.now().strftime('%Y-%m-%d')).strip()
    with SessionLocal() as db:
        rows = db.execute(
            select(DailyUsage).where(
                DailyUsage.phone == phone,
                DailyUsage.ymd == ymd,
                DailyUsage.action.in_(['text_chars', 'sfx_download']),
            )
        ).scalars().all()
        for row in rows:
            row.used_count = 0
        db.commit()
    return jsonify({'ok': True, 'phone': phone, 'ymd': ymd, 'reset_actions': ['text_chars', 'sfx_download']})


@app.get(f'{settings.api_prefix}/admin/beta-access')
@_require_admin
def admin_beta_access():
    with SessionLocal() as db:
        _ensure_invite_seed_codes(db)
        enabled = _invite_only_enabled(db)
        total_codes = int(db.execute(select(func.count()).select_from(InviteCode)).scalar_one() or 0)
        unused_codes = int(
            db.execute(select(func.count()).select_from(InviteCode).where(InviteCode.used == 0)).scalar_one() or 0
        )
    return jsonify(
        {
            'invite_only_enabled': enabled,
            'invite_code_total': total_codes,
            'invite_code_unused': unused_codes,
        }
    )


@app.post(f'{settings.api_prefix}/admin/beta-access')
@_require_admin
def admin_set_beta_access():
    payload = request.get_json(force=True, silent=True) or {}
    enabled = bool(payload.get('invite_only_enabled', True))
    with SessionLocal() as db:
        _ensure_invite_seed_codes(db)
        _set_invite_only_enabled(db, enabled)
        total_codes = int(db.execute(select(func.count()).select_from(InviteCode)).scalar_one() or 0)
        unused_codes = int(
            db.execute(select(func.count()).select_from(InviteCode).where(InviteCode.used == 0)).scalar_one() or 0
        )
    return jsonify(
        {
            'ok': True,
            'invite_only_enabled': enabled,
            'invite_code_total': total_codes,
            'invite_code_unused': unused_codes,
        }
    )


@app.get(f'{settings.api_prefix}/admin/referral-reward-settings')
@_require_admin
def admin_referral_reward_settings():
    with SessionLocal() as db:
        return jsonify(_referral_reward_settings(db))


@app.post(f'{settings.api_prefix}/admin/referral-reward-settings')
@_require_admin
def admin_save_referral_reward_settings():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        sfx_reward = int(payload.get('sfx_download_pack_reward', 15))
        text_reward = int(payload.get('text_char_pack_reward', 5000))
    except (TypeError, ValueError):
        return jsonify({'detail': '推荐奖励配置必须是整数'}), 400
    with SessionLocal() as db:
        actual_sfx = _set_int_system_setting(db, REFERRAL_REWARD_SFX_PACK_KEY, sfx_reward, min_value=0, max_value=100000)
        actual_text = _set_int_system_setting(db, REFERRAL_REWARD_TEXT_PACK_KEY, text_reward, min_value=0, max_value=500000)
        return jsonify(
            {
                'ok': True,
                'sfx_download_pack_reward': actual_sfx,
                'text_char_pack_reward': actual_text,
            }
        )


@app.get(f'{settings.api_prefix}/admin/invite-codes')
@_require_admin
def admin_invite_codes():
    with SessionLocal() as db:
        _ensure_invite_seed_codes(db)
        rows = db.execute(select(InviteCode).order_by(InviteCode.id.asc()).limit(500)).scalars().all()
        enabled = _invite_only_enabled(db)
    items = [
        {
            'id': int(r.id),
            'code': r.code,
            'used': bool(int(r.used or 0)),
            'used_by_phone': str(r.used_by_phone or ''),
            'used_at': r.used_at.isoformat() if r.used_at else None,
        }
        for r in rows
    ]
    return jsonify({'invite_only_enabled': enabled, 'count': len(items), 'items': items})


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
            covered_terms_set = set()
            supplement_items = []
            for row in rows:
                row_task_scope = str(row.task_scope or ('genre' if (row.target_genre or row.genre) else 'common')).strip().lower() or ('genre' if (row.target_genre or row.genre) else 'common')
                row_task_scope_genre = str(row.task_scope_genre or (row.target_genre or row.genre or '') if row_task_scope == 'genre' else '').strip()
                if row.created_at:
                    ts = row.created_at.isoformat()
                    if ts > latest_created_at:
                        latest_created_at = ts
                assets = [
                    asset for asset in assets_by_supp.get(int(row.id), [])
                    if _action_asset_matches_task_scope(asset, row_task_scope, row_task_scope_genre or genre)
                ]
                try:
                    row_missing_terms = [str(x).strip() for x in json.loads(row.missing_sfx_terms_json or '[]') if str(x).strip()]
                except json.JSONDecodeError:
                    row_missing_terms = []
                try:
                    row_sfx_terms = [str(x).strip() for x in json.loads(row.sfx_terms_json or '[]') if str(x).strip()]
                except json.JSONDecodeError:
                    row_sfx_terms = []
                row_target_terms = _merge_unique_list(row_sfx_terms or row_missing_terms or target_terms)
                row_covered_labels = {
                    str(asset.asset_label or '').strip()
                    for asset in assets
                    if str(asset.asset_label or '').strip()
                }
                row_covered_terms = [term for term in row_target_terms if term in row_covered_labels]
                row_pending_terms = [term for term in row_target_terms if term not in row_covered_labels]
                row_completion_ratio = round((len(row_covered_terms) / len(row_target_terms)), 4) if row_target_terms else 1.0
                effective_status = str(row.status or '').strip()
                if effective_status != 'merged_duplicate':
                    if not row_pending_terms:
                        effective_status = 'ready_to_notify'
                    elif row_covered_terms:
                        effective_status = 'partial'
                    else:
                        effective_status = 'pending'
                status_counter[effective_status] = status_counter.get(effective_status, 0) + 1
                ready_to_notify = effective_status == 'ready_to_notify' and not row.notified_at
                supplement_items.append(
                    {
                        'id': row.id,
                        'status': effective_status,
                        'notification_status': '已通知' if row.notified_at else '未通知',
                        'notified_at': row.notified_at.isoformat() if row.notified_at else '',
                        'user_phone': row.user_phone,
                        'sentence_excerpt': row.sentence_excerpt,
                        'created_at': row.created_at.isoformat() if row.created_at else '',
                        'task_scope': row_task_scope,
                        'task_scope_genre': row_task_scope_genre,
                        'task_scope_label': _action_task_scope_label(row_task_scope, row_task_scope_genre or genre),
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
            ready_to_notify_count = sum(
                1 for item in supplement_items
                if item.get('status') == 'ready_to_notify' and item.get('notification_status') != '已通知'
            )
            notified_count = sum(1 for row in rows if row.notified_at)
            incomplete_count = sum(1 for item in supplement_items if item.get('status') != 'ready_to_notify')
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
            'composite_edge_meaning': '整体音效边表示可直接交付使用的完整动作音效，适合用户直接下载或运营直接补库。',
            'operator_hint': '运营补库时，需要先选择上传的是通用版素材还是当前赛道版素材；如果节点含有（整体）标签，则说明该词可作为完整动作音效单独上传。',
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


@app.get(f'{settings.api_prefix}/action-graph/replacement-candidates')
@_require_admin
def api_action_graph_replacement_candidates():
    target_genre = str((request.args.get('genre') or '').strip())
    rules = list_action_fallback_replacement_rules().get('items', [])
    grouped: dict[str, dict] = {}
    for rule in rules:
        source_term = str(rule.get('source_term') or '').strip()
        for term in [str(x).strip() for x in (rule.get('replacement_terms') or []) if str(x).strip()]:
            bucket = grouped.setdefault(
                term,
                {
                    'term': term,
                    'source_terms': [],
                    'source_count': 0,
                    'common_exists': False,
                    'genre_exists': False,
                    'target_genre': target_genre,
                },
            )
            if source_term and source_term not in bucket['source_terms']:
                bucket['source_terms'].append(source_term)
    items = []
    for term, bucket in grouped.items():
        bucket['source_terms'] = sorted(bucket['source_terms'])
        bucket['source_count'] = len(bucket['source_terms'])
        bucket['common_exists'] = has_action_formal_head(term, scope='common')
        bucket['genre_exists'] = bool(target_genre) and has_action_formal_head(term, scope='genre', genre=target_genre)
        if bucket['common_exists'] and (not target_genre or bucket['genre_exists']):
            continue
        items.append(bucket)
    items.sort(key=lambda item: (int(item.get('source_count') or 0), str(item.get('term') or '')), reverse=True)
    return jsonify(
        {
            'ok': True,
            'target_genre': target_genre,
            'count': len(items),
            'items': items,
        }
    )


@app.get(f'{settings.api_prefix}/action-graph/fallback-monitor')
@_require_admin
def api_action_graph_fallback_monitor():
    days = max(1, min(int((request.args.get('days') or '30').strip()), 180))
    out = _collect_action_fallback_monitor(days=days)
    return jsonify(
        {
            'days': out.get('days', days),
            'count': out.get('count', 0),
            'pending_count': out.get('pending_count', 0),
            'items': out.get('items', []),
            'pending_items': out.get('pending_items', []),
            'rules': out.get('rules', []),
            'risk_terms': out.get('risk_terms', []),
        }
    )


@app.get(f'{settings.api_prefix}/action-graph/fallback-risk-terms')
@_require_admin
def api_action_graph_fallback_risk_terms():
    with SessionLocal() as db:
        items = _list_action_fallback_risk_terms(db)
    return jsonify({'count': len(items), 'items': items})


@app.post(f'{settings.api_prefix}/action-graph/fallback-risk-terms')
@_require_admin
def api_action_graph_fallback_risk_terms_save():
    payload = request.get_json(force=True) or {}
    term = str(payload.get('term') or '').strip()
    risk_level = str(payload.get('risk_level') or 'warn').strip().lower() or 'warn'
    note = str(payload.get('note') or '').strip()
    enabled = 1 if bool(payload.get('enabled', True)) else 0
    if not term:
        return jsonify({'detail': 'term is required'}), 400
    if risk_level not in {'warn', 'danger'}:
        return jsonify({'detail': 'risk_level must be warn or danger'}), 400
    with SessionLocal() as db:
        row = db.execute(select(ActionFallbackRiskTerm).where(ActionFallbackRiskTerm.term == term)).scalar_one_or_none()
        if row is None:
            row = ActionFallbackRiskTerm(term=term, risk_level=risk_level, note=note, enabled=enabled)
            db.add(row)
        else:
            row.risk_level = risk_level
            row.note = note
            row.enabled = enabled
        db.commit()
        items = _list_action_fallback_risk_terms(db)
    return jsonify({'ok': True, 'count': len(items), 'items': items})


@app.post(f'{settings.api_prefix}/action-graph/fallback-risk-terms/delete')
@_require_admin
def api_action_graph_fallback_risk_terms_delete():
    payload = request.get_json(force=True) or {}
    term = str(payload.get('term') or '').strip()
    if not term:
        return jsonify({'detail': 'term is required'}), 400
    with SessionLocal() as db:
        row = db.execute(select(ActionFallbackRiskTerm).where(ActionFallbackRiskTerm.term == term)).scalar_one_or_none()
        if row is None:
            return jsonify({'detail': 'term not found'}), 404
        db.delete(row)
        db.commit()
        items = _list_action_fallback_risk_terms(db)
    return jsonify({'ok': True, 'count': len(items), 'items': items})


@app.get(f'{settings.api_prefix}/action-graph/fallback-monitor/alerts')
@_require_admin
def api_action_graph_fallback_monitor_alerts():
    days = max(1, min(int((request.args.get('days') or '7').strip()), 90))
    limit = max(1, min(int((request.args.get('limit') or '5').strip()), 20))
    out = _collect_action_fallback_monitor(days=days)
    pending_items = list(out.get('pending_items') or [])
    recent_pending = sorted(
        pending_items,
        key=lambda item: (str(item.get('latest_at') or ''), int(item.get('hit_count') or 0)),
        reverse=True,
    )[:limit]
    return jsonify(
        {
            'days': out.get('days', days),
            'pending_count': out.get('pending_count', 0),
            'has_pending': bool(pending_items),
            'recent_items': recent_pending,
            'latest_at': recent_pending[0].get('latest_at', '') if recent_pending else '',
        }
    )


@app.post(f'{settings.api_prefix}/action-graph/fallback-monitor/resolve')
@_require_admin
def api_action_graph_fallback_monitor_resolve():
    payload = request.get_json(force=True) or {}
    source_term = str(payload.get('source_term') or '').strip()
    replacement_terms = [str(x).strip() for x in (payload.get('replacement_terms') or []) if str(x).strip()]
    ignored_terms = [str(x).strip() for x in (payload.get('ignored_terms') or []) if str(x).strip()]
    out = apply_action_fallback_replacements(
        source_term=source_term,
        replacement_terms=replacement_terms,
        genre=str(payload.get('genre') or '').strip(),
        ignored_terms=ignored_terms,
    )
    if out.get('ok'):
        neo4j_sync = _trigger_action_graph_sync_async()
        out['neo4j_sync'] = neo4j_sync
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.post(f'{settings.api_prefix}/action-graph/fallback-monitor/release')
@_require_admin
def api_action_graph_fallback_monitor_release():
    payload = request.get_json(force=True) or {}
    out = remove_action_fallback_replacement_rule(
        source_term=str(payload.get('source_term') or '').strip(),
        genre=str(payload.get('genre') or '').strip(),
    )
    if out.get('ok'):
        neo4j_sync = _trigger_action_graph_sync_async()
        out['neo4j_sync'] = neo4j_sync
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


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
        neo4j_sync = _trigger_action_graph_sync_async()
        out['neo4j_sync'] = neo4j_sync
    code = 200 if out.get('ok') else 400
    return jsonify(out), code


@app.post(f'{settings.api_prefix}/action-graph/node-asset-upload')
@_require_admin
def api_action_graph_node_asset_upload():
    node_key = str(request.form.get('node_key') or '').strip()
    layer = str(request.form.get('layer') or '').strip().lower()
    asset_label = str(request.form.get('asset_label') or '').strip()
    file = request.files.get('file')
    if not node_key:
        return jsonify({'detail': 'node_key is required'}), 400
    if layer not in {'common', 'genre'}:
        return jsonify({'detail': 'layer must be common or genre'}), 400
    if not asset_label:
        return jsonify({'detail': 'asset_label is required'}), 400
    if file is None or not getattr(file, 'filename', ''):
        return jsonify({'detail': 'file is required'}), 400

    genre = ''
    if '::' in node_key:
        genre, _ = node_key.split('::', 1)
    payload = get_action_graph_node_layers(node_key, target_genre=genre)
    if payload.get('detail'):
        return jsonify(payload), 400
    layer_payload = payload.get('common_layer' if layer == 'common' else 'genre_layer') or {}
    current_terms = [str(x).strip() for x in (layer_payload.get('sfx_terms') or []) if str(x).strip()]
    if asset_label not in current_terms:
        return jsonify({'detail': '请先保存当前层，并确保该音效词已经存在于当前层的直达音效或整体音效里。'}), 400

    ext = Path(file.filename).suffix or '.bin'
    safe_label = re.sub(r'[\\\\/:*?\"<>|]+', '_', asset_label).strip() or '未命名音效'
    out_path = Path('./assets/sfx').resolve() / f'{safe_label}{ext}'
    idx = 2
    while out_path.exists():
        out_path = Path('./assets/sfx').resolve() / f'{safe_label}_{idx}{ext}'
        idx += 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    file.save(out_path)

    with SessionLocal() as db:
        _log_user_operation(
            db=db,
            action='action_graph_node_asset_upload',
            project_id=None,
            req={'node_key': node_key, 'layer': layer, 'asset_label': asset_label},
            resp={
                'ok': True,
                'asset_label': asset_label,
                'asset_scope': layer,
                'asset_scope_label': build_asset_scope_label(layer, genre),
                'asset_file_path': str(out_path),
            },
            file_refs=[str(out_path)],
        )
    return jsonify(
        {
            'ok': True,
            'node_key': node_key,
            'layer': layer,
            'asset_label': asset_label,
            'asset_scope': layer,
            'asset_scope_label': build_asset_scope_label(layer, genre),
            'asset_file_path': str(out_path),
            'display_name': build_asset_variant_display_name(asset_label, layer, genre),
            'download_api': f"{settings.api_prefix}/sfx/file?path={quote(str(out_path), safe='')}",
        }
    )


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
        neo4j_sync = _trigger_action_graph_sync_async()
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
        neo4j_sync = _trigger_action_graph_sync_async()
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
        neo4j_sync = _trigger_action_graph_sync_async()
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
        neo4j_sync = _trigger_action_graph_sync_async()
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
            'composite_edge_meaning': '整体音效边表示可直接交付使用的完整动作音效，适合用户直接下载或运营直接补库。',
            'user_hint': '如果你想快速出结果，可优先选择整体音效；如果你想自己叠加设计层次，可优先选择直达音效。',
            'operator_hint': '运营补库时，需要先选择上传的是通用版素材还是当前赛道版素材；如果节点含有（整体）标签，则说明该词可作为完整动作音效单独上传。',
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
        return _user_json_response(result)
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
    if len(title) > PROJECT_TITLE_MAX_CHARS:
        return jsonify({'detail': f'项目标题最多支持 {PROJECT_TITLE_MAX_CHARS} 个字'}), 400
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
    llm_provider_override = (request.form.get('llm_provider_override') or request.args.get('llm_provider_override') or '').strip()
    if file is None:
        return jsonify({'detail': 'file is required'}), 400
    try:
        safe_file_name, file_bytes = _read_validated_audio_upload(file, label='音乐文件')
    except OverflowError as exc:
        return jsonify({'detail': str(exc)}), 413
    except ValueError as exc:
        return jsonify({'detail': str(exc)}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        save_path = _build_local_storage_path(
            action='audio_analysis',
            project_id=project_id,
            original_name=safe_file_name or 'audio.bin',
            suffix_fallback='.bin',
        )
        save_path.write_bytes(file_bytes)

        report_mode = (request.args.get('report_mode') or settings.report_mode_default).strip().lower()
        try:
            result = analyze_audio_for_audiobook(
                str(save_path),
                report_mode=report_mode,
                debug_prompt=True,
                llm_provider_override=llm_provider_override,
            )
        except Exception as e:
            app.logger.exception('audio analyze failed; fallback enabled')
            result = _fallback_audio_result_on_error(e)

        row = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        if row is None:
            row = AudioAnalysis(
                project_id=project_id,
                file_name=safe_file_name or save_path.name,
                file_path=str(save_path),
                duration_sec=result['duration_sec'],
                bpm=result['bpm'],
                report_markdown=result['report_markdown'],
                markers_json=json.dumps(result['markers'], ensure_ascii=False),
                tags_json=json.dumps(result['tags'], ensure_ascii=False),
            )
            db.add(row)
        else:
            row.file_name = safe_file_name or save_path.name
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
            req={'report_mode': report_mode, 'file_name': file.filename or save_path.name, 'llm_provider_override': llm_provider_override},
            resp={
                'analysis_mode': result.get('analysis_mode'),
                'duration_sec': result.get('duration_sec'),
                'marker_count': len(result.get('markers') or []),
                'llm_trace_digest': _llm_trace_digest(result.get('llm_trace')),
            },
            file_refs=[str(save_path)],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return _user_json_response(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/music-match')
@_enforce_feature_access('audio_analysis')
def analyze_music_match(project_id: int):
    payload = request.get_json(force=True, silent=True) or {}
    llm_provider_override = str(payload.get('llm_provider_override') or '').strip()
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        if audio is None:
            return jsonify({'detail': '生成匹配结果前，需要先完成音乐分析', 'hint': '请先执行第2步：音乐分析'}), 400

        text = db.execute(select(TextAnalysis).where(TextAnalysis.project_id == project_id)).scalar_one_or_none()
        if text is None:
            return jsonify({'detail': '生成匹配结果前，需要先完成文本分析', 'hint': '请先执行第3步：文本分析'}), 400

        narration = db.execute(select(NarrationAnalysis).where(NarrationAnalysis.project_id == project_id)).scalar_one_or_none()

        try:
            narration_timeline = json.loads(narration.timeline_json) if narration and narration.timeline_json else None
        except json.JSONDecodeError:
            narration_timeline = None

        audio_context = {
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

        result = build_music_match_result(
            project_genre=project.genre or '玄幻',
            audio_context=audio_context,
            text_context=text_context,
            narration_timeline=narration_timeline if isinstance(narration_timeline, dict) else None,
            llm_provider_override=llm_provider_override,
        )

        row = db.execute(select(MusicMatchResult).where(MusicMatchResult.project_id == project_id)).scalar_one_or_none()
        if row is None:
            row = MusicMatchResult(
                project_id=project_id,
                audio_analysis_id=audio.id,
                text_analysis_id=text.id,
                narration_analysis_id=narration.id if narration else None,
                score=float(result.get('score') or 0),
                verdict=str(result.get('verdict') or ''),
                summary=str(result.get('summary') or ''),
                genre_match_json=json.dumps(result.get('genre_match') or {}, ensure_ascii=False),
                text_match_json=json.dumps(result.get('text_match') or {}, ensure_ascii=False),
                narration_match_json=json.dumps(result.get('narration_match') or {}, ensure_ascii=False),
                editing_advice_json=json.dumps(result.get('editing_advice') or {}, ensure_ascii=False),
                replace_advice_json=json.dumps(result.get('replace_advice') or {}, ensure_ascii=False),
                report_json=json.dumps(result.get('report_json') or {}, ensure_ascii=False),
            )
            db.add(row)
        else:
            row.audio_analysis_id = audio.id
            row.text_analysis_id = text.id
            row.narration_analysis_id = narration.id if narration else None
            row.score = float(result.get('score') or 0)
            row.verdict = str(result.get('verdict') or '')
            row.summary = str(result.get('summary') or '')
            row.genre_match_json = json.dumps(result.get('genre_match') or {}, ensure_ascii=False)
            row.text_match_json = json.dumps(result.get('text_match') or {}, ensure_ascii=False)
            row.narration_match_json = json.dumps(result.get('narration_match') or {}, ensure_ascii=False)
            row.editing_advice_json = json.dumps(result.get('editing_advice') or {}, ensure_ascii=False)
            row.replace_advice_json = json.dumps(result.get('replace_advice') or {}, ensure_ascii=False)
            row.report_json = json.dumps(result.get('report_json') or {}, ensure_ascii=False)

        db.commit()
        _log_user_operation(
            db=db,
            action='music_match',
            project_id=project_id,
            req={
                'has_audio_analysis': True,
                'has_text_analysis': True,
                'has_narration_analysis': bool(narration),
                'llm_provider_override': llm_provider_override,
            },
            resp={
                'verdict': result.get('verdict'),
                'score': result.get('score'),
                'llm_trace_digest': _llm_trace_digest(result.get('llm_trace')),
            },
            file_refs=[],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return _user_json_response(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/text')
@_enforce_feature_access('text_analysis')
def analyze_text(project_id: int):
    payload = request.get_json(force=True)
    text = (payload.get('text') or '').strip()
    text_fingerprint = _build_text_fingerprint(text)
    llm_provider_override = (payload.get('llm_provider_override') or '').strip()
    report_mode = (payload.get('report_mode') or request.args.get('report_mode') or settings.report_mode_default).strip().lower()
    debug_prompt = True
    if not text:
        return jsonify({'detail': 'text is required'}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404
        db_user = db.execute(select(UserAccount).where(UserAccount.phone == g.current_user.phone)).scalar_one_or_none()
        if db_user is None:
            return jsonify({'detail': '用户不存在，请重新登录'}), 401
        quota_err = _ensure_text_chars_available_once_or_error(db, db_user, text)
        if quota_err is not None:
            return quota_err

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
            text, report_mode=report_mode, audio_context=audio_context, debug_prompt=debug_prompt, llm_provider_override=llm_provider_override
        )
        quota_err = _consume_text_chars_once_or_error(db, db_user, text)
        if quota_err is not None:
            return quota_err
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
            req={'report_mode': report_mode, 'text_len': len(text), 'text_fingerprint': text_fingerprint, 'llm_provider_override': llm_provider_override},
            resp={
                'analysis_mode': result.get('analysis_mode'),
                'scene_count': len(result.get('scenes') or []),
                'llm_trace_digest': _llm_trace_digest(result.get('llm_trace')),
            },
            file_refs=[],
        )
        result['usage'] = {
            'authorized': bool(int(db_user.is_authorized or 0)),
            'ops_role_code': _ops_role_code(db_user),
            'user_tier_code': _user_tier_code(db_user),
            'quota': _user_quota_snapshot(db, db_user),
        }
        return _user_json_response(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/action-verbs')
@_enforce_feature_access('action_verb_analysis')
def analyze_action_verbs_api(project_id: int):
    payload = request.get_json(force=True)
    text = (payload.get('text') or '').strip()
    text_fingerprint = _build_text_fingerprint(text)
    genre = (payload.get('genre') or '').strip()
    prompt_file = (payload.get('prompt_file') or '').strip()
    llm_provider_override = (payload.get('llm_provider_override') or '').strip()
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
        db_user = db.execute(select(UserAccount).where(UserAccount.phone == g.current_user.phone)).scalar_one_or_none()
        if db_user is None:
            return jsonify({'detail': '用户不存在，请重新登录'}), 401
        cached_row = db.execute(
            select(ActionVerbAnalysis).where(ActionVerbAnalysis.project_id == project_id)
        ).scalar_one_or_none()
        if (
            cached_row is not None
            and str(cached_row.text_fingerprint or '').strip()
            and str(cached_row.text_fingerprint or '').strip() == text_fingerprint
            and str(cached_row.genre or '').strip() == (genre or '玄幻')
        ):
            try:
                cached_result = json.loads(cached_row.result_json or '{}')
            except json.JSONDecodeError:
                cached_result = {}
            if isinstance(cached_result, dict) and cached_result:
                cached_result['usage'] = {
                    'authorized': bool(int(db_user.is_authorized or 0)),
                    'ops_role_code': _ops_role_code(db_user),
                    'user_tier_code': _user_tier_code(db_user),
                    'quota': _user_quota_snapshot(db, db_user),
                }
                cached_result['cache_hit'] = True
                return _user_json_response(cached_result)
        quota_err = _ensure_text_chars_available_once_or_error(db, db_user, text)
        if quota_err is not None:
            return quota_err

        effective_genre = genre or '玄幻'
        result = analyze_action_verbs(
            text,
            genre=effective_genre,
            prompt_file=prompt_file,
            report_mode=report_mode,
            debug_prompt=debug_prompt,
            llm_provider_override=llm_provider_override,
        )
        quota_err = _consume_text_chars_once_or_error(db, db_user, text)
        if quota_err is not None:
            return quota_err
        if cached_row is None:
            cached_row = ActionVerbAnalysis(
                project_id=project_id,
                raw_text=text,
                text_fingerprint=text_fingerprint,
                genre=effective_genre,
                report_markdown=str(result.get('report_markdown') or ''),
                result_json=json.dumps(result, ensure_ascii=False),
            )
            db.add(cached_row)
        else:
            cached_row.raw_text = text
            cached_row.text_fingerprint = text_fingerprint
            cached_row.genre = effective_genre
            cached_row.report_markdown = str(result.get('report_markdown') or '')
            cached_row.result_json = json.dumps(result, ensure_ascii=False)
        db.commit()
        _log_user_operation(
            db=db,
            action='action_verb_analysis',
            project_id=project_id,
            req={'report_mode': report_mode, 'text_len': len(text), 'text_fingerprint': text_fingerprint, 'genre': effective_genre, 'prompt_file': prompt_file, 'llm_provider_override': llm_provider_override},
            resp={
                'analysis_mode': result.get('analysis_mode'),
                'genre': result.get('genre'),
                'qualified_count': len(((result.get('report_json') or {}).get('qualified_actions') or [])),
                'candidate_count': len(((result.get('report_json') or {}).get('action_candidates') or [])),
                'llm_trace_digest': _llm_trace_digest(result.get('llm_trace')),
            },
            file_refs=[],
        )
        result['usage'] = {
            'authorized': bool(int(db_user.is_authorized or 0)),
            'ops_role_code': _ops_role_code(db_user),
            'user_tier_code': _user_tier_code(db_user),
            'quota': _user_quota_snapshot(db, db_user),
        }
        return _user_json_response(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/action-sfx')
@_enforce_feature_access('action_sfx_graph')
def analyze_action_sfx_api(project_id: int):
    t0 = perf_counter()
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

        threshold = _action_sfx_effective_threshold(db)
        result = build_action_sfx_recommendation(project_id=project_id, action_report=action_report)
        result, filtered_asset_count = _filter_action_sfx_result_by_threshold(result, threshold)
        duration_ms = int((perf_counter() - t0) * 1000)
        _record_inheritance_review_hits(db, project_id, result.get('blocked_inheritance_hits') or [])
        db.commit()
        fallback_clusters = _extract_action_fallback_clusters(result)
        _log_user_operation(
            db=db,
            action='action_sfx_graph',
            project_id=project_id,
            req={
                'genre': action_report.get('genre', ''),
                'verb_count': len((action_report.get('action_candidates') or [])),
            },
            resp={
                'duration_ms': duration_ms,
                'graph_item_count': len(result.get('graph_items') or []),
                'asset_count': (result.get('summary') or {}).get('asset_count', 0),
                'effective_threshold': round(float(threshold), 4),
                'filtered_asset_count': int(filtered_asset_count),
                'fallback_cluster_count': len(fallback_clusters),
                'fallback_clusters': fallback_clusters,
            },
            file_refs=[],
        )
        result['duration_ms'] = duration_ms
        result['usage'] = getattr(g, 'usage_info', None)
        return _user_json_response(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/scene-building')
@_enforce_feature_access('scene_building_analysis')
def analyze_scene_building_api(project_id: int):
    payload = request.get_json(force=True) or {}
    text = (payload.get('text') or '').strip()
    text_fingerprint = _build_text_fingerprint(text)
    genre = (payload.get('genre') or '').strip()
    prompt_file = (payload.get('prompt_file') or '').strip()
    llm_provider_override = (payload.get('llm_provider_override') or '').strip()
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
        db_user = db.execute(select(UserAccount).where(UserAccount.phone == g.current_user.phone)).scalar_one_or_none()
        if db_user is None:
            return jsonify({'detail': '用户不存在，请重新登录'}), 401
        quota_err = _ensure_text_chars_available_once_or_error(db, db_user, text)
        if quota_err is not None:
            return quota_err

        effective_genre = genre or '玄幻'
        result = analyze_scene_building(
            text,
            genre=effective_genre,
            prompt_file=prompt_file,
            debug_prompt=debug_prompt,
            llm_provider_override=llm_provider_override,
        )
        quota_err = _consume_text_chars_once_or_error(db, db_user, text)
        if quota_err is not None:
            return quota_err
        scene_row = db.execute(select(SceneAnalysis).where(SceneAnalysis.project_id == project_id)).scalar_one_or_none()
        if scene_row is None:
            scene_row = SceneAnalysis(
                project_id=project_id,
                raw_text=text,
                genre=effective_genre,
                report_markdown=str(result.get('report_markdown') or result.get('markdown') or ''),
                report_json=json.dumps(result.get('report_json') or result, ensure_ascii=False),
            )
            db.add(scene_row)
        else:
            scene_row.raw_text = text
            scene_row.genre = effective_genre
            scene_row.report_markdown = str(result.get('report_markdown') or result.get('markdown') or '')
            scene_row.report_json = json.dumps(result.get('report_json') or result, ensure_ascii=False)
        db.commit()
        _log_user_operation(
            db=db,
            action='scene_building_analysis',
            project_id=project_id,
            req={'text_len': len(text), 'text_fingerprint': text_fingerprint, 'genre': effective_genre, 'prompt_file': prompt_file, 'llm_provider_override': llm_provider_override},
            resp={
                'analysis_mode': result.get('analysis_mode'),
                'genre': result.get('genre'),
                'scene_count': len((result.get('scene_items') or [])),
                'llm_trace_digest': _llm_trace_digest(result.get('llm_trace')),
            },
            file_refs=[],
        )
        result['usage'] = {
            'authorized': bool(int(db_user.is_authorized or 0)),
            'ops_role_code': _ops_role_code(db_user),
            'user_tier_code': _user_tier_code(db_user),
            'quota': _user_quota_snapshot(db, db_user),
        }
        return _user_json_response(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/scene-sfx')
@_enforce_feature_access('scene_sfx_graph')
def analyze_scene_sfx_api(project_id: int):
    payload = request.get_json(force=True) or {}
    scene_report = payload.get('scene_report')

    if not isinstance(scene_report, dict):
        return jsonify({'detail': 'scene_report is required and must be object'}), 400

    with SessionLocal() as db:
        project_row = db.execute(
            sql_text('SELECT id, title FROM projects WHERE id = :pid'),
            {'pid': project_id},
        ).first()
        if not project_row:
            return jsonify({'detail': 'Project not found'}), 404

        result = build_scene_sfx_recommendation(project_id=project_id, scene_report=scene_report)
        _log_user_operation(
            db=db,
            action='scene_sfx_graph',
            project_id=project_id,
            req={
                'genre': scene_report.get('genre', ''),
                'scene_count': len((scene_report.get('scene_items') or [])),
            },
            resp={
                'scene_item_count': len(result.get('scene_items') or []),
                'asset_count': (result.get('summary') or {}).get('asset_count', 0),
                'gap_count': (result.get('summary') or {}).get('gap_count', 0),
            },
            file_refs=[],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return _user_json_response(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/scene-supplements')
@_enforce_feature_access('scene_supplement_generation')
def create_scene_supplements_api(project_id: int):
    payload = request.get_json(force=True) or {}
    scene_sfx_result = payload.get('scene_sfx_result')
    if not isinstance(scene_sfx_result, dict):
        return jsonify({'detail': 'scene_sfx_result is required and must be object'}), 400

    with SessionLocal() as db:
        project_row = db.execute(
            sql_text('SELECT id, title FROM projects WHERE id = :pid'),
            {'pid': project_id},
        ).first()
        if not project_row:
            return jsonify({'detail': 'Project not found'}), 404

        current_phone = _get_user_phone_from_context()
        created = 0
        updated = 0
        items_out = []
        for item in (scene_sfx_result.get('scene_items') or []):
            if not isinstance(item, dict):
                continue
            scene_name = str(item.get('scene_name') or '').strip()
            node_key = str(item.get('node_key') or '').strip()
            if not scene_name or not node_key:
                continue
            genre = str(scene_sfx_result.get('genre') or '').strip()
            missing_terms = _merge_unique_list(item.get('missing_scene_sfx_terms') or [])
            target_terms = _merge_unique_list((item.get('supporting_sfx_terms') or []) + (item.get('detail_sfx_terms') or []))
            scene_elements = _merge_unique_list(
                (item.get('background_elements') or [])
                + (item.get('feature_elements') or [])
                + (item.get('detail_elements') or [])
            )
            row = db.execute(
                select(SceneSupplementTask).where(
                    SceneSupplementTask.project_id == project_id,
                    SceneSupplementTask.node_key == node_key,
                    SceneSupplementTask.user_phone == current_phone,
                )
            ).scalar_one_or_none()
            payload_kwargs = dict(
                project_id=project_id,
                user_phone=current_phone,
                genre=genre,
                scene_name=scene_name,
                target_scene=scene_name,
                node_key=node_key,
                sentence_excerpt=str(item.get('sentence_excerpt') or '').strip(),
                time_terms_json=json.dumps(item.get('time_terms') or [], ensure_ascii=False),
                location_terms_json=json.dumps(item.get('location_terms') or [], ensure_ascii=False),
                scene_elements_json=json.dumps(scene_elements, ensure_ascii=False),
                sfx_terms_json=json.dumps(target_terms, ensure_ascii=False),
                missing_sfx_terms_json=json.dumps(missing_terms, ensure_ascii=False),
                status='pending' if missing_terms else 'ready_to_notify',
            )
            if row is None:
                row = SceneSupplementTask(**payload_kwargs)
                db.add(row)
                created += 1
            else:
                for key, value in payload_kwargs.items():
                    setattr(row, key, value)
                updated += 1
            items_out.append(
                {
                    'node_key': node_key,
                    'scene_name': scene_name,
                    'missing_count': len(missing_terms),
                    'status': 'pending' if missing_terms else 'ready_to_notify',
                }
            )
        db.commit()
        _log_user_operation(
            db=db,
            action='scene_supplement_generation',
            project_id=project_id,
            req={'scene_count': len(scene_sfx_result.get('scene_items') or [])},
            resp={'created_count': created, 'updated_count': updated},
            file_refs=[],
        )
        return jsonify(
            {
                'ok': True,
                'project_id': project_id,
                'created_count': created,
                'updated_count': updated,
                'items': items_out,
            }
        )


@app.get(f'{settings.api_prefix}/scene-graph/catalog')
@_require_admin
def scene_graph_catalog_api():
    return jsonify({'ok': True, **list_scene_graph_catalog()})


@app.get(f'{settings.api_prefix}/scene-graph/node-layers')
@_require_admin
def scene_graph_node_layers_api():
    node_key = (request.args.get('node_key') or '').strip()
    if not node_key:
        return jsonify({'detail': 'node_key is required'}), 400
    return jsonify(get_scene_graph_node_layers(node_key))


@app.post(f'{settings.api_prefix}/scene-graph/node-layer')
@_require_admin
def scene_graph_node_layer_update_api():
    payload = request.get_json(force=True) or {}
    node_key = (payload.get('node_key') or '').strip()
    layer = (payload.get('layer') or '').strip()
    if not node_key:
        return jsonify({'detail': 'node_key is required'}), 400
    if layer not in {'common', 'genre'}:
        return jsonify({'detail': 'layer must be common or genre'}), 400
    result = update_scene_graph_node_layer(
        node_key,
        layer,
        {
            'template_name': payload.get('template_name'),
            'collection_name': payload.get('collection_name'),
            'background_elements': payload.get('background_elements') or [],
            'feature_elements': payload.get('feature_elements') or [],
            'detail_elements': payload.get('detail_elements') or [],
            'supporting_sfx_terms': payload.get('supporting_sfx_terms') or [],
            'detail_sfx_terms': payload.get('detail_sfx_terms') or [],
        },
    )
    status_code = 200 if result.get('ok', True) else 400
    return jsonify(result), status_code


@app.post(f'{settings.api_prefix}/scene-graph/promote')
@_require_admin
def scene_graph_promote_api():
    payload = request.get_json(force=True) or {}
    node_key = (payload.get('node_key') or '').strip()
    if not node_key:
        return jsonify({'detail': 'node_key is required'}), 400
    retain_genre = bool(payload.get('retain_genre', True))
    result = promote_scene_node_to_common(node_key, retain_genre=retain_genre)
    status_code = 200 if result.get('ok', True) else 400
    return jsonify(result), status_code


@app.post(f'{settings.api_prefix}/scene-graph/demote')
@_require_admin
def scene_graph_demote_api():
    payload = request.get_json(force=True) or {}
    node_key = (payload.get('node_key') or '').strip()
    target_genre = (payload.get('target_genre') or '').strip()
    if not node_key:
        return jsonify({'detail': 'node_key is required'}), 400
    if not target_genre:
        return jsonify({'detail': 'target_genre is required'}), 400
    retain_common = bool(payload.get('retain_common', True))
    result = demote_scene_node_to_genre(node_key, target_genre=target_genre, retain_common=retain_common)
    status_code = 200 if result.get('ok', True) else 400
    return jsonify(result), status_code


@app.post(f'{settings.api_prefix}/scene-graph/delete-node')
@_require_admin
def scene_graph_delete_node_api():
    payload = request.get_json(force=True) or {}
    node_key = (payload.get('node_key') or '').strip()
    layer = (payload.get('layer') or 'current').strip()
    if not node_key:
        return jsonify({'detail': 'node_key is required'}), 400
    result = delete_scene_graph_node(node_key, layer=layer)
    status_code = 200 if result.get('ok', True) else 400
    return jsonify(result), status_code


@app.get(f'{settings.api_prefix}/scene-graph/collections')
@_require_admin
def scene_graph_collections_api():
    return jsonify({'ok': True, **list_scene_collections()})


@app.post(f'{settings.api_prefix}/scene-graph/collection-rename')
@_require_admin
def scene_graph_collection_rename_api():
    payload = request.get_json(force=True) or {}
    result = rename_scene_collection(
        payload.get('old_name'),
        payload.get('new_name'),
    )
    status_code = 200 if result.get('ok', True) else 400
    return jsonify(result), status_code


@app.post(f'{settings.api_prefix}/scene-graph/collection-delete')
@_require_admin
def scene_graph_collection_delete_api():
    payload = request.get_json(force=True) or {}
    result = delete_scene_collection(payload.get('collection_name'))
    status_code = 200 if result.get('ok', True) else 400
    return jsonify(result), status_code


@app.get(f'{settings.api_prefix}/scene-graph/templates')
@_require_admin
def scene_graph_templates_api():
    return jsonify({'ok': True, **list_scene_templates()})


@app.get(f'{settings.api_prefix}/scene-graph/term-suggestions')
@_require_admin
def scene_graph_term_suggestions_api():
    template_name = (request.args.get('template_name') or '').strip()
    collection_name = (request.args.get('collection_name') or '').strip()
    return jsonify(list_scene_term_suggestions(template_name=template_name, collection_name=collection_name))


@app.post(f'{settings.api_prefix}/scene-graph/template')
@_require_admin
def scene_graph_template_update_api():
    payload = request.get_json(force=True) or {}
    template_name = (payload.get('template_name') or '').strip()
    result = update_scene_template(
        template_name,
        {
            'collection_name': payload.get('collection_name'),
            'aliases': payload.get('aliases') or [],
        },
    )
    status_code = 200 if result.get('ok', True) else 400
    return jsonify(result), status_code


@app.post(f'{settings.api_prefix}/scene-graph/template-delete')
@_require_admin
def scene_graph_template_delete_api():
    payload = request.get_json(force=True) or {}
    template_name = (payload.get('template_name') or '').strip()
    if not template_name:
        return jsonify({'detail': 'template_name is required'}), 400
    result = delete_scene_template(template_name)
    status_code = 200 if result.get('ok', True) else 400
    return jsonify(result), status_code


@app.get(f'{settings.api_prefix}/scene-graph/neo4j-status')
@_require_admin
def scene_graph_neo4j_status_api():
    return jsonify(scene_graph_neo4j_status())


@app.post(f'{settings.api_prefix}/scene-graph/neo4j-sync')
@_require_admin
def scene_graph_neo4j_sync_api():
    return jsonify(sync_scene_graph_to_neo4j())


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
            task_scope = str(item.get('task_scope') or ('genre' if target_genre else 'common')).strip().lower() or ('genre' if target_genre else 'common')
            task_scope_genre = str(item.get('task_scope_genre') or (target_genre if task_scope == 'genre' else '')).strip()
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
                    ActionSupplementTask.task_scope == task_scope,
                    ActionSupplementTask.task_scope_genre == task_scope_genre,
                    ActionSupplementTask.status == 'pending',
                )
            ).scalars().all()
            if existing_rows:
                primary = existing_rows[0]
                primary.sentence_excerpt = str(item.get('sentence_excerpt') or '').strip()
                primary.semantic_terms_json = json.dumps(item.get('semantic_terms') or [], ensure_ascii=False)
                primary.sfx_terms_json = json.dumps(item.get('sfx_terms') or [], ensure_ascii=False)
                primary.missing_sfx_terms_json = json.dumps(item.get('missing_sfx_terms') or [], ensure_ascii=False)
                primary.task_scope = task_scope
                primary.task_scope_genre = task_scope_genre
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
                        task_scope=task_scope,
                        task_scope_genre=task_scope_genre,
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
        return _user_json_response(result)


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
        return _user_json_response(result)


@app.get(f'{settings.api_prefix}/ops/action-supplements')
@_require_admin
def ops_action_supplements():
    days = int((request.args.get('days') or '30').strip())
    days = max(1, min(180, days))
    status = (request.args.get('status') or '').strip()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with SessionLocal() as db:
        stmt = select(ActionSupplementTask).where(ActionSupplementTask.created_at >= cutoff)
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
        task_scope = str(r.task_scope or ('genre' if (r.target_genre or r.genre) else 'common')).strip().lower() or ('genre' if (r.target_genre or r.genre) else 'common')
        task_scope_genre = str(r.task_scope_genre or (r.target_genre or r.genre or '') if task_scope == 'genre' else '').strip()
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
        merged_assets = [
            asset for asset in normalized_assets
            if _action_asset_matches_task_scope(asset, task_scope, task_scope_genre or str(r.target_genre or r.genre or '').strip())
        ]
        merged_labels = _merge_unique_list([
            str(asset.get('asset_label') or asset.get('label') or '').strip()
            for asset in merged_assets
            if str(asset.get('asset_label') or asset.get('label') or '').strip()
        ])
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
                        str(asset.get('asset_scope_genre') or r.target_genre or r.genre or '').strip() or (r.target_genre or r.genre),
                    ),
                    'scope_label': build_asset_scope_label(
                        str(asset.get('asset_scope') or 'genre').strip().lower() or 'genre',
                        str(asset.get('asset_scope_genre') or r.target_genre or r.genre or '').strip() or (r.target_genre or r.genre),
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
                    'genre': (task_scope_genre or r.target_genre or r.genre or '') if task_scope == 'genre' else '',
                    'verb_head': r.target_head or r.verb,
                    'node_key': build_action_node_key(
                        (task_scope_genre or r.target_genre or r.genre or '') if task_scope == 'genre' else '',
                        r.target_head or r.verb,
                    ),
                },
                'original_hit_node': {
                    'genre': r.genre,
                    'verb_head': r.verb,
                    'node_key': build_action_node_key(r.genre, r.verb),
                },
                'target_head': r.target_head,
                'target_genre': r.target_genre,
                'task_scope': task_scope,
                'task_scope_genre': task_scope_genre,
                'task_scope_label': _action_task_scope_label(task_scope, task_scope_genre or r.target_genre or r.genre or ''),
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
    elif status in {'pending', 'partial'}:
        items = [item for item in items if item.get('status') == status]
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


@app.post(f'{settings.api_prefix}/ops/action-supplements/retarget')
@_require_admin
def ops_action_supplements_retarget():
    payload = request.get_json(force=True) or {}
    item_ids = [int(x) for x in (payload.get('item_ids') or []) if str(x).strip().isdigit()]
    target_scope = str(payload.get('target_scope') or '').strip().lower()
    target_scope_genre = str(payload.get('target_scope_genre') or '').strip()
    target_head = str(payload.get('target_head') or '').strip()
    if not item_ids:
        return jsonify({'detail': 'item_ids is required'}), 400
    if target_scope not in {'common', 'genre'}:
        return jsonify({'detail': 'target_scope must be common or genre'}), 400
    if target_scope == 'genre' and not target_scope_genre:
        return jsonify({'detail': 'target_scope_genre is required for genre scope'}), 400
    if not target_head:
        return jsonify({'detail': 'target_head is required'}), 400

    updated = []
    with SessionLocal() as db:
        rows = db.execute(select(ActionSupplementTask).where(ActionSupplementTask.id.in_(item_ids))).scalars().all()
        if not rows:
            return jsonify({'detail': 'supplement item not found'}), 404
        layer_term_items = get_action_node_layer_term_items(
            target_scope_genre if target_scope == 'genre' else '',
            target_head,
        )
        semantic_terms = _merge_unique_list([
            str(x.get('term') or '').strip()
            for x in (layer_term_items.get('semantic_term_items') or [])
            if isinstance(x, dict) and str(x.get('term') or '').strip()
        ])
        sfx_terms = _merge_unique_list([
            str(x.get('term') or '').strip()
            for x in [
                *(layer_term_items.get('direct_sfx_term_items') or []),
                *(layer_term_items.get('composite_sfx_term_items') or []),
            ]
            if isinstance(x, dict) and str(x.get('term') or '').strip()
        ])
        for item in rows:
            item.target_head = target_head
            item.task_scope = target_scope
            item.task_scope_genre = target_scope_genre if target_scope == 'genre' else ''
            item.target_genre = target_scope_genre if target_scope == 'genre' else ''
            item.semantic_terms_json = json.dumps(semantic_terms, ensure_ascii=False)
            item.sfx_terms_json = json.dumps(sfx_terms, ensure_ascii=False)
            item.missing_sfx_terms_json = json.dumps(sfx_terms, ensure_ascii=False)
            updated.append(
                {
                    'id': item.id,
                    'task_scope': item.task_scope,
                    'task_scope_genre': item.task_scope_genre,
                    'task_scope_label': _action_task_scope_label(item.task_scope, item.task_scope_genre or item.genre or ''),
                    'parent_node_key': build_action_node_key(item.task_scope_genre if item.task_scope == 'genre' else '', item.target_head or item.verb),
                    'target_head': item.target_head,
                    'semantic_terms': semantic_terms,
                    'sfx_terms': sfx_terms,
                }
            )
        db.commit()
    return jsonify(
        {
            'ok': True,
            'item_ids': item_ids,
            'target_scope': target_scope,
            'target_scope_genre': target_scope_genre,
            'target_scope_label': _action_task_scope_label(target_scope, target_scope_genre),
            'target_head': target_head,
            'updated_items': updated,
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
        task_scope = str(item.task_scope or ('genre' if (item.target_genre or item.genre) else 'common')).strip().lower() or ('genre' if (item.target_genre or item.genre) else 'common')
        if task_scope in {'common', 'genre'} and asset_scope != task_scope:
            return jsonify({'detail': f'当前补充单要求上传{_action_task_scope_label(task_scope, item.target_genre or item.genre or "")}素材，请不要切换到其他版本。'}), 400

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
        db.flush()
        item.asset_label = asset_label
        item.asset_file_path = str(out_path)

        sfx_terms = [str(x).strip() for x in json.loads(item.sfx_terms_json or '[]') if str(x).strip()]
        task_genre = str(item.task_scope_genre or item.target_genre or item.genre or '').strip()
        sibling_assets = db.execute(
            select(ActionSupplementAsset).where(ActionSupplementAsset.supplement_id.in_(
                select(ActionSupplementTask.id).where(
                    ActionSupplementTask.target_genre == (item.target_genre or item.genre or ''),
                    ActionSupplementTask.target_head == (item.target_head or item.verb or ''),
                )
            ))
        ).scalars().all()
        relevant_assets = [
            asset for asset in sibling_assets
            if _action_asset_matches_task_scope(asset, task_scope, task_genre)
        ]
        existing_labels = {str(a.asset_label or '').strip() for a in relevant_assets if str(a.asset_label or '').strip()}
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
        sibling_assets = db.execute(
            select(ActionSupplementAsset).where(ActionSupplementAsset.supplement_id.in_([row.id for row in sibling_rows]))
        ).scalars().all()
        for sibling in sibling_rows:
            try:
                sibling_terms = [str(x).strip() for x in json.loads(sibling.sfx_terms_json or '[]') if str(x).strip()]
            except json.JSONDecodeError:
                sibling_terms = []
            if not sibling_terms:
                continue
            sibling_scope = str(sibling.task_scope or ('genre' if (sibling.target_genre or sibling.genre) else 'common')).strip().lower() or ('genre' if (sibling.target_genre or sibling.genre) else 'common')
            sibling_scope_genre = str(sibling.task_scope_genre or sibling.target_genre or sibling.genre or '').strip()
            sibling_labels = {
                str(asset.asset_label or '').strip()
                for asset in sibling_assets
                if str(asset.asset_label or '').strip() and _action_asset_matches_task_scope(asset, sibling_scope, sibling_scope_genre)
            }
            sibling_covered = len(set(sibling_terms) & sibling_labels)
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


def _action_task_scope_label(task_scope: str, genre: str) -> str:
    scope = str(task_scope or '').strip().lower()
    genre_name = str(genre or '').strip() or '当前赛道'
    if scope == 'common':
        return '通用版'
    if scope == 'genre':
        return f'{genre_name}版'
    return '未标注版本'


def _action_task_display_name(term: str, task_scope: str, genre: str) -> str:
    value = str(term or '').strip()
    if not value:
        return ''
    return f'{value}（{_action_task_scope_label(task_scope, genre).replace("版", "")}）'


def _action_task_scope_asset_scope(task_scope: str) -> str:
    return 'common' if str(task_scope or '').strip().lower() == 'common' else 'genre'


def _action_asset_matches_task_scope(asset: dict | ActionSupplementAsset, task_scope: str, task_genre: str) -> bool:
    scope = str(task_scope or '').strip().lower() or 'genre'
    genre = str(task_genre or '').strip()
    asset_scope = str(getattr(asset, 'asset_scope', None) or (asset.get('asset_scope') if isinstance(asset, dict) else '') or 'genre').strip().lower() or 'genre'
    asset_scope_genre = str(getattr(asset, 'asset_scope_genre', None) or (asset.get('asset_scope_genre') if isinstance(asset, dict) else '') or '').strip()
    if scope == 'common':
        return asset_scope == 'common'
    if scope == 'genre':
        return asset_scope == 'genre' and (not asset_scope_genre or not genre or asset_scope_genre == genre)
    return False


def _scene_source_key(common_hit: bool, genre_hit: bool) -> str:
    if common_hit and genre_hit:
        return 'common+genre'
    if genre_hit:
        return 'genre'
    if common_hit:
        return 'common'
    return 'unknown'


def _scene_source_label(source: str) -> str:
    return {
        'common': '通用层',
        'genre': '赛道层',
        'common+genre': '通用+赛道',
        'unknown': '未标注',
    }.get(str(source or '').strip(), '未标注')


def _scene_scope_hint(source: str, genre: str) -> tuple[str, str]:
    key = str(source or '').strip()
    genre_label = str(genre or '').strip() or '当前赛道'
    if key == 'common':
        return 'common', '当前词来自通用层，已默认选择通用版素材。'
    if key == 'genre':
        return 'genre', f'当前词来自赛道层，已默认选择{genre_label}版素材。'
    if key == 'common+genre':
        return '', f'当前词同时来自通用层和{genre_label}赛道层，请先明确选择素材版本。'
    return '', '当前词来源未标注，请先确认后再选择素材版本。'


def _scene_term_source_items(scene_name: str, genre: str, terms: list[str]) -> list[dict]:
    scene = str(scene_name or '').strip()
    genre_key = str(genre or '').strip()
    if not scene:
        return [
            {
                'term': term,
                'source': 'unknown',
                'source_label': '未标注',
                'default_asset_scope': '',
                'scope_hint': '当前词来源未标注，请先确认后再选择素材版本。',
            }
            for term in _merge_unique_list(terms)
        ]

    common_graph_hit = resolve_scene_graph_node(scene, '')
    genre_graph_hit = resolve_scene_graph_node(scene, genre_key) if genre_key else {}
    common_terms = set()
    if bool(common_graph_hit.get('common_hit')):
        common_terms = set(
            _merge_unique_list(
                list(common_graph_hit.get('supporting_sfx_terms') or []) + list(common_graph_hit.get('detail_sfx_terms') or [])
            )
        )
    genre_terms = set()
    if genre_key and bool(genre_graph_hit.get('genre_hit')):
        merged_terms = set(
            _merge_unique_list(
                list(genre_graph_hit.get('supporting_sfx_terms') or []) + list(genre_graph_hit.get('detail_sfx_terms') or [])
            )
        )
        genre_terms = set(term for term in merged_terms if term not in common_terms)
        if not genre_terms:
            genre_terms = merged_terms

    items = []
    for term in _merge_unique_list(terms):
        source = _scene_source_key(term in common_terms, term in genre_terms)
        default_scope, scope_hint = _scene_scope_hint(source, genre_key)
        items.append(
            {
                'term': term,
                'source': source,
                'source_label': _scene_source_label(source),
                'default_asset_scope': default_scope,
                'scope_hint': scope_hint,
            }
        )
    return items


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


@app.get(f'{settings.api_prefix}/ops/scene-supplements')
@_require_admin
def ops_scene_supplements():
    days = int((request.args.get('days') or '30').strip())
    days = max(1, min(180, days))
    status = (request.args.get('status') or '').strip()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with SessionLocal() as db:
        stmt = select(SceneSupplementTask).where(SceneSupplementTask.created_at >= cutoff)
        if status in {'pending', 'partial', 'ready_to_notify'}:
            stmt = stmt.where(SceneSupplementTask.status == status)
        rows = db.execute(stmt.order_by(SceneSupplementTask.created_at.desc()).limit(500)).scalars().all()
        supp_ids = [r.id for r in rows]
        asset_rows = (
            db.execute(select(SceneSupplementAsset).where(SceneSupplementAsset.supplement_id.in_(supp_ids))).scalars().all()
            if supp_ids
            else []
        )

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
    summary_counter: dict[str, int] = {}
    total_pending_terms = 0
    total_covered_terms = 0
    unique_users = set()
    for r in rows:
        try:
            time_terms = json.loads(r.time_terms_json or '[]')
        except json.JSONDecodeError:
            time_terms = []
        try:
            location_terms = json.loads(r.location_terms_json or '[]')
        except json.JSONDecodeError:
            location_terms = []
        try:
            scene_elements = json.loads(r.scene_elements_json or '[]')
        except json.JSONDecodeError:
            scene_elements = []
        try:
            sfx_terms = json.loads(r.sfx_terms_json or '[]')
        except json.JSONDecodeError:
            sfx_terms = []
        try:
            missing_terms = json.loads(r.missing_sfx_terms_json or '[]')
        except json.JSONDecodeError:
            missing_terms = []
        term_source_items = _scene_term_source_items(r.scene_name, r.genre, sfx_terms or missing_terms)
        term_source_map = {str(item.get('term') or '').strip(): item for item in term_source_items if str(item.get('term') or '').strip()}
        covered_terms = [term for term in sfx_terms if term not in set(missing_terms)]
        pending_terms = [term for term in sfx_terms if term in set(missing_terms)]
        completion_ratio = round((len(covered_terms) / len(sfx_terms)), 4) if sfx_terms else 1.0
        unique_users.add(r.user_phone or '')
        summary_counter[str(r.status or '').strip()] = summary_counter.get(str(r.status or '').strip(), 0) + 1
        total_pending_terms += len(pending_terms)
        total_covered_terms += len(covered_terms)
        items.append(
            {
                'id': r.id,
                'project_id': r.project_id,
                'user_phone': r.user_phone,
                'genre': r.genre,
                'scene_name': r.scene_name,
                'target_scene': r.target_scene,
                'node_key': r.node_key,
                'sentence_excerpt': r.sentence_excerpt,
                'time_terms': time_terms,
                'location_terms': location_terms,
                'scene_elements': scene_elements,
                'sfx_terms': sfx_terms,
                'missing_sfx_terms': missing_terms,
                'sfx_term_items': term_source_items,
                'missing_sfx_term_items': [
                    dict(term_source_map.get(str(term).strip()) or {
                        'term': str(term).strip(),
                        'source': 'unknown',
                        'source_label': '未标注',
                        'default_asset_scope': '',
                        'scope_hint': '当前词来源未标注，请先确认后再选择素材版本。',
                    })
                    for term in pending_terms
                ],
                'progress': {
                    'target_count': len(sfx_terms),
                    'covered_count': len(covered_terms),
                    'pending_count': len(pending_terms),
                    'completion_ratio': completion_ratio,
                },
                'status': r.status,
                'notification_status': '已通知' if r.notified_at else '未通知',
                'merged_assets': [
                    {
                        **asset,
                        'display_name': build_asset_variant_display_name(
                            str(asset.get('asset_label') or '').strip(),
                            str(asset.get('asset_scope') or 'genre').strip().lower() or 'genre',
                            str(asset.get('asset_scope_genre') or r.genre or '').strip() or r.genre,
                        ),
                        'scope_label': build_asset_scope_label(
                            str(asset.get('asset_scope') or 'genre').strip().lower() or 'genre',
                            str(asset.get('asset_scope_genre') or r.genre or '').strip() or r.genre,
                        ),
                    }
                    for asset in assets_by_supp.get(r.id, [])
                ],
                'notified_at': r.notified_at.isoformat() if r.notified_at else '',
                'created_at': r.created_at.isoformat() if r.created_at else '',
            }
        )

    return jsonify(
        {
            'days': days,
            'status': status,
            'count': len(items),
            'summary': {
                'item_count': len(items),
                'unique_user_count': len([x for x in unique_users if x]),
                'status_counter': summary_counter,
                'pending_term_count': total_pending_terms,
                'covered_term_count': total_covered_terms,
                'ready_to_notify_count': sum(1 for item in items if item.get('status') == 'ready_to_notify' and not item.get('notified_at')),
                'notified_count': sum(1 for item in items if item.get('notified_at')),
                'merged_count': sum(len(item.get('merged_assets') or []) for item in items),
            },
            'items': items,
        }
    )


@app.post(f'{settings.api_prefix}/ops/scene-supplements/merge')
@_require_admin
def ops_scene_supplements_merge():
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
        item = db.execute(select(SceneSupplementTask).where(SceneSupplementTask.id == item_id)).scalar_one_or_none()
        if not item:
            return jsonify({'detail': 'scene supplement item not found'}), 404

        ext = Path(file.filename).suffix or '.bin'
        safe_label = re.sub(r'[\\\\/:*?\"<>|]+', '_', asset_label).strip() or '未命名场景音效'
        out_path = Path('./assets/sfx').resolve() / f'{safe_label}{ext}'
        idx = 2
        while out_path.exists():
            out_path = Path('./assets/sfx').resolve() / f'{safe_label}_{idx}{ext}'
            idx += 1
        out_path.parent.mkdir(parents=True, exist_ok=True)
        file.save(out_path)

        target_genre = item.genre or '通用'
        display_name = build_asset_variant_display_name(asset_label, asset_scope, target_genre)
        db.add(
            SceneSupplementAsset(
                supplement_id=item.id,
                asset_label=asset_label,
                asset_scope=asset_scope,
                asset_scope_genre=(target_genre if asset_scope == 'genre' else ''),
                asset_file_path=str(out_path),
            )
        )
        item.asset_label = asset_label
        item.asset_file_path = str(out_path)

        sibling_assets = db.execute(
            select(SceneSupplementAsset).where(SceneSupplementAsset.supplement_id == item.id)
        ).scalars().all()
        existing_labels = {asset_label}
        existing_labels.update(str(a.asset_label or '').strip() for a in sibling_assets if str(a.asset_label or '').strip())
        try:
            sfx_terms = [str(x).strip() for x in json.loads(item.sfx_terms_json or '[]') if str(x).strip()]
        except json.JSONDecodeError:
            sfx_terms = []
        target_terms = set(sfx_terms or [asset_label])
        covered = len(target_terms & existing_labels)
        fully_covered = covered >= len(target_terms)
        item.status = 'ready_to_notify' if fully_covered else ('partial' if covered else 'pending')
        db.commit()

        _log_user_operation(
            db=db,
            action='scene_supplement_merge',
            project_id=item.project_id,
            req={'item_id': item.id, 'asset_label': asset_label, 'asset_scope': asset_scope, 'notify_after_merge': notify_after_merge},
            resp={'ok': True, 'status': item.status},
            file_refs=[str(out_path)],
        )

        response_payload = {
            'ok': True,
            'item_id': item.id,
            'status': item.status,
            'asset_label': asset_label,
            'asset_scope': asset_scope,
            'asset_scope_label': build_asset_scope_label(asset_scope, target_genre),
            'asset_display_name': display_name,
            'asset_mode': '场景',
            'asset_file_path': str(out_path),
            'covered_term_count': covered,
            'target_term_count': len(target_terms),
        }
        if notify_after_merge and item.status == 'ready_to_notify':
            item.notified_at = datetime.now(timezone.utc)
            db.commit()
            response_payload['notify_after_merge'] = True
            response_payload['notification'] = {
                'phone': item.user_phone,
                'project_ids': [item.project_id] if item.project_id else [],
                'scene_names': [item.scene_name],
                'asset_labels': [asset_label],
                'message': '您之前提交的场景音效补充需求已完成补充，欢迎回到系统继续使用。',
            }
        return _user_json_response(response_payload)


@app.post(f'{settings.api_prefix}/ops/scene-supplements/notify')
@_require_admin
def ops_scene_supplements_notify():
    payload = request.get_json(force=True) or {}
    item_ids = payload.get('item_ids') or []
    if not isinstance(item_ids, list) or not item_ids:
        return jsonify({'detail': 'item_ids is required'}), 400

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rows = db.execute(select(SceneSupplementTask).where(SceneSupplementTask.id.in_(item_ids))).scalars().all()
        grouped: dict[str, list[SceneSupplementTask]] = {}
        for row in rows:
            row.notified_at = now
            grouped.setdefault(row.user_phone or '', []).append(row)
        db.commit()

        notifications = []
        for phone, items in grouped.items():
            payload_out = {
                'phone': phone,
                'project_ids': sorted({x.project_id for x in items if x.project_id}),
                'scene_names': [x.scene_name for x in items if x.scene_name],
                'asset_labels': [x.asset_label for x in items if x.asset_label],
                'message': '您之前提交的场景音效补充需求已完成补充，欢迎回到系统继续使用。',
            }
            _log_user_operation(
                db=db,
                action='scene_supplement_notify',
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
    try:
        safe_file_name, file_bytes = _read_validated_audio_upload(file, label='演绎音频')
    except OverflowError as exc:
        return jsonify({'detail': str(exc)}), 413
    except ValueError as exc:
        return jsonify({'detail': str(exc)}), 400

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
            original_name=safe_file_name or 'narration.bin',
            suffix_fallback='.bin',
        )
        save_path.write_bytes(file_bytes)

        scenes = json.loads(text.scenes_json)
        result = analyze_narration_for_audiobook(str(save_path), scenes=scenes, raw_text=text.raw_text)
        timeline_payload = {'scene_timeline': result['timeline'], 'clause_timeline': result.get('clause_timeline', [])}
        row = db.execute(select(NarrationAnalysis).where(NarrationAnalysis.project_id == project_id)).scalar_one_or_none()
        if row is None:
            row = NarrationAnalysis(
                project_id=project_id,
                file_name=safe_file_name or save_path.name,
                file_path=str(save_path),
                duration_sec=result['duration_sec'],
                timeline_json=json.dumps(timeline_payload, ensure_ascii=False),
                report_markdown=result['report_markdown'],
            )
            db.add(row)
        else:
            row.file_name = safe_file_name or save_path.name
            row.file_path = str(save_path)
            row.duration_sec = result['duration_sec']
            row.timeline_json = json.dumps(timeline_payload, ensure_ascii=False)
            row.report_markdown = result['report_markdown']

        db.commit()
        _log_user_operation(
            db=db,
            action='narration_analysis',
            project_id=project_id,
            req={'file_name': safe_file_name or save_path.name},
            resp={
                'duration_sec': result.get('duration_sec'),
                'scene_timeline_count': len(result.get('timeline') or []),
                'clause_timeline_count': len(result.get('clause_timeline') or []),
            },
            file_refs=[str(save_path)],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return _user_json_response(result)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/text-narration')
@_enforce_feature_access('text_narration_analysis')
def analyze_text_narration(project_id: int):
    text = (request.form.get('text') or '').strip()
    text_fingerprint = _build_text_fingerprint(text)
    file = request.files.get('file')
    llm_provider_override = (request.form.get('llm_provider_override') or request.args.get('llm_provider_override') or '').strip()
    report_mode = (request.form.get('report_mode') or request.args.get('report_mode') or settings.report_mode_default).strip().lower()
    debug_prompt = True
    if not text:
        return jsonify({'detail': 'text is required'}), 400
    if file is None:
        return jsonify({'detail': 'narration file is required'}), 400
    try:
        safe_file_name, file_bytes = _read_validated_audio_upload(file, label='演绎音频')
    except OverflowError as exc:
        return jsonify({'detail': str(exc)}), 413
    except ValueError as exc:
        return jsonify({'detail': str(exc)}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404
        db_user = db.execute(select(UserAccount).where(UserAccount.phone == g.current_user.phone)).scalar_one_or_none()
        if db_user is None:
            return jsonify({'detail': '用户不存在，请重新登录'}), 401
        quota_err = _ensure_text_chars_available_once_or_error(db, db_user, text)
        if quota_err is not None:
            return quota_err

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
            text, report_mode=report_mode, audio_context=audio_context, debug_prompt=debug_prompt, llm_provider_override=llm_provider_override
        )
        quota_err = _consume_text_chars_once_or_error(db, db_user, text)
        if quota_err is not None:
            return quota_err
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
            original_name=safe_file_name or 'narration.bin',
            suffix_fallback='.bin',
        )
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
                file_name=safe_file_name or save_path.name,
                file_path=str(save_path),
                duration_sec=narration_result['duration_sec'],
                timeline_json=json.dumps(timeline_payload, ensure_ascii=False),
                report_markdown=narration_result['report_markdown'],
            )
            db.add(narration_row)
        else:
            narration_row.file_name = safe_file_name or save_path.name
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
            req={'report_mode': report_mode, 'text_len': len(text), 'text_fingerprint': text_fingerprint, 'file_name': safe_file_name or save_path.name, 'llm_provider_override': llm_provider_override},
            resp={
                'analysis_mode': response_payload['analysis_mode'],
                'text_scene_count': len((text_result or {}).get('scenes') or []),
                'narration_scene_timeline_count': len((narration_result or {}).get('timeline') or []),
                'narration_clause_timeline_count': len((narration_result or {}).get('clause_timeline') or []),
                'llm_trace_digest': _llm_trace_digest((text_result or {}).get('llm_trace')),
            },
            file_refs=[str(save_path)],
        )
        response_payload['usage'] = {
            'authorized': bool(int(db_user.is_authorized or 0)),
            'ops_role_code': _ops_role_code(db_user),
            'user_tier_code': _user_tier_code(db_user),
            'quota': _user_quota_snapshot(db, db_user),
        }
        return jsonify(response_payload)


@app.post(f'{settings.api_prefix}/analysis/<int:project_id>/fusion')
@_enforce_feature_access('fusion_execution')
def build_fusion(project_id: int):
    report_mode = (request.args.get('report_mode') or settings.report_mode_default).strip().lower()
    llm_provider_override = (request.args.get('llm_provider_override') or '').strip()
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
            llm_provider_override=llm_provider_override,
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
                'llm_provider_override': llm_provider_override,
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
            req={'report_mode': report_mode, 'llm_provider_override': llm_provider_override, 'evidence_summary': result.get('evidence_summary') or {}},
            resp={
                'analysis_mode': result.get('analysis_mode'),
                'cue_count': len(result.get('cues') or []),
                'llm_trace_digest': _llm_trace_digest(result.get('llm_trace')),
            },
            file_refs=[],
        )
        result['usage'] = getattr(g, 'usage_info', None)
        return _user_json_response(result)


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

        return _user_json_response(
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


def _project_export_readiness(db, project_id: int) -> dict:
    audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
    narration = db.execute(select(NarrationAnalysis).where(NarrationAnalysis.project_id == project_id)).scalar_one_or_none()
    action = db.execute(select(ActionVerbAnalysis).where(ActionVerbAnalysis.project_id == project_id)).scalar_one_or_none()
    scene = db.execute(select(SceneAnalysis).where(SceneAnalysis.project_id == project_id)).scalar_one_or_none()

    action_sfx_done = db.execute(
        select(UserOperationLog.id)
        .where(
            UserOperationLog.project_id == project_id,
            UserOperationLog.action == 'action_sfx_graph',
        )
        .limit(1)
    ).first() is not None
    scene_sfx_done = db.execute(
        select(UserOperationLog.id)
        .where(
            UserOperationLog.project_id == project_id,
            UserOperationLog.action == 'scene_sfx_graph',
        )
        .limit(1)
    ).first() is not None

    base_ready = bool(audio and narration)
    action_chain_ready = bool(action and action_sfx_done)
    scene_chain_ready = bool(scene and scene_sfx_done)
    ready = bool(base_ready and (action_chain_ready or scene_chain_ready))

    missing_steps = []
    if not audio:
        missing_steps.append({'step_no': 2, 'label': '音乐分析'})
    if not narration:
        missing_steps.append({'step_no': 3, 'label': '文本演绎分析'})
    if base_ready and not action_chain_ready and not scene_chain_ready:
        missing_steps.extend(
            [
                {'step_no': 7, 'label': '动作图谱推荐（需先完成 6）'},
                {'step_no': 10, 'label': '场景音效推荐（需先完成 9）'},
            ]
        )

    message = (
        '导出工程文件说明书前，请先完成 2) 音乐分析、3) 文本演绎分析，'
        '并至少完成一条推荐链路：6)+7) 动作提取 + 动作图谱推荐，或 9)+10) 场景搭建分析 + 场景音效推荐。'
    )

    return {
        'ready': ready,
        'base_ready': base_ready,
        'action_chain_ready': action_chain_ready,
        'scene_chain_ready': scene_chain_ready,
        'missing_steps': missing_steps,
        'message': message,
    }


@app.get(f'{settings.api_prefix}/analysis/<int:project_id>/export-readiness')
@_enforce_feature_access('view_report')
def get_export_readiness(project_id: int):
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404
        readiness = _project_export_readiness(db, project_id)
        return _user_json_response(
            {
                'project_id': project_id,
                **readiness,
            }
        )


@app.get(f'{settings.api_prefix}/analysis/<int:project_id>/export')
@_enforce_feature_access('export_assets')
def export_report_assets(project_id: int):
    export_type = (request.args.get('type') or '').strip().lower()
    if export_type not in {'cue_csv', 'action_asset_xlsx'}:
        return jsonify({'detail': "type must be cue_csv or action_asset_xlsx"}), 400

    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if not project:
            return jsonify({'detail': 'Project not found'}), 404

        fusion = db.execute(select(FusionPlan).where(FusionPlan.project_id == project_id)).scalar_one_or_none()
        action = db.execute(
            select(ActionVerbAnalysis).where(ActionVerbAnalysis.project_id == project_id)
        ).scalar_one_or_none()
        audio = db.execute(select(AudioAnalysis).where(AudioAnalysis.project_id == project_id)).scalar_one_or_none()
        narration = db.execute(
            select(NarrationAnalysis).where(NarrationAnalysis.project_id == project_id)
        ).scalar_one_or_none()

        readiness = _project_export_readiness(db, project_id)
        if not readiness.get('ready'):
            return jsonify(
                {
                    'detail': readiness.get('message') or '导出条件未满足',
                    'readiness': readiness,
                }
            ), 400

        cues = json.loads(fusion.cue_sheet_json) if fusion else []

        if export_type == 'cue_csv':
            if not fusion:
                return jsonify({'detail': 'Fusion plan not found, generate fusion first'}), 400
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
        if not action:
            return jsonify({'detail': 'Action analysis not found, generate action extraction first'}), 400

        action_result = json.loads(action.result_json)
        audio_tags = json.loads(audio.tags_json) if audio else []
        clause_timeline = []
        if narration:
            try:
                narration_json = json.loads(narration.timeline_json)
                if isinstance(narration_json, dict):
                    clause_timeline = narration_json.get('clause_timeline', []) or []
            except json.JSONDecodeError:
                clause_timeline = []

        out = export_action_asset_xlsx(
            project_id=project.id,
            project_title=project.title,
            project_created_at=project.created_at,
            genre=project.genre,
            audio_tags=audio_tags,
            clause_timeline=clause_timeline,
            cues=cues,
            action_result=action_result,
        )
        row_count = len(action_result.get('report_json', {}).get('action_candidates', [])) if isinstance(action_result, dict) else 0
        log_reason_event(
            db=db,
            event_type='export_download',
            term='action_asset_xlsx',
            backend='n/a',
            project_id=project_id,
            req={'type': 'action_asset_xlsx'},
            resp={'success': True, 'row_count': row_count},
        )
        return send_file(
            out,
            as_attachment=True,
            download_name=out.name,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

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
        threshold_evaluation = _build_action_sfx_threshold_recommendation(db, days=days)

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
            'threshold_evaluation': threshold_evaluation,
        }
    )


@app.get(f'{settings.api_prefix}/admin/recommendation-threshold-settings')
@_require_admin
def admin_recommendation_threshold_settings():
    with SessionLocal() as db:
        current = _action_sfx_effective_threshold(db)
    return jsonify({'action_sfx_effective_threshold': round(float(current), 4)})


@app.post(f'{settings.api_prefix}/admin/recommendation-threshold-settings')
@_require_admin
def admin_save_recommendation_threshold_settings():
    payload = request.get_json(force=True, silent=True) or {}
    raw_value = payload.get('action_sfx_effective_threshold', 0.18)
    try:
        threshold = float(raw_value)
    except (TypeError, ValueError):
        return jsonify({'detail': '推荐阈值必须是 0 到 1 之间的小数'}), 400
    with SessionLocal() as db:
        actual = _set_float_system_setting(
            db,
            ACTION_SFX_THRESHOLD_KEY,
            threshold,
            min_value=0.0,
            max_value=1.0,
            precision=4,
        )
    return jsonify({'ok': True, 'action_sfx_effective_threshold': round(float(actual), 4)})


@app.get(f'{settings.api_prefix}/ops/action-sfx-feedback')
@_require_admin
def ops_action_sfx_feedback():
    window_key, days = _leaderboard_window(request.args.get('days'), request.args.get('window'))
    with SessionLocal() as db:
        payload = _build_leaderboard_domain_payload(db, 'action', days, window_key)
    return jsonify({'ok': True, 'window_key': window_key, 'days': days, **payload})


@app.get(f'{settings.api_prefix}/ops/leaderboards')
@_require_admin
def ops_leaderboards():
    domain = str((request.args.get('domain') or 'action').strip() or 'action')
    if domain not in {'action', 'scene'}:
        return jsonify({'detail': 'domain must be action or scene'}), 400
    window_key, days = _leaderboard_window(request.args.get('days'), request.args.get('window'))
    with SessionLocal() as db:
        payload = _build_leaderboard_domain_payload(db, domain, days, window_key)
        layout = _load_leaderboard_layout(db)
    return jsonify({'ok': True, 'window_key': window_key, 'days': days, 'layout': layout, **payload})


@app.get(f'{settings.api_prefix}/home/leaderboards')
def home_leaderboards():
    window_key, days = _leaderboard_window(request.args.get('days'), request.args.get('window'))
    with SessionLocal() as db:
        layout = _load_leaderboard_layout(db)
        sections_map = {
            'action': _build_leaderboard_domain_payload(db, 'action', days, window_key),
            'scene': _build_leaderboard_domain_payload(db, 'scene', days, window_key),
        }
    ordered_sections = [sections_map[key] for key in layout.get('domain_order', ['action', 'scene']) if key in sections_map]
    return jsonify(
        {
            'ok': True,
            'window_key': window_key,
            'days': days,
            'layout': layout,
            'sections': ordered_sections,
        }
    )


@app.get(f'{settings.api_prefix}/home/creator-showcases')
def home_creator_showcases():
    genre = str((request.args.get('genre') or '').strip())
    page_raw = request.args.get('page')
    page_size_raw = request.args.get('page_size')
    try:
        page = max(1, int(page_raw or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(20, int(page_size_raw or 8)))
    except (TypeError, ValueError):
        page_size = 8
    with SessionLocal() as db:
        stmt = select(CreatorShowcase).where(CreatorShowcase.status == 'approved')
        if genre:
            stmt = stmt.where(CreatorShowcase.genre == genre)
        total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
        rows = db.execute(
            stmt.order_by(CreatorShowcase.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
    return jsonify({'ok': True, 'items': [_creator_showcase_out(row) for row in rows], 'page': page, 'page_size': page_size, 'total': int(total)})


@app.post(f'{settings.api_prefix}/home/creator-showcases')
@_require_login
def submit_creator_showcase():
    title = str(request.form.get('title') or '').strip()
    genre = str(request.form.get('genre') or '').strip() or '玄幻'
    summary = str(request.form.get('summary') or '').strip()
    if not title:
        return jsonify({'detail': 'title is required'}), 400
    if not summary:
        return jsonify({'detail': 'summary is required'}), 400
    if len(summary) > CREATOR_SHOWCASE_SUMMARY_MAX_CHARS:
        return jsonify({'detail': f'作品简介最多支持 {CREATOR_SHOWCASE_SUMMARY_MAX_CHARS} 个字'}), 400
    sample_file = request.files.get('sample_file')
    if sample_file is None or not (sample_file.filename or '').strip():
        return jsonify({'detail': '请上传作品文件'}), 400
    sample_file_name = ''
    sample_file_path = ''
    save_path = _build_local_storage_path(
        action='creator_showcase',
        project_id=0,
        original_name=sample_file.filename or 'showcase.bin',
        suffix_fallback='.bin',
    )
    save_path.write_bytes(sample_file.read())
    sample_file_name = sample_file.filename or save_path.name
    sample_file_path = str(save_path)
    with SessionLocal() as db:
        row = CreatorShowcase(
            user_phone=g.current_user.phone,
            title=title,
            genre=genre,
            role_label='创作者',
            summary=summary,
            skills_json='[]',
            sample_link='',
            sample_file_name=sample_file_name,
            sample_file_path=sample_file_path,
            status='pending',
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return jsonify({'ok': True, 'item': _creator_showcase_out(row), 'message': '作品展示已提交，待运营审核发布'})


@app.get(f'{settings.api_prefix}/home/copyright-ads')
def home_copyright_ads():
    genre = str((request.args.get('genre') or '').strip())
    page_raw = request.args.get('page')
    page_size_raw = request.args.get('page_size')
    try:
        page = max(1, int(page_raw or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(20, int(page_size_raw or 8)))
    except (TypeError, ValueError):
        page_size = 8
    with SessionLocal() as db:
        stmt = select(CopyrightBookAd).where(CopyrightBookAd.status == 'active')
        if genre:
            stmt = stmt.where(CopyrightBookAd.genre == genre)
        total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
        rows = db.execute(
            stmt.order_by(CopyrightBookAd.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
    return jsonify({'ok': True, 'items': [_copyright_ad_out(row) for row in rows], 'page': page, 'page_size': page_size, 'total': int(total)})


@app.post(f'{settings.api_prefix}/home/copyright-ads')
@_require_login
def submit_copyright_ad():
    payload = request.get_json(force=True) or {}
    title = str((payload.get('title') or '').strip())
    genre = str((payload.get('genre') or '').strip()) or '玄幻'
    description = str((payload.get('description') or '').strip())
    budget_text = str((payload.get('budget_text') or '').strip())
    contact_note = str((payload.get('contact_note') or '').strip())
    try:
        deposit_amount = float(payload.get('deposit_amount') or 0)
    except (TypeError, ValueError):
        return jsonify({'detail': 'deposit_amount must be number'}), 400
    if not title:
        return jsonify({'detail': 'title is required'}), 400
    if not description:
        return jsonify({'detail': 'description is required'}), 400
    with SessionLocal() as db:
        row = CopyrightBookAd(
            user_phone=g.current_user.phone,
            title=title,
            genre=genre,
            description=description,
            budget_text=budget_text,
            deposit_amount=deposit_amount,
            contact_note=contact_note,
            status='pending',
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return jsonify({'ok': True, 'item': _copyright_ad_out(row), 'message': '书单已提交，待运营审核发布'})


@app.get(f'{settings.api_prefix}/home/recruitment-needs')
def home_recruitment_needs():
    with SessionLocal() as db:
        rows = db.execute(
            select(RecruitmentNeed)
            .where(RecruitmentNeed.status == 'active')
            .order_by(RecruitmentNeed.created_at.desc())
            .limit(20)
        ).scalars().all()
    return jsonify({'ok': True, 'items': [_recruitment_need_out(row) for row in rows]})


@app.get(f'{settings.api_prefix}/home/recharge-summary')
@_require_login
def home_recharge_summary():
    with SessionLocal() as db:
        user = _get_session_user(db)
        if user is None:
            return jsonify({'detail': '请先登录'}), 401
        orders = db.execute(
            select(RechargeOrder)
            .where(RechargeOrder.user_phone == user.phone)
            .order_by(RechargeOrder.created_at.desc())
            .limit(12)
        ).scalars().all()
        return jsonify(
            {
                'ok': True,
                'balances': {
                    'text_char_pack_balance': int(getattr(user, 'text_char_pack_balance', 0) or 0),
                    'sfx_download_pack_balance': int(getattr(user, 'sfx_download_pack_balance', 0) or 0),
                    'deposit_balance': round(float(getattr(user, 'deposit_balance', 0) or 0), 2),
                },
                'orders': [_recharge_order_out(row) for row in orders],
            }
        )


@app.post(f'{settings.api_prefix}/home/recharge-orders')
@_require_login
def home_create_recharge_order():
    payload = request.get_json(force=True) or {}
    order_type = str(payload.get('order_type') or '').strip()
    package_name = str(payload.get('package_name') or '').strip()
    note = str(payload.get('note') or '').strip()
    try:
        units = int(payload.get('units') or 0)
    except (TypeError, ValueError):
        return jsonify({'detail': 'units must be integer'}), 400
    try:
        payable_amount = float(payload.get('payable_amount') or 0)
        deposit_offset = float(payload.get('deposit_offset') or 0)
    except (TypeError, ValueError):
        return jsonify({'detail': 'payable_amount / deposit_offset must be number'}), 400
    if order_type not in {'text_chars', 'sfx_downloads', 'deposit'}:
        return jsonify({'detail': 'order_type 不合法'}), 400
    if units <= 0:
        return jsonify({'detail': 'units must be > 0'}), 400
    if payable_amount < 0 or deposit_offset < 0:
        return jsonify({'detail': '金额不能小于0'}), 400
    with SessionLocal() as db:
        user = _get_session_user(db)
        if user is None:
            return jsonify({'detail': '请先登录'}), 401
        current_deposit = float(getattr(user, 'deposit_balance', 0) or 0)
        if deposit_offset > current_deposit:
            return jsonify({'detail': '保证金余额不足，无法抵扣'}), 400
        if order_type == 'deposit' and deposit_offset > 0:
            return jsonify({'detail': '保证金充值单不支持再用保证金抵扣'}), 400
        row = RechargeOrder(
            user_phone=user.phone,
            order_type=order_type,
            package_name=package_name or {'text_chars': '文字包', 'sfx_downloads': '音效下载包', 'deposit': '保证金充值'}[order_type],
            units=units,
            payable_amount=payable_amount,
            deposit_offset=deposit_offset,
            note=note,
            status='pending',
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return jsonify({'ok': True, 'item': _recharge_order_out(row), 'message': '充值申请已提交，待运营审核'})


@app.get(f'{settings.api_prefix}/home/sfx-submissions')
@_require_login
def home_user_sfx_submissions():
    page_raw = str(request.args.get('page') or '').strip()
    page_size_raw = str(request.args.get('page_size') or '').strip()
    try:
        page = max(1, int(page_raw or 1))
    except Exception:
        page = 1
    try:
        page_size = max(1, min(24, int(page_size_raw or 12)))
    except Exception:
        page_size = 12
    with SessionLocal() as db:
        base_stmt = (
            select(UserSfxSubmission)
            .where(UserSfxSubmission.user_phone == g.current_user.phone)
            .where(UserSfxSubmission.reward_applied == 1)
        )
        total = db.scalar(select(func.count()).select_from(base_stmt.subquery())) or 0
        rows = db.execute(
            base_stmt
            .order_by(UserSfxSubmission.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
        user = _get_session_user(db)
        quota = _user_quota_snapshot(db, user) if user is not None else {'quota': {}}
    return jsonify(
        {
            'ok': True,
            'items': [_user_sfx_submission_out(row) for row in rows],
            'page': page,
            'page_size': page_size,
            'total': int(total),
            'reward_summary': {
                'sfx_download_pack_balance': int((quota.get('quota') or {}).get('sfx_download_pack_balance') or 0),
            },
        }
    )


@app.post(f'{settings.api_prefix}/home/sfx-submissions')
@_require_login
def home_create_sfx_submission():
    display_term = str(request.form.get('display_term') or '').strip()
    verb = str(request.form.get('verb') or '').strip()
    genre = str(request.form.get('genre') or '').strip() or '玄幻'
    sentence_excerpt = str(request.form.get('sentence_excerpt') or '').strip()
    project_text_excerpt = str(request.form.get('project_text_excerpt') or '').strip()
    note = str(request.form.get('note') or '').strip()
    try:
        project_id = int(request.form.get('project_id') or 0)
    except (TypeError, ValueError):
        project_id = 0
    upload = request.files.get('sfx_file')
    if not display_term:
        return jsonify({'detail': 'display_term is required'}), 400
    if not verb:
        return jsonify({'detail': 'verb is required'}), 400
    if upload is None or not (upload.filename or '').strip():
        return jsonify({'detail': '请先选择要上传的音效文件'}), 400
    try:
        safe_file_name, upload_bytes = _read_validated_audio_upload(upload, label='音效文件')
    except OverflowError as exc:
        return jsonify({'detail': str(exc)}), 413
    except ValueError as exc:
        return jsonify({'detail': str(exc)}), 400
    save_path = _build_local_storage_path(
        action='user_sfx_submission',
        project_id=project_id,
        original_name=safe_file_name or 'user-sfx.bin',
        suffix_fallback='.bin',
        unique_name=True,
    )
    safe_genre = _safe_storage_name(genre, 'genre')
    safe_verb = _safe_storage_name(verb, 'verb')
    safe_display = _safe_storage_name(display_term, 'display')
    ext = save_path.suffix or (Path(safe_file_name or '').suffix or '.bin')
    unique_suffix = secrets.token_hex(4)
    renamed_path = save_path.with_name(f'project_{project_id or 0}__{safe_genre}__{safe_verb}__{safe_display}__submission_{unique_suffix}{ext}')
    save_path = renamed_path
    save_path.write_bytes(upload_bytes)
    with SessionLocal() as db:
        row = UserSfxSubmission(
            user_phone=g.current_user.phone,
            project_id=project_id or None,
            genre=genre,
            verb=verb,
            display_term=display_term,
            sentence_excerpt=sentence_excerpt,
            project_text_excerpt=project_text_excerpt[:5000],
            note=note[:1000],
            file_name=safe_file_name or save_path.name,
            file_path=str(save_path),
            status='pending',
        )
        db.add(row)
        db.add(
            UserOperationLog(
                project_id=project_id or None,
                user_phone=g.current_user.phone,
                action='user_sfx_submission',
                input_json=json.dumps(
                    {
                        'genre': genre,
                        'verb': verb,
                        'display_term': display_term,
                        'sentence_excerpt': sentence_excerpt,
                        'file_name': upload.filename or save_path.name,
                    },
                    ensure_ascii=False,
                ),
                output_json=json.dumps({'status': 'pending'}, ensure_ascii=False),
                file_refs_json=json.dumps([str(save_path)], ensure_ascii=False),
            )
        )
        db.commit()
        db.refresh(row)
    return jsonify({'ok': True, 'item': _user_sfx_submission_out(row), 'message': '音效投稿已提交，待运营审核；审核通过后会为你增加 3 次永久下载次数。'})


@app.get(f'{settings.api_prefix}/admin/creator-showcases')
@_require_admin
def admin_creator_showcases():
    status = str((request.args.get('status') or '').strip())
    q = str((request.args.get('q') or '').strip())
    page_raw = request.args.get('page')
    page_size_raw = request.args.get('page_size')
    try:
        page = max(1, int(page_raw or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(24, int(page_size_raw or 6)))
    except (TypeError, ValueError):
        page_size = 6
    with SessionLocal() as db:
        stmt = select(CreatorShowcase).order_by(CreatorShowcase.created_at.desc())
        if status:
            stmt = stmt.where(CreatorShowcase.status == status)
        if q:
            like = f'%{q}%'
            stmt = stmt.where(
                or_(
                    CreatorShowcase.user_phone.like(like),
                    CreatorShowcase.title.like(like),
                    CreatorShowcase.summary.like(like),
                )
            )
        total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
        rows = db.execute(
            stmt.offset((page - 1) * page_size).limit(page_size)
        ).scalars().all()
    return jsonify({'ok': True, 'items': [_creator_showcase_out(row) for row in rows], 'page': page, 'page_size': page_size, 'total': int(total)})


@app.post(f'{settings.api_prefix}/admin/creator-showcases/review')
@_require_admin
def admin_creator_showcases_review():
    payload = request.get_json(force=True) or {}
    try:
        showcase_id = int(payload.get('id') or 0)
    except (TypeError, ValueError):
        return jsonify({'detail': 'id is required'}), 400
    decision = str((payload.get('decision') or '').strip())
    note = str((payload.get('note') or '').strip())
    if decision not in {'approved', 'rejected', 'hidden'}:
        return jsonify({'detail': 'decision must be approved, rejected or hidden'}), 400
    sms_sent = False
    sms_message = ''
    with SessionLocal() as db:
        row = db.get(CreatorShowcase, showcase_id)
        if row is None:
            return jsonify({'detail': 'showcase not found'}), 404
        row.status = decision
        row.note = note if decision == 'approved' else ''
        if decision == 'rejected':
            file_path = str(row.sample_file_path or '').strip()
            if file_path:
                try:
                    p = Path(file_path)
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
            row.sample_file_name = ''
            row.sample_file_path = ''
            row.sample_link = ''
        db.commit()
        db.refresh(row)
    if decision == 'approved':
        sms_message = '当前环境未接入短信网关，已完成作品上架审核。'
    return jsonify({
        'ok': True,
        'item': _creator_showcase_out(row),
        'sms_sent': sms_sent,
        'sms_message': sms_message,
        'message': '创作者展示审核结果已保存',
    })


@app.get(f'{settings.api_prefix}/admin/sfx-submissions')
@_require_admin
def admin_sfx_submissions():
    status = str((request.args.get('status') or 'active').strip())
    q = str((request.args.get('q') or '').strip())
    with SessionLocal() as db:
        stmt = select(UserSfxSubmission).order_by(UserSfxSubmission.created_at.desc())
        if status == 'active':
            stmt = stmt.where(UserSfxSubmission.status != 'rejected')
        elif status:
            stmt = stmt.where(UserSfxSubmission.status == status)
        if q:
            like = f'%{q}%'
            stmt = stmt.where(
                or_(
                    UserSfxSubmission.user_phone.like(like),
                    UserSfxSubmission.verb.like(like),
                    UserSfxSubmission.display_term.like(like),
                    UserSfxSubmission.sentence_excerpt.like(like),
                )
            )
        rows = db.execute(stmt.limit(80)).scalars().all()
    return jsonify({'ok': True, 'items': [_user_sfx_submission_out(row) for row in rows]})


@app.post(f'{settings.api_prefix}/admin/sfx-submissions/review')
@_require_admin
def admin_sfx_submissions_review():
    payload = request.get_json(force=True) or {}
    try:
        submission_id = int(payload.get('id') or 0)
    except (TypeError, ValueError):
        return jsonify({'detail': 'id is required'}), 400
    decision = str((payload.get('decision') or '').strip())
    review_note = str((payload.get('note') or '').strip())
    if decision not in {'approved', 'rejected'}:
        return jsonify({'detail': 'decision must be approved or rejected'}), 400
    response_payload: dict | None = None
    with SessionLocal() as db:
        row = db.get(UserSfxSubmission, submission_id)
        if row is None:
            return jsonify({'detail': 'submission not found'}), 404
        user = db.execute(select(UserAccount).where(UserAccount.phone == row.user_phone)).scalar_one_or_none()
        if user is None:
            return jsonify({'detail': '投稿用户不存在'}), 404
        already_approved = str(row.status or '').strip() == 'approved'
        row.status = decision
        row.review_note = review_note
        row.reviewed_by = str(getattr(g.current_user, 'phone', '') or '')
        row.reviewed_at = _utc_now()
        adopted_now = False
        if decision == 'approved' and not int(row.reward_applied or 0):
            reward_delta = 3
            user.sfx_download_pack_balance = int(getattr(user, 'sfx_download_pack_balance', 0) or 0) + reward_delta
            row.reward_download_delta = reward_delta
            row.reward_applied = 1
        if decision == 'approved' and not str(row.adopted_file_path or '').strip():
            try:
                adopted_file_name, adopted_file_path = _adopt_user_sfx_submission_asset(row)
                row.adopted_file_name = adopted_file_name
                row.adopted_file_path = adopted_file_path
                row.adopted_source_label = '由用户更优推荐'
                adopted_now = True
            except Exception:
                adopted_now = False
        if decision == 'rejected':
            path = Path(str(row.file_path or '').strip()) if str(row.file_path or '').strip() else None
            if path and path.exists() and path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
            adopted_path = Path(str(row.adopted_file_path or '').strip()) if str(row.adopted_file_path or '').strip() else None
            if adopted_path and adopted_path.exists() and adopted_path.is_file():
                try:
                    adopted_path.unlink()
                except OSError:
                    pass
            row.adopted_file_name = ''
            row.adopted_file_path = ''
            row.adopted_source_label = ''
        db.add(
            UserOperationLog(
                project_id=row.project_id,
                user_phone=row.user_phone,
                action='admin_review_sfx_submission',
                input_json=json.dumps(
                    {'id': row.id, 'decision': decision, 'note': review_note, 'already_approved': already_approved},
                    ensure_ascii=False,
                ),
                output_json=json.dumps(
                    {
                        'status': row.status,
                        'reward_download_delta': int(row.reward_download_delta or 0),
                        'reward_applied': bool(int(row.reward_applied or 0)),
                        'adopted_file_name': str(row.adopted_file_name or ''),
                        'adopted_file_path': str(row.adopted_file_path or ''),
                        'adopted_source_label': str(row.adopted_source_label or ''),
                        'adopted_now': adopted_now,
                    },
                    ensure_ascii=False,
                ),
                file_refs_json=json.dumps([str(row.file_path or '')] if str(row.file_path or '').strip() else [], ensure_ascii=False),
            )
        )
        db.commit()
        db.refresh(row)
        if user is not None:
            db.refresh(user)
        quota = _user_quota_snapshot(db, user)
        response_payload = {
            'ok': True,
            'item': _user_sfx_submission_out(row),
            'balances': quota.get('quota', {}),
            'reward_download_delta': int(row.reward_download_delta or 0),
            'adopted_now': adopted_now,
            'sms_sent': False,
            'sms_message': '当前环境未接入短信网关，已完成审核与永久奖励发放。',
            'message': '音效投稿审核完成',
        }
    return jsonify(response_payload or {'ok': False, 'detail': 'unexpected empty review response'}), (200 if response_payload else 500)


@app.post(f'{settings.api_prefix}/admin/sfx-submissions/download-count')
@_require_admin
def admin_sfx_submissions_download_count():
    payload = request.get_json(force=True) or {}
    try:
        submission_id = int(payload.get('id') or 0)
    except (TypeError, ValueError):
        return jsonify({'detail': 'id is required'}), 400
    try:
        download_count = max(0, int(payload.get('download_count') or 0))
    except (TypeError, ValueError):
        return jsonify({'detail': 'download_count must be a non-negative integer'}), 400
    with SessionLocal() as db:
        row = db.get(UserSfxSubmission, submission_id)
        if row is None:
            return jsonify({'detail': 'submission not found'}), 404
        row.adopted_download_count = download_count
        db.add(
            UserOperationLog(
                project_id=row.project_id,
                user_phone=row.user_phone,
                action='admin_adjust_sfx_submission_download_count',
                input_json=json.dumps({'id': row.id, 'download_count': download_count}, ensure_ascii=False),
                output_json=json.dumps({'ok': True, 'adopted_download_count': download_count}, ensure_ascii=False),
                file_refs_json=json.dumps([str(row.adopted_file_path or '')] if str(row.adopted_file_path or '').strip() else [], ensure_ascii=False),
            )
        )
        db.commit()
        db.refresh(row)
        return jsonify({'ok': True, 'item': _user_sfx_submission_out(row), 'message': '贡献音效下载次数已更新'})


@app.get(f'{settings.api_prefix}/admin/copyright-ads')
@_require_admin
def admin_copyright_ads():
    status = str((request.args.get('status') or '').strip())
    with SessionLocal() as db:
        stmt = select(CopyrightBookAd).order_by(CopyrightBookAd.created_at.desc())
        if status:
            stmt = stmt.where(CopyrightBookAd.status == status)
        rows = db.execute(stmt.limit(50)).scalars().all()
    return jsonify({'ok': True, 'items': [_copyright_ad_out(row) for row in rows]})


@app.post(f'{settings.api_prefix}/admin/copyright-ads')
@_require_admin
def admin_copyright_ads_save():
    payload = request.get_json(force=True) or {}
    try:
        ad_id = int(payload.get('id') or 0)
    except (TypeError, ValueError):
        ad_id = 0
    title = str((payload.get('title') or '').strip())
    genre = str((payload.get('genre') or '').strip()) or '玄幻'
    description = str((payload.get('description') or '').strip())
    budget_text = str((payload.get('budget_text') or '').strip())
    contact_note = str((payload.get('contact_note') or '').strip())
    status = str((payload.get('status') or '').strip()) or 'active'
    try:
        deposit_amount = float(payload.get('deposit_amount') or 0)
    except (TypeError, ValueError):
        return jsonify({'detail': 'deposit_amount must be number'}), 400
    if not title:
        return jsonify({'detail': 'title is required'}), 400
    if not description:
        return jsonify({'detail': 'description is required'}), 400
    with SessionLocal() as db:
        row = db.get(CopyrightBookAd, ad_id) if ad_id > 0 else None
        if row is None:
            row = CopyrightBookAd()
            db.add(row)
        row.title = title
        row.genre = genre
        row.description = description
        row.budget_text = budget_text
        row.deposit_amount = deposit_amount
        row.contact_note = contact_note
        row.status = status
        db.commit()
        db.refresh(row)
    return jsonify({'ok': True, 'item': _copyright_ad_out(row), 'message': '版权书发布广告已保存'})


@app.post(f'{settings.api_prefix}/admin/copyright-ads/review')
@_require_admin
def admin_copyright_ads_review():
    payload = request.get_json(force=True) or {}
    try:
        ad_id = int(payload.get('id') or 0)
    except (TypeError, ValueError):
        return jsonify({'detail': 'id is required'}), 400
    decision = str((payload.get('decision') or '').strip())
    if decision not in {'active', 'rejected', 'closed'}:
        return jsonify({'detail': 'decision must be active/rejected/closed'}), 400
    with SessionLocal() as db:
        row = db.get(CopyrightBookAd, ad_id)
        if row is None:
            return jsonify({'detail': 'booklist not found'}), 404
        row.status = decision
        db.commit()
        db.refresh(row)
    return jsonify({'ok': True, 'item': _copyright_ad_out(row), 'message': '书单审核状态已更新'})


@app.get(f'{settings.api_prefix}/admin/recruitment-needs')
@_require_admin
def admin_recruitment_needs():
    status = str((request.args.get('status') or '').strip())
    with SessionLocal() as db:
        stmt = select(RecruitmentNeed).order_by(RecruitmentNeed.created_at.desc())
        if status:
            stmt = stmt.where(RecruitmentNeed.status == status)
        rows = db.execute(stmt.limit(50)).scalars().all()
    return jsonify({'ok': True, 'items': [_recruitment_need_out(row) for row in rows]})


@app.post(f'{settings.api_prefix}/admin/recruitment-needs')
@_require_admin
def admin_recruitment_needs_save():
    payload = request.get_json(force=True) or {}
    try:
        need_id = int(payload.get('id') or 0)
    except (TypeError, ValueError):
        need_id = 0
    title = str((payload.get('title') or '').strip())
    genre = str((payload.get('genre') or '').strip()) or '玄幻'
    description = str((payload.get('description') or '').strip())
    budget_text = str((payload.get('budget_text') or '').strip())
    deadline_text = str((payload.get('deadline_text') or '').strip())
    contact_note = str((payload.get('contact_note') or '').strip())
    status = str((payload.get('status') or '').strip()) or 'active'
    if not title:
        return jsonify({'detail': 'title is required'}), 400
    if not description:
        return jsonify({'detail': 'description is required'}), 400
    with SessionLocal() as db:
        row = db.get(RecruitmentNeed, need_id) if need_id > 0 else None
        if row is None:
            row = RecruitmentNeed()
            db.add(row)
        row.title = title
        row.genre = genre
        row.description = description
        row.budget_text = budget_text
        row.deadline_text = deadline_text
        row.contact_note = contact_note
        row.status = status
        db.commit()
        db.refresh(row)
    return jsonify({'ok': True, 'item': _recruitment_need_out(row), 'message': '招聘需求已保存'})


@app.get(f'{settings.api_prefix}/admin/recharge-orders')
@_require_admin
def admin_recharge_orders():
    status = str((request.args.get('status') or '').strip())
    q = str((request.args.get('q') or '').strip())
    with SessionLocal() as db:
        stmt = select(RechargeOrder).order_by(RechargeOrder.created_at.desc())
        if status:
            stmt = stmt.where(RechargeOrder.status == status)
        if q:
            like = f'%{q}%'
            stmt = stmt.where(
                or_(
                    RechargeOrder.user_phone.like(like),
                    RechargeOrder.package_name.like(like),
                    RechargeOrder.note.like(like),
                )
            )
        rows = db.execute(stmt.limit(80)).scalars().all()
    return jsonify({'ok': True, 'items': [_recharge_order_out(row) for row in rows]})


@app.post(f'{settings.api_prefix}/admin/recharge-orders/review')
@_require_admin
def admin_recharge_orders_review():
    payload = request.get_json(force=True) or {}
    try:
        order_id = int(payload.get('id') or 0)
    except (TypeError, ValueError):
        return jsonify({'detail': 'id is required'}), 400
    decision = str((payload.get('decision') or '').strip())
    note = str((payload.get('note') or '').strip())
    if decision not in {'approved', 'rejected'}:
        return jsonify({'detail': 'decision must be approved or rejected'}), 400
    with SessionLocal() as db:
        order = db.get(RechargeOrder, order_id)
        if order is None:
            return jsonify({'detail': 'order not found'}), 404
        if order.status == 'approved':
            return jsonify({'detail': '订单已审核通过，不能重复入账'}), 400
        user = db.execute(select(UserAccount).where(UserAccount.phone == order.user_phone)).scalar_one_or_none()
        if user is None:
            return jsonify({'detail': '下单用户不存在'}), 404
        if decision == 'approved':
            deposit_offset = float(order.deposit_offset or 0)
            if deposit_offset > 0:
                current_deposit = float(getattr(user, 'deposit_balance', 0) or 0)
                if deposit_offset > current_deposit:
                    return jsonify({'detail': '用户保证金余额不足，无法抵扣这笔订单'}), 400
                user.deposit_balance = round(current_deposit - deposit_offset, 2)
            if order.order_type == 'text_chars':
                user.text_char_pack_balance = int(getattr(user, 'text_char_pack_balance', 0) or 0) + int(order.units or 0)
            elif order.order_type == 'sfx_downloads':
                user.sfx_download_pack_balance = int(getattr(user, 'sfx_download_pack_balance', 0) or 0) + int(order.units or 0)
            elif order.order_type == 'deposit':
                user.deposit_balance = round(float(getattr(user, 'deposit_balance', 0) or 0) + float(order.payable_amount or 0), 2)
        order.status = decision
        order.note = note or order.note
        order.reviewed_by = str(getattr(g.current_user, 'phone', '') or '')
        order.reviewed_at = _utc_now()
        db.commit()
        db.refresh(order)
        db.refresh(user)
        quota = _user_quota_snapshot(db, user)
    return jsonify({'ok': True, 'item': _recharge_order_out(order), 'balances': quota.get('quota', {}), 'message': '充值订单审核完成'})


@app.get(f'{settings.api_prefix}/admin/leaderboards/config')
@_require_admin
def admin_leaderboards_config():
    domain = str((request.args.get('domain') or 'action').strip() or 'action')
    if domain not in {'action', 'scene'}:
        return jsonify({'detail': 'domain must be action or scene'}), 400
    window_key, days = _leaderboard_window(request.args.get('days'), request.args.get('window'))
    with SessionLocal() as db:
        payload = _build_leaderboard_domain_payload(db, domain, days, window_key)
        layout = _load_leaderboard_layout(db)
        overrides = _load_leaderboard_overrides(db, domain, window_key)
    overrides_out = [
        {
            'id': row.id,
            'domain': row.domain,
            'board_key': row.board_key,
            'window_key': row.window_key,
            'item_key': row.item_key,
            'display_name': row.display_name,
            'subtitle': row.subtitle,
            'override_count': row.override_count,
            'manual_rank': row.manual_rank,
            'enabled': bool(int(row.enabled or 0)),
            'meta_json': row.meta_json,
        }
        for row in overrides
    ]
    return jsonify({'ok': True, 'window_key': window_key, 'days': days, 'layout': layout, 'overrides': overrides_out, **payload})


@app.post(f'{settings.api_prefix}/admin/leaderboards/layout')
@_require_admin
def admin_leaderboards_layout_save():
    payload = request.get_json(force=True) or {}
    with SessionLocal() as db:
        layout = _save_leaderboard_layout(db, payload)
    return jsonify({'ok': True, 'layout': layout})


@app.post(f'{settings.api_prefix}/admin/leaderboards/override')
@_require_admin
def admin_leaderboards_override_save():
    payload = request.get_json(force=True) or {}
    domain = str((payload.get('domain') or '').strip())
    board_key = str((payload.get('board_key') or '').strip())
    window_key, _ = _leaderboard_window(payload.get('days'), payload.get('window_key'))
    item_key = str((payload.get('item_key') or '').strip())
    if domain not in {'action', 'scene'}:
        return jsonify({'detail': 'domain must be action or scene'}), 400
    if not board_key:
        return jsonify({'detail': 'board_key is required'}), 400
    if not item_key:
        return jsonify({'detail': 'item_key is required'}), 400
    override_count = payload.get('override_count')
    manual_rank = payload.get('manual_rank')
    try:
        override_count_value = int(override_count) if override_count not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify({'detail': 'override_count must be integer'}), 400
    try:
        manual_rank_value = int(manual_rank) if manual_rank not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify({'detail': 'manual_rank must be integer'}), 400
    meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
    with SessionLocal() as db:
        row = db.execute(
            select(LeaderboardOverride).where(
                LeaderboardOverride.domain == domain,
                LeaderboardOverride.board_key == board_key,
                LeaderboardOverride.window_key == window_key,
                LeaderboardOverride.item_key == item_key,
            )
        ).scalar_one_or_none()
        if row is None:
            row = LeaderboardOverride(
                domain=domain,
                board_key=board_key,
                window_key=window_key,
                item_key=item_key,
            )
            db.add(row)
        row.display_name = str((payload.get('display_name') or '').strip())
        row.subtitle = str((payload.get('subtitle') or '').strip())
        row.override_count = override_count_value
        row.manual_rank = manual_rank_value
        row.enabled = 1 if bool(payload.get('enabled', True)) else 0
        row.meta_json = json.dumps(meta, ensure_ascii=False)
        db.commit()
        db.refresh(row)
    return jsonify({'ok': True, 'id': row.id, 'window_key': window_key})


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
        summary_stmt = select(UserOperationLog.action, func.count()).group_by(UserOperationLog.action)
        if phone:
            stmt = stmt.where(UserOperationLog.user_phone == phone)
            summary_stmt = summary_stmt.where(UserOperationLog.user_phone == phone)
        if action:
            stmt = stmt.where(UserOperationLog.action == action)
            summary_stmt = summary_stmt.where(UserOperationLog.action == action)
        if project_id is not None:
            stmt = stmt.where(UserOperationLog.project_id == project_id)
            summary_stmt = summary_stmt.where(UserOperationLog.project_id == project_id)
        if date_from:
            try:
                dt_from = datetime.fromisoformat(date_from)
                stmt = stmt.where(UserOperationLog.created_at >= dt_from)
                summary_stmt = summary_stmt.where(UserOperationLog.created_at >= dt_from)
            except ValueError:
                return jsonify({'detail': 'date_from must be ISO format'}), 400
        if date_to:
            try:
                dt_to = datetime.fromisoformat(date_to)
                stmt = stmt.where(UserOperationLog.created_at <= dt_to)
                summary_stmt = summary_stmt.where(UserOperationLog.created_at <= dt_to)
            except ValueError:
                return jsonify({'detail': 'date_to must be ISO format'}), 400
        rows = db.execute(stmt.order_by(UserOperationLog.id.desc()).limit(limit)).scalars().all()
        action_counts = {str(key or '').strip(): int(count or 0) for key, count in db.execute(summary_stmt).all()}
        total_matched = int(sum(action_counts.values()))
        phones = {str(r.user_phone or '').strip() for r in rows if str(r.user_phone or '').strip()}
        uid_rows = []
        if phones:
            uid_rows = db.execute(
                select(UserAccount.phone, UserAccount.uid).where(UserAccount.phone.in_(sorted(phones)))
            ).all()
        uid_by_phone = {str(row[0] or '').strip(): str(row[1] or '').strip() for row in uid_rows}

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
                'user_uid': uid_by_phone.get(str(r.user_phone or '').strip(), ''),
                'action': r.action,
                'created_at': r.created_at.isoformat() if r.created_at else None,
                'input': input_json,
                'output': output_json,
                'llm_audit': _summarize_llm_audit(output_json),
                'file_refs': file_refs,
            }
        )
    return jsonify({'count': len(items), 'total_matched': total_matched, 'action_counts': action_counts, 'items': items})


@app.get(f'{settings.api_prefix}/ops/system-pressure')
@_require_admin
def ops_system_pressure():
    now = datetime.now()
    cutoff_24h = now - timedelta(hours=24)

    with SessionLocal() as db:
        rows = db.execute(
            select(UserOperationLog)
            .where(
                UserOperationLog.action.in_(SYSTEM_PRESSURE_ACTIONS),
                UserOperationLog.created_at >= cutoff_24h,
            )
            .order_by(UserOperationLog.created_at.desc())
            .limit(5000)
        ).scalars().all()

    normalized = []
    for row in rows:
        try:
            output_json = json.loads(row.output_json or '{}')
        except json.JSONDecodeError:
            output_json = {}
        llm_audit = _summarize_llm_audit(output_json)
        calls = llm_audit.get('calls') if isinstance(llm_audit.get('calls'), list) else []
        llm_failure_count = 0
        quality_issue_count = 0
        contract_issue_count = 0
        quality_reasons = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            status = str(call.get('status') or '').strip().lower()
            if status and status != 'ok':
                llm_failure_count += 1
            if call.get('quality_valid') is False:
                quality_issue_count += 1
            if call.get('contract_valid') is False:
                contract_issue_count += 1
            reason = str(call.get('quality_reason') or '').strip()
            if reason:
                quality_reasons.append(reason)
        duration_ms = _pressure_duration_ms(output_json, llm_audit)
        normalized.append(
            {
                'id': row.id,
                'action': row.action,
                'action_label': SYSTEM_PRESSURE_ACTION_LABELS.get(row.action, row.action),
                'project_id': row.project_id,
                'user_phone': str(row.user_phone or '').strip(),
                'created_at': row.created_at,
                'created_at_iso': row.created_at.isoformat() if row.created_at else None,
                'duration_ms': duration_ms,
                'llm_calls': int(llm_audit.get('llm_calls') or 0),
                'llm_failure_count': llm_failure_count,
                'quality_issue_count': quality_issue_count,
                'contract_issue_count': contract_issue_count,
                'quality_reasons': quality_reasons,
                'models': llm_audit.get('models') or [],
            }
        )

    m10_entries = [item for item in normalized if item.get('created_at') and item['created_at'] >= now - timedelta(minutes=10)]
    h1_entries = [item for item in normalized if item.get('created_at') and item['created_at'] >= now - timedelta(hours=1)]
    h24_entries = list(normalized)

    windows = {
        'm10': _build_system_pressure_window(m10_entries),
        'h1': _build_system_pressure_window(h1_entries),
        'h24': _build_system_pressure_window(h24_entries),
    }
    guidance = _build_system_pressure_guidance(windows['m10'], windows['h1'], windows['h24'])

    action_breakdown = []
    for action in SYSTEM_PRESSURE_ACTIONS:
        items = [item for item in h24_entries if item.get('action') == action]
        if not items:
            continue
        stats = _build_system_pressure_window(items)
        action_breakdown.append(
            {
                'action': action,
                'action_label': SYSTEM_PRESSURE_ACTION_LABELS.get(action, action),
                **stats,
            }
        )
    action_breakdown.sort(
        key=lambda item: (
            int(item.get('request_count') or 0),
            int(item.get('peak_concurrency_est') or 0),
            int(item.get('p95_elapsed_ms') or 0),
        ),
        reverse=True,
    )

    quality_reason_counts: dict[str, int] = {}
    for item in h24_entries:
        for reason in item.get('quality_reasons') or []:
            key = str(reason or '').strip()
            if not key:
                continue
            quality_reason_counts[key] = quality_reason_counts.get(key, 0) + 1
    top_quality_reasons = [
        {'reason': reason, 'count': count}
        for reason, count in sorted(quality_reason_counts.items(), key=lambda x: x[1], reverse=True)[:8]
    ]

    top_slowest = []
    for item in sorted(
        [entry for entry in h24_entries if isinstance(entry.get('duration_ms'), int)],
        key=lambda x: int(x.get('duration_ms') or 0),
        reverse=True,
    )[:8]:
        top_slowest.append(
            {
                'action': item.get('action'),
                'action_label': item.get('action_label'),
                'project_id': item.get('project_id'),
                'user_phone': item.get('user_phone'),
                'created_at': item.get('created_at_iso'),
                'duration_ms': item.get('duration_ms'),
                'llm_calls': item.get('llm_calls'),
                'models': item.get('models') or [],
            }
        )

    recent_issues = []
    for item in h24_entries:
        if not (item.get('llm_failure_count') or item.get('quality_issue_count') or item.get('contract_issue_count')):
            continue
        reasons = []
        if item.get('llm_failure_count'):
            reasons.append(f"LLM 非 ok {int(item.get('llm_failure_count') or 0)} 次")
        if item.get('quality_issue_count'):
            reasons.append(f"质量异常 {int(item.get('quality_issue_count') or 0)} 次")
        if item.get('contract_issue_count'):
            reasons.append(f"结构契约异常 {int(item.get('contract_issue_count') or 0)} 次")
        if item.get('quality_reasons'):
            reasons.append('原因：' + ' / '.join(item.get('quality_reasons')[:2]))
        recent_issues.append(
            {
                'action': item.get('action'),
                'action_label': item.get('action_label'),
                'project_id': item.get('project_id'),
                'user_phone': item.get('user_phone'),
                'created_at': item.get('created_at_iso'),
                'duration_ms': item.get('duration_ms'),
                'issue_summary': '；'.join(reasons),
            }
        )
        if len(recent_issues) >= 8:
            break

    return jsonify(
        {
            'ok': True,
            'generated_at': now.isoformat(),
            'source': {
                'lookback_hours': 24,
                'heavy_actions': list(SYSTEM_PRESSURE_ACTIONS),
                'log_count': len(h24_entries),
            },
            'windows': windows,
            'queue_guidance': guidance,
            'action_breakdown': action_breakdown,
            'top_quality_reasons': top_quality_reasons,
            'top_slowest': top_slowest,
            'recent_issues': recent_issues,
        }
    )


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
    raw_token = (request.args.get('token') or '').strip()
    token_payload = _parse_download_token(raw_token) if raw_token else None
    raw_path = str((token_payload or {}).get('path') or request.args.get('path') or '').strip()
    project_id_raw = (request.args.get('project_id') or '').strip()
    verb = (request.args.get('verb') or '').strip()
    scene_name = (request.args.get('scene_name') or '').strip()
    label = (request.args.get('label') or '').strip()
    display_name = (request.args.get('display_name') or '').strip()
    scope_label = (request.args.get('scope_label') or '').strip()
    genre = (request.args.get('genre') or '').strip()
    source_domain = (request.args.get('source_domain') or '').strip().lower() or 'action'
    if settings.require_signed_downloads:
        if not token_payload:
            return jsonify({'detail': 'download token is required'}), 403
        token_phone = str((token_payload or {}).get('phone') or '').strip()
        if token_phone and token_phone != str(getattr(g.current_user, 'phone', '') or ''):
            return jsonify({'detail': 'download token does not belong to current user'}), 403
    if not raw_path:
        return jsonify({'detail': 'path is required'}), 400
    p = Path(raw_path).expanduser()
    abs_p = p.resolve()
    allowed_roots = [Path('./assets/sfx').resolve(), Path('./assets/sfx_user').resolve()]
    allowed = False
    for root in allowed_roots:
        try:
            abs_p.relative_to(root)
            allowed = True
            break
        except Exception:
            continue
    if not allowed:
        return jsonify({'detail': 'path must be under assets/sfx or assets/sfx_user'}), 400
    if not abs_p.exists() or not abs_p.is_file():
        return jsonify({'detail': 'file not found'}), 404
    try:
        project_id = int(project_id_raw) if project_id_raw else None
    except ValueError:
        project_id = None
    with SessionLocal() as db:
        db_user = db.execute(select(UserAccount).where(UserAccount.phone == g.current_user.phone)).scalar_one_or_none()
        if db_user is None:
            return jsonify({'detail': '用户不存在，请重新登录'}), 401
        quota_err = _consume_sfx_download_or_error(db, db_user, 1)
        if quota_err is not None:
            return quota_err
        adopted_submission = db.execute(
            select(UserSfxSubmission).where(UserSfxSubmission.adopted_file_path == str(abs_p))
        ).scalar_one_or_none()
        if adopted_submission is not None:
            adopted_submission.adopted_download_count = int(getattr(adopted_submission, 'adopted_download_count', 0) or 0) + 1
        action_name = 'scene_sfx_asset_download' if source_domain == 'scene' else 'action_sfx_asset_download'
        _log_user_operation(
            db=db,
            action=action_name,
            project_id=project_id,
            req={
                'verb': verb,
                'scene_name': scene_name,
                'label': label,
                'display_name': display_name,
                'scope_label': scope_label,
                'genre': genre,
                'source_domain': source_domain,
                'item_key': display_name or label or abs_p.name,
                'file_name': abs_p.name,
                'path': str(abs_p),
                'from_user_better_submission': bool(adopted_submission is not None),
            },
            resp={'ok': True},
            file_refs=[str(abs_p)],
        )
        download_name = _build_user_visible_sfx_download_name(
            abs_path=abs_p,
            display_name=display_name or label or abs_p.stem,
            adopted_submission=adopted_submission,
        )
        resp = send_file(abs_p, as_attachment=True, download_name=download_name)
        resp = _attach_quota_headers(resp, _user_quota_snapshot(db, db_user))
        return resp


if __name__ == '__main__':
    Base.metadata.create_all(bind=engine)
    _ensure_schema_columns()
    app.run(host='0.0.0.0', port=8010, debug=False, use_reloader=False)
