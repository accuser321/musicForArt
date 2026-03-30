from __future__ import annotations

import json
from typing import Any

from app.services.llm import generate_report, llm_enabled


GENRE_PROFILES: dict[str, dict[str, Any]] = {
    '玄幻': {
        'positive_tags': ['史诗', '苍茫', '灵动', '神秘', '战斗', '推进', '恢弘'],
        'negative_tags': ['日常', '轻松', '甜', '悬疑', '压迫'],
        'target_traits': ['史诗感', '神秘感', '灵动感', '战斗推进'],
        'replace_traits': ['史诗', '神秘', '灵动', '中高速推进'],
    },
    '言情': {
        'positive_tags': ['抒情', '细腻', '温柔', '情感', '柔和', '浪漫'],
        'negative_tags': ['战斗', '压迫', '电子', '黑暗'],
        'target_traits': ['抒情', '细腻', '情绪推进', '柔和层次'],
        'replace_traits': ['抒情', '细腻', '温柔', '中低速情绪推进'],
    },
    '悬疑': {
        'positive_tags': ['悬疑', '压迫', '潜伏', '黑暗', '紧张', '低频'],
        'negative_tags': ['甜', '热血', '恢弘', '明亮'],
        'target_traits': ['压迫感', '潜伏感', '不确定性', '低频张力'],
        'replace_traits': ['压迫', '潜伏', '低频张力', '慢速推进'],
    },
    '科幻': {
        'positive_tags': ['电子', '空间', '冷感', '科技', '未来', '脉冲'],
        'negative_tags': ['古风', '悬疑', '甜', '民谣'],
        'target_traits': ['电子质感', '空间感', '科技感', '冷感推进'],
        'replace_traits': ['电子', '空间感', '冷感', '未来感推进'],
    },
}

EXPECTED_MUSIC_MATCH_PROMPT = 'V3-music_match_task.txt'


def _safe_list(raw: Any) -> list[Any]:
    return raw if isinstance(raw, list) else []


def _tags_text(tags: list[str]) -> str:
    return ' / '.join([str(x).strip() for x in tags if str(x).strip()])


def _infer_music_bias_genres(tags: list[str]) -> list[str]:
    scores: list[tuple[str, int]] = []
    for genre, profile in GENRE_PROFILES.items():
        score = 0
        for tag in tags:
            tag_text = str(tag or '')
            for token in profile['positive_tags']:
                if token in tag_text:
                    score += 2
            for token in profile['negative_tags']:
                if token in tag_text:
                    score -= 1
        scores.append((genre, score))
    scores.sort(key=lambda x: x[1], reverse=True)
    top = [genre for genre, score in scores if score > 0][:2]
    return top or ['未明显偏向单一赛道']


def _genre_match(project_genre: str, tags: list[str], bpm: float) -> dict[str, Any]:
    profile = GENRE_PROFILES.get(project_genre, GENRE_PROFILES['玄幻'])
    positive_hits: list[str] = []
    negative_hits: list[str] = []
    score = 55.0
    for tag in tags:
        tag_text = str(tag or '')
        for token in profile['positive_tags']:
            if token in tag_text and token not in positive_hits:
                positive_hits.append(token)
                score += 8
        for token in profile['negative_tags']:
            if token in tag_text and token not in negative_hits:
                negative_hits.append(token)
                score -= 10
    if project_genre == '言情' and bpm > 135:
        score -= 8
        negative_hits.append('节奏过快')
    if project_genre in {'玄幻', '科幻'} and bpm < 85:
        score -= 6
        negative_hits.append('推进偏慢')
    score = max(0.0, min(100.0, score))
    if score >= 80:
        verdict = '高匹配'
    elif score >= 60:
        verdict = '中等匹配'
    else:
        verdict = '低匹配'
    reasons: list[str] = []
    if positive_hits:
        reasons.append(f'当前音乐已出现与{project_genre}相关的特征：{_tags_text(positive_hits)}。')
    if negative_hits:
        reasons.append(f'当前音乐也带有不利于该赛道的特征：{_tags_text(negative_hits)}。')
    if not reasons:
        reasons.append('当前音乐没有明显命中该赛道的稳定风格特征。')
    return {
        'score': round(score, 1),
        'verdict': verdict,
        'current_genre': project_genre,
        'music_bias_genres': _infer_music_bias_genres(tags),
        'reasons': reasons,
    }


