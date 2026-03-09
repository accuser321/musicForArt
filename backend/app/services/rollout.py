import hashlib

from app.config import settings


def _parse_int_set(csv_text: str) -> set[int]:
    out = set()
    for p in (csv_text or '').split(','):
        p = p.strip()
        if not p:
            continue
        try:
            out.add(int(p))
        except ValueError:
            pass
    return out


def resolve_semantic_backend(project_id: int | None = None, subject: str = '') -> str:
    mode = (settings.semantic_rollout_mode or 'fixed').strip().lower()

    if mode == 'fixed':
        return settings.semantic_backend

    if mode == 'by_project':
        pset = _parse_int_set(settings.semantic_hybrid_project_ids)
        if project_id is not None and project_id in pset:
            return 'hybrid'
        return 'local'

    if mode == 'by_hash':
        pct = max(0, min(100, settings.semantic_rollout_percent))
        key = str(project_id or '') + '|' + (subject or '')
        h = int(hashlib.md5(key.encode('utf-8')).hexdigest()[:8], 16) % 100
        return 'hybrid' if h < pct else 'local'

    if mode == 'force_neo4j':
        return 'neo4j'

    if mode == 'force_hybrid':
        return 'hybrid'

    return settings.semantic_backend
