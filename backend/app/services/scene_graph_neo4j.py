from app.config import settings
from app.services.scene_graph_manage import load_scene_graph


def _neo4j_driver():
    if not (settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password):
        return None
    try:
        from neo4j import GraphDatabase
    except Exception:
        return None
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


def scene_graph_neo4j_status() -> dict:
    graph = load_scene_graph()
    driver = _neo4j_driver()
    configured = bool(settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password)
    genre_scene_count = sum(len(v or {}) for v in (graph.get('genres') or {}).values())
    common_scene_count = len(graph.get('common') or {})
    template_count = len(graph.get('templates') or {})
    return {
        'ok': configured and bool(driver),
        'neo4j_configured': configured,
        'neo4j_connected': bool(driver),
        'common_scene_count': common_scene_count,
        'genre_scene_count': genre_scene_count,
        'template_count': template_count,
        'detail': 'scene neo4j ready' if driver else 'neo4j not configured or driver unavailable',
    }


def sync_scene_graph_to_neo4j() -> dict:
    graph = load_scene_graph()
    driver = _neo4j_driver()
    if not driver:
        return {
            'ok': False,
            'detail': 'neo4j not configured or driver unavailable',
            'common_scene_count': len(graph.get('common') or {}),
            'genre_scene_count': sum(len(v or {}) for v in (graph.get('genres') or {}).values()),
            'template_count': len(graph.get('templates') or {}),
            'edge_count': 0,
        }

    common_scene_count = 0
    genre_scene_count = 0
    template_count = 0
    edge_count = 0
    try:
        with driver.session(database=settings.neo4j_database or None) as s:
            s.run('CREATE CONSTRAINT scene_collection_name IF NOT EXISTS FOR (n:SceneCollection) REQUIRE n.name IS UNIQUE')
            s.run('CREATE CONSTRAINT scene_template_name IF NOT EXISTS FOR (n:SceneTemplate) REQUIRE n.name IS UNIQUE')
            s.run('CREATE CONSTRAINT scene_node_key IF NOT EXISTS FOR (n:SceneNode) REQUIRE n.node_key IS UNIQUE')
            s.run('CREATE CONSTRAINT scene_sfx_name IF NOT EXISTS FOR (n:SceneSfxNode) REQUIRE n.name IS UNIQUE')

            for template_name, payload in (graph.get('templates') or {}).items():
                collection_name = str(payload.get('collection_name') or '').strip()
                s.run('MERGE (t:SceneTemplate {name:$name}) SET t.collection_name=$collection_name', name=template_name, collection_name=collection_name)
                template_count += 1
                if collection_name:
                    s.run('MERGE (c:SceneCollection {name:$name})', name=collection_name)
                    s.run('MATCH (c:SceneCollection {name:$collection_name}), (t:SceneTemplate {name:$template_name}) MERGE (c)-[:HAS_TEMPLATE]->(t)', collection_name=collection_name, template_name=template_name)
                    edge_count += 1
                for alias in payload.get('aliases') or []:
                    alias_name = str(alias or '').strip()
                    if alias_name:
                        s.run('MERGE (a:SceneAlias {name:$name})', name=alias_name)
                        s.run('MATCH (a:SceneAlias {name:$alias_name}), (t:SceneTemplate {name:$template_name}) MERGE (t)-[:HAS_ALIAS]->(a)', alias_name=alias_name, template_name=template_name)
                        edge_count += 1

            for scene_name, payload in (graph.get('common') or {}).items():
                node_key = f'common::{scene_name}'
                template_name = str(payload.get('template_name') or '').strip()
                collection_name = str(payload.get('collection_name') or '').strip()
                s.run(
                    'MERGE (n:SceneNode {node_key:$node_key}) SET n.scene_name=$scene_name, n.layer="common", n.genre="", n.template_name=$template_name, n.collection_name=$collection_name',
                    node_key=node_key, scene_name=scene_name, template_name=template_name, collection_name=collection_name,
                )
                common_scene_count += 1
                if template_name:
                    s.run('MATCH (n:SceneNode {node_key:$node_key}), (t:SceneTemplate {name:$template_name}) MERGE (t)-[:HAS_SCENE_NODE]->(n)', node_key=node_key, template_name=template_name)
                    edge_count += 1
                for term in (payload.get('supporting_sfx_terms') or []) + (payload.get('detail_sfx_terms') or []):
                    label = str(term or '').strip()
                    if not label:
                        continue
                    s.run('MERGE (sfx:SceneSfxNode {name:$name})', name=label)
                    s.run('MATCH (n:SceneNode {node_key:$node_key}), (sfx:SceneSfxNode {name:$name}) MERGE (n)-[:HAS_SCENE_SFX]->(sfx)', node_key=node_key, name=label)
                    edge_count += 1

            for genre, scene_map in (graph.get('genres') or {}).items():
                for scene_name, payload in (scene_map or {}).items():
                    node_key = f'{genre}::{scene_name}'
                    template_name = str(payload.get('template_name') or '').strip()
                    collection_name = str(payload.get('collection_name') or '').strip()
                    s.run(
                        'MERGE (n:SceneNode {node_key:$node_key}) SET n.scene_name=$scene_name, n.layer="genre", n.genre=$genre, n.template_name=$template_name, n.collection_name=$collection_name',
                        node_key=node_key, scene_name=scene_name, genre=genre, template_name=template_name, collection_name=collection_name,
                    )
                    genre_scene_count += 1
                    if template_name:
                        s.run('MATCH (n:SceneNode {node_key:$node_key}), (t:SceneTemplate {name:$template_name}) MERGE (t)-[:HAS_SCENE_NODE]->(n)', node_key=node_key, template_name=template_name)
                        edge_count += 1
                    for term in (payload.get('supporting_sfx_terms') or []) + (payload.get('detail_sfx_terms') or []):
                        label = str(term or '').strip()
                        if not label:
                            continue
                        s.run('MERGE (sfx:SceneSfxNode {name:$name})', name=label)
                        s.run('MATCH (n:SceneNode {node_key:$node_key}), (sfx:SceneSfxNode {name:$name}) MERGE (n)-[:HAS_SCENE_SFX]->(sfx)', node_key=node_key, name=label)
                        edge_count += 1
    finally:
        try:
            driver.close()
        except Exception:
            pass

    return {
        'ok': True,
        'detail': 'scene graph synced',
        'common_scene_count': common_scene_count,
        'genre_scene_count': genre_scene_count,
        'template_count': template_count,
        'edge_count': edge_count,
    }
