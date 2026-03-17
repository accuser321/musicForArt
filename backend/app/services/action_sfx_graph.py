import json
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import ActionSupplementAsset, ActionSupplementTask
from app.services.semantic_graph import expand_term
from app.services.sfx_matcher import load_sfx_library, match_sfx_candidates

ACTION_GRAPH_PATH = Path('./assets/sfx/action_graph.json').resolve()
DIRECT_SFX_HINTS = (
    '声', '音效', '响', '鸣', '啸', '吼',
    '呼吸', '喘息', '脚步', '步伐', '摩擦', '碰撞',
    '破风', '门轴', '门把', '拖拽', '爆裂', '碎裂',
    '敲击', '拍击', '掌击', '拉拽', '推动', '抓取',
)


def _normalize_entry(v: dict) -> dict:
    children = v.get('children') or {}
    semantic_terms = v.get('semantic_terms', [])
    sfx_terms = v.get('sfx_terms', [])
    if isinstance(children, dict):
        semantic_terms = children.get('semantic_terms', semantic_terms)
        sfx_terms = children.get('sfx_terms', sfx_terms)
    return {
        'parent_node': v.get('parent_node') or {},
        'children': {
            'semantic_terms': [str(x).strip() for x in semantic_terms if str(x).strip()],
            'sfx_terms': [str(x).strip() for x in sfx_terms if str(x).strip()],
        },
        'semantic_terms': [str(x).strip() for x in semantic_terms if str(x).strip()],
        'sfx_terms': [str(x).strip() for x in sfx_terms if str(x).strip()],
    }


