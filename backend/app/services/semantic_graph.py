import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.services.nlp_zh import normalize_text, tokenize_cn
from app.services.llm import _chat_completion, _extract_json_blob, _load_prompt, _load_system_prompt, llm_enabled


@dataclass
class ExpandResult:
    terms: set[str]
    sources: list[str]


@dataclass
class SemanticSuggestion:
    term: str
    origin: str
    origin_label: str
    score: float = 0.0
    reason: str = ''


SEMANTIC_EXPAND_PROMPT_FILE = 'V3-semantic_expand_action_terms_task.txt'
SFX_EXPAND_PROMPT_FILE = 'V3-semantic_expand_action_sfx_task.txt'


@dataclass
class SfxSuggestion:
    term: str
    mode: str
    origin: str
    origin_label: str
    score: float = 0.0
    reason: str = ''


def _load_local_lexicon() -> dict[str, list[str]]:
    p = Path('./assets/sfx/semantic_lexicon.json').resolve()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {}


def _expand_local(term: str, lexicon: dict[str, list[str]]) -> set[str]:
    out = set()
    term_n = normalize_text(term)
    if not term_n:
        return out

    out.add(term_n)
    out.update(tokenize_cn(term_n))

    for k, vals in lexicon.items():
        kk = normalize_text(k)
        group = {kk} | {normalize_text(v) for v in vals}
        if term_n in group or any(t in group for t in tokenize_cn(term_n)):
            for g in group:
                if g:
                    out.add(g)
                    out.update(tokenize_cn(g))
    return out


def _neo4j_driver():
    if not (settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password):
        return None
    try:
        from neo4j import GraphDatabase
    except Exception:
        return None
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


def _expand_neo4j(term: str, depth: int = 2, backend_override: str | None = None) -> set[str]:
    backend = backend_override or settings.semantic_backend
    if backend not in {'neo4j', 'hybrid'}:
        return set()

    driver = _neo4j_driver()
    if not driver:
        return set()

    term_n = normalize_text(term)
    if not term_n:
        return set()

    query = (
        'MATCH (n:Concept {name:$name}) '
        'MATCH p=(n)-[:SYNONYM|RELATED_TO|IS_A*1..$depth]-(m:Concept) '
        'RETURN DISTINCT m.name AS name LIMIT 200'
    )

    out = set()
    try:
        with driver.session(database=settings.neo4j_database or None) as session:
            rows = session.run(query, name=term_n, depth=depth)
            for r in rows:
                name = normalize_text(r.get('name') or '')
                if name:
                    out.add(name)
                    out.update(tokenize_cn(name))
    except Exception:
        return set()
    finally:
        driver.close()

    return out


def expand_term(term: str, backend_override: str | None = None) -> ExpandResult:
    lexicon = _load_local_lexicon()
    backend = backend_override or settings.semantic_backend

    local_terms = _expand_local(term, lexicon) if backend in {'local', 'hybrid'} else set()
    neo_terms = _expand_neo4j(term, depth=settings.semantic_neo4j_depth, backend_override=backend)

    merged = set(local_terms)
    merged.update(neo_terms)

    sources = []
    if local_terms:
        sources.append('local_lexicon')
    if neo_terms:
        sources.append('neo4j')

    return ExpandResult(terms=merged, sources=sources)


def _normalize_candidate_term(term: str | None, base_term: str) -> str:
    value = normalize_text(term or '')
    if not value:
        return ''
    if value == normalize_text(base_term):
        return ''
    if len(value) < 2:
        return ''
    return value


def _dedupe_suggestions(items: list[SemanticSuggestion], limit: int = 8) -> list[SemanticSuggestion]:
    merged: dict[str, SemanticSuggestion] = {}
    for item in items:
        term = _normalize_candidate_term(item.term, '')
        if not term:
            continue
        current = merged.get(term)
        if current is None:
            merged[term] = SemanticSuggestion(
                term=term,
                origin=item.origin,
                origin_label=item.origin_label,
                score=float(item.score or 0),
                reason=str(item.reason or '').strip(),
            )
            continue
        current.score = max(float(current.score or 0), float(item.score or 0))
        if not current.reason and item.reason:
            current.reason = item.reason
    out = list(merged.values())
    out.sort(key=lambda x: (-float(x.score or 0), x.term))
    return out[:limit]


