from dataclasses import dataclass
from pathlib import Path

from app.services.nlp_zh import char_bigram_counter, cosine_counter, normalize_text
from app.services.semantic_graph import expand_term

ALLOWED_EXT = {'.mp3', '.wav', '.flac'}


@dataclass
class SfxItem:
    path: Path
    stem: str
    canonical: str
    token_set: set[str]
    vec: dict
    source_pool: str = 'system'
    source_label: str = '系统官方素材'


def _stem_to_canonical(stem: str) -> str:
    # User-adopted assets are stored like:
    #   genre__verb__display_term__userbetter_<id>
    # In that case we should match by display_term, not by the genre prefix.
    if '__' in stem:
        parts = [str(x).strip() for x in stem.split('__') if str(x).strip()]
        if len(parts) >= 4 and parts[-1].startswith('userbetter_'):
            return parts[2]
        # Support optional naming like "脚步声__海边"
        return parts[0] if parts else stem
    return stem


def load_sfx_library(backend_override: str | None = None) -> list[SfxItem]:
    sfx_dir = Path('./assets/sfx').resolve()
    user_sfx_dir = Path('./assets/sfx_user').resolve()
    sfx_dir.mkdir(parents=True, exist_ok=True)
    user_sfx_dir.mkdir(parents=True, exist_ok=True)

    items: list[SfxItem] = []
    roots = [
        (sfx_dir, 'system', '系统官方素材'),
        (user_sfx_dir, 'user_better', '由用户更优推荐'),
    ]
    for root, source_pool, source_label in roots:
        for p in sorted(root.rglob('*')):
            if not p.is_file() or p.suffix.lower() not in ALLOWED_EXT:
                continue
            stem = p.stem
            canonical = _stem_to_canonical(stem)
            tokens = expand_term(canonical, backend_override=backend_override).terms
            vec = char_bigram_counter(canonical)
            items.append(
                SfxItem(
                    path=p,
                    stem=stem,
                    canonical=canonical,
                    token_set=tokens,
                    vec=vec,
                    source_pool=source_pool,
                    source_label=source_label,
                )
            )

    return items


def match_sfx_candidates(query: str, library: list[SfxItem], top_n: int = 3, backend_override: str | None = None) -> list[dict]:
    expand = expand_term(query, backend_override=backend_override)
    q_norm = normalize_text(query)
    q_terms = expand.terms
    q_vec = char_bigram_counter(q_norm)

    ranked = []
    for item in library:
        exact = 1.0 if q_norm == normalize_text(item.canonical) else 0.0
        token_overlap = len(q_terms & item.token_set) / max(1, len(q_terms | item.token_set))
        vec_sim = cosine_counter(q_vec, item.vec)

        base_score = exact * 1.5 + token_overlap * 0.8 + vec_sim * 0.6
        if base_score <= 0:
            continue
        source_bonus = 0.35 if item.source_pool == 'user_better' else 0.0
        score = base_score + source_bonus

        ranked.append(
            {
                'file_name': item.path.name,
                'file_path': str(item.path),
                'canonical': item.canonical,
                'score': round(float(score), 4),
                'exact': bool(exact > 0),
                'overlap': round(float(token_overlap), 4),
                'vec_sim': round(float(vec_sim), 4),
                'source_pool': item.source_pool,
                'source_label': item.source_label,
                'semantic_sources': expand.sources,
            }
        )

    ranked.sort(key=lambda x: x['score'], reverse=True)
    return ranked[: max(1, top_n)]
