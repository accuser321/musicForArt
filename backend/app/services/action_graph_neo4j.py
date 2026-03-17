import json
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import ActionSupplementAsset, ActionSupplementTask
from app.services.action_sfx_graph import build_action_node_key, classify_sfx_terms

ACTION_GRAPH_PATH = Path('./assets/sfx/action_graph.json').resolve()


def _neo4j_driver():
    if not (settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password):
        return None
    try:
        from neo4j import GraphDatabase
    except Exception:
        return None
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


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


def _node_payload(genre: str, head: str, row: dict) -> dict:
    parent_node = row.get('parent_node') or {
        'genre': genre,
        'verb_head': head,
        'node_key': f'{genre}::{head}' if genre else head,
    }
    children = row.get('children') or {}
    semantic_terms = children.get('semantic_terms') or row.get('semantic_terms') or []
    sfx_terms = children.get('sfx_terms') or row.get('sfx_terms') or []
    classified = classify_sfx_terms([str(x).strip() for x in sfx_terms if str(x).strip()])
    return {
        'genre': genre,
        'verb_head': head,
        'node_key': parent_node.get('node_key') or (f'{genre}::{head}' if genre else head),
        'semantic_terms': [str(x).strip() for x in semantic_terms if str(x).strip()],
        'sfx_terms': [str(x).strip() for x in sfx_terms if str(x).strip()],
        'direct_sfx_terms': classified['direct_terms'],
        'composite_sfx_terms': classified['composite_terms'],
    }


def _business_stats_by_node_key() -> dict[str, dict]:
    with SessionLocal() as db:
        tasks = db.execute(select(ActionSupplementTask)).scalars().all()
        task_ids = [int(row.id) for row in tasks]
        asset_rows = (
            db.execute(select(ActionSupplementAsset).where(ActionSupplementAsset.supplement_id.in_(task_ids))).scalars().all()
            if task_ids
            else []
        )

    assets_by_supp: dict[int, set[str]] = {}
    for asset in asset_rows:
        label = str(asset.asset_label or '').strip()
        if not label:
            continue
        assets_by_supp.setdefault(int(asset.supplement_id), set()).add(label)

    node_stats: dict[str, dict] = {}
    for task in tasks:
        genre = str(task.target_genre or task.genre or '').strip()
        verb_head = str(task.target_head or task.verb or '').strip()
        if not verb_head:
            continue
        node_key = f'{genre}::{verb_head}' if genre else verb_head
        row = node_stats.setdefault(
            node_key,
            {
                'node_key': node_key,
                'genre': genre,
                'verb_head': verb_head,
                'item_count': 0,
                'status_counter': {},
                'covered_labels': set(),
                'last_supplement_at': '',
                'notified_count': 0,
            },
        )
        row['item_count'] += 1
        row['status_counter'][task.status] = row['status_counter'].get(task.status, 0) + 1
        if task.notified_at:
            row['notified_count'] += 1
        if task.created_at:
            ts = task.created_at.isoformat()
            if ts > row['last_supplement_at']:
                row['last_supplement_at'] = ts
        row['covered_labels'].update(assets_by_supp.get(int(task.id), set()))

    out: dict[str, dict] = {}
    for node_key, row in node_stats.items():
        out[node_key] = {
            'node_key': node_key,
            'genre': row['genre'],
            'verb_head': row['verb_head'],
            'item_count': int(row['item_count']),
            'status_counter': row['status_counter'],
            'ready_to_notify_count': 0,
            'notified_count': int(row['notified_count']),
            'incomplete_count': 0,
            'covered_labels': sorted(str(x) for x in row['covered_labels'] if str(x).strip()),
            'last_supplement_at': row['last_supplement_at'],
        }
    for node_key, row in node_stats.items():
        ready_count = 0
        incomplete_count = 0
        for task in tasks:
            genre = str(task.target_genre or task.genre or '').strip()
            verb_head = str(task.target_head or task.verb or '').strip()
            if build_action_node_key(genre, verb_head) != node_key:
                continue
            if task.notified_at:
                continue
            if str(task.status or '').strip() == 'ready_to_notify':
                ready_count += 1
            else:
                incomplete_count += 1
        out[node_key]['ready_to_notify_count'] = ready_count
        out[node_key]['incomplete_count'] = incomplete_count
    return out


