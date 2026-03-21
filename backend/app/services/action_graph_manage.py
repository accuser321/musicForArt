import json
from pathlib import Path

from app.services.action_sfx_graph import (
    build_action_node_key,
    classify_sfx_terms,
    ensure_action_sfx_terms,
)

ACTION_GRAPH_PATH = Path('./assets/sfx/action_graph.json').resolve()


def _merge_unique(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        value = str(item).strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _load_action_graph() -> dict:
    if not ACTION_GRAPH_PATH.exists():
        return {'_meta': {'version': 'missing'}, 'common': {}, 'genres': {}}
    try:
        data = json.loads(ACTION_GRAPH_PATH.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {'_meta': {'version': 'invalid'}, 'common': {}, 'genres': {}}
    if not isinstance(data, dict):
        return {'_meta': {'version': 'invalid'}, 'common': {}, 'genres': {}}
    data.setdefault('_meta', {})
    data.setdefault('common', {})
    data.setdefault('genres', {})
    return data


def _save_action_graph(data: dict) -> None:
    ACTION_GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTION_GRAPH_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _inheritance_blocks(graph: dict | None = None) -> dict[str, set[str]]:
    data = graph if isinstance(graph, dict) else _load_action_graph()
    meta = data.get('_meta') or {}
    raw = meta.get('inheritance_blocks') or {}
    out: dict[str, set[str]] = {}
    if not isinstance(raw, dict):
        return out
    for genre, heads in raw.items():
        genre_key = str(genre or '').strip()
        if not genre_key:
            continue
        head_set = {str(head or '').strip() for head in (heads or []) if str(head or '').strip()}
        if head_set:
            out[genre_key] = head_set
    return out


def is_inheritance_blocked(genre: str, verb_head: str, graph: dict | None = None) -> bool:
    genre_key = str(genre or '').strip()
    head_key = str(verb_head or '').strip()
    if not genre_key or not head_key:
        return False
    return head_key in _inheritance_blocks(graph).get(genre_key, set())


def _write_inheritance_blocks(graph: dict, blocks: dict[str, set[str]]) -> None:
    graph.setdefault('_meta', {})
    graph['_meta']['inheritance_blocks'] = {
        genre: sorted(heads)
        for genre, heads in sorted(blocks.items())
        if genre and heads
    }


def _fallback_alias_meta(graph: dict | None = None) -> dict:
    data = graph if isinstance(graph, dict) else _load_action_graph()
    meta = data.get('_meta') or {}
    raw = meta.get('fallback_alias_map') or {}
    out = {'common': {}, 'genres': {}}
    if not isinstance(raw, dict):
        return out
    common = raw.get('common') or {}
    if isinstance(common, dict):
        out['common'] = {
            str(alias or '').strip(): str(target or '').strip()
            for alias, target in common.items()
            if str(alias or '').strip() and str(target or '').strip()
        }
    genres = raw.get('genres') or {}
    if isinstance(genres, dict):
        for genre, mapping in genres.items():
            genre_key = str(genre or '').strip()
            if not genre_key or not isinstance(mapping, dict):
                continue
            normalized = {
                str(alias or '').strip(): str(target or '').strip()
                for alias, target in mapping.items()
                if str(alias or '').strip() and str(target or '').strip()
            }
            if normalized:
                out['genres'][genre_key] = normalized
    return out


def _write_fallback_alias_meta(graph: dict, alias_meta: dict) -> None:
    graph.setdefault('_meta', {})
    common = (alias_meta or {}).get('common') or {}
    genres = (alias_meta or {}).get('genres') or {}
    graph['_meta']['fallback_alias_map'] = {
        'common': {
            str(alias or '').strip(): str(target or '').strip()
            for alias, target in sorted(common.items())
            if str(alias or '').strip() and str(target or '').strip()
        },
        'genres': {
            str(genre or '').strip(): {
                str(alias or '').strip(): str(target or '').strip()
                for alias, target in sorted((mapping or {}).items())
                if str(alias or '').strip() and str(target or '').strip()
            }
            for genre, mapping in sorted((genres or {}).items())
            if str(genre or '').strip() and any(str(a or '').strip() and str(t or '').strip() for a, t in (mapping or {}).items())
        },
    }


def list_action_fallback_alias_rules() -> dict:
    graph = _load_action_graph()
    alias_meta = _fallback_alias_meta(graph)
    items = []
    for alias, target in sorted((alias_meta.get('common') or {}).items()):
        items.append({
            'scope': 'common',
            'genre': '',
            'alias_term': alias,
            'formal_head': target,
        })
    for genre, mapping in sorted((alias_meta.get('genres') or {}).items()):
        for alias, target in sorted((mapping or {}).items()):
            items.append({
                'scope': 'genre',
                'genre': genre,
                'alias_term': alias,
                'formal_head': target,
            })
    return {'count': len(items), 'items': items}


def has_action_formal_head(formal_head: str, *, scope: str = 'common', genre: str = '') -> bool:
    head = str(formal_head or '').strip()
    scope_key = str(scope or 'common').strip().lower()
    genre_key = str(genre or '').strip()
    if not head:
        return False
    graph = _load_action_graph()
    if scope_key == 'genre' and genre_key:
        return bool((((graph.get('genres') or {}).get(genre_key) or {}).get(head)) or {})
    return bool(((graph.get('common') or {}).get(head)) or {})


def apply_action_fallback_resolution(
    formal_head: str,
    alias_terms: list[str],
    *,
    scope: str = 'common',
    genre: str = '',
) -> dict:
    head = str(formal_head or '').strip()
    scope_key = str(scope or 'common').strip().lower()
    genre_key = str(genre or '').strip()
    aliases = _merge_unique([str(x or '').strip() for x in (alias_terms or []) if str(x or '').strip()])
    aliases = [term for term in aliases if term != head]
    if not head:
        return {'ok': False, 'detail': 'formal_head is required'}
    if scope_key not in {'common', 'genre'}:
        return {'ok': False, 'detail': 'scope must be common or genre'}
    if scope_key == 'genre' and not genre_key:
        return {'ok': False, 'detail': 'genre scope requires genre'}
    graph = _load_action_graph()
    graph.setdefault('common', {})
    graph.setdefault('genres', {})

    target_bucket = graph['common'] if scope_key == 'common' else graph['genres'].setdefault(genre_key, {})
    existing = target_bucket.get(head) or {}
    semantic_terms, sfx_terms = _row_terms(existing)
    semantic_terms = _merge_unique(semantic_terms + [head])
    sfx_terms = _merge_unique(sfx_terms + [head])
    row_genre = '' if scope_key == 'common' else genre_key
    target_bucket[head] = {
        'parent_node': {
            'genre': row_genre,
            'verb_head': head,
            'node_key': build_action_node_key(row_genre, head),
        },
        'children': {
            'semantic_terms': semantic_terms,
            'sfx_terms': sfx_terms,
        },
        'semantic_terms': semantic_terms,
        'sfx_terms': sfx_terms,
    }

    alias_meta = _fallback_alias_meta(graph)
    common_map = dict(alias_meta.get('common') or {})
    genre_maps = {str(k): dict(v or {}) for k, v in (alias_meta.get('genres') or {}).items()}
    if scope_key == 'common':
        for alias in aliases:
            common_map[alias] = head
        for mapping in genre_maps.values():
            for alias in aliases:
                mapping.pop(alias, None)
    else:
        genre_map = genre_maps.setdefault(genre_key, {})
        for alias in aliases:
            genre_map[alias] = head
    _write_fallback_alias_meta(graph, {'common': common_map, 'genres': genre_maps})
    _save_action_graph(graph)
    return {
        'ok': True,
        'scope': scope_key,
        'genre': genre_key,
        'formal_head': head,
        'alias_terms': aliases,
        'node_key': build_action_node_key(row_genre, head),
        'rules': list_action_fallback_alias_rules(),
    }


def remove_action_fallback_alias_rule(alias_term: str, *, scope: str = 'common', genre: str = '') -> dict:
    alias = str(alias_term or '').strip()
    scope_key = str(scope or 'common').strip().lower()
    genre_key = str(genre or '').strip()
    if not alias:
        return {'ok': False, 'detail': 'alias_term is required'}
    if scope_key not in {'common', 'genre'}:
        return {'ok': False, 'detail': 'scope must be common or genre'}
    if scope_key == 'genre' and not genre_key:
        return {'ok': False, 'detail': 'genre scope requires genre'}

    graph = _load_action_graph()
    alias_meta = _fallback_alias_meta(graph)
    common_map = dict(alias_meta.get('common') or {})
    genre_maps = {str(k): dict(v or {}) for k, v in (alias_meta.get('genres') or {}).items()}

    removed = ''
    if scope_key == 'common':
        removed = str(common_map.pop(alias, '') or '').strip()
    else:
        genre_map = genre_maps.setdefault(genre_key, {})
        removed = str(genre_map.pop(alias, '') or '').strip()

    if not removed:
        return {'ok': False, 'detail': 'rule not found'}

    _write_fallback_alias_meta(graph, {'common': common_map, 'genres': genre_maps})
    _save_action_graph(graph)
    return {
        'ok': True,
        'scope': scope_key,
        'genre': genre_key,
        'alias_term': alias,
        'formal_head': removed,
        'rules': list_action_fallback_alias_rules(),
    }


def _source_key(common_hit: bool, genre_hit: bool) -> str:
    if common_hit and genre_hit:
        return 'common+genre'
    if genre_hit:
        return 'genre'
    if common_hit:
        return 'common'
    return 'unknown'


def _source_label(source: str) -> str:
    return {
        'common': '通用层',
        'genre': '赛道层',
        'common+genre': '通用+赛道',
        'fallback': '保底生成',
        'unknown': '未标注',
    }.get(str(source or '').strip(), '未标注')


def _row_terms(row: dict | None) -> tuple[list[str], list[str]]:
    if not isinstance(row, dict):
        return [], []
    children = row.get('children') or {}
    semantic_terms = children.get('semantic_terms') or row.get('semantic_terms') or []
    sfx_terms = children.get('sfx_terms') or row.get('sfx_terms') or []
    semantic_terms = [str(x).strip() for x in semantic_terms if str(x).strip()]
    sfx_terms = [str(x).strip() for x in sfx_terms if str(x).strip()]
    return _merge_unique(semantic_terms), _merge_unique(sfx_terms)


def _term_items(common_terms: list[str], genre_terms: list[str]) -> list[dict]:
    ordered = _merge_unique(list(common_terms) + list(genre_terms))
    common_set = set(common_terms)
    genre_set = set(genre_terms)
    out = []
    for term in ordered:
        source = _source_key(term in common_set, term in genre_set)
        out.append(
            {
                'term': term,
                'source': source,
                'source_label': _source_label(source),
                'from_common': term in common_set,
                'from_genre': term in genre_set,
            }
        )
    return out


def _append_fallback_items(existing_items: list[dict], fallback_terms: list[str]) -> list[dict]:
    out = [dict(item) for item in (existing_items or []) if isinstance(item, dict)]
    existing_terms = {str(item.get('term') or '').strip() for item in out if str(item.get('term') or '').strip()}
    for term in fallback_terms or []:
        value = str(term or '').strip()
        if not value or value in existing_terms:
            continue
        out.append(
            {
                'term': value,
                'source': 'fallback',
                'source_label': _source_label('fallback'),
                'from_common': False,
                'from_genre': False,
                'is_fallback': True,
            }
        )
        existing_terms.add(value)
    return out


def _layer_payload(layer: str, genre: str, verb_head: str, row: dict | None) -> dict:
    semantic_terms, sfx_terms = _row_terms(row)
    classified = classify_sfx_terms(sfx_terms)
    node_key = build_action_node_key('' if layer == 'common' else genre, verb_head)
    return {
        'layer': layer,
        'layer_label': '通用层' if layer == 'common' else '赛道层',
        'exists': bool(row),
        'genre': '' if layer == 'common' else genre,
        'verb_head': verb_head,
        'node_key': node_key,
        'semantic_terms': semantic_terms,
        'sfx_terms': sfx_terms,
        'direct_sfx_terms': classified['direct_terms'],
        'composite_sfx_terms': classified['composite_terms'],
        'summary': {
            'semantic_count': len(semantic_terms),
            'sfx_count': len(sfx_terms),
            'direct_sfx_count': len(classified['direct_terms']),
            'composite_sfx_count': len(classified['composite_terms']),
        },
    }


def _collect_term_occurrences(graph: dict, term_type: str, verb_head: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for genre_name, bucket in ((graph.get('genres') or {}).items()):
        if not isinstance(bucket, dict):
            continue
        row = bucket.get(verb_head)
        semantic_terms, sfx_terms = _row_terms(row)
        terms = semantic_terms if term_type == 'semantic' else sfx_terms
        for term in terms:
            out.setdefault(term, set()).add(str(genre_name).strip())
    return out


def _promotion_candidates(
    graph: dict,
    verb_head: str,
    common_terms: list[str],
    genre_terms: list[str],
    term_type: str,
) -> list[dict]:
    common_set = {str(x).strip() for x in common_terms if str(x).strip()}
    genre_only = [str(x).strip() for x in genre_terms if str(x).strip() and str(x).strip() not in common_set]
    occurrences = _collect_term_occurrences(graph, term_type, verb_head)
    out = []
    for term in genre_only:
        genres = sorted(str(x).strip() for x in (occurrences.get(term) or set()) if str(x).strip())
        if len(genres) >= 2:
            reason = f'该词已在 {len(genres)} 个赛道出现，可优先评估是否提升为通用层'
            priority = 'high'
        else:
            reason = '当前只在本赛道出现，如判断为跨赛道通用动作边，也可提升到通用层'
            priority = 'normal'
        out.append(
            {
                'term': term,
                'term_type': term_type,
                'candidate_action': 'promote_to_common',
                'reason': reason,
                'priority': priority,
                'appears_in_genres': genres,
                'genre_count': len(genres),
            }
        )
    out.sort(key=lambda item: (-int(item.get('genre_count') or 0), item.get('term') or ''))
    return out


def _demotion_candidates(
    common_terms: list[str],
    genre_terms: list[str],
    term_type: str,
) -> list[dict]:
    genre_set = {str(x).strip() for x in genre_terms if str(x).strip()}
    common_only = [str(x).strip() for x in common_terms if str(x).strip() and str(x).strip() not in genre_set]
    out = []
    for term in common_only:
        out.append(
            {
                'term': term,
                'term_type': term_type,
                'candidate_action': 'demote_to_genre',
                'reason': '该词当前只在通用层存在。如判断它更适合当前赛道特化表达，可迁移到当前赛道层。',
                'priority': 'normal',
                'appears_in_genres': [],
                'genre_count': 0,
            }
        )
    out.sort(key=lambda item: item.get('term') or '')
    return out


def _overlap_candidates(common_terms: list[str], genre_terms: list[str], term_type: str) -> list[dict]:
    common_set = {str(x).strip() for x in common_terms if str(x).strip()}
    genre_set = {str(x).strip() for x in genre_terms if str(x).strip()}
    overlap = sorted(common_set & genre_set)
    return [
        {
            'term': term,
            'term_type': term_type,
            'candidate_action': 'overlap_cleanup',
            'reason': '该词同时存在于通用层和当前赛道层，可按业务判断保留一侧并移除另一侧。',
            'priority': 'normal',
        }
        for term in overlap
    ]


def get_action_graph_node_layers(node_key: str, target_genre: str = '') -> dict:
    nk = str(node_key or '').strip()
    if not nk:
        return {'detail': 'node_key is required'}
    genre, verb_head = '', nk
    if '::' in nk:
        genre, verb_head = nk.split('::', 1)
    genre = str(genre or '').strip()
    compare_genre = str(target_genre or genre or '').strip()
    verb_head = str(verb_head or '').strip()
    if not verb_head:
        return {'detail': 'verb_head is required'}

    graph = _load_action_graph()
    common_row = ((graph.get('common') or {}).get(verb_head)) if isinstance(graph.get('common'), dict) else None
    genre_row = None
    if compare_genre:
        genre_row = ((((graph.get('genres') or {}).get(compare_genre)) or {}).get(verb_head)) if isinstance((graph.get('genres') or {}).get(compare_genre), dict) else None
    inheritance_blocked = is_inheritance_blocked(compare_genre, verb_head, graph) if compare_genre else False

    common_semantic, common_sfx = _row_terms(common_row)
    genre_semantic, genre_sfx = _row_terms(genre_row)
    merged_semantic_items = _term_items(common_semantic, genre_semantic)
    merged_sfx_items = _term_items(common_sfx, genre_sfx)
    front_sfx_terms = ensure_action_sfx_terms(verb_head, [item['term'] for item in merged_sfx_items])
    merged_sfx_items = _append_fallback_items(
        merged_sfx_items,
        [term for term in front_sfx_terms if term not in {str(item.get('term') or '').strip() for item in merged_sfx_items}],
    )
    merged_sfx_classified = classify_sfx_terms([item['term'] for item in merged_sfx_items])
    direct_set = set(merged_sfx_classified['direct_terms'])
    composite_set = set(merged_sfx_classified['composite_terms'])
    direct_items = [item for item in merged_sfx_items if item['term'] in direct_set]
    composite_items = [item for item in merged_sfx_items if item['term'] in composite_set]
    semantic_candidates = _promotion_candidates(graph, verb_head, common_semantic, genre_semantic, 'semantic') if compare_genre else []
    sfx_candidates = _promotion_candidates(graph, verb_head, common_sfx, genre_sfx, 'sfx') if compare_genre else []
    demote_semantic_candidates = _demotion_candidates(common_semantic, genre_semantic, 'semantic')
    demote_sfx_candidates = _demotion_candidates(common_sfx, genre_sfx, 'sfx')
    overlap_semantic_candidates = _overlap_candidates(common_semantic, genre_semantic, 'semantic')
    overlap_sfx_candidates = _overlap_candidates(common_sfx, genre_sfx, 'sfx')

    return {
        'node_key': build_action_node_key(genre, verb_head),
        'genre': genre,
        'compare_genre': compare_genre,
        'verb_head': verb_head,
        'inheritance_blocked': inheritance_blocked,
        'common_layer': _layer_payload('common', compare_genre, verb_head, common_row),
        'genre_layer': _layer_payload('genre', compare_genre, verb_head, genre_row),
        'merged': {
            'semantic_terms': [item['term'] for item in merged_semantic_items],
            'semantic_term_items': merged_semantic_items,
            'sfx_terms': [item['term'] for item in merged_sfx_items],
            'sfx_term_items': merged_sfx_items,
            'direct_sfx_terms': [item['term'] for item in direct_items],
            'direct_sfx_term_items': direct_items,
            'composite_sfx_terms': [item['term'] for item in composite_items],
            'composite_sfx_term_items': composite_items,
            'summary': {
                'semantic_count': len(merged_semantic_items),
                'sfx_count': len(merged_sfx_items),
                'direct_sfx_count': len(direct_items),
                'composite_sfx_count': len(composite_items),
            },
            'has_fallback_terms': any(str(item.get('source') or '').strip() == 'fallback' for item in merged_sfx_items),
        },
        'migration_candidates': {
            'semantic_terms': semantic_candidates,
            'sfx_terms': sfx_candidates,
        },
        'demotion_candidates': {
            'semantic_terms': demote_semantic_candidates,
            'sfx_terms': demote_sfx_candidates,
        },
        'overlap_candidates': {
            'semantic_terms': overlap_semantic_candidates,
            'sfx_terms': overlap_sfx_candidates,
        },
    }


def list_action_graph_maintenance_catalog() -> dict:
    graph = _load_action_graph()
    common_bucket = graph.get('common') or {}
    genres_bucket = graph.get('genres') or {}
    inheritance_blocks = _inheritance_blocks(graph)
    common_heads = {str(head).strip() for head in common_bucket.keys() if str(head).strip()}
    genre_names = sorted({str(name).strip() for name in genres_bucket.keys() if str(name).strip()}, key=lambda x: x)
    sections = []

    def _build_node_item(genre: str, verb_head: str) -> dict:
        common_row = common_bucket.get(verb_head) if isinstance(common_bucket, dict) else None
        genre_bucket = genres_bucket.get(genre) if isinstance(genres_bucket.get(genre), dict) else {}
        genre_row = genre_bucket.get(verb_head) if genre else None
        common_semantic, common_sfx = _row_terms(common_row)
        genre_semantic, genre_sfx = _row_terms(genre_row)
        merged_sfx_items = _term_items(common_sfx, genre_sfx)
        front_sfx_terms = ensure_action_sfx_terms(verb_head, [item['term'] for item in merged_sfx_items])
        has_fallback_terms = any(str(term or '').strip() not in {str(item.get('term') or '').strip() for item in merged_sfx_items} for term in front_sfx_terms)
        return {
            'verb_head': verb_head,
            'node_key': build_action_node_key(genre, verb_head),
            'common_exists': bool(common_row),
            'genre_exists': bool(genre_row),
            'semantic_count': len(_merge_unique(common_semantic + genre_semantic)),
            'sfx_count': len(_merge_unique(common_sfx + genre_sfx)),
            'has_fallback_terms': has_fallback_terms,
        }

    if common_heads:
        common_items = [_build_node_item('', head) for head in sorted(common_heads)]
        sections.append(
            {
                'genre': '',
                'genre_key': 'common',
                'label': '通用层',
                'node_count': len(common_items),
                'items': common_items,
            }
        )

    for genre in genre_names:
        genre_bucket = genres_bucket.get(genre) if isinstance(genres_bucket.get(genre), dict) else {}
        blocked_heads = inheritance_blocks.get(genre, set())
        inherited_heads = common_heads - blocked_heads
        heads = sorted(inherited_heads | {str(head).strip() for head in genre_bucket.keys() if str(head).strip()})
        items = [_build_node_item(genre, head) for head in heads]
        sections.append(
            {
                'genre': genre,
                'genre_key': genre,
                'label': genre,
                'node_count': len(items),
                'items': items,
            }
        )

    return {
        'genres': sections,
        'genre_count': len(sections),
        'total_nodes': sum(int(section.get('node_count') or 0) for section in sections),
    }


def update_action_graph_node_layer(node_key: str, layer: str, semantic_terms: list[str], sfx_terms: list[str]) -> dict:
    nk = str(node_key or '').strip()
    layer_key = str(layer or '').strip().lower()
    if layer_key not in {'common', 'genre'}:
        return {'ok': False, 'detail': 'layer must be common or genre'}
    if not nk:
        return {'ok': False, 'detail': 'node_key is required'}

    genre, verb_head = '', nk
    if '::' in nk:
        genre, verb_head = nk.split('::', 1)
    genre = str(genre or '').strip()
    verb_head = str(verb_head or '').strip()
    if not verb_head:
        return {'ok': False, 'detail': 'verb_head is required'}
    if layer_key == 'genre' and not genre:
        return {'ok': False, 'detail': 'genre layer requires genre in node_key'}

    semantic_terms = _merge_unique(semantic_terms or [])
    sfx_terms = _merge_unique(sfx_terms or [])
    graph = _load_action_graph()
    graph.setdefault('common', {})
    graph.setdefault('genres', {})

    target_bucket = graph['common'] if layer_key == 'common' else graph['genres'].setdefault(genre, {})
    if not semantic_terms and not sfx_terms:
        target_bucket.pop(verb_head, None)
        if layer_key == 'genre' and not graph['genres'].get(genre):
            graph['genres'].pop(genre, None)
    else:
        row_genre = '' if layer_key == 'common' else genre
        target_bucket[verb_head] = {
            'parent_node': {
                'genre': row_genre,
                'verb_head': verb_head,
                'node_key': build_action_node_key(row_genre, verb_head),
            },
            'children': {
                'semantic_terms': semantic_terms,
                'sfx_terms': sfx_terms,
            },
            'semantic_terms': semantic_terms,
            'sfx_terms': sfx_terms,
        }

    _save_action_graph(graph)
    payload = get_action_graph_node_layers(build_action_node_key(genre, verb_head))
    payload['ok'] = True
    payload['updated_layer'] = layer_key
    return payload


def promote_action_graph_terms_to_common(
    node_key: str,
    semantic_terms: list[str],
    sfx_terms: list[str],
    remove_from_genre: bool = False,
) -> dict:
    nk = str(node_key or '').strip()
    if not nk:
        return {'ok': False, 'detail': 'node_key is required'}
    genre, verb_head = '', nk
    if '::' in nk:
        genre, verb_head = nk.split('::', 1)
    genre = str(genre or '').strip()
    verb_head = str(verb_head or '').strip()
    if not genre or not verb_head:
        return {'ok': False, 'detail': 'promotion requires genre::verb_head node_key'}

    graph = _load_action_graph()
    graph.setdefault('common', {})
    graph.setdefault('genres', {})
    genre_bucket = graph['genres'].setdefault(genre, {})
    common_bucket = graph['common']

    common_row = common_bucket.get(verb_head) or {}
    genre_row = genre_bucket.get(verb_head) or {}
    common_semantic, common_sfx = _row_terms(common_row)
    genre_semantic, genre_sfx = _row_terms(genre_row)

    semantic_terms = _merge_unique(semantic_terms or [])
    sfx_terms = _merge_unique(sfx_terms or [])
    genre_semantic_set = set(genre_semantic)
    genre_sfx_set = set(genre_sfx)
    linked_from_semantic = [term for term in semantic_terms if term in genre_sfx_set]
    linked_from_sfx = [term for term in sfx_terms if term in genre_semantic_set]
    semantic_terms = _merge_unique(semantic_terms + linked_from_sfx)
    sfx_terms = _merge_unique(sfx_terms + linked_from_semantic)
    if not semantic_terms and not sfx_terms:
        return {'ok': False, 'detail': 'no terms selected to promote'}

    next_common_semantic = _merge_unique(common_semantic + semantic_terms)
    next_common_sfx = _merge_unique(common_sfx + sfx_terms)
    common_bucket[verb_head] = {
        'parent_node': {
            'genre': '',
            'verb_head': verb_head,
            'node_key': build_action_node_key('', verb_head),
        },
        'children': {
            'semantic_terms': next_common_semantic,
            'sfx_terms': next_common_sfx,
        },
        'semantic_terms': next_common_semantic,
        'sfx_terms': next_common_sfx,
    }

    if remove_from_genre and genre_row:
        next_genre_semantic = [term for term in genre_semantic if term not in set(semantic_terms)]
        next_genre_sfx = [term for term in genre_sfx if term not in set(sfx_terms)]
        if next_genre_semantic or next_genre_sfx:
            genre_bucket[verb_head] = {
                'parent_node': {
                    'genre': genre,
                    'verb_head': verb_head,
                    'node_key': build_action_node_key(genre, verb_head),
                },
                'children': {
                    'semantic_terms': next_genre_semantic,
                    'sfx_terms': next_genre_sfx,
                },
                'semantic_terms': next_genre_semantic,
                'sfx_terms': next_genre_sfx,
            }
        else:
            genre_bucket.pop(verb_head, None)
    if not graph['genres'].get(genre):
        graph['genres'].pop(genre, None)

    _save_action_graph(graph)
    payload = get_action_graph_node_layers(build_action_node_key(genre, verb_head))
    payload['ok'] = True
    payload['promoted_semantic_terms'] = semantic_terms
    payload['promoted_sfx_terms'] = sfx_terms
    payload['remove_from_genre'] = bool(remove_from_genre)
    return payload


def demote_action_graph_terms_to_genre(
    node_key: str,
    semantic_terms: list[str],
    sfx_terms: list[str],
    target_genre: str = '',
    remove_from_common: bool = False,
) -> dict:
    nk = str(node_key or '').strip()
    if not nk:
        return {'ok': False, 'detail': 'node_key is required'}
    genre, verb_head = '', nk
    if '::' in nk:
        genre, verb_head = nk.split('::', 1)
    genre = str(target_genre or genre or '').strip()
    verb_head = str(verb_head or '').strip()
    if not genre or not verb_head:
        return {'ok': False, 'detail': 'demotion requires target genre and verb_head'}

    graph = _load_action_graph()
    graph.setdefault('common', {})
    graph.setdefault('genres', {})
    genre_bucket = graph['genres'].setdefault(genre, {})
    common_bucket = graph['common']

    common_row = common_bucket.get(verb_head) or {}
    genre_row = genre_bucket.get(verb_head) or {}
    common_semantic, common_sfx = _row_terms(common_row)
    genre_semantic, genre_sfx = _row_terms(genre_row)

    semantic_terms = _merge_unique(semantic_terms or [])
    sfx_terms = _merge_unique(sfx_terms or [])
    common_semantic_set = set(common_semantic)
    common_sfx_set = set(common_sfx)
    linked_from_semantic = [term for term in semantic_terms if term in common_sfx_set]
    linked_from_sfx = [term for term in sfx_terms if term in common_semantic_set]
    semantic_terms = _merge_unique(semantic_terms + linked_from_sfx)
    sfx_terms = _merge_unique(sfx_terms + linked_from_semantic)
    if not semantic_terms and not sfx_terms:
        return {'ok': False, 'detail': 'no terms selected to move into genre'}

    next_genre_semantic = _merge_unique(genre_semantic + semantic_terms)
    next_genre_sfx = _merge_unique(genre_sfx + sfx_terms)
    genre_bucket[verb_head] = {
        'parent_node': {
            'genre': genre,
            'verb_head': verb_head,
            'node_key': build_action_node_key(genre, verb_head),
        },
        'children': {
            'semantic_terms': next_genre_semantic,
            'sfx_terms': next_genre_sfx,
        },
        'semantic_terms': next_genre_semantic,
        'sfx_terms': next_genre_sfx,
    }

    if remove_from_common and common_row:
        next_common_semantic = [term for term in common_semantic if term not in set(semantic_terms)]
        next_common_sfx = [term for term in common_sfx if term not in set(sfx_terms)]
        if next_common_semantic or next_common_sfx:
            common_bucket[verb_head] = {
                'parent_node': {
                    'genre': '',
                    'verb_head': verb_head,
                    'node_key': build_action_node_key('', verb_head),
                },
                'children': {
                    'semantic_terms': next_common_semantic,
                    'sfx_terms': next_common_sfx,
                },
                'semantic_terms': next_common_semantic,
                'sfx_terms': next_common_sfx,
            }
        else:
            common_bucket.pop(verb_head, None)

    _save_action_graph(graph)
    payload = get_action_graph_node_layers(build_action_node_key(genre, verb_head))
    payload['ok'] = True
    payload['demoted_semantic_terms'] = semantic_terms
    payload['demoted_sfx_terms'] = sfx_terms
    payload['remove_from_common'] = bool(remove_from_common)
    return payload


def remove_action_graph_overlap_terms(
    node_key: str,
    semantic_terms: list[str],
    sfx_terms: list[str],
    remove_from_layer: str,
    target_genre: str = '',
) -> dict:
    nk = str(node_key or '').strip()
    if not nk:
        return {'ok': False, 'detail': 'node_key is required'}
    genre, verb_head = '', nk
    if '::' in nk:
        genre, verb_head = nk.split('::', 1)
    genre = str(target_genre or genre or '').strip()
    verb_head = str(verb_head or '').strip()
    layer_key = str(remove_from_layer or '').strip().lower()
    if layer_key not in {'common', 'genre'}:
        return {'ok': False, 'detail': 'remove_from_layer must be common or genre'}
    if not genre or not verb_head:
        return {'ok': False, 'detail': 'overlap cleanup requires genre::verb_head node_key'}

    graph = _load_action_graph()
    graph.setdefault('common', {})
    graph.setdefault('genres', {})
    common_bucket = graph['common']
    genre_bucket = graph['genres'].setdefault(genre, {})
    common_row = common_bucket.get(verb_head) or {}
    genre_row = genre_bucket.get(verb_head) or {}
    common_semantic, common_sfx = _row_terms(common_row)
    genre_semantic, genre_sfx = _row_terms(genre_row)

    semantic_terms = _merge_unique(semantic_terms or [])
    sfx_terms = _merge_unique(sfx_terms or [])
    if not semantic_terms and not sfx_terms:
        return {'ok': False, 'detail': 'no overlap terms selected'}

    target_semantic = set(semantic_terms)
    target_sfx = set(sfx_terms)
    if layer_key == 'genre':
        next_semantic = [term for term in genre_semantic if term not in target_semantic]
        next_sfx = [term for term in genre_sfx if term not in target_sfx]
        if next_semantic or next_sfx:
            genre_bucket[verb_head] = {
                'parent_node': {'genre': genre, 'verb_head': verb_head, 'node_key': build_action_node_key(genre, verb_head)},
                'children': {'semantic_terms': next_semantic, 'sfx_terms': next_sfx},
                'semantic_terms': next_semantic,
                'sfx_terms': next_sfx,
            }
        else:
            genre_bucket.pop(verb_head, None)
    else:
        next_semantic = [term for term in common_semantic if term not in target_semantic]
        next_sfx = [term for term in common_sfx if term not in target_sfx]
        if next_semantic or next_sfx:
            common_bucket[verb_head] = {
                'parent_node': {'genre': '', 'verb_head': verb_head, 'node_key': build_action_node_key('', verb_head)},
                'children': {'semantic_terms': next_semantic, 'sfx_terms': next_sfx},
                'semantic_terms': next_semantic,
                'sfx_terms': next_sfx,
            }
        else:
            common_bucket.pop(verb_head, None)
    if not graph['genres'].get(genre):
        graph['genres'].pop(genre, None)
    _save_action_graph(graph)
    payload = get_action_graph_node_layers(build_action_node_key(genre, verb_head), target_genre=genre)
    payload['ok'] = True
    payload['removed_overlap_semantic_terms'] = semantic_terms
    payload['removed_overlap_sfx_terms'] = sfx_terms
    payload['removed_from_layer'] = layer_key
    return payload


def delete_action_graph_node(
    node_key: str,
    action: str,
    target_genre: str = '',
) -> dict:
    nk = str(node_key or '').strip()
    if not nk:
        return {'ok': False, 'detail': 'node_key is required'}
    genre, verb_head = '', nk
    if '::' in nk:
        genre, verb_head = nk.split('::', 1)
    genre = str(genre or '').strip()
    verb_head = str(verb_head or '').strip()
    target_genre = str(target_genre or '').strip()
    action_key = str(action or '').strip().lower()
    if not verb_head:
        return {'ok': False, 'detail': 'verb_head is required'}
    if action_key not in {'delete_current_layer', 'delete_genre_keep_common', 'delete_common_keep_genre'}:
        return {'ok': False, 'detail': 'invalid action'}

    graph = _load_action_graph()
    graph.setdefault('common', {})
    graph.setdefault('genres', {})
    common_bucket = graph['common']
    genres_bucket = graph['genres']
    common_row = common_bucket.get(verb_head) if isinstance(common_bucket, dict) else None
    active_genre = target_genre or genre
    genre_bucket = genres_bucket.get(active_genre) if isinstance(genres_bucket.get(active_genre), dict) else {}
    genre_row = genre_bucket.get(verb_head) if isinstance(genre_bucket, dict) else None

    deleted_layer = ''
    result_node_key = build_action_node_key(genre, verb_head)
    compare_genre = active_genre

    if action_key == 'delete_current_layer':
        if genre:
            if not genre_row:
                return {'ok': False, 'detail': '当前赛道层节点不存在，无法删除'}
            genre_bucket.pop(verb_head, None)
            if not genre_bucket:
                genres_bucket.pop(active_genre, None)
            deleted_layer = 'genre'
            result_node_key = build_action_node_key(genre, verb_head)
            compare_genre = genre
        else:
            if not common_row:
                return {'ok': False, 'detail': '当前通用层节点不存在，无法删除'}
            common_bucket.pop(verb_head, None)
            deleted_layer = 'common'
            result_node_key = build_action_node_key('', verb_head)
            compare_genre = target_genre
    elif action_key == 'delete_genre_keep_common':
        if not genre:
            return {'ok': False, 'detail': '该操作需要在赛道层节点视角下执行'}
        if not common_row:
            return {'ok': False, 'detail': '当前没有通用层节点，无法执行“只保留通用层”'}
        if not genre_row:
            return {'ok': False, 'detail': '当前赛道层节点不存在，无法删除'}
        genre_bucket.pop(verb_head, None)
        if not genre_bucket:
            genres_bucket.pop(active_genre, None)
        deleted_layer = 'genre'
        result_node_key = build_action_node_key(genre, verb_head)
        compare_genre = genre
    else:
        if genre:
            active_genre = genre
            genre_bucket = genres_bucket.get(active_genre) if isinstance(genres_bucket.get(active_genre), dict) else {}
            genre_row = genre_bucket.get(verb_head) if isinstance(genre_bucket, dict) else None
        if not active_genre:
            return {'ok': False, 'detail': '请选择要保留的目标赛道'}
        if not genre_row:
            return {'ok': False, 'detail': '目标赛道层节点不存在，无法执行“只保留当前赛道层”'}
        if not common_row:
            return {'ok': False, 'detail': '当前没有通用层节点，无法删除'}
        common_bucket.pop(verb_head, None)
        deleted_layer = 'common'
        result_node_key = build_action_node_key(active_genre, verb_head)
        compare_genre = active_genre

    _save_action_graph(graph)
    payload = get_action_graph_node_layers(result_node_key, target_genre=compare_genre)
    payload['ok'] = True
    payload['node_delete_action'] = action_key
    payload['deleted_layer'] = deleted_layer
    payload['result_node_key'] = result_node_key
    payload['target_genre'] = active_genre
    return payload


def set_action_graph_inheritance_block(verb_head: str, genre: str, blocked: bool) -> dict:
    head = str(verb_head or '').strip()
    genre_name = str(genre or '').strip()
    if not head:
        return {'ok': False, 'detail': 'verb_head is required'}
    if not genre_name:
        return {'ok': False, 'detail': 'genre is required'}
    graph = _load_action_graph()
    if not ((graph.get('common') or {}).get(head)):
        return {'ok': False, 'detail': '只能屏蔽或恢复通用层真实节点的赛道继承'}
    blocks = _inheritance_blocks(graph)
    heads = set(blocks.get(genre_name, set()))
    if blocked:
        heads.add(head)
    else:
        heads.discard(head)
    if heads:
        blocks[genre_name] = heads
    else:
        blocks.pop(genre_name, None)
    _write_inheritance_blocks(graph, blocks)
    _save_action_graph(graph)
    return {
        'ok': True,
        'verb_head': head,
        'genre': genre_name,
        'blocked': bool(blocked),
        'inheritance_blocked': is_inheritance_blocked(genre_name, head, graph),
    }


def list_action_graph_inheritance_blocks() -> dict:
    graph = _load_action_graph()
    blocks = _inheritance_blocks(graph)
    common_bucket = graph.get('common') or {}
    genres_bucket = graph.get('genres') or {}
    items = []
    for genre, heads in sorted(blocks.items()):
        for head in sorted(heads):
            common_row = common_bucket.get(head) if isinstance(common_bucket, dict) else None
            genre_row = ((((genres_bucket.get(genre)) or {}).get(head)) if isinstance(genres_bucket.get(genre), dict) else None)
            semantic_terms, sfx_terms = _row_terms(common_row)
            genre_semantic_terms, genre_sfx_terms = _row_terms(genre_row)
            items.append(
                {
                    'genre': genre,
                    'verb_head': head,
                    'node_key': build_action_node_key('', head),
                    'blocked_node_key': build_action_node_key(genre, head),
                    'common_exists': bool(common_row),
                    'genre_exists': bool(genre_row),
                    'semantic_count': len(semantic_terms),
                    'sfx_count': len(sfx_terms),
                    'genre_semantic_count': len(genre_semantic_terms),
                    'genre_sfx_count': len(genre_sfx_terms),
                }
            )
    return {
        'items': items,
        'count': len(items),
    }
