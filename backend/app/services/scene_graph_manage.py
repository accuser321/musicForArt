import json
from pathlib import Path

SCENE_GRAPH_PATH = Path(__file__).resolve().parents[2] / 'assets' / 'scene' / 'scene_graph.json'
SUPPORTED_SCENE_GENRES = ['玄幻', '言情', '悬疑', '科幻']


def _merge_unique(items: list[str] | None) -> list[str]:
    seen = set()
    out = []
    for item in items or []:
        value = str(item or '').strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _split_multiline_text(raw: str | None) -> list[str]:
    text = str(raw or '').replace('，', '\n').replace(',', '\n').replace('、', '\n')
    return _merge_unique([line.strip() for line in text.splitlines()])


def _ensure_graph_shape(graph: dict | None) -> dict:
    data = graph if isinstance(graph, dict) else {}
    common = data.get('common') if isinstance(data.get('common'), dict) else {}
    genres = data.get('genres') if isinstance(data.get('genres'), dict) else {}
    templates = data.get('templates') if isinstance(data.get('templates'), dict) else {}
    for genre in SUPPORTED_SCENE_GENRES:
        if not isinstance(genres.get(genre), dict):
            genres[genre] = {}
    return {'common': common, 'genres': genres, 'templates': templates}


def load_scene_graph() -> dict:
    if not SCENE_GRAPH_PATH.exists():
        return _ensure_graph_shape({})
    try:
        return _ensure_graph_shape(json.loads(SCENE_GRAPH_PATH.read_text(encoding='utf-8')))
    except Exception:
        return _ensure_graph_shape({})


def save_scene_graph(graph: dict) -> dict:
    normalized = _ensure_graph_shape(graph)
    SCENE_GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCENE_GRAPH_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding='utf-8')
    return normalized


def parse_scene_node_key(node_key: str) -> tuple[str, str]:
    raw = str(node_key or '').strip()
    if not raw:
      return '', ''
    if '::' in raw:
      layer, scene_name = raw.split('::', 1)
      return str(layer or '').strip(), str(scene_name or '').strip()
    return 'common', raw


def build_scene_node_key(layer_key: str, scene_name: str) -> str:
    layer = str(layer_key or '').strip() or 'common'
    scene = str(scene_name or '').strip()
    return f'{layer}::{scene}' if scene else ''


def _normalize_scene_node_payload(payload: dict | None) -> dict:
    row = payload if isinstance(payload, dict) else {}
    return {
        'template_name': str(row.get('template_name') or '').strip(),
        'collection_name': str(row.get('collection_name') or '').strip(),
        'background_elements': _merge_unique(row.get('background_elements') or []),
        'feature_elements': _merge_unique(row.get('feature_elements') or []),
        'detail_elements': _merge_unique(row.get('detail_elements') or []),
        'supporting_sfx_terms': _merge_unique(row.get('supporting_sfx_terms') or []),
        'detail_sfx_terms': _merge_unique(row.get('detail_sfx_terms') or []),
    }


def list_scene_term_suggestions(template_name: str | None = None, collection_name: str | None = None) -> dict:
    graph = load_scene_graph()
    template = str(template_name or '').strip()
    collection = str(collection_name or '').strip()
    suggestion_map = {
        'background_elements': [],
        'feature_elements': [],
        'detail_elements': [],
        'supporting_sfx_terms': [],
        'detail_sfx_terms': [],
    }
    if not template and not collection:
        return {'ok': True, 'template_name': '', 'collection_name': '', 'suggestions': suggestion_map}

    def _collect_from_row(row: dict | None):
        row_norm = _normalize_scene_node_payload(row)
        if template and str(row_norm.get('template_name') or '').strip() != template:
            return
        if collection and str(row_norm.get('collection_name') or '').strip() != collection:
            return
        for key in suggestion_map.keys():
            suggestion_map[key] = _merge_unique(suggestion_map[key] + (row_norm.get(key) or []))

    for _, row in (graph.get('common') or {}).items():
        _collect_from_row(row)
    for _, scene_map in (graph.get('genres') or {}).items():
        for _, row in (scene_map or {}).items():
            _collect_from_row(row)

    return {
        'ok': True,
        'template_name': template,
        'collection_name': collection,
        'suggestions': suggestion_map,
    }


