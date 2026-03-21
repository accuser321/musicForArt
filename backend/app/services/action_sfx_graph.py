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

    meta = (data or {}).get('_meta') if isinstance(data, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    return {'_meta': meta, 'common': common, 'genres': genres}


def _merge_unique(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        value = str(item).strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


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


def resolve_action_fallback_head(genre: str, term: str, graph: dict | None = None) -> str:
    value = str(term or '').strip()
    if not value:
        return ''
    alias_meta = _fallback_alias_meta(graph)
    genre_key = str(genre or '').strip()
    current = value
    for _ in range(4):
        next_value = ''
        if genre_key:
            next_value = str((alias_meta.get('genres') or {}).get(genre_key, {}).get(current) or '').strip()
        if not next_value:
            next_value = str((alias_meta.get('common') or {}).get(current) or '').strip()
        if not next_value or next_value == current:
            break
        current = next_value
    return current or value


def _suppress_alias_fragments(genre: str, canonical_head: str, terms: list[str], graph: dict | None = None) -> list[str]:
    out = []
    head = str(canonical_head or '').strip()
    for term in terms or []:
        value = str(term or '').strip()
        if not value:
            continue
        resolved = resolve_action_fallback_head(genre, value, graph)
        if head and value != head and resolved == head:
            continue
        out.append(value)
    return _merge_unique(out)


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
        out[genre_key] = {str(head or '').strip() for head in (heads or []) if str(head or '').strip()}
    return out


def is_inheritance_blocked(genre: str, head: str, graph: dict | None = None) -> bool:
    genre_key = str(genre or '').strip()
    head_key = str(head or '').strip()
    if not genre_key or not head_key:
        return False
    return head_key in _inheritance_blocks(graph).get(genre_key, set())


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


def build_asset_scope_label(asset_scope: str, genre: str) -> str:
    scope = str(asset_scope or '').strip().lower()
    genre_name = str(genre or '').strip()
    if scope == 'common':
        return '通用'
    if scope == 'genre':
        return genre_name or '赛道'
    return '候选'


def build_asset_variant_display_name(term: str, asset_scope: str, genre: str) -> str:
    value = str(term or '').strip()
    if not value:
        return ''
    return f'{value}（{build_asset_scope_label(asset_scope, genre)}）'


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
            asset_scope = str(asset.asset_scope or 'genre').strip().lower() or 'genre'
            asset_scope_genre = str(asset.asset_scope_genre or genre or '').strip()
            entry['covered_labels'].add(label)
            display_name = build_asset_variant_display_name(label, asset_scope, asset_scope_genre or genre)
            key = f'{label}|{asset_scope}|{asset_scope_genre}|{asset.asset_file_path}'
            if key in {
                f"{str(x.get('asset_label') or '').strip()}|{str(x.get('asset_scope') or '').strip()}|{str(x.get('asset_scope_genre') or '').strip()}|{str(x.get('asset_file_path') or '').strip()}"
                for x in entry['assets']
            }:
                continue
            entry['assets'].append(
                {
                    'label': label,
                    'asset_label': label,
                    'asset_scope': asset_scope,
                    'asset_scope_genre': asset_scope_genre,
                    'scope_label': build_asset_scope_label(asset_scope, asset_scope_genre or genre),
                    'variant_key': f'{label}|{asset_scope}|{asset_scope_genre}',
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
        asset_scope = str(asset.asset_scope or 'genre').strip().lower() or 'genre'
        asset_scope_genre = str(asset.asset_scope_genre or genre or '').strip()
        display_name = build_asset_variant_display_name(label, asset_scope, asset_scope_genre or genre)
        assets_by_label.setdefault(label, []).append(
            {
                'label': label,
                'asset_label': label,
                'asset_scope': asset_scope,
                'asset_scope_genre': asset_scope_genre,
                'scope_label': build_asset_scope_label(asset_scope, asset_scope_genre or genre),
                'variant_key': f'{label}|{asset_scope}|{asset_scope_genre}',
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


def _layered_term_items(common_terms: list[str], genre_terms: list[str]) -> list[dict]:
    ordered = _merge_unique(list(common_terms or []) + list(genre_terms or []))
    common_set = {str(x).strip() for x in (common_terms or []) if str(x).strip()}
    genre_set = {str(x).strip() for x in (genre_terms or []) if str(x).strip()}
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


def _append_fallback_term_items(existing_items: list[dict], fallback_terms: list[str]) -> list[dict]:
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


def get_action_node_layer_term_items(genre: str, head: str) -> dict:
    graph = _load_action_graph()
    head_value = str(head or '').strip()
    genre_value = str(genre or '').strip()
    common_entry = ((graph.get('common') or {}).get(head_value) or {})
    if is_inheritance_blocked(genre_value, head_value, graph):
        common_entry = {}
    genre_entry = ((((graph.get('genres') or {}).get(genre_value) or {}).get(head_value)) or {})
    common_semantic_terms = list(common_entry.get('semantic_terms') or ((common_entry.get('children') or {}).get('semantic_terms') or []))
    genre_semantic_terms = list(genre_entry.get('semantic_terms') or ((genre_entry.get('children') or {}).get('semantic_terms') or []))
    common_sfx_terms = list(common_entry.get('sfx_terms') or ((common_entry.get('children') or {}).get('sfx_terms') or []))
    genre_sfx_terms = list(genre_entry.get('sfx_terms') or ((genre_entry.get('children') or {}).get('sfx_terms') or []))
    sfx_term_items = _layered_term_items(common_sfx_terms, genre_sfx_terms)
    direct_set = set(classify_sfx_terms(_merge_unique(list(common_sfx_terms) + list(genre_sfx_terms)))['direct_terms'])
    return {
        'semantic_term_items': _layered_term_items(common_semantic_terms, genre_semantic_terms),
        'sfx_term_items': sfx_term_items,
        'direct_sfx_term_items': [item for item in sfx_term_items if str(item.get('term') or '').strip() in direct_set],
        'composite_sfx_term_items': [item for item in sfx_term_items if str(item.get('term') or '').strip() not in direct_set],
    }


def build_action_sfx_recommendation(project_id: int, action_report: dict, backend_override: str | None = None) -> dict:
    report = action_report if isinstance(action_report, dict) else {}
    candidates = report.get('action_candidates') or []
    graph = _load_action_graph()
    genre = str(report.get('genre') or '').strip()
    library = load_sfx_library(backend_override=backend_override)
    global_label_coverage = load_global_sfx_label_coverage()
    candidate_node_keys = set()
    blocked_hits = []
    candidate_groups: dict[str, dict] = {}
    for row in candidates:
        if not isinstance(row, dict):
            continue
        raw_verb = str((row or {}).get('verb') or '').strip()
        if not raw_verb:
            continue
        verb = resolve_action_fallback_head(genre, raw_verb, graph)
        genre_entry = (((graph.get('genres') or {}).get(genre) or {}).get(verb)) or {}
        common_entry = ((graph.get('common') or {}).get(verb)) or {}
        if is_inheritance_blocked(genre, verb, graph) and common_entry and not genre_entry:
            blocked_hits.append(
                {
                    'genre': genre,
                    'verb_head': verb,
                    'sentence_excerpt': str((row or {}).get('sentence_excerpt') or '').strip(),
                    'reason': '该通用层节点已对当前赛道关闭继承，但用户文本仍然命中了这个动作词。',
                }
            )
            continue
        head = str((genre_entry.get('parent_node') or {}).get('verb_head') or verb).strip()
        node_key = build_action_node_key(genre, head)
        if node_key:
            candidate_node_keys.add(node_key)
        bucket = candidate_groups.setdefault(
            verb,
            {
                'verb': verb,
                'raw_verbs': [],
                'sentence_excerpts': [],
                'reasons': [],
            },
        )
        if raw_verb not in bucket['raw_verbs']:
            bucket['raw_verbs'].append(raw_verb)
        excerpt = str((row or {}).get('sentence_excerpt') or '').strip()
        if excerpt and excerpt not in bucket['sentence_excerpts']:
            bucket['sentence_excerpts'].append(excerpt)
        reason = str((row or {}).get('reason') or '').strip()
        if reason and reason not in bucket['reasons']:
            bucket['reasons'].append(reason)
    coverage_by_node = load_action_node_coverage(candidate_node_keys)

    items = []
    gap_map: dict[str, dict] = {}
    for group in candidate_groups.values():
        verb = str(group.get('verb') or '').strip()
        excerpt = ' / '.join((group.get('sentence_excerpts') or [])[:2]).strip()
        if not verb:
            continue
        common_entry = ((graph.get('common') or {}).get(verb) or {})
        genre_entry = ((((graph.get('genres') or {}).get(genre) or {}).get(verb)) or {})
        if is_inheritance_blocked(genre, verb, graph) and common_entry and not genre_entry:
            continue
        graph_entry = _merge_graph_entries(common_entry, genre_entry)
        common_semantic_terms = list(common_entry.get('semantic_terms') or ((common_entry.get('children') or {}).get('semantic_terms') or []))
        genre_semantic_terms = list(genre_entry.get('semantic_terms') or ((genre_entry.get('children') or {}).get('semantic_terms') or []))
        common_sfx_terms = list(common_entry.get('sfx_terms') or ((common_entry.get('children') or {}).get('sfx_terms') or []))
        genre_sfx_terms = list(genre_entry.get('sfx_terms') or ((genre_entry.get('children') or {}).get('sfx_terms') or []))
        node_key = str((graph_entry.get('parent_node') or {}).get('node_key') or build_action_node_key(genre, verb)).strip()
        coverage = coverage_by_node.get(node_key) or {}
        semantic_terms = list(graph_entry.get('semantic_terms') or [])
        expanded = expand_term(verb, backend_override=backend_override)
        semantic_terms = _suppress_alias_fragments(
            genre,
            verb,
            _merge_unique([verb] + semantic_terms + sorted(expanded.terms)),
            graph,
        )

        sfx_terms = _suppress_alias_fragments(
            genre,
            verb,
            ensure_action_sfx_terms(verb, list(graph_entry.get('sfx_terms') or [])),
            graph,
        )
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

        all_sfx_terms = _suppress_alias_fragments(genre, verb, _merge_unique(sfx_terms + picked_labels), graph)
        all_sfx_classified = classify_sfx_terms(all_sfx_terms)
        semantic_term_items = _layered_term_items(common_semantic_terms, genre_semantic_terms)
        sfx_term_items = _layered_term_items(common_sfx_terms, genre_sfx_terms)
        semantic_term_items = _append_fallback_term_items(
            semantic_term_items,
            [term for term in semantic_terms if term not in {str(item.get('term') or '').strip() for item in semantic_term_items}],
        )
        sfx_term_items = _append_fallback_term_items(
            sfx_term_items,
            [term for term in all_sfx_terms if term not in {str(item.get('term') or '').strip() for item in sfx_term_items}],
        )
        direct_term_items = [item for item in sfx_term_items if item['term'] in set(all_sfx_classified['direct_terms'])]
        composite_term_items = [item for item in sfx_term_items if item['term'] in set(all_sfx_classified['composite_terms'])]
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
                'raw_verbs': list(group.get('raw_verbs') or []),
                'sentence_excerpt': excerpt,
                'reason': ' / '.join((group.get('reasons') or [])[:2]).strip(),
                'semantic_terms': semantic_terms[:10],
                'sfx_terms': all_sfx_terms[:8],
                'sfx_terms_classified': {
                    'direct_terms': all_sfx_classified['direct_terms'][:8],
                    'composite_terms': all_sfx_classified['composite_terms'][:8],
                    'display_terms': all_sfx_classified['display_terms'][:8],
                },
                'children': {
                    'semantic_terms': semantic_terms[:10],
                    'semantic_term_items': semantic_term_items[:10],
                    'sfx_terms': all_sfx_terms[:8],
                    'sfx_term_items': sfx_term_items[:8],
                    'missing_sfx_terms': missing_sfx_terms[:10],
                    'covered_sfx_terms': covered_sfx_terms[:10],
                    'direct_sfx_terms': all_sfx_classified['direct_terms'][:8],
                    'direct_sfx_term_items': direct_term_items[:8],
                    'composite_sfx_terms': all_sfx_classified['composite_terms'][:8],
                    'composite_sfx_term_items': composite_term_items[:8],
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
                    'has_fallback_terms': any(str(item.get('source') or '') == 'fallback' for item in (sfx_term_items + semantic_term_items)),
                    'semantic_term_items': semantic_term_items[:10],
                    'sfx_term_items': sfx_term_items[:8],
                    'direct_sfx_term_items': direct_term_items[:8],
                    'composite_sfx_term_items': composite_term_items[:8],
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
        'blocked_inheritance_hits': blocked_hits,
    }
