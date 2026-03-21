from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = 'projects'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    genre: Mapped[str] = mapped_column(String(32), nullable=False, default='玄幻')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class AudioAnalysis(Base):
    __tablename__ = 'audio_analysis'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), unique=True, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    duration_sec: Mapped[float] = mapped_column(Float, nullable=False)
    bpm: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    markers_json: Mapped[str] = mapped_column(Text, nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class TextAnalysis(Base):
    __tablename__ = 'text_analysis'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), unique=True, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    scenes_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class FusionPlan(Base):
    __tablename__ = 'fusion_plan'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), unique=True, index=True)
    cue_sheet_json: Mapped[str] = mapped_column(Text, nullable=False)
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class NarrationAnalysis(Base):
    __tablename__ = 'narration_analysis'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), unique=True, index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    duration_sec: Mapped[float] = mapped_column(Float, nullable=False)
    timeline_json: Mapped[str] = mapped_column(Text, nullable=False)
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class SceneAnalysis(Base):
    __tablename__ = 'scene_analysis'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), unique=True, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    genre: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='')
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False, default='')
    report_json: Mapped[str] = mapped_column(Text, nullable=False, default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ReasoningCache(Base):
    __tablename__ = 'reasoning_cache'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    backend: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class ReasoningLog(Base):
    __tablename__ = 'reasoning_log'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    term: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    backend: Mapped[str] = mapped_column(String(32), nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False)
    output_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class DraftReview(Base):
    __tablename__ = 'draft_review'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    target_head: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='pending')
    note: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UserOperationLog(Base):
    __tablename__ = 'user_operation_log'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    user_phone: Mapped[str] = mapped_column(String(32), index=True, nullable=True, default='')
    action: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    input_json: Mapped[str] = mapped_column(Text, nullable=False, default='{}')
    output_json: Mapped[str] = mapped_column(Text, nullable=False, default='{}')
    file_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class UserAccount(Base):
    __tablename__ = 'user_account'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    uid: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False, default='')
    ops_role_code: Mapped[str] = mapped_column(String(2), index=True, nullable=False, default='33')
    user_tier_code: Mapped[str] = mapped_column(String(2), index=True, nullable=False, default='33')
    is_authorized: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0/1
    is_admin: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0/1
    daily_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    daily_text_char_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    daily_sfx_download_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    invite_activated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0/1
    invite_code_used: Mapped[str] = mapped_column(String(64), nullable=False, default='')
    referred_by_user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    referred_by_phone: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='')
    referred_by_uid: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default='')
    referral_input: Mapped[str] = mapped_column(String(64), nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuthCode(Base):
    __tablename__ = 'auth_code'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0/1
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class AuthSession(Base):
    __tablename__ = 'auth_session'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    token: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class InviteCode(Base):
    __tablename__ = 'invite_code'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0/1
    used_by_phone: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='')
    used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class SystemSetting(Base):
    __tablename__ = 'system_setting'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    setting_value: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LeaderboardOverride(Base):
    __tablename__ = 'leaderboard_override'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='action')
    board_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default='')
    window_key: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default='10d')
    item_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False, default='')
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    subtitle: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    override_count: Mapped[int] = mapped_column(Integer, nullable=True)
    manual_rank: Mapped[int] = mapped_column(Integer, nullable=True)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    meta_json: Mapped[str] = mapped_column(Text, nullable=False, default='{}')
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DailyUsage(Base):
    __tablename__ = 'daily_usage'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    ymd: Mapped[str] = mapped_column(String(10), index=True, nullable=False)  # YYYY-MM-DD
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ActionSupplementTask(Base):
    __tablename__ = 'action_supplement_task'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    user_phone: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='')
    genre: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='')
    verb: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    target_head: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default='')
    target_genre: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='')
    sentence_excerpt: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    semantic_terms_json: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    sfx_terms_json: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    missing_sfx_terms_json: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='pending')
    asset_label: Mapped[str] = mapped_column(String(128), nullable=False, default='')
    asset_file_path: Mapped[str] = mapped_column(String(1024), nullable=False, default='')
    notified_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ActionSupplementAsset(Base):
    __tablename__ = 'action_supplement_asset'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplement_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    asset_label: Mapped[str] = mapped_column(String(128), index=True, nullable=False, default='')
    asset_scope: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='genre')
    asset_scope_genre: Mapped[str] = mapped_column(String(32), nullable=False, default='')
    asset_file_path: Mapped[str] = mapped_column(String(1024), nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ActionGraphInheritanceReview(Base):
    __tablename__ = 'action_graph_inheritance_review'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    genre: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='')
    verb_head: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default='')
    project_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sample_excerpt: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='active')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ActionFallbackRiskTerm(Base):
    __tablename__ = 'action_fallback_risk_term'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    term: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), index=True, nullable=False, default='warn')
    note: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SceneSupplementTask(Base):
    __tablename__ = 'scene_supplement_task'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    user_phone: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='')
    genre: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='')
    scene_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False, default='')
    target_scene: Mapped[str] = mapped_column(String(128), index=True, nullable=False, default='')
    node_key: Mapped[str] = mapped_column(String(255), index=True, nullable=False, default='')
    sentence_excerpt: Mapped[str] = mapped_column(String(255), nullable=False, default='')
    time_terms_json: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    location_terms_json: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    scene_elements_json: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    sfx_terms_json: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    missing_sfx_terms_json: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='pending')
    asset_label: Mapped[str] = mapped_column(String(128), nullable=False, default='')
    asset_file_path: Mapped[str] = mapped_column(String(1024), nullable=False, default='')
    notified_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SceneSupplementAsset(Base):
    __tablename__ = 'scene_supplement_asset'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplement_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    asset_label: Mapped[str] = mapped_column(String(128), index=True, nullable=False, default='')
    asset_scope: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default='genre')
    asset_scope_genre: Mapped[str] = mapped_column(String(32), nullable=False, default='')
    asset_file_path: Mapped[str] = mapped_column(String(1024), nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
