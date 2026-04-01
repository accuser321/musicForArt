import json
from datetime import datetime
from pathlib import Path

from app.services.action_sfx_graph import (
    build_action_node_key,
    classify_sfx_terms,
    ensure_action_sfx_terms,
)
from app.services.semantic_graph import suggest_action_semantic_terms, suggest_action_sfx_terms

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


def _genre_assignments(graph: dict | None = None) -> dict[str, set[str]]:
    data = graph if isinstance(graph, dict) else _load_action_graph()
    meta = data.get('_meta') or {}
    raw = meta.get('genre_assignments') or {}
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


def is_genre_assigned(genre: str, verb_head: str, graph: dict | None = None) -> bool:
    genre_key = str(genre or '').strip()
    head_key = str(verb_head or '').strip()
    if not genre_key or not head_key:
        return False
    return head_key in _genre_assignments(graph).get(genre_key, set())


def _write_genre_assignments(graph: dict, assignments: dict[str, set[str]]) -> None:
    graph.setdefault('_meta', {})
    graph['_meta']['genre_assignments'] = {
        genre: sorted(heads)
        for genre, heads in sorted(assignments.items())
        if genre and heads
    }


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


def _deleted_genre_nodes_meta(graph: dict | None = None) -> dict[str, dict[str, dict]]:
    data = graph if isinstance(graph, dict) else _load_action_graph()
    meta = data.get('_meta') or {}
    raw = meta.get('deleted_genre_nodes') or {}
    out: dict[str, dict[str, dict]] = {}
    if not isinstance(raw, dict):
        return out
    for genre, mapping in raw.items():
        genre_key = str(genre or '').strip()
        if not genre_key or not isinstance(mapping, dict):
            continue
        normalized: dict[str, dict] = {}
        for verb_head, payload in mapping.items():
            head_key = str(verb_head or '').strip()
            if not head_key or not isinstance(payload, dict):
                continue
            semantic_terms = _merge_unique([str(x or '').strip() for x in (payload.get('semantic_terms') or []) if str(x or '').strip()])
            sfx_terms = _merge_unique([str(x or '').strip() for x in (payload.get('sfx_terms') or []) if str(x or '').strip()])
            if not semantic_terms and not sfx_terms:
                continue
            normalized[head_key] = {
                'semantic_terms': semantic_terms,
                'sfx_terms': sfx_terms,
                'deleted_at': str(payload.get('deleted_at') or '').strip(),
            }
        if normalized:
            out[genre_key] = normalized
    return out


def _write_deleted_genre_nodes_meta(graph: dict, deleted_nodes: dict[str, dict[str, dict]]) -> None:
    graph.setdefault('_meta', {})
    normalized: dict[str, dict[str, dict]] = {}
    for genre, mapping in sorted((deleted_nodes or {}).items()):
        genre_key = str(genre or '').strip()
        if not genre_key or not isinstance(mapping, dict):
            continue
        genre_map: dict[str, dict] = {}
        for verb_head, payload in sorted(mapping.items()):
            head_key = str(verb_head or '').strip()
            if not head_key or not isinstance(payload, dict):
                continue
            semantic_terms = _merge_unique([str(x or '').strip() for x in (payload.get('semantic_terms') or []) if str(x or '').strip()])
            sfx_terms = _merge_unique([str(x or '').strip() for x in (payload.get('sfx_terms') or []) if str(x or '').strip()])
            if not semantic_terms and not sfx_terms:
                continue
            genre_map[head_key] = {
                'semantic_terms': semantic_terms,
                'sfx_terms': sfx_terms,
                'deleted_at': str(payload.get('deleted_at') or '').strip(),
            }
        if genre_map:
            normalized[genre_key] = genre_map
    graph['_meta']['deleted_genre_nodes'] = normalized


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