def _parse_llm_suggestions(payload: dict, base_term: str, limit: int) -> list[SemanticSuggestion]:
    suggestions = payload.get('suggestions') if isinstance(payload, dict) else None
    if not isinstance(suggestions, list):
        suggestions = payload.get('terms') if isinstance(payload, dict) else None
    if not isinstance(suggestions, list):
        return []
    out: list[SemanticSuggestion] = []
    for idx, row in enumerate(suggestions):
        if isinstance(row, dict):
            candidate = _normalize_candidate_term(row.get('term'), base_term)
            reason = str(row.get('reason') or '').strip()
        else:
            candidate = _normalize_candidate_term(str(row or ''), base_term)
            reason = ''
        if not candidate:
            continue
        out.append(
            SemanticSuggestion(
                term=candidate,
                origin='llm',
                origin_label='LLM建议',
                score=max(0.1, 1.0 - idx * 0.08),
                reason=reason,
            )
        )
    return _dedupe_suggestions(out, limit=limit)


@lru_cache(maxsize=256)
def _cached_llm_action_suggestions(term: str, genre: str, provider_override: str, limit: int) -> tuple[tuple[str, str, float, str], ...]:
    if not llm_enabled(provider_override):
        return tuple()
    task_prompt = _load_prompt(SEMANTIC_EXPAND_PROMPT_FILE)
    if not task_prompt:
        return tuple()
    system_prompt, _ = _load_system_prompt()
    payload = {
        'genre': str(genre or '').strip() or '通用',
        'action_term': normalize_text(term),
        'limit': int(limit),
        'goal': '给有声书后期声音设计推荐围绕当前动作词的语义扩展动作词',
    }
    user_prompt = (
        f'{task_prompt}\n\n'
        '输出要求：\n'
        '1) 只输出一个 JSON 对象，不要输出代码块。\n'
        '2) suggestions 中每个 term 必须是适合声音设计检索的动作词或动作短语。\n'
        '3) 不要输出单字词，不要重复输入词本身，不要输出解释性前言。\n\n'
        f'输入 JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}'
    )
    try:
        raw, _meta = _chat_completion(system_prompt, user_prompt, evidence_kind='semantic_expand_action', provider_override=provider_override)
    except Exception:
        return tuple()
    parsed = _extract_json_blob(raw or '')
    if not isinstance(parsed, dict):
        return tuple()
    rows = _parse_llm_suggestions(parsed, base_term=term, limit=limit)
    return tuple((row.term, row.origin_label, float(row.score or 0), row.reason) for row in rows)


def _llm_action_suggestions(term: str, genre: str, limit: int = 6, provider_override: str | None = None) -> list[SemanticSuggestion]:
    rows = _cached_llm_action_suggestions(normalize_text(term), str(genre or '').strip(), str(provider_override or ''), int(limit))
    return [
        SemanticSuggestion(term=term, origin='llm', origin_label=origin_label, score=score, reason=reason)
        for term, origin_label, score, reason in rows
        if term
    ]


def suggest_action_semantic_terms(term: str, genre: str = '', limit: int = 8, provider_override: str | None = None) -> list[SemanticSuggestion]:
    base = normalize_text(term)
    if not base:
        return []
    llm_items = _llm_action_suggestions(base, genre, limit=max(4, min(limit, 6)), provider_override=provider_override)
    return [item for item in _dedupe_suggestions(llm_items, limit=limit) if _normalize_candidate_term(item.term, base)]


def _parse_llm_sfx_suggestions(payload: dict, base_term: str, limit: int) -> list[SfxSuggestion]:
    if not isinstance(payload, dict):
        return []
    groups = [
        ('direct_terms', 'direct'),
        ('composite_terms', 'composite'),
    ]
    out: list[SfxSuggestion] = []
    seen: set[tuple[str, str]] = set()
    for key, mode in groups:
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        for idx, row in enumerate(rows):
            if isinstance(row, dict):
                candidate = _normalize_candidate_term(row.get('term'), base_term)
                reason = str(row.get('reason') or '').strip()
            else:
                candidate = _normalize_candidate_term(str(row or ''), base_term)
                reason = ''
            if not candidate:
                continue
            pair = (candidate, mode)
            if pair in seen:
                continue
            seen.add(pair)
            out.append(
                SfxSuggestion(
                    term=candidate,
                    mode=mode,
                    origin='llm',
                    origin_label='LLM建议',
                    score=max(0.1, 1.0 - idx * 0.08),
                    reason=reason,
                )
            )
    out.sort(key=lambda x: (-float(x.score or 0), x.term, x.mode))
    return out[:limit]