def sync_action_graph_to_neo4j() -> dict:
    graph = _load_action_graph()
    business_stats = _business_stats_by_node_key()
    driver = _neo4j_driver()
    if not driver:
        return {
            'ok': False,
            'detail': 'neo4j not configured or driver unavailable',
            'version': (graph.get('_meta') or {}).get('version'),
            'verb_nodes': 0,
            'semantic_nodes': 0,
            'sfx_nodes': 0,
            'edges': 0,
        }

    version = (graph.get('_meta') or {}).get('version') or 'unknown'
    verb_nodes = 0
    semantic_nodes = set()
    sfx_nodes = set()
    edge_count = 0

    q_genre = 'MERGE (g:ActionGenre {name:$name})'
    q_action = (
        'MERGE (a:ActionNode {node_key:$node_key}) '
        'SET a.verb_head=$verb_head, a.genre=$genre, a.version=$version, '
        '    a.supplement_item_count=$supplement_item_count, '
        '    a.ready_to_notify_count=$ready_to_notify_count, '
        '    a.notified_count=$notified_count, '
        '    a.incomplete_count=$incomplete_count, '
        '    a.covered_count=$covered_count, '
        '    a.pending_count=$pending_count, '
        '    a.direct_sfx_count=$direct_sfx_count, '
        '    a.composite_sfx_count=$composite_sfx_count, '
        '    a.sfx_count=$sfx_count, '
        '    a.completion_ratio=$completion_ratio, '
        '    a.last_supplement_at=$last_supplement_at'
    )
    q_has_genre = (
        'MATCH (g:ActionGenre {name:$genre}), (a:ActionNode {node_key:$node_key}) '
        'MERGE (g)-[:HAS_ACTION]->(a)'
    )
    q_semantic = 'MERGE (s:SemanticNode {name:$name})'
    q_sfx = 'MERGE (s:SfxNode {name:$name}) SET s.mode=$mode'
    q_sem_edge = (
        'MATCH (a:ActionNode {node_key:$node_key}), (s:SemanticNode {name:$name}) '
        'MERGE (a)-[:HAS_SEMANTIC_CHILD]->(s)'
    )
    q_direct_sfx_edge = (
        'MATCH (a:ActionNode {node_key:$node_key}), (s:SfxNode {name:$name}) '
        'MERGE (a)-[:HAS_DIRECT_SFX_CHILD]->(s)'
    )
    q_composite_sfx_edge = (
        'MATCH (a:ActionNode {node_key:$node_key}), (s:SfxNode {name:$name}) '
        'MERGE (a)-[:HAS_COMPOSITE_SFX_CHILD]->(s)'
    )

    try:
        with driver.session(database=settings.neo4j_database or None) as s:
            s.run('CREATE CONSTRAINT action_genre_name IF NOT EXISTS FOR (n:ActionGenre) REQUIRE n.name IS UNIQUE')
            s.run('CREATE CONSTRAINT action_node_key IF NOT EXISTS FOR (n:ActionNode) REQUIRE n.node_key IS UNIQUE')
            s.run('CREATE CONSTRAINT semantic_node_name IF NOT EXISTS FOR (n:SemanticNode) REQUIRE n.name IS UNIQUE')
            s.run('CREATE CONSTRAINT sfx_node_name IF NOT EXISTS FOR (n:SfxNode) REQUIRE n.name IS UNIQUE')

            for head, row in (graph.get('common') or {}).items():
                if not isinstance(row, dict):
                    continue
                payload = _node_payload('', str(head), row)
                stats = business_stats.get(payload['node_key']) or {}
                target_terms = payload['sfx_terms']
                covered_labels = {str(x).strip() for x in (stats.get('covered_labels') or []) if str(x).strip()}
                covered_count = len([term for term in target_terms if term in covered_labels])
                pending_count = len([term for term in target_terms if term not in covered_labels])
                s.run(
                    q_action,
                    **payload,
                    version=version,
                    supplement_item_count=int(stats.get('item_count') or 0),
                    ready_to_notify_count=int(stats.get('ready_to_notify_count') or 0),
                    notified_count=int(stats.get('notified_count') or 0),
                    incomplete_count=int(stats.get('incomplete_count') or 0),
                    covered_count=covered_count,
                    pending_count=pending_count,
                    direct_sfx_count=len(payload['direct_sfx_terms']),
                    composite_sfx_count=len(payload['composite_sfx_terms']),
                    sfx_count=len(payload['sfx_terms']),
                    completion_ratio=round((covered_count / len(target_terms)), 4) if target_terms else 1.0,
                    last_supplement_at=str(stats.get('last_supplement_at') or ''),
                )
                verb_nodes += 1
                for term in payload['semantic_terms']:
                    s.run(q_semantic, name=term)
                    s.run(q_sem_edge, node_key=payload['node_key'], name=term)
                    semantic_nodes.add(term)
                    edge_count += 1
                for term in payload['direct_sfx_terms']:
                    s.run(q_sfx, name=term, mode='直达')
                    s.run(q_direct_sfx_edge, node_key=payload['node_key'], name=term)
                    sfx_nodes.add(term)
                    edge_count += 1
                for term in payload['composite_sfx_terms']:
                    s.run(q_sfx, name=term, mode='组合')
                    s.run(q_composite_sfx_edge, node_key=payload['node_key'], name=term)
                    sfx_nodes.add(term)
                    edge_count += 1

            for genre, bucket in (graph.get('genres') or {}).items():
                s.run(q_genre, name=genre)
                for head, row in (bucket or {}).items():
                    if not isinstance(row, dict):
                        continue
                    payload = _node_payload(str(genre), str(head), row)
                    stats = business_stats.get(payload['node_key']) or {}
                    target_terms = payload['sfx_terms']
                    covered_labels = {str(x).strip() for x in (stats.get('covered_labels') or []) if str(x).strip()}
                    covered_count = len([term for term in target_terms if term in covered_labels])
                    pending_count = len([term for term in target_terms if term not in covered_labels])
                    s.run(
                        q_action,
                        **payload,
                        version=version,
                        supplement_item_count=int(stats.get('item_count') or 0),
                        ready_to_notify_count=int(stats.get('ready_to_notify_count') or 0),
                        notified_count=int(stats.get('notified_count') or 0),
                        incomplete_count=int(stats.get('incomplete_count') or 0),
                        covered_count=covered_count,
                        pending_count=pending_count,
                        direct_sfx_count=len(payload['direct_sfx_terms']),
                        composite_sfx_count=len(payload['composite_sfx_terms']),
                        sfx_count=len(payload['sfx_terms']),
                        completion_ratio=round((covered_count / len(target_terms)), 4) if target_terms else 1.0,
                        last_supplement_at=str(stats.get('last_supplement_at') or ''),
                    )
                    s.run(q_has_genre, genre=genre, node_key=payload['node_key'])
                    verb_nodes += 1
                    edge_count += 1
                    for term in payload['semantic_terms']:
                        s.run(q_semantic, name=term)
                        s.run(q_sem_edge, node_key=payload['node_key'], name=term)
                        semantic_nodes.add(term)
                        edge_count += 1
                    for term in payload['direct_sfx_terms']:
                        s.run(q_sfx, name=term, mode='直达')
                        s.run(q_direct_sfx_edge, node_key=payload['node_key'], name=term)
                        sfx_nodes.add(term)
                        edge_count += 1
                    for term in payload['composite_sfx_terms']:
                        s.run(q_sfx, name=term, mode='组合')
                        s.run(q_composite_sfx_edge, node_key=payload['node_key'], name=term)
                        sfx_nodes.add(term)
                        edge_count += 1
    finally:
        driver.close()

    return {
        'ok': True,
        'version': version,
        'verb_nodes': verb_nodes,
        'semantic_nodes': len(semantic_nodes),
        'sfx_nodes': len(sfx_nodes),
        'edges': edge_count,
    }