def list_scene_graph_catalog() -> dict:
    graph = load_scene_graph()
    common_items = [
        {
            'scene_name': scene_name,
            'node_key': build_scene_node_key('common', scene_name),
            'template_name': str(payload.get('template_name') or '').strip(),
            'collection_name': str(payload.get('collection_name') or '').strip(),
            'has_common': True,
            'has_genre': False,
        }
        for scene_name, payload in sorted((graph.get('common') or {}).items(), key=lambda x: x[0])
    ]
    genres = []
    for genre in SUPPORTED_SCENE_GENRES:
        items = [
            {
                'scene_name': scene_name,
                'node_key': build_scene_node_key(genre, scene_name),
                'template_name': str(payload.get('template_name') or '').strip(),
                'collection_name': str(payload.get('collection_name') or '').strip(),
                'has_common': scene_name in (graph.get('common') or {}),
                'has_genre': True,
            }
            for scene_name, payload in sorted((graph.get('genres') or {}).get(genre, {}).items(), key=lambda x: x[0])
        ]
        inherited_only = [
            {
                'scene_name': scene_name,
                'node_key': build_scene_node_key(genre, scene_name),
                'template_name': str(payload.get('template_name') or '').strip(),
                'collection_name': str(payload.get('collection_name') or '').strip(),
                'has_common': True,
                'has_genre': False,
            }
            for scene_name, payload in sorted((graph.get('common') or {}).items(), key=lambda x: x[0])
            if scene_name not in (graph.get('genres') or {}).get(genre, {})
        ]
        genres.append(
            {
                'genre_key': genre,
                'label': genre,
                'node_count': len(items) + len(inherited_only),
                'items': items + inherited_only,
            }
        )
    template_items = [
        {
            'template_name': template_name,
            'collection_name': str(payload.get('collection_name') or '').strip(),
            'aliases': _merge_unique(payload.get('aliases') or []),
        }
        for template_name, payload in sorted((graph.get('templates') or {}).items(), key=lambda x: x[0])
    ]
    return {
        'common': common_items,
        'genres': genres,
        'templates': template_items,
        'summary': {
            'common_node_count': len(common_items),
            'genre_node_count': sum(len(section.get('items') or []) for section in genres),
            'template_count': len(template_items),
        },
    }


def get_scene_graph_node_layers(node_key: str) -> dict:
    graph = load_scene_graph()
    layer_key, scene_name = parse_scene_node_key(node_key)
    common_row = _normalize_scene_node_payload((graph.get('common') or {}).get(scene_name))
    genre_row = _normalize_scene_node_payload(((graph.get('genres') or {}).get(layer_key) or {}).get(scene_name))
    is_common_view = layer_key in {'', 'common'}
    inheriting_genres = []
    real_genre_nodes = []
    for genre in SUPPORTED_SCENE_GENRES:
        genre_has_real = bool(((graph.get('genres') or {}).get(genre) or {}).get(scene_name))
        if genre_has_real:
            real_genre_nodes.append(genre)
        if bool((graph.get('common') or {}).get(scene_name)) and not genre_has_real:
            inheriting_genres.append(genre)
    merged = {
        'template_name': genre_row.get('template_name') or common_row.get('template_name') or '',
        'collection_name': genre_row.get('collection_name') or common_row.get('collection_name') or '',
        'background_elements': _merge_unique(common_row.get('background_elements') + genre_row.get('background_elements')),
        'feature_elements': _merge_unique(common_row.get('feature_elements') + genre_row.get('feature_elements')),
        'detail_elements': _merge_unique(common_row.get('detail_elements') + genre_row.get('detail_elements')),
        'supporting_sfx_terms': _merge_unique(common_row.get('supporting_sfx_terms') + genre_row.get('supporting_sfx_terms')),
        'detail_sfx_terms': _merge_unique(common_row.get('detail_sfx_terms') + genre_row.get('detail_sfx_terms')),
    }
    return {
        'ok': True,
        'node_key': build_scene_node_key('common' if is_common_view else layer_key, scene_name),
        'scene_name': scene_name,
        'view_layer': 'common' if is_common_view else 'genre',
        'view_genre': '' if is_common_view else layer_key,
        'common_layer': {'exists': bool((graph.get('common') or {}).get(scene_name)), **common_row},
        'genre_layer': {'exists': bool(((graph.get('genres') or {}).get(layer_key) or {}).get(scene_name)), **genre_row},
        'inheritance_scope': {
            'inheriting_genres': inheriting_genres,
            'real_genre_nodes': real_genre_nodes,
        },
        'merged': merged,
        'source_explanation': (
            '通用层节点视角'
            if is_common_view
            else ('通用层+赛道层' if ((graph.get('common') or {}).get(scene_name) and ((graph.get('genres') or {}).get(layer_key) or {}).get(scene_name)) else ('赛道层维护' if ((graph.get('genres') or {}).get(layer_key) or {}).get(scene_name) else '通用层继承'))
        ),
    }


