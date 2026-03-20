from urllib.parse import quote
from pathlib import Path

from app.config import settings
from app.services.action_sfx_graph import (
    build_asset_scope_label,
    build_asset_variant_display_name,
    load_global_sfx_label_coverage,
)
from app.services.scene_graph_manage import resolve_scene_graph_node
from app.services.sfx_matcher import load_sfx_library, match_sfx_candidates


def _merge_unique(items: list[str] | None) -> list[str]:
    seen = set()
    out = []
    for item in items or []:
        value = str(item or '').strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def build_scene_sfx_recommendation(project_id: int, scene_report: dict, backend_override: str | None = None) -> dict:
    report = scene_report or {}
    genre = str(report.get('genre') or '').strip()
    items = []
    library = load_sfx_library()
    global_label_coverage = load_global_sfx_label_coverage()
    gap_map: dict[str, dict] = {}

    for row in (report.get('scene_items') or []):
        if not isinstance(row, dict):
            continue
        scene_name = str(row.get('scene_name') or '').strip()
        if not scene_name:
            continue
        graph_hit = resolve_scene_graph_node(scene_name, genre)
        supporting_terms = _merge_unique((row.get('supporting_sfx_terms') or []) + (graph_hit.get('supporting_sfx_terms') or []))
        detail_terms = _merge_unique((row.get('detail_sfx_terms') or []) + (graph_hit.get('detail_sfx_terms') or []))
        background_elements = _merge_unique((row.get('background_elements') or []) + (graph_hit.get('background_elements') or []))
        feature_elements = _merge_unique((row.get('feature_elements') or []) + (graph_hit.get('feature_elements') or []))
        detail_elements = _merge_unique((row.get('detail_elements') or []) + (graph_hit.get('detail_elements') or []))
        all_terms = _merge_unique(supporting_terms + detail_terms)
        asset_rows = []
        for term in all_terms[:12]:
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
        for a in sorted(asset_rows, key=lambda x: (-float(x.get('score') or 0), str(x.get('file_name') or ''))):
            asset_scope = str(a.get('asset_scope') or 'common').strip().lower() or 'common'
            asset_scope_genre = str(a.get('asset_scope_genre') or '').strip()
            normalized_scope_genre = asset_scope_genre if asset_scope == 'genre' else ''
            raw_identity = str(a.get('asset_file_path') or a.get('file_path') or a.get('file_name') or '').strip()
            file_identity = Path(raw_identity).name if raw_identity else ''
            key = (
                f"{asset_scope}|"
                f"{normalized_scope_genre}|"
                f"{file_identity.lower()}"
            )
            if key in seen_asset:
                continue
            seen_asset.add(key)
            label = str(a.get('asset_label') or a.get('label') or '').strip()
            asset_scope_genre = str(a.get('asset_scope_genre') or genre or '').strip()
            dedup_assets.append(
                {
                    **a,
                    'display_name': str(a.get('display_name') or build_asset_variant_display_name(label, asset_scope, asset_scope_genre or genre)).strip(),
                    'scope_label': build_asset_scope_label(asset_scope, asset_scope_genre or genre),
                }
            )
        display_dedup_assets = []
        seen_display = set()
        for asset in dedup_assets:
            display_key = (
                f"{str(asset.get('display_name') or '').strip()}|"
                f"{str(asset.get('scope_label') or '').strip()}"
            )
            if display_key in seen_display:
                continue
            seen_display.add(display_key)
            display_dedup_assets.append(asset)
        dedup_assets = display_dedup_assets
        covered_labels = {
            str(x).strip()
            for x in (global_label_coverage.get('labels') or [])
            if str(x).strip()
        }
        covered_labels.update(str(x.get('label') or x.get('asset_label') or '').strip() for x in dedup_assets if str(x.get('label') or x.get('asset_label') or '').strip())
        missing_terms = [term for term in all_terms if term not in covered_labels]
        for label in missing_terms:
            entry = gap_map.setdefault(
                label,
                {
                    'sfx_term': label,
                    'scenes': [],
                    'examples': [],
                    'missing_count': 0,
                },
            )
            if scene_name not in entry['scenes']:
                entry['scenes'].append(scene_name)
            excerpt = str(row.get('sentence_excerpt') or '').strip()
            if excerpt and excerpt not in entry['examples']:
                entry['examples'].append(excerpt)
            entry['missing_count'] += 1
        items.append(
            {
                'scene_id': row.get('scene_id'),
                'scene_name': scene_name,
                'node_key': row.get('node_key') or (f'{genre}::{scene_name}' if genre else scene_name),
                'sentence_excerpt': row.get('sentence_excerpt') or '',
                'time_terms': row.get('time_terms') or [],
                'location_terms': row.get('location_terms') or [],
                'background_elements': background_elements,
                'feature_elements': feature_elements,
                'detail_elements': detail_elements,
                'supporting_sfx_terms': supporting_terms,
                'detail_sfx_terms': detail_terms,
                'assets': dedup_assets[:8],
                'missing_scene_sfx_terms': missing_terms,
                'scene_graph_source': {
                    **(row.get('scene_graph_source') if isinstance(row.get('scene_graph_source'), dict) else {}),
                    'common_hit': bool(graph_hit.get('common_hit')),
                    'genre_hit': bool(graph_hit.get('genre_hit')),
                    'template_hit': str(graph_hit.get('template_hit') or (row.get('scene_graph_source') or {}).get('template_hit') or '').strip(),
                    'collection_name': str(graph_hit.get('collection_name') or '').strip(),
                    'has_fallback_terms': bool((row.get('scene_graph_source') or {}).get('has_fallback_terms')) and not (graph_hit.get('common_hit') or graph_hit.get('genre_hit')),
                },
            }
        )

    gap_items = sorted(gap_map.values(), key=lambda x: (-int(x['missing_count']), x['sfx_term']))
    return {
        'title': '场景搭建推荐',
        'project_id': project_id,
        'genre': genre,
        'graph_model': {
            'parent': '赛道+场景主节点',
            'children': ['scene_elements', 'scene_sfx_terms', 'missing_scene_sfx_terms'],
            'node_example': f'{genre}::山林夜路' if genre else '山林夜路',
        },
        'scene_items': items,
        'summary': {
            'scene_count': len(items),
            'asset_count': sum(len(x.get('assets') or []) for x in items),
            'gap_count': len(gap_items),
        },
        'asset_gap_summary': {
            'gap_count': len(gap_items),
            'gap_items': gap_items[:30],
        },
    }
