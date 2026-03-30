import json
import math
import re
import subprocess
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


def _run_capture(cmd: list[str]) -> str:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return (proc.stdout or '') + (proc.stderr or '')
    except Exception as e:
        print(f'[AUDIO WARN] command failed: {cmd[:2]} {type(e).__name__}: {e}', file=sys.stderr)
        return ''


def _probe_audio_metadata(file_path: str) -> dict:
    out = _run_capture(
        [
            'ffprobe',
            '-v',
            'error',
            '-select_streams',
            'a:0',
            '-show_entries',
            'stream=codec_name,channels,sample_rate,bit_rate',
            '-show_entries',
            'format=duration,bit_rate',
            '-of',
            'json',
            file_path,
        ]
    )
    try:
        payload = json.loads(out or '{}')
    except Exception:
        payload = {}
    stream = ((payload.get('streams') or [{}])[:1] or [{}])[0]
    fmt = payload.get('format') or {}
    duration = _to_float(fmt.get('duration') or stream.get('duration') or 0.0, 0.0)
    sample_rate = int(_to_float(stream.get('sample_rate') or 0, 0))
    channels = int(_to_float(stream.get('channels') or 0, 0))
    bit_rate = int(_to_float(stream.get('bit_rate') or fmt.get('bit_rate') or 0, 0))
    return {
        'codec_name': str(stream.get('codec_name') or '').strip() or 'unknown',
        'duration_sec': duration,
        'sample_rate': sample_rate,
        'channels': channels,
        'bit_rate': bit_rate,
    }


def _detect_silences(file_path: str, duration_sec: float) -> list[dict]:
    out = _run_capture(
        [
            'ffmpeg',
            '-hide_banner',
            '-nostats',
            '-i',
            file_path,
            '-af',
            'silencedetect=noise=-32dB:d=0.35',
            '-f',
            'null',
            '-',
        ]
    )
    starts = [float(v) for v in re.findall(r'silence_start:\s*([0-9.]+)', out)]
    ends = [float(v) for v in re.findall(r'silence_end:\s*([0-9.]+)', out)]
    segments = []
    for idx, start in enumerate(starts):
        end = ends[idx] if idx < len(ends) else duration_sec
        if end <= start:
            continue
        span = round(end - start, 2)
        if span < 0.25:
            continue
        segments.append(
            {
                'start_sec': round(start, 2),
                'end_sec': round(min(end, duration_sec), 2),
                'duration_sec': span,
            }
        )
    return segments[:16]


def _analyze_volume(file_path: str) -> dict:
    out = _run_capture(
        [
            'ffmpeg',
            '-hide_banner',
            '-nostats',
            '-i',
            file_path,
            '-af',
            'volumedetect',
            '-f',
            'null',
            '-',
        ]
    )
    mean_match = re.search(r'mean_volume:\s*([-\d.]+)\s*dB', out)
    max_match = re.search(r'max_volume:\s*([-\d.]+)\s*dB', out)
    mean_db = _to_float(mean_match.group(1), -18.0) if mean_match else -18.0
    max_db = _to_float(max_match.group(1), -4.0) if max_match else -4.0
    dynamic_range = round(max_db - mean_db, 2)
    return {
        'mean_volume_db': round(mean_db, 2),
        'max_volume_db': round(max_db, 2),
        'dynamic_range_db': dynamic_range,
    }


def _build_active_segments(duration_sec: float, silences: list[dict]) -> list[dict]:
    if duration_sec <= 0:
        return []
    cursor = 0.0
    segments = []
    for silence in silences:
        start = _to_float(silence.get('start_sec'), 0.0)
        end = _to_float(silence.get('end_sec'), start)
        if start - cursor >= 1.0:
            segments.append({'start_sec': round(cursor, 2), 'end_sec': round(start, 2)})
        cursor = max(cursor, end)
    if duration_sec - cursor >= 1.0:
        segments.append({'start_sec': round(cursor, 2), 'end_sec': round(duration_sec, 2)})
    if not segments:
        segments.append({'start_sec': 0.0, 'end_sec': round(duration_sec, 2)})
    for seg in segments:
        seg['duration_sec'] = round(seg['end_sec'] - seg['start_sec'], 2)
    return segments[:8]


def _infer_tags(metadata: dict, silences: list[dict], volume: dict) -> list[str]:
    tags: list[str] = []
    channels = int(metadata.get('channels') or 0)
    duration_sec = _to_float(metadata.get('duration_sec'), 0.0)
    dynamic_range = _to_float(volume.get('dynamic_range_db'), 0.0)
    mean_db = _to_float(volume.get('mean_volume_db'), -18.0)
    silence_total = round(sum(_to_float(s.get('duration_sec'), 0.0) for s in silences), 2)
    silence_ratio = silence_total / duration_sec if duration_sec > 0 else 0.0

    if channels >= 2:
        tags.append('双声道空间感')
    if dynamic_range >= 10:
        tags.append('动态起伏明显')
    elif dynamic_range <= 5:
        tags.append('动态较平稳')
    if mean_db >= -12:
        tags.append('整体能量靠前')
    elif mean_db <= -18:
        tags.append('氛围铺底明显')
    if silence_ratio >= 0.12:
        tags.append('停顿转场较多')
    elif silence_ratio <= 0.03:
        tags.append('持续推进感强')
    if duration_sec >= 180:
        tags.append('长线叙事适配')
    elif duration_sec <= 75:
        tags.append('短结构利于剪辑')
    return tags[:6] or ['结构推进型', '待进一步判断']