@lru_cache(maxsize=256)
def _cached_llm_action_sfx_suggestions(term: str, genre: str, semantic_terms_key: str, limit: int, provider_override: str) -> tuple[tuple[str, str, str, float, str], ...]:
    if not llm_enabled(provider_override):
        return tuple()
    task_prompt = _load_prompt(SFX_EXPAND_PROMPT_FILE)
    if not task_prompt:
        return tuple()
    system_prompt, _ = _load_system_prompt()
    semantic_terms = [str(x).strip() for x in str(semantic_terms_key or '').split('|') if str(x).strip()]
    payload = {
        'genre': str(genre or '').strip() or '通用',
        'action_term': normalize_text(term),
        'semantic_terms': semantic_terms[:8],
        'limit': int(limit),
        'goal': '给有声书后期声音设计推荐围绕当前动作词的直达音效词和整体音效词',
    }
    user_prompt = (
        f'{task_prompt}\n\n'
        '输出要求：\n'
        '1) 只输出一个 JSON 对象，不要输出代码块。\n'
        '2) direct_terms 只放更像具体声音标签、可直接用于搜索音效的词。\n'
        '3) composite_terms 只放更像完整动作声音方案的词。\n'
        '4) 不要输出单字词，不要重复输入词本身，不要输出解释性前言。\n\n'
        f'输入 JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}'
    )
    try:
        raw, _meta = _chat_completion(system_prompt, user_prompt, evidence_kind='semantic_expand_action_sfx', provider_override=provider_override)
    except Exception:
        return tuple()
    parsed = _extract_json_blob(raw or '')
    rows = _parse_llm_sfx_suggestions(parsed, base_term=term, limit=limit)
    return tuple((row.term, row.mode, row.origin, row.origin_label, float(row.score or 0), row.reason) for row in rows)


def suggest_action_sfx_terms(
    term: str,
    *,
    genre: str = '',
    semantic_terms: list[str] | None = None,
    limit: int = 8,
    provider_override: str | None = None,
) -> list[SfxSuggestion]:
    base = normalize_text(term)
    if not base:
        return []
    semantic_terms_key = '|'.join(
        _normalize_candidate_term(item, '')
        for item in _merge_semantic_seed_terms(semantic_terms or [])
    )
    rows = _cached_llm_action_sfx_suggestions(
        base,
        str(genre or '').strip(),
        semantic_terms_key,
        int(limit),
        str(provider_override or ''),
    )
    return [
        SfxSuggestion(term=term, mode=mode, origin=origin, origin_label=origin_label, score=score, reason=reason)
        for term, mode, origin, origin_label, score, reason in rows
        if term and mode in {'direct', 'composite'}
    ]