def _text_match(project_genre: str, scenes: list[dict], raw_text: str, tags: list[str]) -> dict[str, Any]:
    scene_count = len(scenes)
    action_count = sum(len(_safe_list(scene.get('actions'))) for scene in scenes if isinstance(scene, dict))
    sfx_count = sum(len(_safe_list(scene.get('sfx'))) for scene in scenes if isinstance(scene, dict))
    text_len = len(raw_text or '')
    score = 60.0
    reasons: list[str] = []

    if action_count >= 8 and any(any(token in tag for token in ['战斗', '推进', '史诗']) for tag in tags):
        score += 15
        reasons.append('文本动作密度较高，当前音乐具备推进或冲突承接能力。')
    if project_genre == '言情' and any(any(token in tag for token in ['战斗', '压迫']) for tag in tags):
        score -= 12
        reasons.append('文本更需要细腻情绪承接，但当前音乐偏冲突或压迫。')
    if project_genre == '科幻' and not any(any(token in tag for token in ['电子', '空间', '科技', '冷感']) for tag in tags):
        score -= 10
        reasons.append('当前音乐缺少科幻常见的电子、空间或科技质感。')
    if sfx_count and any(any(token in tag for token in ['柔和', '温柔']) for tag in tags) and project_genre in {'玄幻', '科幻'}:
        score -= 6
        reasons.append('文本事件较多，但当前音乐能量偏柔，可能撑不住段落转折。')
    if text_len > 1200 and scene_count >= 4:
        score += 5
        reasons.append('文本段落较完整，适合结合音乐结构做分段对位。')
    score = max(0.0, min(100.0, score))
    if score >= 80:
        verdict = '较匹配'
    elif score >= 60:
        verdict = '可用'
    else:
        verdict = '偏弱'
    if not reasons:
        reasons.append('当前音乐与文本整体可对位，但仍建议结合实际朗读节奏复核。')
    return {
        'score': round(score, 1),
        'verdict': verdict,
        'reasons': reasons,
        'scene_count': scene_count,
        'action_count': action_count,
        'sfx_count': sfx_count,
    }


def _narration_match(duration_sec: float, markers: list[dict], narration_timeline: dict | None) -> dict[str, Any]:
    if not narration_timeline:
        return {
            'score': 0.0,
            'verdict': '未提供演绎音频',
            'reasons': ['当前未提供演绎音频，本次未评估朗读节奏与音乐的对位可执行性。'],
            'available': False,
        }
    clause_count = len(_safe_list((narration_timeline or {}).get('clause_timeline')))
    scene_count = len(_safe_list((narration_timeline or {}).get('scene_timeline')))
    score = 62.0
    reasons: list[str] = []
    if markers:
        score += 8
        reasons.append('当前音乐已识别出可用于入点和转折的锚点。')
    if clause_count >= 6:
        score += 10
        reasons.append('演绎语句时间线较完整，适合据此安排进入点与高潮点。')
    if duration_sec and clause_count <= 2:
        score -= 10
        reasons.append('演绎切分较少，细节对位信息不足。')
    score = max(0.0, min(100.0, score))
    if score >= 80:
        verdict = '可直接对位'
    elif score >= 60:
        verdict = '可对位'
    else:
        verdict = '需补充演绎证据'
    return {
        'score': round(score, 1),
        'verdict': verdict,
        'reasons': reasons or ['可结合演绎时间线做基础对位。'],
        'available': True,
        'scene_timeline_count': scene_count,
        'clause_timeline_count': clause_count,
    }


