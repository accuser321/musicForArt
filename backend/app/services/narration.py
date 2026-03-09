import re
from pathlib import Path

from mutagen import File as MutagenFile


def _audio_duration_sec(file_path: str) -> float:
    try:
        audio = MutagenFile(file_path)
        if audio is None or audio.info is None:
            return 0.0
        return float(getattr(audio.info, 'length', 0.0) or 0.0)
    except Exception:
        return 0.0


def _scene_weight(scene: dict) -> float:
    text = str(scene.get('text') or '')
    chars = max(1, len(text))
    punct = len(re.findall(r'[，,。！？!?；;：:、]', text))
    intensity = int(scene.get('intensity') or 0)
    # 字数为主，停顿和情绪强度作为修正
    return chars * 1.0 + punct * 1.6 + intensity * 0.08 + 1.0


def _build_timeline(scenes: list[dict], duration_sec: float) -> list[dict]:
    if not scenes:
        return []

    n = len(scenes)
    # 给场景间留一点自然停顿
    total_pause = min(max(0, n - 1) * 0.18, duration_sec * 0.25)
    effective = max(1.0, duration_sec - total_pause)

    weights = [_scene_weight(s) for s in scenes]
    s = sum(weights) or 1.0
    alloc = [effective * w / s for w in weights]

    out = []
    cursor = 0.0
    for idx, scene in enumerate(scenes):
        start = cursor
        end = start + alloc[idx]
        out.append(
            {
                'scene_no': scene.get('scene_no'),
                'text': scene.get('text', ''),
                'text_start_char': scene.get('char_start'),
                'text_end_char': scene.get('char_end'),
                'start_sec': round(start, 3),
                'end_sec': round(end, 3),
                'estimated_len_sec': round(alloc[idx], 3),
            }
        )
        cursor = end + (0.18 if idx < n - 1 else 0.0)
    return out


def analyze_narration_for_audiobook(file_path: str, scenes: list[dict]) -> dict:
    file_path = str(Path(file_path).resolve())
    duration_sec = _audio_duration_sec(file_path)
    if duration_sec <= 0:
        # 无法读取真实时长时，按每秒4字兜底
        total_chars = sum(len(str(s.get('text') or '')) for s in scenes)
        duration_sec = max(8.0, total_chars / 4.0)

    timeline = _build_timeline(scenes, duration_sec)
    lines = [
        '# 演绎音频时间轴（系统估算）',
        '',
        f'- 演绎音频总时长：{duration_sec:.2f}s',
        f'- 场景数：{len(timeline)}',
        '- 本时间轴用于把“文本区间”映射到“真实旁白时间段”，供音乐对位使用。',
        '',
        '## 场景时间轴',
    ]
    for t in timeline:
        lines.append(
            f"- Scene {t['scene_no']}: {t['start_sec']:.2f}s ~ {t['end_sec']:.2f}s | 字符区间 {t.get('text_start_char')}~{t.get('text_end_char')}"
        )

    return {
        'duration_sec': duration_sec,
        'timeline': timeline,
        'report_markdown': '\n'.join(lines),
        'analysis_mode': 'rules-timing',
    }
