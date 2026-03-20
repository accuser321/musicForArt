from app.config import settings
from app.services.llm import generate_report, llm_enabled


def _estimate_narration_range(scene: dict, scene_map: dict[int, dict], clause_timeline: list[dict]) -> dict | None:
    scene_no = int(scene.get('scene_no') or 0)
    if scene_no and scene_no in scene_map:
        row = scene_map[scene_no]
        if row.get('start_sec') is not None and row.get('end_sec') is not None:
            return {'start_sec': float(row['start_sec']), 'end_sec': float(row['end_sec']), 'source': 'scene_timeline'}

    s0 = int(scene.get('char_start') or 0)
    s1 = int(scene.get('char_end') or s0)
    overlaps = []
    for c in clause_timeline:
        c0 = int(c.get('text_start_char') or 0)
        c1 = int(c.get('text_end_char') or c0)
        ov = min(s1, c1) - max(s0, c0)
        if ov > 0:
            overlaps.append((float(c.get('start_sec') or 0.0), float(c.get('end_sec') or 0.0), ov))
    if overlaps:
        return {'start_sec': min(x[0] for x in overlaps), 'end_sec': max(x[1] for x in overlaps), 'source': 'clause_overlap'}
    return None


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
            f"- Scene {cue['scene_no']} -> {cue['target_time_sec']:.2f}s ({cue['marker_label']})"
        )
    return '\n'.join(lines)


def build_fusion_plan(
    audio_markers: list[dict],
    scenes: list[dict],
    narration_timeline: dict | list | None = None,
    music_context: dict | None = None,
    text_context: dict | None = None,
    report_mode: str | None = None,
    debug_prompt: bool = True,
    llm_provider_override: str = '',
) -> dict:
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

    scene_timeline: list[dict] = []
    clause_timeline: list[dict] = []
    if isinstance(narration_timeline, dict):
        scene_timeline = narration_timeline.get('scene_timeline') or []
        clause_timeline = narration_timeline.get('clause_timeline') or []
    elif isinstance(narration_timeline, list):
        scene_timeline = narration_timeline
    narration_map = {int(x.get('scene_no')): x for x in scene_timeline if isinstance(x, dict) and x.get('scene_no')}

    for i, scene in enumerate(scenes):
        marker_idx = min(i, marker_len - 1)
        marker = audio_markers[marker_idx]
        next_marker = audio_markers[min(marker_idx + 1, marker_len - 1)]
        seg_start = float(marker['time_sec'])
        narr = _estimate_narration_range(scene, narration_map, clause_timeline)
        narr_len = None
        if narr and narr.get('start_sec') is not None and narr.get('end_sec') is not None:
            narr_len = max(1.5, float(narr['end_sec']) - float(narr['start_sec']))
        seg_end = float(next_marker['time_sec']) if marker_idx + 1 < marker_len else seg_start + (narr_len or 10.0)
        if narr_len is not None:
            seg_end = min(seg_end, seg_start + narr_len + 0.8)
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
                'narration_start_sec': narr.get('start_sec') if narr else None,
                'narration_end_sec': narr.get('end_sec') if narr else None,
                'narration_source': narr.get('source') if narr else None,
            }
        )

    report_json, report_markdown, llm_meta = generate_report(
        mode,
        {
            'kind': 'fusion',
            # 三类核心证据：音乐分析、文本分析、演绎时间轴
            'music_analysis': music_context or {},
            'text_analysis': text_context or {},
            'audio_markers': audio_markers,
            'scenes': scenes,
            'narration_timeline': {'scene_timeline': scene_timeline, 'clause_timeline': clause_timeline},
            'cues': cues,
            'fit': fit,
            'llm_provider_override': llm_provider_override,
        },
        debug_prompt=debug_prompt,
    )

    llm_hit = bool(llm_meta.get('effective_mode')) and bool(report_markdown)
    if isinstance(report_json, dict):
        fv = report_json.get('fit_verdict')
        if isinstance(fv, dict):
            fit = {
                'fit_score': int(fv.get('score', fit.get('fit_score', 0)) or 0),
                'verdict': str(fv.get('verdict') or fit.get('verdict') or '未判定'),
                'reasons': fv.get('reasons') if isinstance(fv.get('reasons'), list) else fit.get('reasons', []),
            }
        plan = report_json.get('music_entry_plan')
        if isinstance(plan, list) and plan:
            # If LLM returns a detailed entry plan, expose it for frontend rendering.
            for idx, row in enumerate(plan):
                if idx >= len(cues) or not isinstance(row, dict):
                    continue
                cues[idx]['music_segment_start_sec'] = row.get('music_start_sec', cues[idx]['music_segment_start_sec'])
                cues[idx]['music_segment_end_sec'] = row.get('music_end_sec', cues[idx]['music_segment_end_sec'])
                cues[idx]['dialogue_music_ratio'] = row.get('dialogue_music_ratio', cues[idx]['dialogue_music_ratio'])
                if isinstance(row.get('sfx'), list) and row.get('sfx'):
                    cues[idx]['recommended_sfx'] = row['sfx']
                if row.get('entry_reason'):
                    cues[idx]['entry_reason'] = row['entry_reason']

    return {
        'cues': cues,
        'fit': fit,
        'report_markdown': report_markdown if report_markdown else _build_rule_report(cues),
        'report_json': report_json,
        'analysis_mode': 'llm+rules' if llm_hit else 'rules-only',
        'llm_structured': bool(report_json),
        'llm_enabled': llm_enabled(llm_provider_override),
        'llm_provider_used': llm_meta.get('llm_provider_used'),
        'llm_model_used': llm_meta.get('llm_model_used'),
        'report_mode': mode,
        'effective_report_mode': llm_meta.get('effective_mode'),
        'llm_fallback_applied': llm_meta.get('fallback_applied', False),
        'llm_attempted_modes': llm_meta.get('attempted_modes', []),
        'llm_trace': llm_meta.get('llm_trace'),
    }