def _editing_advice(markers: list[dict], duration_sec: float, narration_match: dict[str, Any], text_match: dict[str, Any]) -> dict[str, Any]:
    first_marker = markers[0] if markers else {}
    peak_marker = next((m for m in markers if str(m.get('type') or '').lower() == 'peak'), markers[1] if len(markers) > 1 else first_marker)
    valley_marker = next((m for m in reversed(markers) if str(m.get('type') or '').lower() == 'valley'), markers[-1] if markers else {})
    intro_start_sec = round(float(first_marker.get('time_sec') or 0.0), 2) if first_marker else 0.0
    highlight_start_sec = round(float(peak_marker.get('time_sec') or intro_start_sec), 2) if peak_marker else intro_start_sec
    fade_out_sec = round(float(valley_marker.get('time_sec') or (duration_sec * 0.9 if duration_sec else 0.0)), 2)
    advice = [
        '先按“文字-旁白-音乐对位”结果做粗剪，再根据实际人声能量微调进入点。',
        '若当前音乐整体可用，优先保留前奏建立氛围，中段命中高潮，尾段做淡出收口。',
    ]
    if narration_match.get('available'):
        advice.append('旁白密集段建议压低音乐层次，优先让锚点落在情绪转折或动作触发句。')
    if float(text_match.get('score') or 0) < 60:
        advice.append('当前音乐与文本匹配偏弱，若仍要使用，建议只截取最适合的局部乐段，不建议全曲通用。')
    return {
        'intro_start_sec': intro_start_sec,
        'highlight_start_sec': highlight_start_sec,
        'fade_out_sec': fade_out_sec,
        'advice': advice,
    }


def _replace_advice(project_genre: str, total_score: float, genre_match: dict[str, Any]) -> dict[str, Any]:
    need_replace = total_score < 60 or float(genre_match.get('score') or 0) < 55
    profile = GENRE_PROFILES.get(project_genre, GENRE_PROFILES['玄幻'])
    reasons: list[str] = []
    if need_replace:
        reasons.append('当前音乐的赛道气质和文本节奏承接能力不足，继续使用会削弱作品统一感。')
    return {
        'need_replace': need_replace,
        'reasons': reasons,
        'target_music_traits': profile['replace_traits'] if need_replace else [],
    }


def _normalize_llm_music_match_review(llm_json: dict | None, fallback_verdict: str, fallback_summary: str) -> dict | None:
    if not isinstance(llm_json, dict):
        return None
    return {
        'title': str(llm_json.get('title') or 'LLM增强解读'),
        'summary': str(llm_json.get('summary') or fallback_summary),
        'professional_verdict': str(llm_json.get('professional_verdict') or fallback_verdict),
        'professional_reasons': [str(x).strip() for x in (llm_json.get('professional_reasons') or []) if str(x).strip()][:5],
        'editing_focus': [str(x).strip() for x in (llm_json.get('editing_focus') or []) if str(x).strip()][:5],
        'replace_direction': [str(x).strip() for x in (llm_json.get('replace_direction') or []) if str(x).strip()][:4],
        'evidence_focus': [str(x).strip() for x in (llm_json.get('evidence_focus') or []) if str(x).strip()][:4],
        'markdown': str(llm_json.get('markdown') or '').strip(),
    }


def build_music_match_llm_review(
    project_genre: str,
    audio_context: dict[str, Any],
    text_context: dict[str, Any],
    narration_timeline: dict[str, Any] | None,
    rule_result: dict[str, Any],
    llm_provider_override: str = '',
) -> tuple[dict | None, dict]:
    if not llm_enabled(llm_provider_override):
        return None, {
            'llm_enabled': False,
            'llm_trace': [],
            'effective_prompt_file': EXPECTED_MUSIC_MATCH_PROMPT,
        }

    compact_audio = {
        'duration_sec': float(audio_context.get('duration_sec') or 0.0),
        'bpm': float(audio_context.get('bpm') or 0.0),
        'tags': [str(x).strip() for x in (audio_context.get('tags') or []) if str(x).strip()][:10],
        'markers': (audio_context.get('markers') or [])[:8],
    }
    compact_text = {
        'raw_text_excerpt': str(text_context.get('raw_text') or '')[:600],
        'scene_count': len(text_context.get('scenes') or []),
        'scenes': (text_context.get('scenes') or [])[:6],
    }
    compact_narration = None
    if isinstance(narration_timeline, dict):
        compact_narration = {
            'scene_timeline': (narration_timeline.get('scene_timeline') or [])[:6],
            'clause_timeline': (narration_timeline.get('clause_timeline') or [])[:10],
        }
    payload = {
        'kind': 'music_match',
        'project_genre': project_genre,
        'audio_context': compact_audio,
        'text_context': compact_text,
        'narration_timeline': compact_narration,
        'rule_result': {
            'verdict': rule_result.get('verdict'),
            'score': rule_result.get('score'),
            'summary': rule_result.get('summary'),
            'genre_match': rule_result.get('genre_match'),
            'text_match': rule_result.get('text_match'),
            'narration_match': rule_result.get('narration_match'),
            'editing_advice': rule_result.get('editing_advice'),
            'replace_advice': rule_result.get('replace_advice'),
            'key_points': rule_result.get('key_points'),
            'risks': rule_result.get('risks'),
        },
        'llm_provider_override': llm_provider_override,
    }
    llm_json, _llm_md, llm_meta = generate_report('analysis', payload, debug_prompt=True)
    normalized = _normalize_llm_music_match_review(
        llm_json,
        fallback_verdict=str(rule_result.get('verdict') or ''),
        fallback_summary=str(rule_result.get('summary') or ''),
    )
    return normalized, llm_meta or {}