def _load_action_graph() -> dict[str, dict]:
    if not ACTION_GRAPH_PATH.exists():
        return {'common': {}, 'genres': {}}
    try:
        data = json.loads(ACTION_GRAPH_PATH.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {'common': {}, 'genres': {}}

    # Backward compatible: top-level verb mapping
    if isinstance(data, dict) and 'common' not in data and 'genres' not in data:
        out = {}
        for k, v in (data or {}).items():
            if isinstance(v, dict):
                out[str(k).strip()] = _normalize_entry(v)
        return {'common': out, 'genres': {}}

    common = {}
    for k, v in ((data or {}).get('common') or {}).items():
        if isinstance(v, dict):
            common[str(k).strip()] = _normalize_entry(v)

    genres = {}
    for genre, mapping in ((data or {}).get('genres') or {}).items():
        if not isinstance(mapping, dict):
            continue
        bucket = {}
        for k, v in mapping.items():
            if isinstance(v, dict):
                bucket[str(k).strip()] = _normalize_entry(v)
        genres[str(genre).strip()] = bucket

    return {'common': common, 'genres': genres}


def _merge_unique(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        value = str(item).strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def is_direct_sfx_term(term: str) -> bool:
    value = str(term or '').strip()
    if not value:
        return False
    return any(hint in value for hint in DIRECT_SFX_HINTS)


def classify_sfx_terms(terms: list[str]) -> dict:
    ordered = _merge_unique(terms or [])
    direct = [term for term in ordered if is_direct_sfx_term(term)]
    composite = [term for term in ordered if term not in set(direct)]
    return {
        'direct_terms': direct,
        'composite_terms': composite,
        'display_terms': [*direct, *[f'{term}（组合）' for term in composite]],
    }


def ensure_action_sfx_terms(verb: str, terms: list[str] | None) -> list[str]:
    ordered = _merge_unique(terms or [])
    fallback = str(verb or '').strip()
    if ordered:
        return ordered
    return [fallback] if fallback else []


def build_sfx_display_name(term: str, genre: str, composite_terms: list[str] | None = None) -> str:
    value = str(term or '').strip()
    if not value:
        return ''
    composite_set = {str(x).strip() for x in (composite_terms or []) if str(x).strip()}
    mode = '组合' if value in composite_set or not is_direct_sfx_term(value) else '直达'
    genre_part = str(genre or '').strip()
    return f'{value}（{mode}{("-" + genre_part) if genre_part else ""}）'


def build_action_node_key(genre: str, head: str) -> str:
    g = str(genre or '').strip()
    h = str(head or '').strip()
    if not h:
        return ''
    return f'{g}::{h}' if g else h


def load_action_node_coverage(node_keys: set[str] | None = None) -> dict[str, dict]:
    with SessionLocal() as db:
        tasks = db.execute(select(ActionSupplementTask)).scalars().all()
        task_ids = [int(row.id) for row in tasks]
        asset_rows = (
            db.execute(select(ActionSupplementAsset).where(ActionSupplementAsset.supplement_id.in_(task_ids))).scalars().all()
            if task_ids
            else []
        )

    assets_by_supp: dict[int, list[ActionSupplementAsset]] = {}
    for row in asset_rows:
        assets_by_supp.setdefault(int(row.supplement_id), []).append(row)

    coverage: dict[str, dict] = {}
    for task in tasks:
        genre = str(task.target_genre or task.genre or '').strip()
        head = str(task.target_head or task.verb or '').strip()
        node_key = build_action_node_key(genre, head)
        if not node_key:
            continue
        if node_keys and node_key not in node_keys:
            continue
        entry = coverage.setdefault(
            node_key,
            {
                'node_key': node_key,
                'genre': genre,
                'verb_head': head,
                'covered_labels': set(),
                'target_terms': set(),
                'assets': [],
            },
        )
        try:
            sfx_terms = [str(x).strip() for x in json.loads(task.sfx_terms_json or '[]') if str(x).strip()]
        except json.JSONDecodeError:
            sfx_terms = []
        entry['target_terms'].update(sfx_terms)
        classified = classify_sfx_terms(sfx_terms)
        composite_terms = classified['composite_terms']
        for asset in assets_by_supp.get(int(task.id), []):
            label = str(asset.asset_label or '').strip()
            if not label:
                continue
            entry['covered_labels'].add(label)
            display_name = build_sfx_display_name(label, genre, composite_terms)
            key = f'{label}|{asset.asset_file_path}'
            if key in {f"{str(x.get('asset_label') or '').strip()}|{str(x.get('asset_file_path') or '').strip()}" for x in entry['assets']}:
                continue
            entry['assets'].append(
                {
                    'label': label,
                    'asset_label': label,
                    'file_name': Path(asset.asset_file_path).name if asset.asset_file_path else '',
                    'file_path': asset.asset_file_path,
                    'asset_file_path': asset.asset_file_path,
                    'download_api': f"{settings.api_prefix}/sfx/file?path={quote(asset.asset_file_path, safe='')}" if asset.asset_file_path else '',
                    'score': 3.0,
                    'canonical': label,
                    'display_name': display_name,
                }
            )

    for entry in coverage.values():
        entry['covered_labels'] = sorted(entry['covered_labels'])
        entry['target_terms'] = sorted(entry['target_terms'])
    return coverage


def load_global_sfx_label_coverage() -> dict:
    with SessionLocal() as db:
        tasks = db.execute(select(ActionSupplementTask)).scalars().all()
        task_ids = [int(row.id) for row in tasks]
        asset_rows = (
            db.execute(select(ActionSupplementAsset).where(ActionSupplementAsset.supplement_id.in_(task_ids))).scalars().all()
            if task_ids
            else []
        )

    task_by_id = {int(row.id): row for row in tasks}
    labels = set()
    assets_by_label: dict[str, list[dict]] = {}
    for asset in asset_rows:
        label = str(asset.asset_label or '').strip()
        if not label:
            continue
        labels.add(label)
        task = task_by_id.get(int(asset.supplement_id))
        genre = str((task.target_genre if task else '') or (task.genre if task else '') or '').strip()
        display_name = build_sfx_display_name(label, genre, [label] if not is_direct_sfx_term(label) else [])
        assets_by_label.setdefault(label, []).append(
            {
                'label': label,
                'asset_label': label,
                'file_name': Path(asset.asset_file_path).name if asset.asset_file_path else '',
                'file_path': asset.asset_file_path,
                'asset_file_path': asset.asset_file_path,
                'download_api': f"{settings.api_prefix}/sfx/file?path={quote(asset.asset_file_path, safe='')}" if asset.asset_file_path else '',
                'score': 3.0,
                'canonical': label,
                'display_name': display_name,
            }
        )
    return {
        'labels': sorted(labels),
        'assets_by_label': assets_by_label,
    }


def _merge_graph_entries(base: dict | None, extra: dict | None) -> dict:
    return {
        'parent_node': (extra or {}).get('parent_node') or (base or {}).get('parent_node') or {},
        'children': {
            'semantic_terms': _merge_unique(list(((base or {}).get('children') or {}).get('semantic_terms') or []) + list(((extra or {}).get('children') or {}).get('semantic_terms') or [])),
            'sfx_terms': _merge_unique(list(((base or {}).get('children') or {}).get('sfx_terms') or []) + list(((extra or {}).get('children') or {}).get('sfx_terms') or [])),
        },
        'semantic_terms': _merge_unique(list((base or {}).get('semantic_terms') or []) + list((extra or {}).get('semantic_terms') or [])),
        'sfx_terms': _merge_unique(list((base or {}).get('sfx_terms') or []) + list((extra or {}).get('sfx_terms') or [])),
    }


def build_action_sfx_recommendation(project_id: int, action_report: dict, backend_override: str | None = None) -> dict:
    report = action_report if isinstance(action_report, dict) else {}
    candidates = report.get('action_candidates') or []
    graph = _load_action_graph()
    genre = str(report.get('genre') or '').strip()
    library = load_sfx_library(backend_override=backend_override)
    global_label_coverage = load_global_sfx_label_coverage()
    candidate_node_keys = set()
    for row in candidates:
        if not isinstance(row, dict):
            continue
        verb = str((row or {}).get('verb') or '').strip()
        if not verb:
            continue
        genre_entry = (((graph.get('genres') or {}).get(genre) or {}).get(verb)) or {}
        head = str((genre_entry.get('parent_node') or {}).get('verb_head') or verb).strip()
        node_key = build_action_node_key(genre, head)
        if node_key:
            candidate_node_keys.add(node_key)
    coverage_by_node = load_action_node_coverage(candidate_node_keys)

    items = []
    gap_map: dict[str, dict] = {}
    for row in candidates:
        if not isinstance(row, dict):
            continue
        verb = str(row.get('verb') or '').strip()
        excerpt = str(row.get('sentence_excerpt') or '').strip()
        if not verb:
            continue
        common_entry = ((graph.get('common') or {}).get(verb) or {})
        genre_entry = ((((graph.get('genres') or {}).get(genre) or {}).get(verb)) or {})
        graph_entry = _merge_graph_entries(common_entry, genre_entry)
        node_key = str((graph_entry.get('parent_node') or {}).get('node_key') or build_action_node_key(genre, verb)).strip()
        coverage = coverage_by_node.get(node_key) or {}
        semantic_terms = list(graph_entry.get('semantic_terms') or [])
        expanded = expand_term(verb, backend_override=backend_override)
        semantic_terms = _merge_unique([verb] + semantic_terms + sorted(expanded.terms))

        sfx_terms = ensure_action_sfx_terms(verb, list(graph_entry.get('sfx_terms') or []))
        sfx_classified = classify_sfx_terms(sfx_terms)
        asset_rows = [dict(x) for x in (coverage.get('assets') or [])]
        picked_labels = []
        missing_labels = []
        for term in semantic_terms[:8]:
            matched = match_sfx_candidates(term, library, top_n=3, backend_override=backend_override)
            if matched:
                picked_labels.append(term)
            else:
                missing_labels.append(term)
            for m in matched:
                asset_rows.append(
                    {
                        'label': term,
                        'file_name': m['file_name'],
                        'file_path': m['file_path'],
                        'download_api': f"{settings.api_prefix}/sfx/file?path={quote(m['file_path'], safe='')}",
                        'score': m['score'],
                        'canonical': m['canonical'],
                    }
                )

        for term in sfx_terms[:8]:
            for asset in (global_label_coverage.get('assets_by_label') or {}).get(term, []):
                asset_rows.append(dict(asset))
            matched = match_sfx_candidates(term, library, top_n=3, backend_override=backend_override)
            for m in matched:
                asset_rows.append(
                    {
                        'label': term,
                        'file_name': m['file_name'],
                        'file_path': m['file_path'],
                        'download_api': f"{settings.api_prefix}/sfx/file?path={quote(m['file_path'], safe='')}",
                        'score': m['score'],
                        'canonical': m['canonical'],
                    }
                )

        dedup_assets = []
        seen_asset = set()
        for a in sorted(asset_rows, key=lambda x: (-float(x['score']), x['file_name'])):
            key = a['file_path']
            if key in seen_asset:
                continue
            seen_asset.add(key)
            dedup_assets.append(a)

        all_sfx_terms = _merge_unique(sfx_terms + picked_labels)
        all_sfx_classified = classify_sfx_terms(all_sfx_terms)
        covered_label_set = {str(x).strip() for x in (coverage.get('covered_labels') or []) if str(x).strip()}
        covered_label_set.update(str(x).strip() for x in (global_label_coverage.get('labels') or []) if str(x).strip())
        covered_label_set.update(str(x.get('label') or '').strip() for x in dedup_assets if str(x.get('label') or '').strip())
        covered_sfx_terms = [term for term in all_sfx_terms if term in covered_label_set]
        missing_sfx_terms = [term for term in all_sfx_terms if term not in covered_label_set]
        missing_sfx_classified = classify_sfx_terms(missing_sfx_terms)
        for asset in dedup_assets:
            asset['display_name'] = build_sfx_display_name(
                str(asset.get('label') or ''),
                genre,
                all_sfx_classified['composite_terms'],
            )
        items.append(
            {
                'parent_node': graph_entry.get('parent_node') or {
                    'genre': genre,
                    'verb_head': verb,
                    'node_key': f'{genre}::{verb}' if genre else verb,
                },
                'verb': verb,
                'sentence_excerpt': excerpt,
                'reason': str(row.get('reason') or '').strip(),
                'semantic_terms': semantic_terms[:10],
                'sfx_terms': all_sfx_terms[:8],
                'sfx_terms_classified': {
                    'direct_terms': all_sfx_classified['direct_terms'][:8],
                    'composite_terms': all_sfx_classified['composite_terms'][:8],
                    'display_terms': all_sfx_classified['display_terms'][:8],
                },
                'children': {
                    'semantic_terms': semantic_terms[:10],
                    'sfx_terms': all_sfx_terms[:8],
                    'missing_sfx_terms': missing_sfx_terms[:10],
                    'covered_sfx_terms': covered_sfx_terms[:10],
                    'direct_sfx_terms': all_sfx_classified['direct_terms'][:8],
                    'composite_sfx_terms': all_sfx_classified['composite_terms'][:8],
                    'display_sfx_terms': all_sfx_classified['display_terms'][:8],
                    'missing_direct_sfx_terms': missing_sfx_classified['direct_terms'][:10],
                    'missing_composite_sfx_terms': missing_sfx_classified['composite_terms'][:10],
                    'display_missing_sfx_terms': missing_sfx_classified['display_terms'][:10],
                },
                'assets': dedup_assets[:6],
                'missing_sfx_terms': missing_sfx_terms[:10],
                'missing_sfx_terms_classified': {
                    'direct_terms': missing_sfx_classified['direct_terms'][:10],
                    'composite_terms': missing_sfx_classified['composite_terms'][:10],
                    'display_terms': missing_sfx_classified['display_terms'][:10],
                },
                'graph_source': {
                    'common_hit': bool(common_entry),
                    'genre_hit': bool(genre_entry),
                    'genre': genre,
                },
            }
        )
        for label in _merge_unique(sfx_terms + missing_labels):
            entry = gap_map.setdefault(
                label,
                {
                    'sfx_term': label,
                    'verbs': [],
                    'examples': [],
                    'missing_count': 0,
                },
            )
            if verb not in entry['verbs']:
                entry['verbs'].append(verb)
            if excerpt and excerpt not in entry['examples']:
                entry['examples'].append(excerpt)
            entry['missing_count'] += 1

    gap_items = sorted(
        gap_map.values(),
        key=lambda x: (-int(x['missing_count']), x['sfx_term']),
    )

    return {
        'title': '动作图谱推荐',
        'project_id': project_id,
        'genre': report.get('genre', ''),
        'graph_model': {
            'parent': '赛道+动作词',
            'children': ['semantic_terms', 'sfx_terms', 'missing_sfx_terms'],
            'node_example': '玄幻::躲闪',
        },
        'graph_items': items,
        'asset_gap_summary': {
            'gap_count': len(gap_items),
            'gap_items': gap_items[:30],
        },
        'summary': {
            'verb_count': len(items),
            'asset_count': sum(len(x.get('assets') or []) for x in items),
            'gap_count': len(gap_items),
        },
    }