def update_scene_graph_node_layer(node_key: str, layer: str, payload: dict | None) -> dict:
    graph = load_scene_graph()
    layer_key, scene_name = parse_scene_node_key(node_key)
    target_layer = str(layer or '').strip().lower()
    normalized = _normalize_scene_node_payload(payload)
    has_content = any(normalized.get(k) for k in normalized)
    if not scene_name:
        return {'ok': False, 'detail': 'scene_name is required'}
    if target_layer == 'common':
        bucket = graph.setdefault('common', {})
        if has_content:
            bucket[scene_name] = normalized
        else:
            bucket.pop(scene_name, None)
    elif target_layer == 'genre':
        if layer_key not in SUPPORTED_SCENE_GENRES:
            return {'ok': False, 'detail': 'genre node_key is required for genre layer update'}
        bucket = graph.setdefault('genres', {}).setdefault(layer_key, {})
        if has_content:
            bucket[scene_name] = normalized
        else:
            bucket.pop(scene_name, None)
    else:
        return {'ok': False, 'detail': 'layer must be common or genre'}
    save_scene_graph(graph)
    return get_scene_graph_node_layers(build_scene_node_key('common' if target_layer == 'common' else layer_key, scene_name))


def promote_scene_node_to_common(node_key: str, retain_genre: bool = True) -> dict:
    graph = load_scene_graph()
    genre_key, scene_name = parse_scene_node_key(node_key)
    if genre_key in {'', 'common'} or genre_key not in SUPPORTED_SCENE_GENRES:
        return {'ok': False, 'detail': '需要从赛道层节点发起迁移'}
    genre_bucket = (graph.get('genres') or {}).get(genre_key) or {}
    genre_row = genre_bucket.get(scene_name)
    if not isinstance(genre_row, dict):
        return {'ok': False, 'detail': '当前赛道层节点不存在，无法迁移'}
    graph.setdefault('common', {})[scene_name] = _normalize_scene_node_payload(genre_row)
    if not retain_genre:
        genre_bucket.pop(scene_name, None)
    save_scene_graph(graph)
    return get_scene_graph_node_layers(build_scene_node_key(genre_key, scene_name))


def demote_scene_node_to_genre(node_key: str, target_genre: str, retain_common: bool = True) -> dict:
    graph = load_scene_graph()
    layer_key, scene_name = parse_scene_node_key(node_key)
    genre_key = str(target_genre or '').strip()
    if genre_key not in SUPPORTED_SCENE_GENRES:
        return {'ok': False, 'detail': 'target_genre is invalid'}
    if layer_key not in {'', 'common'}:
        return {'ok': False, 'detail': '需要从通用层节点发起迁移'}
    common_bucket = graph.get('common') or {}
    common_row = common_bucket.get(scene_name)
    if not isinstance(common_row, dict):
        return {'ok': False, 'detail': '当前通用层节点不存在，无法迁移'}
    graph.setdefault('genres', {}).setdefault(genre_key, {})[scene_name] = _normalize_scene_node_payload(common_row)
    if not retain_common:
        common_bucket.pop(scene_name, None)
    save_scene_graph(graph)
    return get_scene_graph_node_layers(build_scene_node_key(genre_key, scene_name))


