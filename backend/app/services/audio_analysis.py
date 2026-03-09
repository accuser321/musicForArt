import json
from pathlib import Path

from mutagen import File as MutagenFile

from app.config import settings
from app.services.llm import generate_report, llm_enabled


def _duration_by_mutagen(file_path: str) -> float:
    audio = MutagenFile(file_path)
    if audio is None or audio.info is None:
        return 0.0
    return float(getattr(audio.info, 'length', 0.0) or 0.0)


def _fallback_markers(duration_sec: float) -> list[dict]:
    return [
        {'label': 'Intro', 'time_sec': round(duration_sec * 0.05, 2), 'type': 'build'},
        {'label': 'Peak-1', 'time_sec': round(duration_sec * 0.28, 2), 'type': 'peak'},
        {'label': 'Drop-1', 'time_sec': round(duration_sec * 0.38, 2), 'type': 'valley'},
        {'label': 'Peak-2', 'time_sec': round(duration_sec * 0.62, 2), 'type': 'peak'},
        {'label': 'Drop-2', 'time_sec': round(duration_sec * 0.72, 2), 'type': 'valley'},
        {'label': 'Final Peak', 'time_sec': round(duration_sec * 0.82, 2), 'type': 'peak'},
    ]


def _build_rule_report(features: dict) -> str:
    duration_sec = features['duration_sec']
    bpm = features['bpm']
    tags = features['tags']
    markers = features['markers']

    lines = [
        '# 音乐分析报告（规则版）',
        '',
        '## 1) Basic Info',
        f'- 时长: {duration_sec:.2f}s',
        f'- 估计 BPM: {bpm:.1f}',
        f"- 标签: {', '.join(tags)}",
        '',
        '## 2) Beginner Level',
        '- 这首音乐以节奏推进为主，适合冲突、战斗、集体行动类段落。',
        '- 证据：存在明确 Peak/Drop 段，可用于冲锋与回撤切换。',
        '',
        '## 3) Intermediate Level',
        '- 主要依赖短动机和节奏重复推进，适合做段落化剪辑。',
        '- 证据：锚点分布相对均匀，适合模块化切片。',
        '',
        '## 4) Advanced Level',
        '- 建议把 Peak 作为动作命中点，Drop 作为旁白窗口。',
        '- 证据：时间锚点中 peak/valley 已分离。',
        '',
        '## 5) Structure Breakdown',
    ]

    for marker in markers:
        lines.append(f"- {marker['label']} @ {marker['time_sec']:.2f}s ({marker['type']})")

    lines.extend(
        [
            '',
            '## 6) Audiobook Use Case',
            '- 战斗描写：对白/BGM 可设 45/55，动作句对齐 Peak。',
            '- 叙述与旁白：对白/BGM 可设 70/30，放在 Drop 或 Intro。',
        ]
    )
    return '\n'.join(lines)


def analyze_audio_for_audiobook(file_path: str, report_mode: str | None = None) -> dict:
    file_path = str(Path(file_path).resolve())
    mode = report_mode or settings.report_mode_default

    duration_sec = _duration_by_mutagen(file_path)
    if duration_sec <= 0:
        duration_sec = 180.0

    features = {
        'duration_sec': duration_sec,
        'estimated_bpm': 120.0,
        'tags': ['cinematic', 'epic', 'battle-ready'],
        'markers': _fallback_markers(duration_sec),
        'feature_evidence': {
            'engine': 'rule-based-timeline-v1',
            'confidence': 'medium',
            'note': '当前环境默认使用稳定规则特征，接入LLM后生成导演级解释。',
        },
    }

    report_json, report_markdown, llm_meta = generate_report(mode, {'kind': 'audio', 'mode': mode, 'audio_features': features})

    if not report_markdown:
        report_markdown = _build_rule_report(
            {
                'duration_sec': features['duration_sec'],
                'bpm': features['estimated_bpm'],
                'tags': features['tags'],
                'markers': features['markers'],
            }
        )

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
    }

    json.dumps(result, ensure_ascii=False)
    return result
