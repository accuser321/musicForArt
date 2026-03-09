import json
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ReasoningCache, ReasoningLog


def _cache_key(term: str, backend: str, limit: int, max_hops: int) -> str:
    return f'{backend}|{term.strip().lower()}|{limit}|{max_hops}'


def get_reason_cache(db: Session, term: str, backend: str, limit: int, max_hops: int) -> dict | None:
    key = _cache_key(term, backend, limit, max_hops)
    row = db.execute(select(ReasoningCache).where(ReasoningCache.cache_key == key)).scalar_one_or_none()
    if not row:
        return None

    if row.expires_at < datetime.utcnow():
        db.delete(row)
        db.commit()
        return None

    row.hit_count += 1
    db.commit()

    try:
        return json.loads(row.result_json)
    except json.JSONDecodeError:
        return None


def set_reason_cache(db: Session, term: str, backend: str, limit: int, max_hops: int, result: dict) -> None:
    key = _cache_key(term, backend, limit, max_hops)
    row = db.execute(select(ReasoningCache).where(ReasoningCache.cache_key == key)).scalar_one_or_none()

    exp = datetime.utcnow() + timedelta(seconds=settings.reason_cache_ttl_sec)
    payload = json.dumps(result, ensure_ascii=False)

    if not row:
        row = ReasoningCache(
            cache_key=key,
            backend=backend,
            result_json=payload,
            expires_at=exp,
            hit_count=0,
        )
        db.add(row)
    else:
        row.backend = backend
        row.result_json = payload
        row.expires_at = exp

    db.commit()


def log_reason_event(
    db: Session,
    event_type: str,
    term: str,
    backend: str,
    project_id: int | None,
    req: dict,
    resp: dict,
) -> None:
    row = ReasoningLog(
        event_type=event_type,
        project_id=project_id,
        term=term,
        backend=backend,
        input_json=json.dumps(req, ensure_ascii=False),
        output_json=json.dumps(resp, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