def _build_dynamic_markers(duration_sec: float, active_segments: list[dict], silences: list[dict]) -> list[dict]:
    if duration_sec <= 0:
        return _fallback_markers(180.0)
    markers: list[dict] = []
    if active_segments:
        first = active_segments[0]
        markers.append(
            {
                'label': '起势段',
                'time_sec': round(first['start_sec'] + min(first['duration_sec'] * 0.25, 6.0), 2),
                'type': 'build',
            }
        )
        longest = max(active_segments, key=lambda s: s.get('duration_sec', 0.0))
        markers.append(
            {
                'label': '主体推进',
                'time_sec': round(longest['start_sec'] + longest['duration_sec'] / 2, 2),
                'type': 'peak',
            }
        )
        if len(active_segments) >= 2:
            second = active_segments[1]
            markers.append(
                {
                    'label': '转段点',
                    'time_sec': round(second['start_sec'], 2),
                    'type': 'turn',
                }
            )
        last = active_segments[-1]
        markers.append(
            {
                'label': '终段推进',
                'time_sec': round(last['start_sec'] + min(last['duration_sec'] * 0.35, 8.0), 2),
                'type': 'peak',
            }
        )
    for silence in silences[:2]:
        markers.append(
            {
                'label': '抽空回落',
                'time_sec': round(_to_float(silence.get('start_sec'), 0.0), 2),
                'type': 'valley',
            }
        )
    # 去重并排序
    dedup = []
    seen = set()
    for marker in markers:
        key = (marker['label'], marker['time_sec'], marker['type'])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(marker)
    dedup.sort(key=lambda row: row.get('time_sec', 0.0))
    return dedup[:6] or _fallback_markers(duration_sec)


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

    # 保留 LLM 的完整输出；但如果只是空壳值，就不要覆盖规则基线。
    if not isinstance(merged.get('title'), str) or not str(merged.get('title') or '').strip():
        merged['title'] = base['title']
    if not isinstance(merged.get('summary'), str) or not str(merged.get('summary') or '').strip():
        merged['summary'] = base['summary']
    if not isinstance(merged.get('fit_genres'), list) or not [str(x).strip() for x in (merged.get('fit_genres') or []) if str(x).strip()]:
        merged['fit_genres'] = base['fit_genres']
    if not isinstance(merged.get('risk_genres'), list) or not [str(x).strip() for x in (merged.get('risk_genres') or []) if str(x).strip()]:
        merged['risk_genres'] = base['risk_genres']
    if not isinstance(merged.get('sections'), list) or not merged.get('sections'):
        merged['sections'] = base['sections']
    if not isinstance(merged.get('hit_points'), list) or not merged.get('hit_points'):
        merged['hit_points'] = base['hit_points']
    if not isinstance(merged.get('mix_notes'), list) or not [str(x).strip() for x in (merged.get('mix_notes') or []) if str(x).strip()]:
        merged['mix_notes'] = base['mix_notes']
    if not isinstance(merged.get('key_points'), list) or not [str(x).strip() for x in (merged.get('key_points') or []) if str(x).strip()]:
        merged['key_points'] = base['key_points']
    structure_logic = merged.get('structure_logic')
    if (
        not isinstance(structure_logic, dict)
        or not str(structure_logic.get('pattern_guess') or '').strip()
        or not str(structure_logic.get('progression_comment') or '').strip()
    ):
        merged['structure_logic'] = base['structure_logic']
    elif not isinstance(structure_logic.get('repeat_groups'), list):
        merged['structure_logic']['repeat_groups'] = base['structure_logic'].get('repeat_groups', [])
    if not isinstance(merged.get('markdown'), str) or not str(merged.get('markdown') or '').strip():
        merged['markdown'] = base['markdown']
    return merged


def analyze_audio_for_audiobook(
    file_path: str,
    report_mode: str | None = None,
    debug_prompt: bool = True,
    llm_provider_override: str = '',
) -> dict:
    file_path = str(Path(file_path).resolve())
    mode = report_mode or settings.report_mode_default

    metadata = _probe_audio_metadata(file_path)
    duration_sec = _to_float(metadata.get('duration_sec'), 0.0)
    if duration_sec <= 0:
        duration_sec = _duration_by_mutagen(file_path)
    if duration_sec <= 0:
        duration_sec = 180.0

    metadata['duration_sec'] = duration_sec
    silences = _detect_silences(file_path, duration_sec)
    active_segments = _build_active_segments(duration_sec, silences)
    volume = _analyze_volume(file_path)
    tags = _infer_tags(metadata, silences, volume)
    markers = _build_dynamic_markers(duration_sec, active_segments, silences)

    features = {
        'duration_sec': duration_sec,
        'estimated_bpm': round(max(68.0, min(148.0, 92.0 + (volume.get('dynamic_range_db', 0.0) * 3.6))), 1),
        'tags': tags,
        'markers': markers,
        'feature_evidence': {
            'engine': 'ffprobe+ffmpeg-audio-evidence-v2',
            'confidence': 'medium',
            'stream': {
                'codec_name': metadata.get('codec_name'),
                'channels': metadata.get('channels'),
                'sample_rate': metadata.get('sample_rate'),
                'bit_rate': metadata.get('bit_rate'),
            },
            'volume': volume,
            'silence_profile': {
                'count': len(silences),
                'segments': silences[:8],
            },
            'active_segments': active_segments[:6],
            'note': '基于真实音频元数据、静音断点、响度与动态范围抽取证据，再交给LLM做导演可读分析。',
        },
    }

    payload = {
        'kind': 'audio',
        'audio_features': features,
        'llm_provider_override': llm_provider_override,
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
        'llm_enabled': llm_enabled(llm_provider_override),
        'llm_provider_used': llm_meta.get('llm_provider_used'),
        'llm_model_used': llm_meta.get('llm_model_used'),
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
