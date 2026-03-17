import json
from pathlib import Path

from app.services.nlp_zh import clear_domain_term_cache

LEXICON_PATH = Path('./assets/sfx/semantic_lexicon.json').resolve()


def get_lexicon() -> dict[str, list[str]]:
    if not LEXICON_PATH.exists():
        return {}
    try:
        data = json.loads(LEXICON_PATH.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return {str(k): [str(x) for x in v] for k, v in data.items() if isinstance(v, list)}
        return {}
    except json.JSONDecodeError:
        return {}


def save_lexicon(data: dict[str, list[str]]) -> None:
    LEXICON_PATH.parent.mkdir(parents=True, exist_ok=True)
    norm = {}
    for k, v in data.items():
        key = str(k).strip()
        if not key:
            continue
        vals = []
        for item in v:
            t = str(item).strip()
            if t:
                vals.append(t)
        # dedup keep order
        seen = set()
        uniq = []
        for x in vals:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        norm[key] = uniq
    LEXICON_PATH.write_text(json.dumps(norm, ensure_ascii=False, indent=2), encoding='utf-8')
    clear_domain_term_cache()


def merge_lexicon(delta: dict[str, list[str]]) -> dict[str, list[str]]:
    base = get_lexicon()
    for k, vals in delta.items():
        key = str(k).strip()
        if not key:
            continue
        exist = base.get(key, [])
        merged = exist + [str(x).strip() for x in vals if str(x).strip()]
        seen = set()
        uniq = []
        for x in merged:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        base[key] = uniq
    save_lexicon(base)
    return base
