import re

from app.config import settings
from app.services.llm import generate_report, llm_enabled
from app.services.nlp_zh import tokenize_cn

ACTION_KEYWORDS = {
    '冲': '冲锋',
    '杀': '近战',
    '奔': '奔跑',
    '拔剑': '武器出鞘',
    '破': '破坏',
    '坍塌': '坍塌',
    '嘶吼': '兽吼',
    '低语': '低语',
}

EMOTION_KEYWORDS = {
    '愤怒': '高压',
    '恐惧': '压迫',
    '悲伤': '低落',
    '紧张': '紧张',
    '平静': '平稳',
    '绝望': '低谷',
    '希望': '抬升',
}

SFX_MAP = {
    '冲锋': '群体脚步+战吼',
    '近战': '刀剑碰撞+短促重击',
    '奔跑': '急促脚步+衣物摩擦',
    '武器出鞘': '金属抽离声',
    '破坏': '碎裂+坠落',
    '坍塌': '结构崩塌低频轰鸣',
    '兽吼': '低频兽吼+空间混响',
    '低语': '近讲呼吸+房间底噪',
}


def _split_sentences_with_span(text: str) -> list[dict]:
    out = []
    start = 0
    for m in re.finditer(r'[。！？!?\n]+', text):
        end = m.start()
        raw = text[start:end]
        stripped = raw.strip()
        if stripped:
            left_trim = len(raw) - len(raw.lstrip())
            right_trim = len(raw) - len(raw.rstrip())
            out.append(
                {
                    'text': stripped,
                    'char_start': start + left_trim,
                    'char_end': end - right_trim,
                }
            )
        start = m.end()
    tail = text[start:]
    tail_stripped = tail.strip()
    if tail_stripped:
        left_trim = len(tail) - len(tail.lstrip())
        right_trim = len(tail) - len(tail.rstrip())
        out.append(
            {
                'text': tail_stripped,
                'char_start': start + left_trim,
                'char_end': len(text) - right_trim,
            }
        )
    return out


def _build_rule_report(scenes: list[dict]) -> str:
    lines = [
        '# 文本分析报告（规则版）',
        '',
        f'- 句子数：{len(scenes)}',
        '- 输出：场景动作、情绪曲线、音效建议',
        '',
        '## 场景建议',
    ]

    for scene in scenes:
        lines.append(
            f"- Scene {scene['scene_no']}: 强度 {scene['intensity']} | 动作 {', '.join(scene['actions']) or '无'} | 音效 {', '.join(scene['sfx']) or '无'}"
        )
    return '\n'.join(lines)


def analyze_text_for_audiobook(text: str, report_mode: str | None = None) -> dict:
    mode = report_mode or settings.report_mode_default
    sentence_items = _split_sentences_with_span(text)
    scenes = []

    for idx, item in enumerate(sentence_items, start=1):
        sentence = item['text']
        actions = []
        emotions = []
        tokens = tokenize_cn(sentence)

        for k, v in ACTION_KEYWORDS.items():
            if k in sentence:
                actions.append(v)

        for k, v in EMOTION_KEYWORDS.items():
            if k in sentence:
                emotions.append(v)

        intensity = min(100, 20 + len(actions) * 20 + len(emotions) * 10)
        sfx = [SFX_MAP[a] for a in actions if a in SFX_MAP]

        scenes.append(
            {
                'scene_no': idx,
                'text': sentence,
                'char_start': item['char_start'],
                'char_end': item['char_end'],
                'tokens': tokens,
                'actions': sorted(set(actions)),
                'emotions': sorted(set(emotions)),
                'intensity': intensity,
                'sfx': sorted(set(sfx)),
            }
        )

    report_json, report_markdown, llm_meta = generate_report(mode, {'kind': 'text', 'mode': mode, 'raw_text': text, 'scenes': scenes})
    if not report_markdown:
        report_markdown = _build_rule_report(scenes)

    llm_hit = bool(llm_meta.get('effective_mode')) and bool(report_markdown)
    return {
        'scenes': scenes,
        'report_markdown': report_markdown,
        'report_json': report_json,
        'analysis_mode': 'llm+rules' if llm_hit else 'rules-only',
        'llm_structured': bool(report_json),
        'llm_enabled': llm_enabled(),
        'report_mode': mode,
        'effective_report_mode': llm_meta.get('effective_mode'),
        'llm_fallback_applied': llm_meta.get('fallback_applied', False),
        'llm_attempted_modes': llm_meta.get('attempted_modes', []),
    }
