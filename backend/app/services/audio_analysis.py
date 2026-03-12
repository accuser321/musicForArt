import json
import sys
from pathlib import Path

from mutagen import File as MutagenFile

from app.config import settings
from app.services.llm import generate_report, llm_enabled

EXPECTED_AUDIO_PROMPT = 'V3-music_analysis_task.txt'


def _duration_by_mutagen(file_path: str) -> float:
    try:
        audio = MutagenFile(file_path)
        if audio is None or audio.info is None:
            return 0.0
        return float(getattr(audio.info, 'length', 0.0) or 0.0)
    except Exception as e:
        print(f'[AUDIO WARN] duration parse failed: {type(e).__name__}: {e}', file=sys.stderr)
        return 0.0


def _fallback_markers(duration_sec: float) -> list[dict]:
    return [
        {'label': '起势段', 'time_sec': round(duration_sec * 0.05, 2), 'type': 'build'},
        {'label': '高潮一', 'time_sec': round(duration_sec * 0.28, 2), 'type': 'peak'},
        {'label': '回落一', 'time_sec': round(duration_sec * 0.38, 2), 'type': 'valley'},
        {'label': '高潮二', 'time_sec': round(duration_sec * 0.62, 2), 'type': 'peak'},
        {'label': '回落二', 'time_sec': round(duration_sec * 0.72, 2), 'type': 'valley'},
        {'label': '终局高潮', 'time_sec': round(duration_sec * 0.82, 2), 'type': 'peak'},
    ]


def _build_rule_report(features: dict) -> str:
    duration_sec = features['duration_sec']
    bpm = features['bpm']
    tags = features['tags']
    markers = features['markers']

    lines = [
        '# 音乐分析报告（规则版）',
        '',
        '## 1) 基本信息',
        f'- 时长: {duration_sec:.2f}s',
        f'- 估计 BPM: {bpm:.1f}',
        f"- 标签: {', '.join(tags)}",
        '',
        '## 2) 入门理解',
        '- 这首音乐以节奏推进为主，适合冲突、战斗、集体行动类段落。',
        '- 证据：存在明确 Peak/Drop 段，可用于冲锋与回撤切换。',
        '',
        '## 3) 中级分析',
        '- 主要依赖短动机和节奏重复推进，适合做段落化剪辑。',
        '- 证据：锚点分布相对均匀，适合模块化切片。',
        '',
        '## 4) 高级建议',
        '- 建议把 Peak 作为动作命中点，Drop 作为旁白窗口。',
        '- 证据：时间锚点中 peak/valley 已分离。',
        '',
        '## 5) 结构时间轴',
    ]

    for marker in markers:
        lines.append(f"- {marker['label']} @ {marker['time_sec']:.2f}s ({marker['type']})")

    lines.extend(
        [
            '',
            '## 6) 有声书制作建议',
            '- 战斗描写：对白/BGM 可设 45/55，动作句对齐 Peak。',
            '- 叙述与旁白：对白/BGM 可设 70/30，放在 Drop 或 Intro。',
        ]
    )
    return '\n'.join(lines)


def _clip_list(items, n: int) -> list:
    if not isinstance(items, list):
        return []
    return items[: max(0, n)]


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _build_fallback_audio_json(features: dict, report_markdown: str) -> dict:
    duration = float(features.get('duration_sec') or 0.0)
    tags = list(features.get('tags') or [])
    markers = list(features.get('markers') or [])
    fit_genres = []
    if any('战' in t for t in tags):
        fit_genres.extend(['武侠战斗', '玄幻战场', '军事冲突'])
    if not fit_genres:
        fit_genres = ['剧情推进', '紧张冲突']

    hit_points = [
        {
            'time_sec': _to_float(m.get('time_sec'), 0.0),
            'type': str(m.get('type') or '转折'),
            'usage': str(m.get('label') or '可用于动作或台词对齐'),
        }
        for m in markers
    ][:6]

    split = [0.0]
    for h in hit_points[:3]:
        t = _to_float(h.get('time_sec'), 0.0)
        if 0 < t < duration:
            split.append(t)
    if duration > 0:
        split.append(duration)
    split = sorted(set(split))
    sections = []
    for i in range(len(split) - 1):
        sections.append(
            {
                'section_no': i + 1,
                'label': f'乐段{i + 1}',
                'start_sec': round(split[i], 2),
                'end_sec': round(split[i + 1], 2),
                'energy_level': '中',
                'main_layers': ['低频节奏层', '中频纹理层'],
                'instrument_guess': ['战鼓', '短弦'],
                'entry_suggestion': '渐入',
                'exit_suggestion': '淡出',
            }
        )

    summary = '节奏推进明显，适合冲突与战斗场景。'
    if isinstance(report_markdown, str) and report_markdown.strip():
        one_line = report_markdown.strip().splitlines()
        if one_line:
            summary = one_line[0].replace('#', '').strip()[:60] or summary

    return {
        'title': '音乐分析结果',
        'summary': summary,
        'fit_genres': _clip_list(fit_genres, 5),
        'risk_genres': ['轻松日常', '温柔抒情'],
        'sections': _clip_list(sections, 6),
        'structure_logic': {
            'pattern_guess': '分段推进',
            'repeat_groups': ['按锚点分段'],
            'progression_comment': '整体由低到高推进，峰值点适合命中动作。',
        },
        'hit_points': _clip_list(hit_points, 6),
        'mix_notes': _clip_list(
            [
                '对白段压低低频，避免遮蔽人声。',
                '命中点前后保留动态，突出动作打点。',
                '转场段使用短淡入淡出，避免硬切突兀。',
            ],
            6,
        ),
        'key_points': _clip_list(
            [
                '先确定命中点，再对齐动作句。',
                '乐段进入优先渐入，退出优先淡出。',
                '避免全程高能，保留起伏与对比。',
            ],
            6,
        ),
        'markdown': report_markdown or '',
    }


