import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path


def normalize_text(text: str) -> str:
    text = (text or '').strip().lower()
    text = re.sub(r'\s+', '', text)
    return text


@lru_cache(maxsize=1)
def _load_domain_terms() -> tuple[str, ...]:
    terms: set[str] = set()

    lexicon_path = Path('./assets/sfx/semantic_lexicon.json').resolve()
    if lexicon_path.exists():
        try:
            data = json.loads(lexicon_path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                for k, vals in data.items():
                    kk = normalize_text(str(k))
                    if kk:
                        terms.add(kk)
                    if isinstance(vals, list):
                        for v in vals:
                            vv = normalize_text(str(v))
                            if vv:
                                terms.add(vv)
        except json.JSONDecodeError:
            pass

    graph_path = Path('./assets/sfx/action_graph.json').resolve()
    if graph_path.exists():
        try:
            data = json.loads(graph_path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                common = (data.get('common') or {}) if isinstance(data.get('common'), dict) else {}
                for k, v in common.items():
                    kk = normalize_text(str(k))
                    if kk:
                        terms.add(kk)
                    if isinstance(v, dict):
                        children = v.get('children') or {}
                        semantic_terms = children.get('semantic_terms') if isinstance(children, dict) else None
                        sfx_terms = children.get('sfx_terms') if isinstance(children, dict) else None
                        for x in (semantic_terms or v.get('semantic_terms') or []) + (sfx_terms or v.get('sfx_terms') or []):
                            xx = normalize_text(str(x))
                            if xx:
                                terms.add(xx)
                genres = data.get('genres') or {}
                if isinstance(genres, dict):
                    for mapping in genres.values():
                        if not isinstance(mapping, dict):
                            continue
                        for k, v in mapping.items():
                            kk = normalize_text(str(k))
                            if kk:
                                terms.add(kk)
                            if isinstance(v, dict):
                                children = v.get('children') or {}
                                semantic_terms = children.get('semantic_terms') if isinstance(children, dict) else None
                                sfx_terms = children.get('sfx_terms') if isinstance(children, dict) else None
                                for x in (semantic_terms or v.get('semantic_terms') or []) + (sfx_terms or v.get('sfx_terms') or []):
                                    xx = normalize_text(str(x))
                                    if xx:
                                        terms.add(xx)
        except json.JSONDecodeError:
            pass

    # keep only useful chinese/latin terms with semantic value
    filtered = [t for t in terms if len(t) >= 2 and re.fullmatch(r'[\u4e00-\u9fff_a-z0-9]+', t)]
    filtered.sort(key=lambda x: (-len(x), x))
    return tuple(filtered)


def clear_domain_term_cache() -> None:
    _load_domain_terms.cache_clear()


def _domain_segment_chunk(chunk: str) -> list[str]:
    if not chunk:
        return []
    domain_terms = _load_domain_terms()
    out: list[str] = []
    i = 0
    while i < len(chunk):
        matched = None
        for term in domain_terms:
            if len(term) < 2:
                continue
            if chunk.startswith(term, i):
                matched = term
                break
        if matched:
            out.append(matched)
            i += len(matched)
        else:
            out.append(chunk[i])
            i += 1
    return [x for x in out if x]


def tokenize_cn(text: str) -> list[str]:
    """A domain-enhanced tokenizer that works without extra dependencies."""
    text = normalize_text(text)
    if not text:
        return []

    # Keep Chinese continuous chunks + latin/number chunks
    chunks = re.findall(r'[\u4e00-\u9fff]+|[a-z0-9_]+', text)
    tokens: list[str] = []

    for c in chunks:
        if re.fullmatch(r'[\u4e00-\u9fff]+', c):
            segmented = _domain_segment_chunk(c)
            tokens.append(c)
            tokens.extend(segmented)
            if len(c) <= 2:
                tokens.append(c)
            else:
                for i in range(len(c) - 1):
                    tokens.append(c[i : i + 2])
        else:
            tokens.append(c)

    # de-dup while preserving order
    seen = set()
    out = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def analyze_cn_tokens(text: str) -> dict:
    norm = normalize_text(text)
    if not norm:
        return {'text': '', 'normalized': '', 'tokens': [], 'domain_hits': [], 'mode': 'domain+bigram'}

    chunks = re.findall(r'[\u4e00-\u9fff]+|[a-z0-9_]+', norm)
    domain_hits: list[str] = []
    for c in chunks:
        if re.fullmatch(r'[\u4e00-\u9fff]+', c):
            for tok in _domain_segment_chunk(c):
                if len(tok) >= 2 and tok not in domain_hits:
                    domain_hits.append(tok)

    return {
        'text': text,
        'normalized': norm,
        'tokens': tokenize_cn(text),
        'domain_hits': domain_hits,
        'mode': 'domain+bigram',
    }


def char_bigram_counter(text: str) -> Counter:
    s = normalize_text(text)
    c = Counter()
    if not s:
        return c
    if len(s) == 1:
        c[s] += 1
        return c
    for i in range(len(s) - 1):
        c[s[i : i + 2]] += 1
    return c


def cosine_counter(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[k] * b.get(k, 0) for k in a)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
