import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    app_name: str = os.getenv('APP_NAME', 'Music For Art API')
    app_env: str = os.getenv('APP_ENV', 'dev')
    api_prefix: str = os.getenv('API_PREFIX', '/api')
    database_url: str = os.getenv('DATABASE_URL', 'sqlite:///./music_for_art.db')
    upload_dir: str = os.getenv('UPLOAD_DIR', './uploads')
    max_upload_mb: int = int(os.getenv('MAX_UPLOAD_MB', '100'))

    # OpenAI-compatible LLM settings (supports OpenAI and DeepSeek)
    llm_provider: str = os.getenv('LLM_PROVIDER', 'none')
    llm_base_url: str = os.getenv('LLM_BASE_URL', '')
    llm_api_key: str = os.getenv('LLM_API_KEY', '')
    llm_model: str = os.getenv('LLM_MODEL', '')
    llm_timeout_sec: int = int(os.getenv('LLM_TIMEOUT_SEC', '120'))
    llm_temperature: float = float(os.getenv('LLM_TEMPERATURE', '0.3'))
    llm_max_tokens: int = int(os.getenv('LLM_MAX_TOKENS', '2600'))
    report_mode_default: str = os.getenv('REPORT_MODE_DEFAULT', 'production')

    # Semantic matching backend
    semantic_backend: str = os.getenv('SEMANTIC_BACKEND', 'local')  # local | neo4j | hybrid
    semantic_neo4j_depth: int = int(os.getenv('SEMANTIC_NEO4J_DEPTH', '2'))
    neo4j_uri: str = os.getenv('NEO4J_URI', '')
    neo4j_user: str = os.getenv('NEO4J_USER', '')
    neo4j_password: str = os.getenv('NEO4J_PASSWORD', '')
    neo4j_database: str = os.getenv('NEO4J_DATABASE', '')

    # rollout + cache + observability
    semantic_rollout_mode: str = os.getenv('SEMANTIC_ROLLOUT_MODE', 'fixed')  # fixed|by_project|by_hash|force_neo4j|force_hybrid
    semantic_rollout_percent: int = int(os.getenv('SEMANTIC_ROLLOUT_PERCENT', '20'))
    semantic_hybrid_project_ids: str = os.getenv('SEMANTIC_HYBRID_PROJECT_IDS', '')
    reason_cache_ttl_sec: int = int(os.getenv('REASON_CACHE_TTL_SEC', '900'))


settings = Settings()