def delete_scene_graph_node(node_key: str, layer: str | None = None) -> dict:
    graph = load_scene_graph()
    parsed_layer, scene_name = parse_scene_node_key(node_key)
    target_layer = str(layer or '').strip().lower()
    if not scene_name:
        return {'ok': False, 'detail': 'scene_name is required'}
    if target_layer not in {'', 'current', 'common', 'genre'}:
        return {'ok': False, 'detail': 'layer must be current/common/genre'}
    if target_layer in {'', 'current'}:
        target_layer = 'common' if parsed_layer in {'', 'common'} else 'genre'
    if target_layer == 'common':
        common_bucket = graph.get('common') or {}
        if scene_name not in common_bucket:
            return {'ok': False, 'detail': '当前通用层节点不存在，无法删除'}
        common_bucket.pop(scene_name, None)
        save_scene_graph(graph)
        return {
            'ok': True,
            'deleted_layer': 'common',
            'scene_name': scene_name,
            'node_key': build_scene_node_key('common', scene_name),
            'detail': '通用层节点已删除',
        }
    genre_key = parsed_layer
    if genre_key not in SUPPORTED_SCENE_GENRES:
        return {'ok': False, 'detail': '当前赛道层节点不存在，无法删除'}
    genre_bucket = ((graph.get('genres') or {}).get(genre_key) or {})
    if scene_name not in genre_bucket:
        return {'ok': False, 'detail': '当前赛道层节点不存在，无法删除'}
    genre_bucket.pop(scene_name, None)
    save_scene_graph(graph)
    return {
        'ok': True,
        'deleted_layer': 'genre',
        'scene_name': scene_name,
        'node_key': build_scene_node_key(genre_key, scene_name),
        'detail': '赛道层节点已删除',
    }


def list_scene_collections() -> dict:
    graph = load_scene_graph()
    items = {}
    for scene_name, payload in (graph.get('common') or {}).items():
        collection_name = str(payload.get('collection_name') or '').strip()
        if not collection_name:
            continue
        bucket = items.setdefault(collection_name, {'collection_name': collection_name, 'common_scenes': [], 'genre_scenes': []})
        bucket['common_scenes'].append(scene_name)
    for genre, scene_map in (graph.get('genres') or {}).items():
        for scene_name, payload in (scene_map or {}).items():
            collection_name = str(payload.get('collection_name') or '').strip()
            if not collection_name:
                continue
            bucket = items.setdefault(collection_name, {'collection_name': collection_name, 'common_scenes': [], 'genre_scenes': []})
            bucket['genre_scenes'].append(f'{genre}::{scene_name}')
    return {'items': sorted(items.values(), key=lambda x: x['collection_name'])}


def rename_scene_collection(old_name: str, new_name: str) -> dict:
    graph = load_scene_graph()
    source = str(old_name or '').strip()
    target = str(new_name or '').strip()
    if not source:
        return {'ok': False, 'detail': 'old_name is required'}
    if not target:
        return {'ok': False, 'detail': 'new_name is required'}
    if source == target:
        return {'ok': False, 'detail': '新旧集合名称相同，无需修改'}
    updated_nodes = []
    updated_templates = []
    for scene_name, row in (graph.get('common') or {}).items():
        row_norm = _normalize_scene_node_payload(row)
        if str(row_norm.get('collection_name') or '').strip() == source:
            row_norm['collection_name'] = target
            graph.setdefault('common', {})[scene_name] = row_norm
            updated_nodes.append(f'common::{scene_name}')
    for genre, scene_map in (graph.get('genres') or {}).items():
        for scene_name, row in (scene_map or {}).items():
            row_norm = _normalize_scene_node_payload(row)
            if str(row_norm.get('collection_name') or '').strip() == source:
                row_norm['collection_name'] = target
                graph.setdefault('genres', {}).setdefault(genre, {})[scene_name] = row_norm
                updated_nodes.append(f'{genre}::{scene_name}')
    for template_name, payload in (graph.get('templates') or {}).items():
        collection_name = str(payload.get('collection_name') or '').strip()
        if collection_name == source:
            graph.setdefault('templates', {})[template_name] = {
                'collection_name': target,
                'aliases': _merge_unique(payload.get('aliases') or []),
            }
            updated_templates.append(template_name)
    save_scene_graph(graph)
    return {
        'ok': True,
        'old_name': source,
        'new_name': target,
        'updated_node_count': len(updated_nodes),
        'updated_template_count': len(updated_templates),
        'updated_nodes': updated_nodes,
        'updated_templates': updated_templates,
        'detail': '场景集合已改名',
    }