def action_graph_neo4j_status() -> dict:
    graph = _load_action_graph()
    out = {
        'version': (graph.get('_meta') or {}).get('version'),
        'neo4j_configured': bool(settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password),
        'neo4j_connected': False,
        'action_genres': None,
        'action_nodes': None,
        'semantic_nodes': None,
        'sfx_nodes': None,
        'semantic_edges': None,
        'direct_sfx_edges': None,
        'composite_sfx_edges': None,
    }
    driver = _neo4j_driver()
    if not driver:
        return out
    try:
        with driver.session(database=settings.neo4j_database or None) as s:
            out['neo4j_connected'] = True
            out['action_genres'] = s.run('MATCH (n:ActionGenre) RETURN count(n) AS n').single()['n']
            out['action_nodes'] = s.run('MATCH (n:ActionNode) RETURN count(n) AS n').single()['n']
            out['semantic_nodes'] = s.run('MATCH (n:SemanticNode) RETURN count(n) AS n').single()['n']
            out['sfx_nodes'] = s.run('MATCH (n:SfxNode) RETURN count(n) AS n').single()['n']
            out['semantic_edges'] = s.run('MATCH ()-[r:HAS_SEMANTIC_CHILD]->() RETURN count(r) AS n').single()['n']
            out['direct_sfx_edges'] = s.run('MATCH ()-[r:HAS_DIRECT_SFX_CHILD]->() RETURN count(r) AS n').single()['n']
            out['composite_sfx_edges'] = s.run('MATCH ()-[r:HAS_COMPOSITE_SFX_CHILD]->() RETURN count(r) AS n').single()['n']
    except Exception:
        out['neo4j_connected'] = False
    finally:
        driver.close()
    return out


