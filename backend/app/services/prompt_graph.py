import json
from pathlib import Path

from app.config import settings


PROMPT_DIR = Path(__file__).resolve().parent.parent / 'prompts'
SPEC_PATH = PROMPT_DIR / 'V3_prompt_graph_spec.json'
SPEC_PATH_FALLBACK = PROMPT_DIR / 'V2_prompt_graph_spec.json'


def _neo4j_driver():
    if not (settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password):
        return None
    try:
        from neo4j import GraphDatabase
    except Exception:
        return None
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))


def load_prompt_graph_spec() -> dict:
    target = SPEC_PATH if SPEC_PATH.exists() else SPEC_PATH_FALLBACK
    if not target.exists():
        return {'version': 'none', 'tasks': [], 'fields': [], 'edges': []}
    try:
        return json.loads(target.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {'version': 'invalid', 'tasks': [], 'fields': [], 'edges': []}


def _save_prompt_graph_spec(spec: dict) -> None:
    SPEC_PATH.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def prompt_graph_overview() -> dict:
    spec = load_prompt_graph_spec()
    return {
        'version': spec.get('version'),
        'task_count': len(spec.get('tasks') or []),
        'field_count': len(spec.get('fields') or []),
        'edge_count': len(spec.get('edges') or []),
        'tasks': spec.get('tasks') or [],
        'fields': spec.get('fields') or [],
        'edges': spec.get('edges') or [],
    }


def prompt_field_detail(field_id: str) -> dict:
    spec = load_prompt_graph_spec()
    fields = spec.get('fields') or []
    edges = spec.get('edges') or []
    target = next((f for f in fields if str(f.get('id')) == str(field_id)), None)
    if not target:
        return {'detail': 'field not found'}

    upstream = [e for e in edges if e.get('to') == field_id]
    downstream = [e for e in edges if e.get('from') == field_id]

    by_id = {f.get('id'): f for f in fields}
    return {
        'field': target,
        'upstream': [
            {
                'field_id': e.get('from'),
                'field_name': (by_id.get(e.get('from')) or {}).get('name') or e.get('from'),
                'reason': e.get('reason', ''),
            }
            for e in upstream
        ],
        'downstream': [
            {
                'field_id': e.get('to'),
                'field_name': (by_id.get(e.get('to')) or {}).get('name') or e.get('to'),
                'reason': e.get('reason', ''),
            }
            for e in downstream
        ],
    }


def upsert_prompt_field(payload: dict) -> dict:
    fid = str((payload or {}).get('id') or '').strip()
    if not fid:
        return {'ok': False, 'detail': 'id is required'}

    spec = load_prompt_graph_spec()
    if spec.get('version') in {'none', 'invalid'}:
        return {'ok': False, 'detail': 'invalid spec'}

    fields = spec.get('fields') or []
    tasks = {str(t.get('id')) for t in (spec.get('tasks') or [])}

    task = str((payload or {}).get('task') or '').strip()
    role = str((payload or {}).get('role') or '').strip()
    name = str((payload or {}).get('name') or '').strip() or fid
    description = str((payload or {}).get('description') or '').strip()

    if not task or task not in tasks:
        return {'ok': False, 'detail': 'task is required and must exist'}
    if role not in {'input', 'output'}:
        return {'ok': False, 'detail': 'role must be input or output'}

    idx = next((i for i, f in enumerate(fields) if str(f.get('id')) == fid), -1)
    item = {'id': fid, 'name': name, 'task': task, 'role': role, 'description': description}
    created = idx < 0
    if created:
        fields.append(item)
    else:
        fields[idx] = item

    spec['fields'] = fields
    _save_prompt_graph_spec(spec)
    return {'ok': True, 'created': created, 'field': item, 'field_count': len(fields), 'version': spec.get('version')}


def remove_prompt_field(field_id: str) -> dict:
    fid = str(field_id or '').strip()
    if not fid:
        return {'ok': False, 'detail': 'field_id is required'}
    spec = load_prompt_graph_spec()
    fields = spec.get('fields') or []
    edges = spec.get('edges') or []
    kept_fields = [f for f in fields if str(f.get('id')) != fid]
    if len(kept_fields) == len(fields):
        return {'ok': False, 'detail': 'field not found'}
    kept_edges = [e for e in edges if str(e.get('from')) != fid and str(e.get('to')) != fid]
    spec['fields'] = kept_fields
    spec['edges'] = kept_edges
    _save_prompt_graph_spec(spec)
    return {
        'ok': True,
        'removed_field_id': fid,
        'field_count': len(kept_fields),
        'edge_count': len(kept_edges),
        'version': spec.get('version'),
    }


def upsert_prompt_edge(payload: dict) -> dict:
    src = str((payload or {}).get('from') or '').strip()
    dst = str((payload or {}).get('to') or '').strip()
    reason = str((payload or {}).get('reason') or '').strip()
    if not src or not dst:
        return {'ok': False, 'detail': 'from and to are required'}

    spec = load_prompt_graph_spec()
    fields = {str(f.get('id')) for f in (spec.get('fields') or [])}
    if src not in fields or dst not in fields:
        return {'ok': False, 'detail': 'from/to field_id must exist first'}

    edges = spec.get('edges') or []
    idx = next((i for i, e in enumerate(edges) if str(e.get('from')) == src and str(e.get('to')) == dst), -1)
    item = {'from': src, 'to': dst, 'reason': reason}
    created = idx < 0
    if created:
        edges.append(item)
    else:
        edges[idx] = item
    spec['edges'] = edges
    _save_prompt_graph_spec(spec)
    return {'ok': True, 'created': created, 'edge': item, 'edge_count': len(edges), 'version': spec.get('version')}


def remove_prompt_edge(src: str, dst: str) -> dict:
    src = str(src or '').strip()
    dst = str(dst or '').strip()
    if not src or not dst:
        return {'ok': False, 'detail': 'from and to are required'}
    spec = load_prompt_graph_spec()
    edges = spec.get('edges') or []
    kept = [e for e in edges if not (str(e.get('from')) == src and str(e.get('to')) == dst)]
    if len(kept) == len(edges):
        return {'ok': False, 'detail': 'edge not found'}
    spec['edges'] = kept
    _save_prompt_graph_spec(spec)
    return {'ok': True, 'removed': {'from': src, 'to': dst}, 'edge_count': len(kept), 'version': spec.get('version')}


def sync_prompt_graph_to_neo4j() -> dict:
    spec = load_prompt_graph_spec()
    driver = _neo4j_driver()
    if not driver:
        return {
            'ok': False,
            'detail': 'neo4j not configured or driver unavailable',
            'version': spec.get('version'),
            'tasks': len(spec.get('tasks') or []),
            'fields': len(spec.get('fields') or []),
            'edges': len(spec.get('edges') or []),
        }

    tasks = spec.get('tasks') or []
    fields = spec.get('fields') or []
    edges = spec.get('edges') or []
    version = spec.get('version') or 'v2'

    q_task = (
        'MERGE (t:PromptTask {id:$id}) '
        'SET t.name=$name, t.prompt_file=$prompt_file, t.summary=$summary, t.version=$version'
    )
    q_field = (
        'MERGE (f:PromptField {id:$id}) '
        'SET f.name=$name, f.description=$description, f.task=$task, f.role=$role, f.version=$version'
    )
    q_has_in = (
        'MATCH (t:PromptTask {id:$task}), (f:PromptField {id:$field}) '
        'MERGE (t)-[:HAS_INPUT]->(f)'
    )
    q_has_out = (
        'MATCH (t:PromptTask {id:$task}), (f:PromptField {id:$field}) '
        'MERGE (t)-[:HAS_OUTPUT]->(f)'
    )
    q_feed = (
        'MATCH (a:PromptField {id:$from}), (b:PromptField {id:$to}) '
        'MERGE (a)-[r:FEEDS]->(b) '
        'SET r.reason=$reason, r.version=$version'
    )

    try:
        with driver.session(database=settings.neo4j_database or None) as s:
            s.run('CREATE CONSTRAINT prompt_task_id IF NOT EXISTS FOR (n:PromptTask) REQUIRE n.id IS UNIQUE')
            s.run('CREATE CONSTRAINT prompt_field_id IF NOT EXISTS FOR (n:PromptField) REQUIRE n.id IS UNIQUE')

            for t in tasks:
                s.run(
                    q_task,
                    id=t.get('id'),
                    name=t.get('name'),
                    prompt_file=t.get('prompt_file'),
                    summary=t.get('summary', ''),
                    version=version,
                )
            for f in fields:
                s.run(
                    q_field,
                    id=f.get('id'),
                    name=f.get('name'),
                    description=f.get('description', ''),
                    task=f.get('task'),
                    role=f.get('role', ''),
                    version=version,
                )
                if f.get('role') == 'input':
                    s.run(q_has_in, task=f.get('task'), field=f.get('id'))
                else:
                    s.run(q_has_out, task=f.get('task'), field=f.get('id'))
            for e in edges:
                s.run(
                    q_feed,
                    **{
                        'from': e.get('from'),
                        'to': e.get('to'),
                        'reason': e.get('reason', ''),
                        'version': version,
                    },
                )
    finally:
        driver.close()

    return {
        'ok': True,
        'version': version,
        'tasks': len(tasks),
        'fields': len(fields),
        'edges': len(edges),
    }


def prompt_graph_status() -> dict:
    spec = load_prompt_graph_spec()
    out = {
        'version': spec.get('version'),
        'neo4j_configured': bool(settings.neo4j_uri and settings.neo4j_user and settings.neo4j_password),
        'neo4j_connected': False,
        'task_nodes': None,
        'field_nodes': None,
        'feed_edges': None,
    }
    driver = _neo4j_driver()
    if not driver:
        return out
    try:
        with driver.session(database=settings.neo4j_database or None) as s:
            out['neo4j_connected'] = True
            out['task_nodes'] = s.run('MATCH (n:PromptTask) RETURN count(n) AS n').single()['n']
            out['field_nodes'] = s.run('MATCH (n:PromptField) RETURN count(n) AS n').single()['n']
            out['feed_edges'] = s.run('MATCH ()-[r:FEEDS]->() RETURN count(r) AS n').single()['n']
    except Exception:
        out['neo4j_connected'] = False
    finally:
        driver.close()
    return out
