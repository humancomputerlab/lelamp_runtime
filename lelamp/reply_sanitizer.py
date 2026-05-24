from __future__ import annotations

import re


_BRACKET_STAGE_RE = re.compile(
    r"[（(][^()（）]{0,80}(?:shock|headshake|nod|sad|curious|wake_up|idle|wiggle|动作|灯|光|rgb|白光|黄灯|节奏灯)[^()（）]{0,80}[)）]",
    re.IGNORECASE,
)

_WHOLE_CLAUSE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|[，。！？!?])\s*(?:那|来|行啊|好啊|嘿嘿|哼|欸|诶|啊)?我给你亮个[^，。！？!?]*"),
    re.compile(r"(^|[，。！？!?])\s*节奏灯安排上[！!]*"),
    re.compile(r"(^|[，。！？!?])\s*看我给你来个[^，。！？!?]*"),
    re.compile(r"(^|[，。！？!?])\s*我现在给你[^，。！？!?]*(?:摇|晃|点头|摇头)[^，。！？!?]*"),
    re.compile(r"(^|[，。！？!?])\s*[白暖冷橙蓝粉紫黄红绿][^，。！？!?]{0,8}(?:灯|光|渐变)[^，。！？!?]*"),
)

_INLINE_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"我跟着晃"), "我陪着你"),
    (re.compile(r"我也得亮个灯给你助助兴"), "我也得给你助助兴"),
    (re.compile(r"亮个灯给你助助兴"), "给你助助兴"),
)


def sanitize_spoken_reply(text: str | None) -> str:
    original = (text or "").strip()
    if not original:
        return ""

    sanitized = _BRACKET_STAGE_RE.sub("", original)
    for pattern, replacement in _INLINE_REPLACEMENTS:
        sanitized = pattern.sub(replacement, sanitized)
    for pattern in _WHOLE_CLAUSE_PATTERNS:
        sanitized = pattern.sub(r"\1", sanitized)

    sanitized = re.sub(r"\s+", "", sanitized)
    sanitized = re.sub(r"^[，,。！？!?]+", "", sanitized)
    sanitized = re.sub(r"[，,]{2,}", "，", sanitized)
    sanitized = re.sub(r"([。！？!?])[，,]+", r"\1", sanitized)
    sanitized = re.sub(r"[，,](?=[。！？!?]|$)", "", sanitized)
    sanitized = re.sub(r"([。！？!?]){2,}", r"\1", sanitized)

    return sanitized or original
