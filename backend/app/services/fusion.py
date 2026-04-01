from app.config import settings
from app.services.llm import generate_report, llm_enabled


def _round_sec(value) -> float:
    try:
        return round(float(value or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


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


def _assess_fit(
    audio_markers: list[dict],
    scenes: list[dict],
    narration_timeline: dict | list | None = None,
    music_context: dict | None = None,
) -> dict:
    markers = [m for m in (audio_markers or []) if isinstance(m, dict)]
    peaks = sum(1 for m in markers if str(m.get('type') or '').strip().lower() == 'peak')
    valleys = sum(1 for m in markers if str(m.get('type') or '').strip().lower() == 'valley')
    builds = sum(1 for m in markers if str(m.get('type') or '').strip().lower() == 'build')
    turns = sum(1 for m in markers if str(m.get('type') or '').strip().lower() == 'turn')
    scene_rows = [s for s in (scenes or []) if isinstance(s, dict)]
    scene_count = len(scene_rows)
    intensities = [int(s.get('intensity', 0) or 0) for s in scene_rows]
    high_intensity = sum(1 for value in intensities if value >= 60)
    low_intensity = sum(1 for value in intensities if value < 40)
    mid_intensity = max(0, scene_count - high_intensity - low_intensity)
    duration_sec = None
    if isinstance(music_context, dict):
        try:
            duration_sec = float(music_context.get('duration_sec')) if music_context.get('duration_sec') is not None else None
        except (TypeError, ValueError):
            duration_sec = None
    scene_timeline = []
    clause_timeline = []
    if isinstance(narration_timeline, dict):
        scene_timeline = [x for x in (narration_timeline.get('scene_timeline') or []) if isinstance(x, dict)]
        clause_timeline = [x for x in (narration_timeline.get('clause_timeline') or []) if isinstance(x, dict)]
    elif isinstance(narration_timeline, list):
        scene_timeline = [x for x in narration_timeline if isinstance(x, dict)]
    total_marker_budget = max(1, peaks + valleys + builds + turns)
    dynamic_alignment = min(scene_count, total_marker_budget)
    score = 52
    score += min(12, peaks * 4)
    score += min(8, valleys * 3)
    score += min(6, builds * 2)
    score += min(6, turns * 2)
    score += min(8, dynamic_alignment)
    if scene_timeline:
        score += 6
    if clause_timeline:
        score += 4
    if high_intensity > peaks + 1:
        score -= min(16, (high_intensity - peaks - 1) * 4)
    if low_intensity > valleys + 2:
        score -= min(12, (low_intensity - valleys - 2) * 3)
    if scene_count >= 10 and total_marker_budget <= 2:
        score -= 10

    score = max(0, min(100, score))
    if score >= 80:
        verdict = '适配'
        conclusion = '音乐结构起伏与文本推进基本同向，可直接进入精修阶段。'
    elif score >= 60:
        verdict = '部分适配'
        conclusion = '音乐可用，但高能段与旁白窗口仍需人工精修切分。'
    else:
        verdict = '不建议使用'
        conclusion = '音乐结构支点不足，难以稳定承接当前文本的节奏变化。'

    reasons = [conclusion]
    if duration_sec:
        reasons.append(f'音乐时长约 {_round_sec(duration_sec)} 秒，识别到峰值 {peaks} 个、回落 {valleys} 个、转段 {builds + turns} 个。')
    else:
        reasons.append(f'音乐侧识别到峰值 {peaks} 个、回落 {valleys} 个、转段 {builds + turns} 个。')
    reasons.append(f'文本侧共有 {scene_count} 个场景，其中高强度 {high_intensity} 个、中强度 {mid_intensity} 个、低强度 {low_intensity} 个。')
    if scene_timeline or clause_timeline:
        reasons.append(
            f'演绎证据已提供场景时间线 {len(scene_timeline)} 条、语句时间线 {len(clause_timeline)} 条，可支持对白窗口与入点微调。'
        )
    if verdict == '部分适配':
        reasons.append('建议优先把峰值段留给动作或情绪推进，把回落段留给对白与旁白换气。')
    elif verdict == '不建议使用':
        reasons.append('若坚持使用，建议只截取局部片段，不建议整曲贯穿。')

    return {
        'fit_score': score,
        'verdict': verdict,
        'reasons': reasons[:5],
    }


def _build_rule_report(cues: list[dict]) -> str:
    lines = ['# 音乐-文本融合执行单（规则版）', '', '## Cue Sheet']
    for cue in cues:
        lines.append(
            f"- Scene {cue['scene_no']} -> {cue['target_time_sec']:.2f}s ({cue['marker_label']})"
        )
    return '\n'.join(lines)


def _marker_section_type(marker_type: str, index: int, total: int) -> tuple[str, str, str]:
    mtype = str(marker_type or '').strip().lower()
    if mtype == 'build':
        return '起势段', '起势铺垫', '中'
    if mtype == 'peak':
        if index >= max(0, total - 2):
            return '高潮段', '高潮推进', '高'
        return '推进段', '主体推进', '高'
    if mtype == 'valley':
        return '回落段', '抽空回落', '低'
    return '转段', '结构转段', '中'


def _build_rule_structure_logic(audio_markers: list[dict], duration_sec: float | None) -> dict:
    markers = [m for m in (audio_markers or []) if isinstance(m, dict) and m.get('time_sec') is not None]
    if not markers:
        return {'pattern_guess': '', 'repeat_groups': [], 'evidence': []}
    type_seq = [str(m.get('type') or '').strip().lower() for m in markers]
    label_seq = [str(m.get('label') or '').strip() for m in markers if str(m.get('label') or '').strip()]
    peaks = sum(1 for t in type_seq if t == 'peak')
    valleys = sum(1 for t in type_seq if t == 'valley')
    builds = sum(1 for t in type_seq if t == 'build')
    if peaks >= 2 and valleys >= 1:
        pattern_guess = '起势后多次推进，并带回落缓冲'
    elif peaks >= 1 and builds >= 1:
        pattern_guess = '起势后进入单次主推进'
    elif valleys >= 1:
        pattern_guess = '推进中夹有抽空回落'
    else:
        pattern_guess = '按锚点顺序逐段推进'

    label_counts = {}
    for label in label_seq:
        label_counts[label] = label_counts.get(label, 0) + 1
    repeat_groups = [f'{label} ×{count}' for label, count in label_counts.items() if count >= 2][:4]
    evidence = [
        f'共识别 {len(markers)} 个音乐锚点，类型序列为 {" / ".join([t or "未知" for t in type_seq[:6]])}',
    ]
    if peaks:
        evidence.append(f'峰值锚点 {peaks} 个，可作为剧情推进或动作命中点')
    if valleys:
        evidence.append(f'回落锚点 {valleys} 个，可作为旁白换气或对白窗口')
    if duration_sec and len(markers) >= 2:
        evidence.append(f'音乐时长约 {_round_sec(duration_sec)} 秒，锚点间距支持按段落做分段进入')
    return {
        'pattern_guess': pattern_guess,
        'repeat_groups': repeat_groups,
        'evidence': evidence[:4],
    }


def _build_rule_sections(audio_markers: list[dict], duration_sec: float | None) -> list[dict]:
    markers = [m for m in (audio_markers or []) if isinstance(m, dict) and m.get('time_sec') is not None]
    if not markers:
        return []
    sections = []
    for idx, marker in enumerate(markers):
        start_sec = _round_sec(marker.get('time_sec'))
        next_sec = _round_sec(markers[idx + 1].get('time_sec')) if idx + 1 < len(markers) else _round_sec(duration_sec or (start_sec + 8.0))
        if next_sec <= start_sec:
            next_sec = _round_sec(start_sec + 4.0)
        section_type, label, energy = _marker_section_type(marker.get('type'), idx, len(markers))
        sections.append(
            {
                'section_no': idx + 1,
                'section_type': section_type,
                'label': str(marker.get('label') or label).strip() or label,
                'start_sec': start_sec,
                'end_sec': next_sec,
                'instruments': [],
                'new_instruments_vs_prev': [],
                'energy_level': energy,
                'layer_progression': f'依据 {str(marker.get("type") or "锚点").strip() or "结构锚点"} 进入{label}阶段',
            }
        )
    return sections[:8]


def _build_rule_key_points(audio_markers: list[dict], scenes: list[dict], narration_timeline: dict | list | None) -> list[str]:
    markers = [m for m in (audio_markers or []) if isinstance(m, dict)]
    scene_count = len(scenes or [])
    scene_timeline = []
    clause_timeline = []
    if isinstance(narration_timeline, dict):
        scene_timeline = narration_timeline.get('scene_timeline') or []
        clause_timeline = narration_timeline.get('clause_timeline') or []
    elif isinstance(narration_timeline, list):
        scene_timeline = narration_timeline
    points = []
    if markers:
        points.append(f'音乐侧已提供 {len(markers)} 个锚点，可支撑执行单分段进入')
    if scene_count:
        points.append(f'文本侧共 {scene_count} 个场景单元，已可对齐音乐入点')
    if scene_timeline:
        points.append(f'演绎场景时间线 {len(scene_timeline)} 条，优先用于秒级落点')
    if clause_timeline:
        points.append(f'演绎语句时间线 {len(clause_timeline)} 条，可辅助对白窗口控制')
    return points[:6]


def _build_rule_hit_points(audio_markers: list[dict]) -> list[dict]:
    out = []
    for marker in (audio_markers or [])[:10]:
        if not isinstance(marker, dict):
            continue
        out.append(
            {
                'time_sec': _round_sec(marker.get('time_sec')),
                'event': str(marker.get('label') or marker.get('type') or '结构锚点').strip(),
                'why': (
                    '峰值适合动作推进'
                    if str(marker.get('type') or '').strip().lower() == 'peak'
                    else '回落适合对白或旁白换气'
                    if str(marker.get('type') or '').strip().lower() == 'valley'
                    else '可作为段落进入或转场提示'
                ),
            }
        )
    return out


def _build_rule_risks(cues: list[dict], audio_markers: list[dict], fit: dict) -> list[str]:
    risks = []
    peaks = sum(1 for m in (audio_markers or []) if str((m or {}).get('type') or '').strip().lower() == 'peak')
    if peaks >= 3:
        risks.append('音乐峰值较多，连续高能段使用时要避免压住对白')
    long_segments = [
        cue for cue in (cues or [])
        if _round_sec(cue.get('music_segment_end_sec')) - _round_sec(cue.get('music_segment_start_sec')) >= 20
    ]
    if long_segments:
        risks.append('部分场景对应音乐段较长，建议结合台词做二次切片')
    if str((fit or {}).get('verdict') or '') == '部分适配':
        risks.append('当前音乐与文本只属部分适配，关键转场需人工复核')
    return risks[:6]


def _build_rule_export_hints(cues: list[dict], narration_timeline: dict | list | None) -> list[str]:
    hints = []
    if cues:
        hints.append('先按 cue 表粗切音乐，再结合对白做细修')
    if isinstance(narration_timeline, dict) and (narration_timeline.get('clause_timeline') or []):
        hints.append('对白密集段优先参考语句时间线做压混')
    if any(str((cue or {}).get('marker_label') or '').strip() for cue in (cues or [])):
        hints.append('动作密集段优先对齐峰值锚点，回落段留给旁白换气')
    return hints[:6]


def _build_rule_report_json(
    cues: list[dict],
    fit: dict,
    audio_markers: list[dict],
    scenes: list[dict],
    narration_timeline: dict | list | None = None,
    music_context: dict | None = None,
) -> dict:
    duration_sec = None
    if isinstance(music_context, dict):
        try:
            duration_sec = float(music_context.get('duration_sec')) if music_context.get('duration_sec') is not None else None
        except (TypeError, ValueError):
            duration_sec = None
    return {
        'title': '后期执行单',
        'fit_verdict': {
            'verdict': str((fit or {}).get('verdict') or '未判定'),
            'score': int((fit or {}).get('fit_score') or 0),
            'reasons': list((fit or {}).get('reasons') or [])[:6],
        },
        'key_points': _build_rule_key_points(audio_markers, scenes, narration_timeline),
        'sections': _build_rule_sections(audio_markers, duration_sec),
        'structure_logic': _build_rule_structure_logic(audio_markers, duration_sec),
        'music_entry_plan': [
            {
                'scene_no': cue.get('scene_no'),
                'text_start_char': cue.get('text_start_char'),
                'text_end_char': cue.get('text_end_char'),
                'text_excerpt': cue.get('text_excerpt'),
                'music_start_sec': cue.get('music_segment_start_sec'),
                'music_end_sec': cue.get('music_segment_end_sec'),
                'entry_reason': cue.get('entry_reason') or f"当前场景对齐 {cue.get('marker_label') or '结构锚点'} 进入",
                'dialogue_music_ratio': cue.get('dialogue_music_ratio'),
                'sfx': list(cue.get('recommended_sfx') or []),
            }
            for cue in (cues or [])[:12]
        ],
        'hit_points': _build_rule_hit_points(audio_markers),
        'risks': _build_rule_risks(cues, audio_markers, fit),
        'export_hints': _build_rule_export_hints(cues, narration_timeline),
    }


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
    fit = _assess_fit(
        audio_markers,
        scenes,
        narration_timeline=narration_timeline,
        music_context=music_context,
    )

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
    else:
        report_json = _build_rule_report_json(
            cues,
            fit,
            audio_markers,
            scenes,
            narration_timeline=narration_timeline,
            music_context=music_context,
        )

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