def delete_scene_collection(collection_name: str) -> dict:
    graph = load_scene_graph()
    name = str(collection_name or '').strip()
    if not name:
        return {'ok': False, 'detail': 'collection_name is required'}
    updated_nodes = []
    updated_templates = []
    for scene_name, row in (graph.get('common') or {}).items():
        row_norm = _normalize_scene_node_payload(row)
        if str(row_norm.get('collection_name') or '').strip() == name:
            row_norm['collection_name'] = ''
            graph.setdefault('common', {})[scene_name] = row_norm
            updated_nodes.append(f'common::{scene_name}')
    for genre, scene_map in (graph.get('genres') or {}).items():
        for scene_name, row in (scene_map or {}).items():
            row_norm = _normalize_scene_node_payload(row)
            if str(row_norm.get('collection_name') or '').strip() == name:
                row_norm['collection_name'] = ''
                graph.setdefault('genres', {}).setdefault(genre, {})[scene_name] = row_norm
                updated_nodes.append(f'{genre}::{scene_name}')
    for template_name, payload in (graph.get('templates') or {}).items():
        if str(payload.get('collection_name') or '').strip() == name:
            graph.setdefault('templates', {})[template_name] = {
                'collection_name': '',
                'aliases': _merge_unique(payload.get('aliases') or []),
            }
            updated_templates.append(template_name)
    if not updated_nodes and not updated_templates:
        return {'ok': False, 'detail': '当前场景集合不存在，无法删除'}
    save_scene_graph(graph)
    return {
        'ok': True,
        'collection_name': name,
        'updated_node_count': len(updated_nodes),
        'updated_template_count': len(updated_templates),
        'updated_nodes': updated_nodes,
        'updated_templates': updated_templates,
        'detail': '场景集合引用已清空',
    }


def list_scene_templates() -> dict:
    graph = load_scene_graph()
    items = []
    for template_name, payload in sorted((graph.get('templates') or {}).items(), key=lambda x: x[0]):
        referenced_nodes = []
        for scene_name, row in (graph.get('common') or {}).items():
            row_norm = _normalize_scene_node_payload(row)
            if str(row_norm.get('template_name') or '').strip() == template_name:
                referenced_nodes.append(f'common::{scene_name}')
        for genre, scene_map in (graph.get('genres') or {}).items():
            for scene_name, row in (scene_map or {}).items():
                row_norm = _normalize_scene_node_payload(row)
                if str(row_norm.get('template_name') or '').strip() == template_name:
                    referenced_nodes.append(f'{genre}::{scene_name}')
        items.append(
            {
                'template_name': template_name,
                'collection_name': str(payload.get('collection_name') or '').strip(),
                'aliases': _merge_unique(payload.get('aliases') or []),
                'referenced_nodes': referenced_nodes,
                'referenced_node_count': len(referenced_nodes),
            }
        )
    return {'items': items}


def update_scene_template(template_name: str, payload: dict | None) -> dict:
    graph = load_scene_graph()
    name = str(template_name or '').strip()
    if not name:
        return {'ok': False, 'detail': 'template_name is required'}
    row = payload if isinstance(payload, dict) else {}
    normalized = {
        'collection_name': str(row.get('collection_name') or '').strip(),
        'aliases': _merge_unique(row.get('aliases') or []),
    }
    has_content = bool(normalized['collection_name'] or normalized['aliases'])
    bucket = graph.setdefault('templates', {})
    if has_content:
        bucket[name] = normalized
    else:
        bucket.pop(name, None)
    save_scene_graph(graph)
    return {'ok': True, 'template_name': name, **normalized}


def delete_scene_template(template_name: str) -> dict:
    graph = load_scene_graph()
    name = str(template_name or '').strip()
    if not name:
        return {'ok': False, 'detail': 'template_name is required'}
    bucket = graph.get('templates') or {}
    if name not in bucket:
        return {'ok': False, 'detail': '当前模板不存在，无法删除'}
    affected_nodes = []
    for scene_name, row in (graph.get('common') or {}).items():
        row_norm = _normalize_scene_node_payload(row)
        if str(row_norm.get('template_name') or '').strip() == name:
            affected_nodes.append(f'common::{scene_name}')
    for genre, scene_map in (graph.get('genres') or {}).items():
        for scene_name, row in (scene_map or {}).items():
            row_norm = _normalize_scene_node_payload(row)
            if str(row_norm.get('template_name') or '').strip() == name:
                affected_nodes.append(f'{genre}::{scene_name}')
    bucket.pop(name, None)
    save_scene_graph(graph)
    return {
        'ok': True,
        'template_name': name,
        'affected_node_count': len(affected_nodes),
        'affected_nodes': affected_nodes,
        'detail': '场景模板已删除',
    }


