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


def _split_clauses_with_span(raw_text: str) -> list[dict]:
    out = []
    start = 0
    # 仅按句号分段，忽略逗号、问号、顿号等标点切分
    for m in re.finditer(r'[。]', raw_text):
        end = m.start()
        seg = raw_text[start:end].strip()
        if seg:
            left = len(raw_text[start:end]) - len(raw_text[start:end].lstrip())
            right = len(raw_text[start:end]) - len(raw_text[start:end].rstrip())
            out.append(
                {
                    'text': seg,
                    'text_start_char': start + left,
                    'text_end_char': end - right,
                    'punct': m.group(0),
                }
            )
        start = m.end()
    tail = raw_text[start:].strip()
    if tail:
        out.append(
            {
                'text': tail,
                'text_start_char': start,
                'text_end_char': len(raw_text),
                'punct': '',
            }
        )
    return out


def _punct_pause_weight(punct: str) -> float:
    p = str(punct or '')
    if p in {'。', '！', '？', '!', '?'}:
        return 1.0
    if p in {'；', ';', '：', ':'}:
        return 0.65
    if p in {'，', ',', '、'}:
        return 0.35
    return 0.25


def _build_clause_timeline(raw_text: str, duration_sec: float) -> list[dict]:
    clauses = _split_clauses_with_span(raw_text)
    if not clauses:
        return []
    n = len(clauses)
    pause_weights = [_punct_pause_weight(c.get('punct', '')) for c in clauses[:-1]]
    total_pause = sum(pause_weights) * 0.32
    total_pause = min(total_pause, duration_sec * 0.28)
    speaking_sec = max(1.0, duration_sec - total_pause)

    weights = [max(1.0, len(c['text']) * 1.0 + _punct_pause_weight(c.get('punct', '')) * 2.0) for c in clauses]
    ws = sum(weights) or 1.0
    cursor = 0.0
    out = []
    for i, c in enumerate(clauses, start=1):
        seg_len = speaking_sec * (weights[i - 1] / ws)
        start = cursor
        end = min(duration_sec, start + seg_len)
        pause_after = 0.0
        if i <= len(pause_weights):
            pause_after = total_pause * (pause_weights[i - 1] / (sum(pause_weights) or 1.0))
        out.append(
            {
                'clause_no': i,
                'text': c['text'],
                'text_start_char': c['text_start_char'],
                'text_end_char': c['text_end_char'],
                'punct': c['punct'],
                'start_sec': round(start, 3),
                'end_sec': round(end, 3),
                'pause_after_sec': round(pause_after, 3),
            }
        )
        cursor = min(duration_sec, end + pause_after)
    return out


def _scene_timeline_from_clauses(scenes: list[dict], clauses: list[dict], duration_sec: float, raw_text: str) -> list[dict]:
    if not scenes:
        return []
    if not clauses:
        return _build_timeline(scenes, duration_sec)

    text_len = max(1, len(raw_text or ''))
    out = []
    for scene in scenes:
        s0 = int(scene.get('char_start') or 0)
        s1 = int(scene.get('char_end') or s0)
        overlaps: list[tuple[float, float, int]] = []
        for c in clauses:
            c0 = int(c.get('text_start_char') or 0)
            c1 = int(c.get('text_end_char') or c0)
            ov = min(s1, c1) - max(s0, c0)
            if ov > 0:
                overlaps.append((float(c.get('start_sec') or 0.0), float(c.get('end_sec') or 0.0), ov))

        if overlaps:
            start = min(x[0] for x in overlaps)
            end = max(x[1] for x in overlaps)
            source = 'clause-overlap'
        else:
            # 无重叠时，按字符比例插值兜底
            start = duration_sec * (s0 / text_len)
            end = duration_sec * (s1 / text_len)
            source = 'char-ratio-fallback'

        if end <= start:
            end = min(duration_sec, start + 0.6)

        out.append(
            {
                'scene_no': scene.get('scene_no'),
                'text': scene.get('text', ''),
                'text_start_char': scene.get('char_start'),
                'text_end_char': scene.get('char_end'),
                'start_sec': round(start, 3),
                'end_sec': round(end, 3),
                'estimated_len_sec': round(end - start, 3),
                'source': source,
            }
        )
    return out


def analyze_narration_for_audiobook(file_path: str, scenes: list[dict], raw_text: str = '') -> dict:
    file_path = str(Path(file_path).resolve())
    duration_sec = _audio_duration_sec(file_path)
    if duration_sec <= 0:
        # 无法读取真实时长时，按每秒4字兜底
        total_chars = sum(len(str(s.get('text') or '')) for s in scenes)
        duration_sec = max(8.0, total_chars / 4.0)

    clause_timeline = _build_clause_timeline(raw_text, duration_sec) if raw_text else []
    timeline = _scene_timeline_from_clauses(scenes, clause_timeline, duration_sec, raw_text) if raw_text else _build_timeline(scenes, duration_sec)
    lines = [
        '# 演绎音频时间轴（文本+标点精准映射）',
        '',
        f'- 演绎音频总时长：{duration_sec:.2f}s',
        f'- 场景数：{len(timeline)}',
        '- 本时间轴用于把“文本区间”映射到“真实旁白时间段”，供音乐对位使用。',
        '- 算法：标点级语句时间轴 + 字符区间重叠映射；无重叠时使用字符比例回退。',
        '',
        '## 场景时间轴',
    ]
    for t in timeline:
        lines.append(
            f"- Scene {t['scene_no']}: {t['start_sec']:.2f}s ~ {t['end_sec']:.2f}s | 字符区间 {t.get('text_start_char')}~{t.get('text_end_char')} | 来源 {t.get('source', 'weighted')}"
        )
    if clause_timeline:
        lines.extend(['', '## 标点级语句时间轴'])
        for c in clause_timeline:
            lines.append(
                f"- Clause {c['clause_no']}: {c['start_sec']:.2f}s ~ {c['end_sec']:.2f}s | 字符区间 {c['text_start_char']}~{c['text_end_char']} | 标点 {c['punct'] or '无'}"
            )

    return {
        'duration_sec': duration_sec,
        'timeline': timeline,
        'clause_timeline': clause_timeline,
        'report_markdown': '\n'.join(lines),
        'analysis_mode': 'rules-timing',
    }