def query_action_graph_node(node_key: str) -> dict:
    nk = str(node_key or '').strip()
    if not nk:
        return {'detail': 'node_key is required'}
    driver = _neo4j_driver()
    if not driver:
        return {'detail': 'neo4j not configured or driver unavailable'}
    query = (
        'MATCH (a:ActionNode {node_key:$node_key}) '
        'OPTIONAL MATCH (g:ActionGenre)-[:HAS_ACTION]->(a) '
        'OPTIONAL MATCH (a)-[:HAS_SEMANTIC_CHILD]->(sem:SemanticNode) '
        'OPTIONAL MATCH (a)-[:HAS_DIRECT_SFX_CHILD]->(directSfx:SfxNode) '
        'OPTIONAL MATCH (a)-[:HAS_COMPOSITE_SFX_CHILD]->(compositeSfx:SfxNode) '
        'RETURN a.node_key AS node_key, a.verb_head AS verb_head, a.genre AS genre, '
        '       a.supplement_item_count AS supplement_item_count, '
        '       a.ready_to_notify_count AS ready_to_notify_count, '
        '       a.notified_count AS notified_count, '
        '       a.incomplete_count AS incomplete_count, '
        '       a.covered_count AS covered_count, '
        '       a.pending_count AS pending_count, '
        '       a.completion_ratio AS completion_ratio, '
        '       a.last_supplement_at AS last_supplement_at, '
        '       collect(DISTINCT g.name) AS genres, '
        '       collect(DISTINCT sem.name) AS semantic_terms, '
        '       collect(DISTINCT directSfx.name) AS direct_sfx_terms, '
        '       collect(DISTINCT compositeSfx.name) AS composite_sfx_terms'
    )
    try:
        with driver.session(database=settings.neo4j_database or None) as s:
            row = s.run(query, node_key=nk).single()
            if not row:
                return {'detail': 'node not found'}
            semantic_terms = [x for x in (row.get('semantic_terms') or []) if x]
            direct_sfx_terms = [x for x in (row.get('direct_sfx_terms') or []) if x]
            composite_sfx_terms = [x for x in (row.get('composite_sfx_terms') or []) if x]
            sfx_terms = [*direct_sfx_terms, *composite_sfx_terms]
            return {
                'node_key': row.get('node_key'),
                'verb_head': row.get('verb_head'),
                'genre': row.get('genre'),
                'genres': row.get('genres') or [],
                'semantic_terms': semantic_terms,
                'direct_sfx_terms': direct_sfx_terms,
                'composite_sfx_terms': composite_sfx_terms,
                'sfx_terms': sfx_terms,
                'summary': {
                    'semantic_count': len(semantic_terms),
                    'direct_sfx_count': len(direct_sfx_terms),
                    'composite_sfx_count': len(composite_sfx_terms),
                    'sfx_count': len(sfx_terms),
                },
                'supplement_summary': {
                    'item_count': int(row.get('supplement_item_count') or 0),
                    'ready_to_notify_count': int(row.get('ready_to_notify_count') or 0),
                    'notified_count': int(row.get('notified_count') or 0),
                    'incomplete_count': int(row.get('incomplete_count') or 0),
                    'covered_count': int(row.get('covered_count') or 0),
                    'pending_count': int(row.get('pending_count') or 0),
                    'completion_ratio': float(row.get('completion_ratio') or 0.0),
                    'last_supplement_at': row.get('last_supplement_at') or '',
                },
            }
    finally:
        driver.close()