def resolve_scene_graph_node(scene_name: str, genre: str) -> dict:
    graph = load_scene_graph()
    scene = str(scene_name or '').strip()
    genre_key = str(genre or '').strip()
    common_row = _normalize_scene_node_payload((graph.get('common') or {}).get(scene))
    genre_row = _normalize_scene_node_payload(((graph.get('genres') or {}).get(genre_key) or {}).get(scene))
    return {
        'common_hit': bool((graph.get('common') or {}).get(scene)),
        'genre_hit': bool(((graph.get('genres') or {}).get(genre_key) or {}).get(scene)),
        'template_hit': str(genre_row.get('template_name') or common_row.get('template_name') or '').strip(),
        'collection_name': str(genre_row.get('collection_name') or common_row.get('collection_name') or '').strip(),
        'background_elements': _merge_unique(common_row.get('background_elements') + genre_row.get('background_elements')),
        'feature_elements': _merge_unique(common_row.get('feature_elements') + genre_row.get('feature_elements')),
        'detail_elements': _merge_unique(common_row.get('detail_elements') + genre_row.get('detail_elements')),
        'supporting_sfx_terms': _merge_unique(common_row.get('supporting_sfx_terms') + genre_row.get('supporting_sfx_terms')),
        'detail_sfx_terms': _merge_unique(common_row.get('detail_sfx_terms') + genre_row.get('detail_sfx_terms')),
    }


def resolve_scene_template_from_text(location_terms: list[str], genre: str) -> dict:
    graph = load_scene_graph()
    common = graph.get('common') or {}
    genre_map = (graph.get('genres') or {}).get(str(genre or '').strip(), {}) or {}
    templates = graph.get('templates') or {}
    normalized_terms = [str(x or '').strip() for x in location_terms if str(x or '').strip()]

    for term in normalized_terms:
        if term in genre_map:
            row = _normalize_scene_node_payload(genre_map.get(term))
            return {
                'matched_term': term,
                'scene_name': term,
                'template': row.get('template_name') or '',
                'collection_name': row.get('collection_name') or '',
                'background': row.get('background_elements') or [],
                'feature': row.get('feature_elements') or [],
                'detail': row.get('detail_elements') or [],
                'supporting_sfx_terms': row.get('supporting_sfx_terms') or [],
                'detail_sfx_terms': row.get('detail_sfx_terms') or [],
                'location_inference': [],
                'common_hit': term in common,
                'genre_hit': True,
            }
        if term in common:
            row = _normalize_scene_node_payload(common.get(term))
            return {
                'matched_term': term,
                'scene_name': term,
                'template': row.get('template_name') or '',
                'collection_name': row.get('collection_name') or '',
                'background': row.get('background_elements') or [],
                'feature': row.get('feature_elements') or [],
                'detail': row.get('detail_elements') or [],
                'supporting_sfx_terms': row.get('supporting_sfx_terms') or [],
                'detail_sfx_terms': row.get('detail_sfx_terms') or [],
                'location_inference': [],
                'common_hit': True,
                'genre_hit': False,
            }

    for template_name, payload in templates.items():
        aliases = _merge_unique(payload.get('aliases') or [])
        for alias in aliases:
            if alias in normalized_terms:
                candidates = []
                for scene_name, row in genre_map.items():
                    row_norm = _normalize_scene_node_payload(row)
                    if str(row_norm.get('template_name') or '').strip() == template_name:
                        candidates.append((scene_name, row_norm, True))
                for scene_name, row in common.items():
                    row_norm = _normalize_scene_node_payload(row)
                    if str(row_norm.get('template_name') or '').strip() == template_name:
                        candidates.append((scene_name, row_norm, False))
                if candidates:
                    scene_name, row_norm, is_genre = candidates[0]
                    return {
                        'matched_term': alias,
                        'scene_name': scene_name,
                        'template': template_name,
                        'collection_name': str(payload.get('collection_name') or row_norm.get('collection_name') or '').strip(),
                        'background': row_norm.get('background_elements') or [],
                        'feature': row_norm.get('feature_elements') or [],
                        'detail': row_norm.get('detail_elements') or [],
                        'supporting_sfx_terms': row_norm.get('supporting_sfx_terms') or [],
                        'detail_sfx_terms': row_norm.get('detail_sfx_terms') or [],
                        'location_inference': [],
                        'common_hit': not is_genre,
                        'genre_hit': is_genre,
                    }
                return {
                    'matched_term': alias,
                    'scene_name': alias,
                    'template': template_name,
                    'collection_name': str(payload.get('collection_name') or '').strip(),
                    'background': [],
                    'feature': [],
                    'detail': [],
                    'supporting_sfx_terms': [],
                    'detail_sfx_terms': [],
                    'location_inference': [],
                    'common_hit': False,
                    'genre_hit': False,
                }
    return {}
