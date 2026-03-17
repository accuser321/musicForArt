import json
from pathlib import Path

from app.services.action_sfx_graph import ensure_action_sfx_terms
from app.services.nlp_zh import (
    char_bigram_counter,
    clear_domain_term_cache,
    cosine_counter,
    normalize_text,
    tokenize_cn,
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
        return {}
    try:
        return json.loads(ACTION_GRAPH_PATH.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {}


def save_action_graph(data: dict) -> None:
    ACTION_GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACTION_GRAPH_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    clear_domain_term_cache()


def merge_action_graph_draft(draft_graph: dict) -> dict:
    base = _load_action_graph() or {}
    base.setdefault('_meta', {'version': 'genre-v2'})
    base.setdefault('common', {})
    base.setdefault('genres', {})

    incoming_common = (draft_graph or {}).get('common') or {}
    for head, row in incoming_common.items():
        if not isinstance(row, dict):
            continue
        exist = base['common'].get(
            head,
            {
                'parent_node': {'genre': '', 'verb_head': head, 'node_key': head},
                'children': {'semantic_terms': [], 'sfx_terms': []},
                'semantic_terms': [],
                'sfx_terms': [],
            },
        )
        sem = [str(x).strip() for x in row.get('semantic_terms', []) if str(x).strip()]
        sfx = [str(x).strip() for x in row.get('sfx_terms', []) if str(x).strip()]
        exist['semantic_terms'] = _merge_unique(
            list(exist.get('semantic_terms', []) or [])
            + sem
        )
        exist['sfx_terms'] = _merge_unique(
            list(exist.get('sfx_terms', []) or [])
            + sfx
        )
        exist['parent_node'] = exist.get('parent_node') or {'genre': '', 'verb_head': head, 'node_key': head}
        exist['children'] = {
            'semantic_terms': list(exist.get('semantic_terms', []) or []),
            'sfx_terms': list(exist.get('sfx_terms', []) or []),
        }
        base['common'][head] = exist

    incoming_genres = (draft_graph or {}).get('genres') or {}
    for genre, mapping in incoming_genres.items():
        if not isinstance(mapping, dict):
            continue
        base['genres'].setdefault(genre, {})
        for head, row in mapping.items():
            if not isinstance(row, dict):
                continue
            exist = base['genres'][genre].get(
                head,
                {
                    'parent_node': {'genre': genre, 'verb_head': head, 'node_key': f'{genre}::{head}'},
                    'children': {'semantic_terms': [], 'sfx_terms': []},
                    'semantic_terms': [],
                    'sfx_terms': [],
                },
            )
            sem = [str(x).strip() for x in row.get('semantic_terms', []) if str(x).strip()]
            sfx = [str(x).strip() for x in row.get('sfx_terms', []) if str(x).strip()]
            exist['semantic_terms'] = _merge_unique(
                list(exist.get('semantic_terms', []) or [])
                + sem
            )
            exist['sfx_terms'] = _merge_unique(
                list(exist.get('sfx_terms', []) or [])
                + sfx
            )
            exist['parent_node'] = exist.get('parent_node') or {'genre': genre, 'verb_head': head, 'node_key': f'{genre}::{head}'}
            exist['children'] = {
                'semantic_terms': list(exist.get('semantic_terms', []) or []),
                'sfx_terms': list(exist.get('sfx_terms', []) or []),
            }
            base['genres'][genre][head] = exist

    save_action_graph(base)
    return base


def _flatten_existing_heads(graph: dict, genre: str) -> dict[str, set[str]]:
    common = (graph.get('common') or {}) if isinstance(graph, dict) else {}
    genre_map = ((graph.get('genres') or {}).get(genre) or {}) if isinstance(graph, dict) else {}
    out = {}
    for bucket in (common, genre_map):
        for head, row in bucket.items():
            if not isinstance(row, dict):
                continue
            vals = {normalize_text(head)}
            semantic_terms = row.get('semantic_terms', [])
            children = row.get('children') or {}
            if isinstance(children, dict) and children.get('semantic_terms'):
                semantic_terms = children.get('semantic_terms') or semantic_terms
            vals.update(normalize_text(x) for x in semantic_terms if str(x).strip())
            out[str(head)] = {x for x in vals if x}
    return out


def _pick_target_head(verb: str, heads: dict[str, set[str]]) -> tuple[str, float, str]:
    v_norm = normalize_text(verb)
    v_tokens = set(tokenize_cn(v_norm))
    v_vec = char_bigram_counter(v_norm)
    best_head = verb
    best_score = 0.0
    best_reason = 'new_head'

    for head, vocab in heads.items():
        head_norm = normalize_text(head)
        head_tokens = set(tokenize_cn(head_norm))
        overlap = len(v_tokens & head_tokens) / max(1, len(v_tokens | head_tokens))
        sim = cosine_counter(v_vec, char_bigram_counter(head_norm))
        score = overlap * 0.6 + sim * 0.4
        if v_norm in vocab:
            score += 0.4
        if score > best_score:
            best_score = score
            best_head = head
            best_reason = f'overlap={overlap:.3f},sim={sim:.3f}'

    if best_score < 0.34:
        return verb, round(best_score, 4), 'new_head'
    return best_head, round(best_score, 4), best_reason


def generate_action_graph_draft(action_sfx_result: dict) -> dict:
    data = action_sfx_result if isinstance(action_sfx_result, dict) else {}
    genre = str(data.get('genre') or '').strip()
    graph = _load_action_graph()
    existing_heads = _flatten_existing_heads(graph, genre)
    items = []
    draft_graph = {'common': {}, 'genres': {genre: {}}}

    for row in data.get('graph_items') or []:
        if not isinstance(row, dict):
            continue
        verb = str(row.get('verb') or '').strip()
        if not verb:
            continue
        semantic_terms = [str(x).strip() for x in row.get('semantic_terms', []) if str(x).strip()]
        sfx_terms = ensure_action_sfx_terms(verb, [str(x).strip() for x in row.get('sfx_terms', []) if str(x).strip()])
        graph_source = row.get('graph_source') or {}
        needs_draft = not bool(graph_source.get('genre_hit')) or bool(row.get('missing_sfx_terms'))
        if not needs_draft:
            continue

        target_head, confidence, reason = _pick_target_head(verb, existing_heads)
        target_bucket = 'genres'
        target_genre = genre
        if target_head == verb and not genre:
            target_bucket = 'common'

        item = {
            'verb': verb,
            'target_head': target_head,
            'target_bucket': target_bucket,
            'target_genre': target_genre,
            'confidence': confidence,
            'reason': reason,
            'sentence_excerpt': str(row.get('sentence_excerpt') or '').strip(),
            'semantic_terms': semantic_terms[:8],
            'sfx_terms': sfx_terms[:8],
            'missing_sfx_terms': [str(x).strip() for x in row.get('missing_sfx_terms', []) if str(x).strip()][:8],
            'graph_source': graph_source,
        }
        items.append(item)

        entry = {
            'parent_node': {
                'genre': target_genre if target_bucket != 'common' else '',
                'verb_head': target_head,
                'node_key': f'{target_genre}::{target_head}' if target_bucket != 'common' and target_genre else target_head,
            },
            'children': {
                'semantic_terms': semantic_terms[:8],
                'sfx_terms': sfx_terms[:8],
            },
            'semantic_terms': semantic_terms[:8],
            'sfx_terms': sfx_terms[:8],
        }
        if target_bucket == 'common':
            draft_graph['common'][target_head] = entry
        else:
            draft_graph['genres'].setdefault(target_genre, {})[target_head] = entry

    return {
        'title': '动作补充单',
        'genre': genre,
        'draft_count': len(items),
        'draft_items': items,
        'draft_graph': draft_graph,
    }


def apply_action_graph_draft(draft_result: dict) -> dict:
    if not isinstance(draft_result, dict):
        raise ValueError('draft_result must be object')
    draft_graph = draft_result.get('draft_graph')
    if not isinstance(draft_graph, dict):
        raise ValueError('draft_graph must be object')
    merged = merge_action_graph_draft(draft_graph)
    return {
        'ok': True,
        'genre': draft_result.get('genre', ''),
        'draft_count': int(draft_result.get('draft_count') or 0),
        'graph': merged,
    }
