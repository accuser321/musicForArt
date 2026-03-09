from app.config import settings
from app.services.llm import generate_report, llm_enabled


def _assess_fit(audio_markers: list[dict], scenes: list[dict]) -> dict:
    peaks = sum(1 for m in audio_markers if str(m.get('type')) == 'peak')
    valleys = sum(1 for m in audio_markers if str(m.get('type')) == 'valley')
    high_intensity = sum(1 for s in scenes if int(s.get('intensity', 0)) >= 60)
    low_intensity = sum(1 for s in scenes if int(s.get('intensity', 0)) < 40)

    score = 50
    score += min(20, peaks * 5)
    score += min(10, valleys * 3)
    score += min(10, high_intensity * 3)
    if high_intensity > peaks + 1:
        score -= 12
    if low_intensity > valleys + 2:
        score -= 8

    score = max(0, min(100, score))
    if score >= 78:
        verdict = '适配'
    elif score >= 58:
        verdict = '部分适配'
    else:
        verdict = '不建议使用'

    return {
        'fit_score': score,
        'verdict': verdict,
        'reasons': [
            f'音乐峰值段数量：{peaks}',
            f'音乐回落段数量：{valleys}',
            f'文本高强度场景数量：{high_intensity}',
            f'文本低强度场景数量：{low_intensity}',
        ],
    }


def _build_rule_report(cues: list[dict]) -> str:
    lines = ['# 音乐-文本融合执行单（规则版）', '', '## Cue Sheet']
    for cue in cues:
        lines.append(
            f"- Scene {cue['scene_no']} -> {cue['target_time_sec']:.2f}s ({cue['marker_label']}) | 比例 {cue['dialogue_music_ratio']} | 音效 {', '.join(cue['recommended_sfx']) or '无'}"
        )
    return '\n'.join(lines)


def build_fusion_plan(audio_markers: list[dict], scenes: list[dict], report_mode: str | None = None) -> dict:
    mode = report_mode or settings.report_mode_default
    if not audio_markers or not scenes:
        return {
            'cues': [],
            'report_markdown': '# 融合建议\n\n- 缺少音频或文本分析数据，暂无法生成。',
            'report_json': None,
            'analysis_mode': 'rules-only',
            'llm_structured': False,
            'llm_enabled': llm_enabled(),
            'report_mode': mode,
            'effective_report_mode': None,
            'llm_fallback_applied': False,
            'llm_attempted_modes': [],
        }

    cues = []
    marker_len = len(audio_markers)
    fit = _assess_fit(audio_markers, scenes)

    for i, scene in enumerate(scenes):
        marker_idx = min(i, marker_len - 1)
        marker = audio_markers[marker_idx]
        next_marker = audio_markers[min(marker_idx + 1, marker_len - 1)]
        seg_start = float(marker['time_sec'])
        seg_end = float(next_marker['time_sec']) if marker_idx + 1 < marker_len else seg_start + 10.0
        cues.append(
            {
                'scene_no': scene['scene_no'],
                'target_time_sec': marker['time_sec'],
                'music_segment_start_sec': round(seg_start, 2),
                'music_segment_end_sec': round(max(seg_end, seg_start + 2.0), 2),
                'marker_label': marker['label'],
                'recommended_sfx': scene['sfx'],
                'dialogue_music_ratio': '70/30' if scene['intensity'] < 50 else '45/55',
                'mix_tip': '对白优先并侧链压低BGM' if scene['intensity'] < 50 else '动作优先并抬高低频冲击',
                'text_start_char': scene.get('char_start'),
                'text_end_char': scene.get('char_end'),
                'text_excerpt': scene.get('text', ''),
            }
        )

    report_json, report_markdown, llm_meta = generate_report(
        mode,
        {
            'kind': 'fusion',
            'mode': mode,
            'audio_markers': audio_markers,
            'scenes': scenes,
            'cues': cues,
            'fit': fit,
        },
    )

    llm_hit = bool(llm_meta.get('effective_mode')) and bool(report_markdown)
    return {
        'cues': cues,
        'fit': fit,
        'report_markdown': report_markdown if report_markdown else _build_rule_report(cues),
        'report_json': report_json,
        'analysis_mode': 'llm+rules' if llm_hit else 'rules-only',
        'llm_structured': bool(report_json),
        'llm_enabled': llm_enabled(),
        'report_mode': mode,
        'effective_report_mode': llm_meta.get('effective_mode'),
        'llm_fallback_applied': llm_meta.get('fallback_applied', False),
        'llm_attempted_modes': llm_meta.get('attempted_modes', []),
    }