def _fallback_replacement_meta(graph: dict | None = None) -> dict:
    data = graph if isinstance(graph, dict) else _load_action_graph()
    meta = data.get('_meta') or {}
    raw = meta.get('fallback_replacement_map') or {}
    out = {'common': {}, 'genres': {}}
    if not isinstance(raw, dict):
        return out
    common = raw.get('common') or {}
    if isinstance(common, dict):
        out['common'] = {
            str(source or '').strip(): _merge_unique(
                [str(term or '').strip() for term in (targets or []) if str(term or '').strip()]
            )
            for source, targets in common.items()
            if str(source or '').strip()
        }
        out['common'] = {k: v for k, v in out['common'].items() if v}
    genres = raw.get('genres') or {}
    if isinstance(genres, dict):
        for genre, mapping in genres.items():
            genre_key = str(genre or '').strip()
            if not genre_key or not isinstance(mapping, dict):
                continue
            normalized = {
                str(source or '').strip(): _merge_unique(
                    [str(term or '').strip() for term in (targets or []) if str(term or '').strip()]
                )
                for source, targets in mapping.items()
                if str(source or '').strip()
            }
            normalized = {k: v for k, v in normalized.items() if v}
            if normalized:
                out['genres'][genre_key] = normalized
    return out


def _write_fallback_replacement_meta(graph: dict, replacement_meta: dict) -> None:
    graph.setdefault('_meta', {})
    common = (replacement_meta or {}).get('common') or {}
    genres = (replacement_meta or {}).get('genres') or {}
    graph['_meta']['fallback_replacement_map'] = {
        'common': {
            str(source or '').strip(): _merge_unique(
                [str(term or '').strip() for term in (targets or []) if str(term or '').strip()]
            )
            for source, targets in sorted(common.items())
            if str(source or '').strip()
            and _merge_unique([str(term or '').strip() for term in (targets or []) if str(term or '').strip()])
        },
        'genres': {
            str(genre or '').strip(): {
                str(source or '').strip(): _merge_unique(
                    [str(term or '').strip() for term in (targets or []) if str(term or '').strip()]
                )
                for source, targets in sorted((mapping or {}).items())
                if str(source or '').strip()
                and _merge_unique([str(term or '').strip() for term in (targets or []) if str(term or '').strip()])
            }
            for genre, mapping in sorted((genres or {}).items())
            if str(genre or '').strip()
            and any(
                str(source or '').strip()
                and _merge_unique([str(term or '').strip() for term in (targets or []) if str(term or '').strip()])
                for source, targets in (mapping or {}).items()
            )
        },
    }


def _fallback_reopen_queue_meta(graph: dict | None = None) -> list[dict]:
    data = graph if isinstance(graph, dict) else _load_action_graph()
    meta = data.get('_meta') or {}
    raw = meta.get('fallback_reopen_queue') or []
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        source_term = str(item.get('source_term') or '').strip()
        if not source_term:
            continue
        out.append(
            {
                'source_term': source_term,
                'genre': str(item.get('genre') or '').strip(),
                'replacement_terms': _merge_unique([str(x or '').strip() for x in (item.get('replacement_terms') or []) if str(x or '').strip()]),
                'released_at': str(item.get('released_at') or '').strip(),
            }
        )
    return out


def _write_fallback_reopen_queue_meta(graph: dict, items: list[dict]) -> None:
    graph.setdefault('_meta', {})
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        source_term = str(item.get('source_term') or '').strip()
        if not source_term:
            continue
        normalized.append(
            {
                'source_term': source_term,
                'genre': str(item.get('genre') or '').strip(),
                'replacement_terms': _merge_unique([str(x or '').strip() for x in (item.get('replacement_terms') or []) if str(x or '').strip()]),
                'released_at': str(item.get('released_at') or '').strip(),
            }
        )
    graph['_meta']['fallback_reopen_queue'] = normalized


def _fallback_ignored_meta(graph: dict | None = None) -> dict:
    data = graph if isinstance(graph, dict) else _load_action_graph()
    meta = data.get('_meta') or {}
    raw = meta.get('fallback_ignored_terms') or {}
    out = {'common': set(), 'genres': {}}
    if not isinstance(raw, dict):
        return out
    common = raw.get('common') or []
    out['common'] = {str(term or '').strip() for term in common if str(term or '').strip()}
    genres = raw.get('genres') or {}
    if isinstance(genres, dict):
        for genre, items in genres.items():
            genre_key = str(genre or '').strip()
            if not genre_key:
                continue
            term_set = {str(term or '').strip() for term in (items or []) if str(term or '').strip()}
            if term_set:
                out['genres'][genre_key] = term_set
    return out


