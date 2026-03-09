import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DraftReview, ReasoningLog
from app.services.nlp_zh import char_bigram_counter, cosine_counter, normalize_text, tokenize_cn
from app.services.semantic_store import get_lexicon


def _pick_target_head(term: str, heads: list[str]) -> tuple[str | None, float, str]:
    t_norm = normalize_text(term)
    t_tokens = set(tokenize_cn(t_norm))
    t_vec = char_bigram_counter(t_norm)

    best = None
    best_score = 0.0
    best_reason = ''

    for h in heads:
        h_norm = normalize_text(h)
        h_tokens = set(tokenize_cn(h_norm))
        overlap = len(t_tokens & h_tokens) / max(1, len(t_tokens | h_tokens))
        sim = cosine_counter(t_vec, char_bigram_counter(h_norm))

        score = overlap * 0.7 + sim * 0.3
        if t_norm in h_norm or h_norm in t_norm:
            score += 0.2

        if score > best_score:
            best_score = score
            best = h
            best_reason = f'overlap={overlap:.3f},sim={sim:.3f}'

    if best_score < 0.34:
        return None, best_score, best_reason
    return best, best_score, best_reason


def generate_lexicon_draft(db: Session, days: int = 7) -> dict:
    days = max(1, min(90, days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = db.execute(
        select(ReasoningLog.event_type, ReasoningLog.term, ReasoningLog.output_json)
        .where(ReasoningLog.created_at >= cutoff)
    ).all()

    low_match_counter = Counter()
    missing_sfx_counter = Counter()

    for event_type, term, output_json in rows:
        try:
            payload = json.loads(output_json)
        except json.JSONDecodeError:
            payload = {}

        if event_type == 'search_sfx':
            match_count = payload.get('match_count')
            if match_count is None:
                matches = payload.get('matches') or []
                match_count = len(matches)
            if int(match_count or 0) == 0 and term:
                low_match_counter[normalize_text(term)] += 1

        if event_type == 'export_download':
            for m in payload.get('missing_sfx', []) or []:
                mm = normalize_text(m)
                if mm:
                    missing_sfx_counter[mm] += 1

    candidates = Counter()
    for k, v in low_match_counter.items():
        candidates[k] += v
    for k, v in missing_sfx_counter.items():
        candidates[k] += v

    lex = get_lexicon()
    heads = list(lex.keys())
    reviewed_map = {
        normalize_text(cand): status
        for cand, status in db.execute(select(DraftReview.candidate, DraftReview.status)).all()
    }

    draft_lexicon: dict[str, list[str]] = {}
    draft_items = []

    for cand, cnt in candidates.most_common(40):
        reviewed_status = reviewed_map.get(cand)
        if reviewed_status in {'approved', 'rejected'}:
            continue
        target, score, reason = _pick_target_head(cand, heads)
        if target is None:
            # If we cannot confidently map to existing head, propose a new head bucket
            target = cand
            score = max(score, 0.2)
            reason = reason or 'new_head_fallback'

        # Skip only if candidate already exists in an existing head bucket.
        # For a new head fallback (target == cand and head not in lex), keep it.
        if target in lex:
            existing = set([normalize_text(target)] + [normalize_text(x) for x in lex.get(target, [])])
            if cand in existing:
                continue

        draft_lexicon.setdefault(target, []).append(cand)
        draft_items.append(
            {
                'candidate': cand,
                'target_head': target,
                'count': cnt,
                'confidence': round(float(score), 4),
                'reason': reason,
                'sources': {
                    'low_match': low_match_counter.get(cand, 0),
                    'missing_sfx': missing_sfx_counter.get(cand, 0),
                },
                'review_status': reviewed_status or 'pending',
            }
        )

    # dedup synonyms per head
    for h, arr in list(draft_lexicon.items()):
        seen = set()
        uniq = []
        for a in arr:
            if a not in seen:
                seen.add(a)
                uniq.append(a)
        draft_lexicon[h] = uniq

    return {
        'days': days,
        'candidate_count': sum(candidates.values()),
        'candidate_unique_count': len(candidates),
        'draft_head_count': len(draft_lexicon),
        'draft_item_count': len(draft_items),
        'draft_lexicon': draft_lexicon,
        'draft_items': draft_items,
    }