def _normalize_audio_report_json(report_json: dict | None, features: dict, report_markdown: str) -> dict:
    base = _build_fallback_audio_json(features, report_markdown)
    if not isinstance(report_json, dict):
        return base

    merged = dict(base)
    merged.update({k: v for k, v in report_json.items() if v is not None})

    # 保留 LLM 的完整输出，不在此处裁剪；仅在缺失或类型错误时回退到基础值。
    if not isinstance(merged.get('fit_genres'), list):
        merged['fit_genres'] = base['fit_genres']
    if not isinstance(merged.get('risk_genres'), list):
        merged['risk_genres'] = base['risk_genres']
    if not isinstance(merged.get('sections'), list):
        merged['sections'] = base['sections']
    if not isinstance(merged.get('hit_points'), list):
        merged['hit_points'] = base['hit_points']
    if not isinstance(merged.get('mix_notes'), list):
        merged['mix_notes'] = base['mix_notes']
    if not isinstance(merged.get('key_points'), list):
        merged['key_points'] = base['key_points']
    if not isinstance(merged.get('structure_logic'), dict):
        merged['structure_logic'] = base['structure_logic']
    return merged


def analyze_audio_for_audiobook(
    file_path: str,
    report_mode: str | None = None,
    debug_prompt: bool = True,
) -> dict:
    file_path = str(Path(file_path).resolve())
    mode = report_mode or settings.report_mode_default

    duration_sec = _duration_by_mutagen(file_path)
    if duration_sec <= 0:
        duration_sec = 180.0

    features = {
        'duration_sec': duration_sec,
        'estimated_bpm': 120.0,
        'tags': ['电影感', '史诗感', '战斗推进'],
        'markers': _fallback_markers(duration_sec),
        'feature_evidence': {
            'engine': 'rule-based-timeline-v1',
            'confidence': 'medium',
            'note': '当前环境默认使用稳定规则特征，接入LLM后生成导演级解释。',
        },
    }

    payload = {
        'kind': 'audio',
        'audio_features': features,
    }
    report_json, report_markdown, llm_meta = generate_report(mode, payload, debug_prompt=debug_prompt)
    trace = llm_meta.get('llm_trace') or []
    actual_prompt = None
    if isinstance(trace, list) and trace:
        actual_prompt = trace[0].get('prompt_file')
    prompt_guard_passed = actual_prompt == EXPECTED_AUDIO_PROMPT if actual_prompt else False

    if not report_markdown:
        report_markdown = _build_rule_report(
            {
                'duration_sec': features['duration_sec'],
                'bpm': features['estimated_bpm'],
                'tags': features['tags'],
                'markers': features['markers'],
            }
        )
    report_json = _normalize_audio_report_json(report_json, features, report_markdown)

    llm_hit = bool(llm_meta.get('effective_mode')) and bool(report_markdown)
    result = {
        'duration_sec': features['duration_sec'],
        'bpm': features['estimated_bpm'],
        'tags': features['tags'],
        'markers': features['markers'],
        'report_markdown': report_markdown,
        'report_json': report_json,
        'analysis_mode': 'llm+features' if llm_hit else 'rules-only',
        'llm_structured': bool(report_json),
        'llm_enabled': llm_enabled(),
        'report_mode': mode,
        'effective_report_mode': llm_meta.get('effective_mode'),
        'llm_fallback_applied': llm_meta.get('fallback_applied', False),
        'llm_attempted_modes': llm_meta.get('attempted_modes', []),
        'custom_prompt_chain': llm_meta.get('custom_prompt_chain'),
        'llm_trace': trace,
        'prompt_guard': {
            'expected': EXPECTED_AUDIO_PROMPT,
            'actual': actual_prompt,
            'passed': prompt_guard_passed,
        },
    }

    json.dumps(result, ensure_ascii=False)
    return result