def list_action_graph_nodes(
    genre: str = '',
    q: str = '',
    limit: int = 50,
    only_with_gap: bool = False,
    sort_by: str = 'pending',
    status_filter: str = 'all',
) -> dict:
    driver = _neo4j_driver()
    if not driver:
        return {'detail': 'neo4j not configured or driver unavailable'}
    genre = str(genre or '').strip()
    q = str(q or '').strip()
    limit = max(1, min(int(limit or 50), 200))
    sort_key = str(sort_by or 'pending').strip().lower()
    status_key = str(status_filter or 'all').strip().lower()
    order_by = {
        'pending': 'a.pending_count DESC, a.supplement_item_count DESC, a.genre, a.verb_head',
        'notify': 'a.ready_to_notify_count DESC, a.pending_count DESC, a.genre, a.verb_head',
        'recent': 'a.last_supplement_at DESC, a.pending_count DESC, a.genre, a.verb_head',
        'alpha': 'a.genre, a.verb_head',
    }.get(sort_key, 'a.pending_count DESC, a.supplement_item_count DESC, a.genre, a.verb_head')
    query = (
        'MATCH (a:ActionNode) '
        'OPTIONAL MATCH (g:ActionGenre)-[:HAS_ACTION]->(a) '
        'OPTIONAL MATCH (a)-[:HAS_SEMANTIC_CHILD]->(sem:SemanticNode) '
        'WHERE ($genre = "" OR a.genre = $genre) '
        'WITH a, collect(DISTINCT g.name) AS genres, count(DISTINCT sem) AS semantic_count '
        'WHERE ($q = "" OR a.node_key CONTAINS $q OR a.verb_head CONTAINS $q) '
        'RETURN a.node_key AS node_key, a.verb_head AS verb_head, a.genre AS genre, genres, semantic_count, a.sfx_count AS sfx_count, '
        '       a.direct_sfx_count AS direct_sfx_count, '
        '       a.composite_sfx_count AS composite_sfx_count, '
        '       a.supplement_item_count AS supplement_item_count, '
        '       a.ready_to_notify_count AS ready_to_notify_count, '
        '       a.notified_count AS notified_count, '
        '       a.incomplete_count AS incomplete_count, '
        '       a.covered_count AS covered_count, '
        '       a.pending_count AS pending_count, '
        '       a.completion_ratio AS completion_ratio, '
        '       a.last_supplement_at AS last_supplement_at '
        f'ORDER BY {order_by} '
        'LIMIT $limit'
    )
    try:
        with driver.session(database=settings.neo4j_database or None) as s:
            rows = s.run(query, genre=genre, q=q, limit=limit).data()
            items = [
                {
                    'node_key': row.get('node_key'),
                    'verb_head': row.get('verb_head'),
                    'genre': row.get('genre'),
                    'genres': row.get('genres') or [],
                    'semantic_count': int(row.get('semantic_count') or 0),
                    'sfx_count': int(row.get('sfx_count') or 0),
                    'direct_sfx_count': int(row.get('direct_sfx_count') or 0),
                    'composite_sfx_count': int(row.get('composite_sfx_count') or 0),
                    'supplement_item_count': int(row.get('supplement_item_count') or 0),
                    'ready_to_notify_count': int(row.get('ready_to_notify_count') or 0),
                    'notified_count': int(row.get('notified_count') or 0),
                    'incomplete_count': int(row.get('incomplete_count') or 0),
                    'covered_count': int(row.get('covered_count') or 0),
                    'pending_count': int(row.get('pending_count') or 0),
                    'completion_ratio': float(row.get('completion_ratio') or 0.0),
                    'last_supplement_at': row.get('last_supplement_at') or '',
                }
                for row in rows
            ]
            if only_with_gap:
                items = [item for item in items if int(item.get('pending_count') or 0) > 0]
            if status_key == 'notify':
                items = [item for item in items if int(item.get('ready_to_notify_count') or 0) > 0]
            elif status_key == 'notified':
                items = [item for item in items if int(item.get('notified_count') or 0) > 0]
            elif status_key == 'incomplete':
                items = [item for item in items if int(item.get('incomplete_count') or 0) > 0]
            return {
                'items': items,
                'genre': genre,
                'q': q,
                'limit': limit,
                'only_with_gap': bool(only_with_gap),
                'sort_by': sort_key,
                'status_filter': status_key,
                'count': len(items),
            }
    finally:
        driver.close()
