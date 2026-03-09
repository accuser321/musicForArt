import re
from collections import Counter


def normalize_text(text: str) -> str:
    text = (text or '').strip().lower()
    text = re.sub(r'\s+', '', text)
    return text


def tokenize_cn(text: str) -> list[str]:
    """A lightweight tokenizer that works without extra dependencies."""
    text = normalize_text(text)
    if not text:
        return []

    # Keep Chinese continuous chunks + latin/number chunks
    chunks = re.findall(r'[\u4e00-\u9fff]+|[a-z0-9_]+', text)
    tokens: list[str] = []

    for c in chunks:
        if re.fullmatch(r'[\u4e00-\u9fff]+', c):
            # 2-char windows + full chunk for rough semantic capture
            if len(c) <= 2:
                tokens.append(c)
            else:
                tokens.append(c)
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
