from app.config import settings
from app.services.llm import generate_report, llm_enabled


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

    for i, scene in enumerate(scenes):
        marker = audio_markers[min(i, marker_len - 1)]
        cues.append(
            {
                'scene_no': scene['scene_no'],
                'target_time_sec': marker['time_sec'],
                'marker_label': marker['label'],
                'recommended_sfx': scene['sfx'],
                'dialogue_music_ratio': '70/30' if scene['intensity'] < 50 else '45/55',
                'mix_tip': '对白优先并侧链压低BGM' if scene['intensity'] < 50 else '动作优先并抬高低频冲击',
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
        },
    )

    llm_hit = bool(llm_meta.get('effective_mode')) and bool(report_markdown)
    return {
        'cues': cues,
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
