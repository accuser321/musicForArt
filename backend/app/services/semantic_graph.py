import json
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.services.nlp_zh import normalize_text, tokenize_cn


@dataclass
class ExpandResult:
    terms: set[str]
    sources: list[str]


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