def _merge_semantic_seed_terms(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items or []:
        value = normalize_text(item or '')
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def graph_status() -> dict:
    out = {
        'backend': settings.semantic_backend,
        'neo4j_configured': bool(settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password),
        'neo4j_connected': False,
        'concept_nodes': None,
        'synonym_edges': None,
    }

    driver = _neo4j_driver()
    if not driver:
        return out

    try:
        with driver.session(database=settings.neo4j_database or None) as session:
            out['neo4j_connected'] = True
            out['concept_nodes'] = session.run('MATCH (c:Concept) RETURN count(c) AS n').single()['n']
            out['synonym_edges'] = session.run('MATCH ()-[r:SYNONYM]->() RETURN count(r) AS n').single()['n']
    except Exception:
        out['neo4j_connected'] = False
    finally:
        driver.close()

    return out


def upsert_relation(head: str, relation: str, tail: str, bidirectional: bool = False) -> dict:
    rel = relation.strip().upper()
    if rel not in {'SYNONYM', 'RELATED_TO', 'IS_A'}:
        raise ValueError('relation must be one of SYNONYM, RELATED_TO, IS_A')

    h = normalize_text(head)
    t = normalize_text(tail)
    if not h or not t:
        raise ValueError('head and tail are required')

    driver = _neo4j_driver()
    if not driver:
        raise RuntimeError('neo4j is not configured or neo4j driver unavailable')

    q = (
        'MERGE (a:Concept {name:$h}) '
        'MERGE (b:Concept {name:$t}) '
        f'MERGE (a)-[:{rel}]->(b)'
    )

    try:
        with driver.session(database=settings.neo4j_database or None) as session:
            session.run('CREATE CONSTRAINT concept_name IF NOT EXISTS FOR (c:Concept) REQUIRE c.name IS UNIQUE')
            session.run(q, h=h, t=t)
            if bidirectional:
                session.run(q, h=t, t=h)
    finally:
        driver.close()

    return {'ok': True, 'head': h, 'relation': rel, 'tail': t, 'bidirectional': bidirectional}


def _local_reason(term: str, limit: int) -> list[dict]:
    lex = _load_local_lexicon()
    t = normalize_text(term)
    if not t:
        return []

    results = []
    seen = set()

    for k, vals in lex.items():
        kk = normalize_text(k)
        group = [kk] + [normalize_text(v) for v in vals]
        if t in group or any(x in group for x in tokenize_cn(t)):
            for v in group:
                if not v or v == t or v in seen:
                    continue
                seen.add(v)
                results.append({'term': v, 'score': 0.7, 'reason': 'local_synonym_group', 'path': [t, v]})

    # token-overlap inference for weak related terms
    tset = set(tokenize_cn(t))
    for k in lex.keys():
        kk = normalize_text(k)
        if kk == t or kk in seen:
            continue
        kset = set(tokenize_cn(kk))
        overlap = len(tset & kset) / max(1, len(tset | kset))
        if overlap >= 0.34:
            seen.add(kk)
            results.append({'term': kk, 'score': round(0.35 + overlap, 4), 'reason': 'local_token_overlap', 'path': [t, kk]})

    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:limit]


def _neo4j_reason(term: str, max_hops: int, limit: int, backend_override: str | None = None) -> list[dict]:
    backend = backend_override or settings.semantic_backend
    if backend not in {'neo4j', 'hybrid'}:
        return []
    driver = _neo4j_driver()
    if not driver:
        return []

    t = normalize_text(term)
    if not t:
        return []

    query = (
        'MATCH (a:Concept {name:$name}) '
        'MATCH p=(a)-[:SYNONYM|RELATED_TO|IS_A*1..$hops]-(b:Concept) '
        'WHERE b.name <> $name '
        'RETURN b.name AS name, length(p) AS hops, '
        '       [n IN nodes(p) | n.name] AS node_path, '
        '       [r IN relationships(p) | type(r)] AS rel_path '
        'ORDER BY hops ASC LIMIT $limit'
    )

    rows_out = []
    try:
        with driver.session(database=settings.neo4j_database or None) as session:
            rows = session.run(query, name=t, hops=max_hops, limit=limit)
            for r in rows:
                hops = int(r.get('hops') or 1)
                score = round(1.0 / hops, 4)
                rows_out.append(
                    {
                        'term': normalize_text(r.get('name') or ''),
                        'score': score,
                        'reason': 'neo4j_path',
                        'path': r.get('node_path') or [],
                        'relations': r.get('rel_path') or [],
                    }
                )
    except Exception:
        return []
    finally:
        driver.close()

    # dedup by term keep highest score
    best = {}
    for item in rows_out:
        tt = item.get('term')
        if not tt:
            continue
        if tt not in best or item['score'] > best[tt]['score']:
            best[tt] = item

    out = list(best.values())
    out.sort(key=lambda x: x['score'], reverse=True)
    return out[:limit]


def reason_term(term: str, limit: int = 12, max_hops: int = 2, backend_override: str | None = None) -> dict:
    backend = backend_override or settings.semantic_backend
    local = _local_reason(term, limit=limit) if backend in {'local', 'hybrid'} else []
    neo = _neo4j_reason(term, max_hops=max_hops, limit=limit, backend_override=backend)

    merged = {}
    for src, arr in (('local', local), ('neo4j', neo)):
        for item in arr:
            tt = item['term']
            if tt not in merged or item['score'] > merged[tt]['score']:
                merged[tt] = dict(item)
                merged[tt]['source'] = src

    inferred = list(merged.values())
    inferred.sort(key=lambda x: x['score'], reverse=True)
    inferred = inferred[:limit]

    return {
        'term': normalize_text(term),
        'inferred': inferred,
        'local_count': len(local),
        'neo4j_count': len(neo),
        'semantic_backend': backend,
    }