def build_music_match_result(
    project_genre: str,
    audio_context: dict[str, Any],
    text_context: dict[str, Any],
    narration_timeline: dict[str, Any] | None = None,
    llm_provider_override: str = '',
) -> dict[str, Any]:
    tags = [str(x).strip() for x in _safe_list(audio_context.get('tags')) if str(x).strip()]
    markers = _safe_list(audio_context.get('markers'))
    bpm = float(audio_context.get('bpm') or 0.0)
    duration_sec = float(audio_context.get('duration_sec') or 0.0)
    scenes = _safe_list(text_context.get('scenes'))
    raw_text = str(text_context.get('raw_text') or '')

    genre_match = _genre_match(project_genre, tags, bpm)
    text_match = _text_match(project_genre, scenes, raw_text, tags)
    narration_match = _narration_match(duration_sec, markers, narration_timeline)

    total_score = round(
        float(genre_match['score']) * 0.35
        + float(text_match['score']) * 0.4
        + (float(narration_match['score']) * 0.25 if narration_match.get('available') else 15.0),
        1,
    )
    if total_score >= 80:
        verdict = '适合使用'
        summary = '当前音乐与赛道、文本和演绎节奏整体匹配，可直接进入剪辑与对位阶段。'
    elif total_score >= 60:
        verdict = '可用但建议剪辑调整'
        summary = '当前音乐基本可用，但建议根据文本转折和旁白时间线做局部剪辑与进入点调整。'
    else:
        verdict = '不建议使用'
        summary = '当前音乐与作品风格存在明显偏差，建议更换更符合赛道和文本气质的音乐。'

    editing_advice = _editing_advice(markers, duration_sec, narration_match, text_match)
    replace_advice = _replace_advice(project_genre, total_score, genre_match)

    key_points = [
        f'赛道匹配：{genre_match["verdict"]}（{genre_match["score"]}分）',
        f'文本匹配：{text_match["verdict"]}（{text_match["score"]}分）',
    ]
    if narration_match.get('available'):
        key_points.append(f'演绎对位：{narration_match["verdict"]}（{narration_match["score"]}分）')
    if replace_advice.get('need_replace'):
        risks = ['当前音乐不建议作为本项目主音乐使用，除非只截取少量局部乐段。']
    else:
        risks = ['若实际录音语速变化较大，仍需根据成片节奏微调进入点和淡出点。']

    result = {
        'title': '音乐是否适合作品',
        'verdict': verdict,
        'score': total_score,
        'summary': summary,
        'genre_match': genre_match,
        'text_match': text_match,
        'narration_match': narration_match,
        'editing_advice': editing_advice,
        'replace_advice': replace_advice,
        'key_points': key_points,
        'risks': risks,
        'report_json': {
            'title': '音乐匹配结果',
            'summary': summary,
            'verdict': verdict,
            'score': total_score,
            'genre_match': genre_match,
            'text_match': text_match,
            'narration_match': narration_match,
            'editing_advice': editing_advice,
            'replace_advice': replace_advice,
            'key_points': key_points,
            'risks': risks,
        },
    }
    llm_review, llm_meta = build_music_match_llm_review(
        project_genre=project_genre,
        audio_context=audio_context,
        text_context=text_context,
        narration_timeline=narration_timeline,
        rule_result=result,
        llm_provider_override=llm_provider_override,
    )
    result['llm_review'] = llm_review
    result['llm_trace'] = llm_meta.get('llm_trace') or []
    result['llm_enabled'] = bool(llm_meta.get('llm_enabled', True)) if llm_meta else llm_enabled(llm_provider_override)
    result['report_json']['llm_review'] = llm_review or {}
    return result