def _write_fallback_ignored_meta(graph: dict, ignored_meta: dict) -> None:
    graph.setdefault('_meta', {})
    common = sorted({str(term or '').strip() for term in ((ignored_meta or {}).get('common') or set()) if str(term or '').strip()})
    genres = {}
    for genre, items in sorted((((ignored_meta or {}).get('genres') or {}).items())):
        genre_key = str(genre or '').strip()
        if not genre_key:
            continue
        term_list = sorted({str(term or '').strip() for term in (items or set()) if str(term or '').strip()})
        if term_list:
            genres[genre_key] = term_list
    graph['_meta']['fallback_ignored_terms'] = {
        'common': common,
        'genres': genres,
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


def list_action_fallback_replacement_rules() -> dict:
    graph = _load_action_graph()
    replacement_meta = _fallback_replacement_meta(graph)
    items = []
    for source, targets in sorted((replacement_meta.get('common') or {}).items()):
        items.append({
            'scope': 'common',
            'genre': '',
            'source_term': source,
            'replacement_terms': targets,
        })
    for genre, mapping in sorted((replacement_meta.get('genres') or {}).items()):
        for source, targets in sorted((mapping or {}).items()):
            items.append({
                'scope': 'genre',
                'genre': genre,
                'source_term': source,
                'replacement_terms': targets,
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
    ignored_terms: list[str] | None = None,
) -> dict:
    head = str(formal_head or '').strip()
    scope_key = str(scope or 'common').strip().lower()
    genre_key = str(genre or '').strip()
    aliases = _merge_unique([str(x or '').strip() for x in (alias_terms or []) if str(x or '').strip()])
    aliases = [term for term in aliases if term != head]
    ignored = _merge_unique([str(x or '').strip() for x in (ignored_terms or []) if str(x or '').strip()])
    ignored = [term for term in ignored if term != head and term not in aliases]
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
    ignored_meta = _fallback_ignored_meta(graph)
    common_map = dict(alias_meta.get('common') or {})
    genre_maps = {str(k): dict(v or {}) for k, v in (alias_meta.get('genres') or {}).items()}
    common_ignored = set((ignored_meta.get('common') or set()))
    genre_ignored = {str(k): set(v or set()) for k, v in (ignored_meta.get('genres') or {}).items()}
    if scope_key == 'common':
        for alias in aliases:
            common_map[alias] = head
            common_ignored.discard(alias)
        for mapping in genre_maps.values():
            for alias in aliases:
                mapping.pop(alias, None)
        for term in ignored:
            common_ignored.add(term)
            common_map.pop(term, None)
            for mapping in genre_maps.values():
                mapping.pop(term, None)
    else:
        genre_map = genre_maps.setdefault(genre_key, {})
        genre_ignore_set = genre_ignored.setdefault(genre_key, set())
        for alias in aliases:
            genre_map[alias] = head
            genre_ignore_set.discard(alias)
        for term in ignored:
            genre_ignore_set.add(term)
            genre_map.pop(term, None)
    _write_fallback_alias_meta(graph, {'common': common_map, 'genres': genre_maps})
    _write_fallback_ignored_meta(graph, {'common': common_ignored, 'genres': genre_ignored})
    _save_action_graph(graph)
    return {
        'ok': True,
        'scope': scope_key,
        'genre': genre_key,
        'formal_head': head,
        'alias_terms': aliases,
        'ignored_terms': ignored,
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


def apply_action_fallback_replacements(
    source_term: str,
    replacement_terms: list[str],
    *,
    genre: str = '',
    ignored_terms: list[str] | None = None,
) -> dict:
    source = str(source_term or '').strip()
    genre_key = str(genre or '').strip()
    replacements = _merge_unique([str(x or '').strip() for x in (replacement_terms or []) if str(x or '').strip()])
    ignored = _merge_unique([str(x or '').strip() for x in (ignored_terms or []) if str(x or '').strip()])
    ignored = [term for term in ignored if term not in replacements]
    if not source:
        return {'ok': False, 'detail': 'source_term is required'}
    if not replacements and not ignored:
        return {'ok': False, 'detail': 'replacement_terms or ignored_terms is required'}

    graph = _load_action_graph()
    replacement_meta = _fallback_replacement_meta(graph)
    ignored_meta = _fallback_ignored_meta(graph)
    reopen_queue = _fallback_reopen_queue_meta(graph)
    common_map = {str(k): list(v or []) for k, v in (replacement_meta.get('common') or {}).items()}
    genre_maps = {str(k): {str(a): list(b or []) for a, b in (v or {}).items()} for k, v in (replacement_meta.get('genres') or {}).items()}
    common_ignored = set((ignored_meta.get('common') or set()))
    genre_ignored = {str(k): set(v or set()) for k, v in (ignored_meta.get('genres') or {}).items()}

    if genre_key:
        mapping = genre_maps.setdefault(genre_key, {})
        ignore_set = genre_ignored.setdefault(genre_key, set())
        if replacements:
            mapping[source] = replacements
            ignore_set.discard(source)
        elif source in mapping:
            mapping.pop(source, None)
        for term in ignored:
            ignore_set.add(term)
            mapping.pop(term, None)
    else:
        if replacements:
            common_map[source] = replacements
            common_ignored.discard(source)
        elif source in common_map:
            common_map.pop(source, None)
        for term in ignored:
            common_ignored.add(term)
            common_map.pop(term, None)
            for mapping in genre_maps.values():
                mapping.pop(term, None)
        if source in ignored and source in common_map:
            common_map.pop(source, None)

    _write_fallback_replacement_meta(graph, {'common': common_map, 'genres': genre_maps})
    _write_fallback_ignored_meta(graph, {'common': common_ignored, 'genres': genre_ignored})
    reopen_queue = [
        item for item in reopen_queue
        if not (
            str(item.get('source_term') or '').strip() == source
            and str(item.get('genre') or '').strip() == genre_key
        )
    ]
    _write_fallback_reopen_queue_meta(graph, reopen_queue)
    _save_action_graph(graph)
    return {
        'ok': True,
        'genre': genre_key,
        'source_term': source,
        'replacement_terms': replacements,
        'ignored_terms': ignored,
        'rules': list_action_fallback_replacement_rules(),
    }


def remove_action_fallback_replacement_rule(source_term: str, *, genre: str = '') -> dict:
    source = str(source_term or '').strip()
    genre_key = str(genre or '').strip()
    if not source:
        return {'ok': False, 'detail': 'source_term is required'}
    graph = _load_action_graph()
    replacement_meta = _fallback_replacement_meta(graph)
    reopen_queue = _fallback_reopen_queue_meta(graph)
    common_map = {str(k): list(v or []) for k, v in (replacement_meta.get('common') or {}).items()}
    genre_maps = {str(k): {str(a): list(b or []) for a, b in (v or {}).items()} for k, v in (replacement_meta.get('genres') or {}).items()}
    removed = []
    if genre_key:
        mapping = genre_maps.setdefault(genre_key, {})
        removed = list(mapping.pop(source, []) or [])
    else:
        removed = list(common_map.pop(source, []) or [])
    if not removed:
        return {'ok': False, 'detail': 'rule not found'}
    _write_fallback_replacement_meta(graph, {'common': common_map, 'genres': genre_maps})
    reopen_queue.append(
        {
            'source_term': source,
            'genre': genre_key,
            'replacement_terms': removed,
            'released_at': '',
        }
    )
    _write_fallback_reopen_queue_meta(graph, reopen_queue)
    _save_action_graph(graph)
    return {
        'ok': True,
        'genre': genre_key,
        'source_term': source,
        'replacement_terms': removed,
        'rules': list_action_fallback_replacement_rules(),
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
        'suggested': '系统建议扩展词',
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
                'origin': 'system_maintained',
                'origin_label': '系统维护',
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
                'origin': 'system_fallback',
                'origin_label': '系统兜底',
                'from_common': False,
                'from_genre': False,
                'is_fallback': True,
            }
        )
        existing_terms.add(value)
    return out


def _append_suggested_items(existing_items: list[dict], suggested_terms: list[dict | str]) -> list[dict]:
    out = [dict(item) for item in (existing_items or []) if isinstance(item, dict)]
    existing_terms = {str(item.get('term') or '').strip() for item in out if str(item.get('term') or '').strip()}
    for term in suggested_terms or []:
        if isinstance(term, dict):
            value = str(term.get('term') or '').strip()
            origin = str(term.get('origin') or 'system').strip() or 'system'
            origin_label = str(term.get('origin_label') or '系统建议').strip() or '系统建议'
            reason = str(term.get('reason') or '').strip()
            source_label = str(term.get('source_label') or _source_label('suggested')).strip() or _source_label('suggested')
            suggested_mode = str(term.get('suggested_mode') or '').strip()
        else:
            value = str(term or '').strip()
            origin = 'system'
            origin_label = '系统建议'
            reason = ''
            source_label = _source_label('suggested')
            suggested_mode = ''
        if not value or value in existing_terms:
            continue
        out.append(
            {
                'term': value,
                'source': 'suggested',
                'source_label': source_label,
                'origin': origin,
                'origin_label': origin_label,
                'reason': reason,
                'suggested_mode': suggested_mode,
                'from_common': False,
                'from_genre': False,
                'is_suggested': True,
            }
        )
        existing_terms.add(value)
    return out


def _split_sfx_term_items(term_items: list[dict]) -> tuple[list[dict], list[dict]]:
    direct_items: list[dict] = []
    composite_items: list[dict] = []
    classified = classify_sfx_terms([str(item.get('term') or '').strip() for item in (term_items or [])])
    direct_set = set(classified['direct_terms'])
    for item in term_items or []:
        term = str(item.get('term') or '').strip()
        mode = str(item.get('suggested_mode') or '').strip()
        if mode == 'direct' or (not mode and term in direct_set):
            direct_items.append(item)
        else:
            composite_items.append(item)
    return direct_items, composite_items


def _layer_payload(layer: str, genre: str, verb_head: str, row: dict | None) -> dict:
    semantic_terms, sfx_terms = _row_terms(row)
    classified = classify_sfx_terms(sfx_terms)
    node_key = build_action_node_key('' if layer == 'common' else genre, verb_head)
    return {
        'layer': layer,
        'layer_label': '通用元数据' if layer == 'common' else '赛道特化',
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
    any_genre_rows = []
    for genre_name, bucket in ((graph.get('genres') or {}).items()):
        genre_name = str(genre_name or '').strip()
        if not genre_name or not isinstance(bucket, dict):
            continue
        row = bucket.get(verb_head)
        if isinstance(row, dict) and row:
            any_genre_rows.append({'genre': genre_name, 'row': row})
    any_genre_exists = bool(any_genre_rows)
    any_genre_names = sorted({str(item.get('genre') or '').strip() for item in any_genre_rows if str(item.get('genre') or '').strip()})
    genre_assigned = is_genre_assigned(compare_genre, verb_head, graph) if compare_genre else False
    inheritance_blocked = (not genre_assigned) if compare_genre and common_row else False

    active_common_row = common_row if (not compare_genre or genre_assigned or bool(genre_row)) else None
    common_semantic, common_sfx = _row_terms(active_common_row)
    genre_semantic, genre_sfx = _row_terms(genre_row)
    merged_semantic_items = _term_items(common_semantic, genre_semantic)
    existing_semantic_set = {str(item.get('term') or '').strip() for item in merged_semantic_items if str(item.get('term') or '').strip()}
    pending_semantic_items = [
        {
            'term': item.term,
            'origin': item.origin,
            'origin_label': item.origin_label,
            'source_label': '系统建议扩展词',
            'reason': item.reason,
            'score': item.score,
        }
        for item in suggest_action_semantic_terms(verb_head, genre=compare_genre, limit=10)
        if str(item.term or '').strip() not in existing_semantic_set
    ][:10]
    merged_semantic_items = _append_suggested_items(merged_semantic_items, pending_semantic_items)
    merged_sfx_items = _term_items(common_sfx, genre_sfx)
    pending_sfx_items = [
        {
            'term': item.term,
            'origin': item.origin,
            'origin_label': item.origin_label,
            'source_label': '系统建议音效',
            'suggested_mode': 'direct' if str(item.mode or '').strip() == 'direct' else 'composite',
            'reason': item.reason,
            'score': item.score,
        }
        for item in suggest_action_sfx_terms(
            verb_head,
            genre=compare_genre,
            semantic_terms=[item['term'] for item in merged_semantic_items],
            limit=10,
        )
        if str(item.term or '').strip() not in {str(existing.get('term') or '').strip() for existing in merged_sfx_items}
    ][:10]
    front_sfx_terms = ensure_action_sfx_terms(
        verb_head,
        [item['term'] for item in merged_sfx_items] + [item['term'] for item in pending_sfx_items],
    )
    merged_sfx_items = _append_fallback_items(
        merged_sfx_items,
        [term for term in front_sfx_terms if term not in {str(item.get('term') or '').strip() for item in merged_sfx_items}],
    )
    merged_sfx_items = _append_suggested_items(
        merged_sfx_items,
        [
            item for item in pending_sfx_items
            if str(item.get('term') or '').strip() not in {str(existing.get('term') or '').strip() for existing in merged_sfx_items}
        ],
    )
    direct_items, composite_items = _split_sfx_term_items(merged_sfx_items)
    merged_sfx_classified = classify_sfx_terms(
        [item['term'] for item in direct_items] + [item['term'] for item in composite_items]
    )
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
        'genre_assigned': genre_assigned,
        'inheritance_blocked': inheritance_blocked,
        'genre_layer_any_exists': any_genre_exists,
        'genre_layer_any_genres': any_genre_names,
        'common_layer': _layer_payload('common', compare_genre, verb_head, common_row),
        'genre_layer': _layer_payload('genre', compare_genre, verb_head, genre_row),
        'merged': {
            'semantic_terms': [item['term'] for item in merged_semantic_items],
            'semantic_term_items': merged_semantic_items,
            'pending_semantic_term_items': [item for item in merged_semantic_items if str(item.get('source') or '').strip() == 'suggested'],
            'sfx_terms': [item['term'] for item in merged_sfx_items],
            'sfx_term_items': merged_sfx_items,
            'direct_sfx_terms': [item['term'] for item in direct_items],
            'direct_sfx_term_items': direct_items,
            'composite_sfx_terms': [item['term'] for item in composite_items],
            'composite_sfx_term_items': composite_items,
            'pending_sfx_term_items': [item for item in merged_sfx_items if str(item.get('source') or '').strip() == 'suggested'],
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
    assignments = _genre_assignments(graph)
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

    common_items = [_build_node_item('', head) for head in sorted(common_heads)]
    sections.append(
        {
            'genre': '',
            'genre_key': 'common',
            'label': '通用元数据',
            'node_count': len(common_items),
            'items': common_items,
        }
    )

    for genre in genre_names:
        genre_bucket = genres_bucket.get(genre) if isinstance(genres_bucket.get(genre), dict) else {}
        assigned_heads = assignments.get(genre, set())
        heads = sorted(assigned_heads | {str(head).strip() for head in genre_bucket.keys() if str(head).strip()})
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
    deleted_genre_nodes = _deleted_genre_nodes_meta(graph)

    target_bucket = graph['common'] if layer_key == 'common' else graph['genres'].setdefault(genre, {})
    if not semantic_terms and not sfx_terms:
        existing_row = target_bucket.get(verb_head) or {}
        existing_semantic_terms, existing_sfx_terms = _row_terms(existing_row)
        target_bucket.pop(verb_head, None)
        if layer_key == 'genre':
            genre_deleted = deleted_genre_nodes.setdefault(genre, {})
            if existing_semantic_terms or existing_sfx_terms:
                genre_deleted[verb_head] = {
                    'semantic_terms': existing_semantic_terms,
                    'sfx_terms': existing_sfx_terms,
                    'deleted_at': datetime.now().isoformat(),
                }
            else:
                genre_deleted.pop(verb_head, None)
                if not genre_deleted:
                    deleted_genre_nodes.pop(genre, None)
        if layer_key == 'genre' and not graph['genres'].get(genre):
            graph['genres'].pop(genre, None)
        if layer_key == 'common':
            assignments = _genre_assignments(graph)
            blocks = _inheritance_blocks(graph)
            for genre_key in list(assignments.keys()):
                heads = set(assignments.get(genre_key, set()))
                if verb_head in heads:
                    heads.discard(verb_head)
                    if heads:
                        assignments[genre_key] = heads
                    else:
                        assignments.pop(genre_key, None)
            for genre_key in list(blocks.keys()):
                heads = set(blocks.get(genre_key, set()))
                if verb_head in heads:
                    heads.discard(verb_head)
                    if heads:
                        blocks[genre_key] = heads
                    else:
                        blocks.pop(genre_key, None)
            _write_genre_assignments(graph, assignments)
            _write_inheritance_blocks(graph, blocks)
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
        if layer_key == 'genre':
            genre_deleted = deleted_genre_nodes.get(genre) or {}
            genre_deleted.pop(verb_head, None)
            if genre_deleted:
                deleted_genre_nodes[genre] = genre_deleted
            else:
                deleted_genre_nodes.pop(genre, None)

    _write_deleted_genre_nodes_meta(graph, deleted_genre_nodes)
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
    if remove_from_genre:
        assignments = _genre_assignments(graph)
        genre_heads = set(assignments.get(genre, set()))
        genre_heads.add(verb_head)
        assignments[genre] = genre_heads
        _write_genre_assignments(graph, assignments)

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
    if not remove_from_common:
        assignments = _genre_assignments(graph)
        genre_heads = set(assignments.get(genre, set()))
        genre_heads.add(verb_head)
        assignments[genre] = genre_heads
        _write_genre_assignments(graph, assignments)
    else:
        assignments = _genre_assignments(graph)
        genre_heads = set(assignments.get(genre, set()))
        genre_heads.discard(verb_head)
        if genre_heads:
            assignments[genre] = genre_heads
        else:
            assignments.pop(genre, None)
        _write_genre_assignments(graph, assignments)

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
    deleted_genre_nodes = _deleted_genre_nodes_meta(graph)
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
            genre_semantic_terms, genre_sfx_terms = _row_terms(genre_row)
            genre_bucket.pop(verb_head, None)
            if genre_semantic_terms or genre_sfx_terms:
                genre_deleted = deleted_genre_nodes.setdefault(active_genre, {})
                genre_deleted[verb_head] = {
                    'semantic_terms': genre_semantic_terms,
                    'sfx_terms': genre_sfx_terms,
                    'deleted_at': datetime.now().isoformat(),
                }
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
        genre_semantic_terms, genre_sfx_terms = _row_terms(genre_row)
        genre_bucket.pop(verb_head, None)
        if genre_semantic_terms or genre_sfx_terms:
            genre_deleted = deleted_genre_nodes.setdefault(active_genre, {})
            genre_deleted[verb_head] = {
                'semantic_terms': genre_semantic_terms,
                'sfx_terms': genre_sfx_terms,
                'deleted_at': datetime.now().isoformat(),
            }
        if not genre_bucket:
            genres_bucket.pop(active_genre, None)
        assignments = _genre_assignments(graph)
        genre_heads = set(assignments.get(active_genre, set()))
        genre_heads.add(verb_head)
        assignments[active_genre] = genre_heads
        _write_genre_assignments(graph, assignments)
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

    _write_deleted_genre_nodes_meta(graph, deleted_genre_nodes)
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
    deleted_genre_nodes = _deleted_genre_nodes_meta(graph)
    if not blocked:
        deleted_payload = ((deleted_genre_nodes.get(genre_name) or {}).get(head)) or {}
        if deleted_payload:
            semantic_terms = _merge_unique([str(x or '').strip() for x in (deleted_payload.get('semantic_terms') or []) if str(x or '').strip()])
            sfx_terms = _merge_unique([str(x or '').strip() for x in (deleted_payload.get('sfx_terms') or []) if str(x or '').strip()])
            genre_bucket = graph['genres'].setdefault(genre_name, {})
            genre_bucket[head] = {
                'parent_node': {
                    'genre': genre_name,
                    'verb_head': head,
                    'node_key': build_action_node_key(genre_name, head),
                },
                'children': {
                    'semantic_terms': semantic_terms,
                    'sfx_terms': sfx_terms,
                },
                'semantic_terms': semantic_terms,
                'sfx_terms': sfx_terms,
            }
            genre_deleted = deleted_genre_nodes.get(genre_name) or {}
            genre_deleted.pop(head, None)
            if genre_deleted:
                deleted_genre_nodes[genre_name] = genre_deleted
            else:
                deleted_genre_nodes.pop(genre_name, None)
            _write_deleted_genre_nodes_meta(graph, deleted_genre_nodes)
            _save_action_graph(graph)
            return {
                'ok': True,
                'verb_head': head,
                'genre': genre_name,
                'blocked': False,
                'restored_from': 'genre_deleted_pool',
                'inheritance_blocked': False,
                'genre_assigned': is_genre_assigned(genre_name, head, graph),
            }
    if not ((graph.get('common') or {}).get(head)):
        return {'ok': False, 'detail': '只能调整通用元数据节点的赛道分配'}
    assignments = _genre_assignments(graph)
    blocks = _inheritance_blocks(graph)
    heads = set(assignments.get(genre_name, set()))
    blocked_heads = set(blocks.get(genre_name, set()))
    if blocked:
        heads.discard(head)
        blocked_heads.add(head)
    else:
        heads.add(head)
        blocked_heads.discard(head)
    if heads:
        assignments[genre_name] = heads
    else:
        assignments.pop(genre_name, None)
    if blocked_heads:
        blocks[genre_name] = blocked_heads
    else:
        blocks.pop(genre_name, None)
    _write_genre_assignments(graph, assignments)
    _write_inheritance_blocks(graph, blocks)
    _save_action_graph(graph)
    return {
        'ok': True,
        'verb_head': head,
        'genre': genre_name,
        'blocked': bool(blocked),
        'inheritance_blocked': not is_genre_assigned(genre_name, head, graph),
        'genre_assigned': is_genre_assigned(genre_name, head, graph),
    }


def list_action_graph_inheritance_blocks() -> dict:
    graph = _load_action_graph()
    blocks = _inheritance_blocks(graph)
    deleted_genre_nodes = _deleted_genre_nodes_meta(graph)
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
    for genre, mapping in sorted(deleted_genre_nodes.items()):
        for head, payload in sorted((mapping or {}).items()):
            genre_row = ((((genres_bucket.get(genre)) or {}).get(head)) if isinstance(genres_bucket.get(genre), dict) else None)
            semantic_terms = _merge_unique([str(x or '').strip() for x in (payload.get('semantic_terms') or []) if str(x or '').strip()])
            sfx_terms = _merge_unique([str(x or '').strip() for x in (payload.get('sfx_terms') or []) if str(x or '').strip()])
            items.append(
                {
                    'genre': genre,
                    'verb_head': head,
                    'node_key': build_action_node_key(genre, head),
                    'blocked_node_key': build_action_node_key(genre, head),
                    'common_exists': bool((common_bucket.get(head) if isinstance(common_bucket, dict) else None)),
                    'genre_exists': bool(genre_row),
                    'semantic_count': 0,
                    'sfx_count': 0,
                    'genre_semantic_count': len(semantic_terms),
                    'genre_sfx_count': len(sfx_terms),
                    'deleted_from': 'genre_layer',
                    'deleted_at': str(payload.get('deleted_at') or '').strip(),
                }
            )
    return {
        'items': items,
        'count': len(items),
    }
