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


def _stem_to_canonical(stem: str) -> str:
    # Support optional naming like "脚步声__海边"
    if '__' in stem:
        return stem.split('__', 1)[0]
    return stem


def load_sfx_library(backend_override: str | None = None) -> list[SfxItem]:
    sfx_dir = Path('./assets/sfx').resolve()
    sfx_dir.mkdir(parents=True, exist_ok=True)

    items: list[SfxItem] = []
    for p in sorted(sfx_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in ALLOWED_EXT:
            continue
        stem = p.stem
        canonical = _stem_to_canonical(stem)
        tokens = expand_term(canonical, backend_override=backend_override).terms
        vec = char_bigram_counter(canonical)
        items.append(SfxItem(path=p, stem=stem, canonical=canonical, token_set=tokens, vec=vec))

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

        score = exact * 1.5 + token_overlap * 0.8 + vec_sim * 0.6
        if score <= 0:
            continue

        ranked.append(
            {
                'file_name': item.path.name,
                'file_path': str(item.path),
                'canonical': item.canonical,
                'score': round(float(score), 4),
                'exact': bool(exact > 0),
                'overlap': round(float(token_overlap), 4),
                'vec_sim': round(float(vec_sim), 4),
                'semantic_sources': expand.sources,
            }
        )

    ranked.sort(key=lambda x: x['score'], reverse=True)
    return ranked[: max(1, top_n)]
