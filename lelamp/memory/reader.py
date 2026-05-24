"""Prompt-injection reader: ``build_memory_header()``.

Contract anchors:

* ``PROMPT_INTEGRATION.md`` -- 6 sections (P0..P5), 512-token budget,
  3-tier degrade, pure-read contract, deterministic output
* ``LIFECYCLE.md#\u5bb9\u9519\u4e0e\u6062\u590d`` -- reader **never** writes
* ``SCHEMA.md#\u53bb\u91cd\u5951\u7ea6`` -- manual sessions filtered out

This module is the **only** read-path surface memory exposes to
callers outside the package.  Two hard invariants:

1.  No filesystem writes.  If the index is stale / missing / broken,
    we degrade instead of repairing it -- the writer's self-check
    owns all repair logic.
2.  Deterministic: given the same disk state, the returned string is
    byte-identical across calls.  This lets tests diff-check header
    contents and lets operators reason about what the LLM will see.

The environment variable ``LELAMP_MEMORY_DISABLE=1`` short-circuits
everything: the reader returns an empty string so the runtime
behaves exactly like the "no memory" era.  This is the escape hatch
documented in ``PROMPT_INTEGRATION.md#\u7528\u6237\u53ef\u89c1 / \u53ef\u8986\u76d6``.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import ids as _ids
from .recent_index import (
    RECENT_EVENT_TAIL_LIMIT,
    RECENT_SESSION_LIMIT,
    recent_index_path,
)
from .root import memory_root, resolve_user_id, user_memory_root

_logger = logging.getLogger(__name__)

DEFAULT_BUDGET_TOKENS = 512
_BUDGET_ENV = "LELAMP_MEMORY_PROMPT_BUDGET"
_DISABLE_ENV = "LELAMP_MEMORY_DISABLE"

_RECENT_CONVERSATION_LIMIT = 5
_TOOL_DIGEST_TOOL_LIMIT = 3
_TOOL_DIGEST_TAIL_LIMIT = 10
_PLAYBACK_DIGEST_TAIL_LIMIT = 10
_CONVERSATION_TEXT_CLIP = 60
_FALLBACK_UNAVAILABLE = '<memory status="unavailable"/>'


# ---------------------------------------------------------------------------
# env / utility helpers
# ---------------------------------------------------------------------------


def _reader_disabled() -> bool:
    value = os.environ.get(_DISABLE_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _budget(explicit: Optional[int]) -> int:
    if explicit is not None and explicit > 0:
        return explicit
    raw = os.environ.get(_BUDGET_ENV)
    if raw:
        try:
            parsed = int(raw)
            if parsed > 0:
                return parsed
        except ValueError:
            _logger.warning(
                "invalid %s=%r; falling back to %d",
                _BUDGET_ENV,
                raw,
                DEFAULT_BUDGET_TOKENS,
            )
    return DEFAULT_BUDGET_TOKENS


def estimate_tokens(text: str) -> int:
    """Rough token count used for budgeting.

    Design-sheet heuristic: ``len // 3`` with a minimum of 1.  It
    over-estimates English and under-estimates Chinese; both are fine
    for the 512-token budget headroom we carry.
    """

    if not text:
        return 0
    return max(1, len(text) // 3)


def _truncate_by_sentence(text: str, budget_tokens: int) -> str:
    """Drop trailing sentences until ``text`` fits.

    Sentence boundary = CJK/ASCII full stop / question mark / bang.
    If no boundary is found we fall back to character truncation so
    the output still fits.
    """

    if estimate_tokens(text) <= budget_tokens:
        return text
    boundaries = {"\u3002", ".", "!", "?", "\uff01", "\uff1f"}
    working = text
    while working and estimate_tokens(working) > budget_tokens:
        cut = -1
        for idx in range(len(working) - 1, -1, -1):
            if working[idx] in boundaries:
                cut = idx
                break
        if cut <= 0:
            break
        working = working[:cut].rstrip()
    if estimate_tokens(working) <= budget_tokens:
        return working
    # Final fallback: raw character truncation to an ASCII-safe cap.
    char_cap = max(0, budget_tokens * 3)
    return text[:char_cap]


# ---------------------------------------------------------------------------
# data collection
# ---------------------------------------------------------------------------


@dataclass
class _ReaderState:
    tier: str  # "normal" / "degraded" / "fallback"
    profile: dict[str, Any]
    summaries: list[dict[str, Any]]  # newest first, agent-only, length <= 3
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    # 最近一条 fluxchi_listener 源的 session_summary 事件（若存在），
    # 用来渲染 P1.5 "LAMP COMPANION RECAP" section。fluxchi session
    # 与 voice_agent session 的 id 空间独立，也不走 sessions/*.summary.json
    # 管线，所以要单独扫 events.jsonl 拿。
    fluxchi_summary: Optional[dict[str, Any]] = None


def _load_profile(user_dir: Path) -> dict[str, Any]:
    path = user_dir / "profile.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("profile.json unreadable (%s); using empty profile", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _index_is_fresh(index_path: Path, events_path: Path) -> Optional[dict[str, Any]]:
    if not index_path.exists():
        return None
    try:
        with index_path.open("r", encoding="utf-8") as fh:
            idx = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(idx, dict):
        return None
    built_at = idx.get("built_at_ms")
    if not isinstance(built_at, int):
        return None
    if events_path.exists():
        events_mtime_ms = int(events_path.stat().st_mtime * 1000)
        if events_mtime_ms > built_at:
            return None
    return idx


def _load_summaries_via_index(user_dir: Path, idx: Mapping[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for ref in idx.get("sessions") or []:
        if not isinstance(ref, Mapping):
            continue
        session_id = ref.get("session_id")
        if not isinstance(session_id, str):
            continue
        if _ids.is_manual_session(session_id):
            # Defensive: the index builder filters these already, but
            # never trust an aggregate.
            continue
        rel = ref.get("summary_ref")
        if not isinstance(rel, str):
            continue
        path = user_dir / rel
        try:
            with path.open("r", encoding="utf-8") as fh:
                summary = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(summary, dict):
            summaries.append(summary)
    return summaries


def _load_summaries_degraded(user_dir: Path) -> list[dict[str, Any]]:
    sessions_dir = user_dir / "sessions"
    if not sessions_dir.exists():
        return []
    entries: list[tuple[int, str, Path]] = []
    for path in sessions_dir.glob("*.summary.json"):
        try:
            with path.open("r", encoding="utf-8") as fh:
                summary = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(summary, dict):
            continue
        session_id = summary.get("session_id")
        if not isinstance(session_id, str) or _ids.is_manual_session(session_id):
            continue
        start_ts_ms = int(summary.get("start_ts_ms") or 0)
        entries.append((start_ts_ms, session_id, path))
    entries.sort(reverse=True)
    result: list[dict[str, Any]] = []
    for _, _, path in entries[:RECENT_SESSION_LIMIT]:
        try:
            with path.open("r", encoding="utf-8") as fh:
                result.append(json.load(fh))
        except (OSError, json.JSONDecodeError):
            continue
    return result


def _read_recent_events(events_path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    """Return the last ``limit`` eligible events, crash-tail tolerant."""

    return _scan_recent_events(
        events_path,
        limit=limit,
        allowed_session_ids=None,
        allowed_event_ids=None,
    )


def _scan_recent_events(
    events_path: Path,
    *,
    limit: int,
    allowed_session_ids: Optional[set[str]],
    allowed_event_ids: Optional[set[str]],
) -> list[dict[str, Any]]:
    """Tail-scan ``events.jsonl`` without ever writing to disk.

    ``allowed_session_ids`` enforces the "recent 3 agent sessions"
    half of the recent-window contract.  ``allowed_event_ids`` lets
    the normal tier honor ``recent_index.json:event_tail_refs`` at
    face value while degraded tier can omit it and derive the same
    window directly from summaries + raw events.
    """

    if not events_path.exists():
        return []
    buffered: list[dict[str, Any]] = []
    try:
        with events_path.open("r", encoding="utf-8") as fh:
            raw_lines = fh.readlines()
    except OSError:
        return []
    for i, raw in enumerate(raw_lines):
        stripped = raw.rstrip("\n")
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            if i == len(raw_lines) - 1:
                # Crash-tail line: design contract says skip silently.
                continue
            # Mid-file corruption would normally raise, but reader is
            # pure-read and must degrade -- skip and carry on.
            continue
        if not isinstance(event, dict):
            continue
        session_id = event.get("session_id")
        if not isinstance(session_id, str):
            continue
        if _ids.is_manual_session(session_id):
            continue
        if allowed_session_ids is not None and session_id not in allowed_session_ids:
            continue
        if allowed_event_ids is not None:
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or event_id not in allowed_event_ids:
                continue
        buffered.append(event)
    return buffered[-limit:]


def _summary_session_ids(summaries: Sequence[Mapping[str, Any]]) -> set[str]:
    session_ids: set[str] = set()
    for summary in summaries:
        session_id = summary.get("session_id")
        if isinstance(session_id, str) and not _ids.is_manual_session(session_id):
            session_ids.add(session_id)
    return session_ids


def _load_recent_events_via_index(
    events_path: Path,
    idx: Mapping[str, Any],
    *,
    session_ids: set[str],
) -> list[dict[str, Any]]:
    refs = idx.get("event_tail_refs") or []
    event_ids: set[str] = set()
    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        event_id = ref.get("event_id")
        if isinstance(event_id, str):
            event_ids.add(event_id)
    if not event_ids or not session_ids:
        return []
    return _scan_recent_events(
        events_path,
        limit=RECENT_EVENT_TAIL_LIMIT,
        allowed_session_ids=session_ids,
        allowed_event_ids=event_ids,
    )


def _load_latest_fluxchi_summary(events_path: Path) -> Optional[dict[str, Any]]:
    """Tail-scan ``events.jsonl`` for the newest FluxChi session summary.

    Independent of the voice_agent summary pipeline: fluxchi sessions
    never emit ``sessions/<id>.summary.json``, so we look at the raw
    ``kind=session_summary`` events written by ``fluxchi_listener``.

    Returns the most recent event by ``ts_ms`` (falling back to file
    order when ts_ms is missing or equal), or ``None`` if no matching
    event exists. Crash-tail tolerant like ``_scan_recent_events``.
    """

    if not events_path.exists():
        return None
    try:
        with events_path.open("r", encoding="utf-8") as fh:
            raw_lines = fh.readlines()
    except OSError:
        return None
    best: Optional[dict[str, Any]] = None
    best_ts: int = -1
    for i, raw in enumerate(raw_lines):
        stripped = raw.rstrip("\n")
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            # Crash-tail or mid-file corruption — reader must degrade.
            continue
        if not isinstance(event, dict):
            continue
        if event.get("kind") != "session_summary":
            continue
        if event.get("source") != "fluxchi_listener":
            continue
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or _ids.is_manual_session(session_id):
            continue
        ts = event.get("ts_ms")
        ts_int = int(ts) if isinstance(ts, (int, float)) else 0
        # Ties broken by "later in file wins" — events.jsonl is append-only.
        if ts_int > best_ts or (ts_int == best_ts and best is not None):
            best_ts = ts_int
            best = event
    return best


def _collect_state(user_dir: Path) -> _ReaderState:
    profile = _load_profile(user_dir)
    events_path = user_dir / "events.jsonl"
    fluxchi_summary = _load_latest_fluxchi_summary(events_path)
    idx = _index_is_fresh(recent_index_path_for(user_dir), events_path)
    if idx is not None:
        summaries = _load_summaries_via_index(user_dir, idx)
        tier = "normal"
    else:
        summaries = _load_summaries_degraded(user_dir)
        tier = "degraded"
    if not summaries:
        # Tier 3: still load recent_events so a profile_hint-only header
        # could, in principle, exist -- but PROMPT_INTEGRATION says
        # fallback returns the unavailable marker with nothing else.
        # Exception: if fluxchi has been logging independently (lamp
        # running before voice_agent ever wakes up), surface that recap
        # alone so R4 relationship memory still closes the loop.
        return _ReaderState(
            tier="fallback",
            profile=profile,
            summaries=[],
            recent_events=[],
            fluxchi_summary=fluxchi_summary,
        )
    session_ids = _summary_session_ids(summaries)
    if idx is not None:
        events = _load_recent_events_via_index(
            events_path,
            idx,
            session_ids=session_ids,
        )
    else:
        events = _scan_recent_events(
            events_path,
            limit=RECENT_EVENT_TAIL_LIMIT,
            allowed_session_ids=session_ids,
            allowed_event_ids=None,
        )
    return _ReaderState(
        tier=tier,
        profile=profile,
        summaries=summaries,
        recent_events=events,
        fluxchi_summary=fluxchi_summary,
    )


def recent_index_path_for(user_dir: Path) -> Path:
    return user_dir / "recent_index.json"


# ---------------------------------------------------------------------------
# section builders
# ---------------------------------------------------------------------------


def _section_profile_hint(state: _ReaderState) -> str:
    profile = state.profile
    nickname = profile.get("nickname")
    banned = profile.get("banned_styles") or []
    lines: list[str] = []
    if isinstance(nickname, str) and nickname.strip():
        lines.append(f"- \u7528\u6237\u6635\u79f0\uff1a{nickname.strip()}")
    if isinstance(banned, list):
        legal = [str(s) for s in banned if isinstance(s, str) and s]
        if legal:
            lines.append(
                "- \u660e\u786e\u4e0d\u559c\u6b22\u7684\u98ce\u683c\uff1a" + ", ".join(legal)
            )
    if not lines:
        return ""
    return "USER CONTEXT\n" + "\n".join(lines)


def _format_duration(duration_s: int) -> str:
    if duration_s < 0:
        duration_s = 0
    hours, remainder = divmod(duration_s, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{duration_s}s"


def _format_date(ts_ms: int) -> str:
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "unknown"


def _section_session_summary_recent(state: _ReaderState) -> str:
    if not state.summaries:
        return ""
    latest = state.summaries[0]
    start_ts_ms = int(latest.get("start_ts_ms") or 0)
    duration_s = int(latest.get("duration_s") or 0)
    header = (
        f"LAST SESSION RECAP ({_format_date(start_ts_ms)}, "
        f"{_format_duration(duration_s)})"
    )
    narrative = latest.get("narrative")
    if isinstance(narrative, str) and narrative.strip():
        body = narrative.strip()
    else:
        body = _synthesize_recap(latest)
    if not body:
        return header
    return f"{header}\n{body}"


_FLUXCHI_PROFILE_LABELS = {
    "scene_a_traditional": "表情提醒",
    "scene_b_breath": "呼吸共振",
}

_FLUXCHI_LEVEL_LABELS = {
    "mild": "轻提醒",
    "moderate": "中提醒",
    "severe": "打断",
    "recovered": "回应恢复",
}


def _section_lamp_companion_recap(state: _ReaderState) -> str:
    """Render the lamp's last companion session in Chinese prose.

    Reads the latest FluxChi session_summary event (written by the
    fluxchi_listener on ws disconnect / shutdown / session_end). The
    goal is to give the voice agent enough context to say things like
    "昨天我们做了两次慢呼吸，今天先休息会？" — closing the R4
    relationship-memory loop at the language layer without touching
    ``summary.narrative`` (which LIFECYCLE.md still pins to None in v0).
    """

    event = state.fluxchi_summary
    if not event:
        return ""
    payload = event.get("payload") or {}
    if not isinstance(payload, Mapping):
        return ""

    ts_ms = int(event.get("ts_ms") or 0)
    duration_s = payload.get("duration_sec")
    try:
        duration_int = int(duration_s) if duration_s is not None else 0
    except (TypeError, ValueError):
        duration_int = 0
    header_date = _format_date(ts_ms) if ts_ms else "unknown"
    header_duration = _format_duration(duration_int) if duration_int > 0 else ""
    if header_duration:
        header = f"LAMP COMPANION RECAP ({header_date}, {header_duration})"
    else:
        header = f"LAMP COMPANION RECAP ({header_date})"

    profile_name = payload.get("profile_name")
    profile_label = _FLUXCHI_PROFILE_LABELS.get(
        profile_name if isinstance(profile_name, str) else "",
        profile_name if isinstance(profile_name, str) else "",
    )

    level_counts = payload.get("level_counts")
    if not isinstance(level_counts, Mapping):
        level_counts = {}
    # Deterministic order: severe → moderate → mild → recovered → others.
    ordered_levels = ["severe", "moderate", "mild", "recovered"]
    level_fragments: list[str] = []
    total_interventions = 0
    for lvl in ordered_levels:
        raw = level_counts.get(lvl)
        try:
            count = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            continue
        label = _FLUXCHI_LEVEL_LABELS.get(lvl, lvl)
        level_fragments.append(f"{label} {count} 次")
        if lvl != "recovered":
            total_interventions += count
    # Catch any unknown levels defensively.
    for name, raw in sorted((level_counts or {}).items()):
        if name in ordered_levels:
            continue
        try:
            count = int(raw)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        level_fragments.append(f"{name} {count} 次")
        total_interventions += count

    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    breath_completed = _safe_int(payload.get("breath_completed"))
    breath_interrupts = _safe_int(payload.get("breath_interrupts"))
    manual_fallback = _safe_int(payload.get("manual_fallback_count"))
    final_stamina = payload.get("final_stamina")

    # "有信号"判定：profile_name 本身不算信号——一个什么都没发生的 session
    # 哪怕标了 scene_a 也不值得占 prompt 预算。is_interesting() 理应在
    # listener 端拦住这种，这里保底防御。
    has_signal = (
        total_interventions > 0
        or breath_completed > 0
        or breath_interrupts > 0
        or manual_fallback > 0
        or isinstance(final_stamina, (int, float))
    )
    if not has_signal:
        return ""

    body_parts: list[str] = []
    if profile_label:
        body_parts.append(f"模式：{profile_label}")
    if level_fragments:
        body_parts.append("；".join(level_fragments))
    breath_frag: list[str] = []
    if breath_completed > 0:
        breath_frag.append(f"完整呼吸 {breath_completed} 次")
    if breath_interrupts > 0:
        breath_frag.append(f"被打断 {breath_interrupts} 次")
    if breath_frag:
        body_parts.append("呼吸：" + "、".join(breath_frag))
    if manual_fallback > 0:
        body_parts.append(f"手动按钮 {manual_fallback} 次")
    if isinstance(final_stamina, (int, float)):
        body_parts.append(f"最终状态约 {int(final_stamina)}")

    body = "；".join(body_parts) + "。"
    return f"{header}\n{body}"


def _synthesize_recap(summary: Mapping[str, Any]) -> str:
    counts = summary.get("event_counts") or {}
    conv = int(counts.get("conversation") or 0)
    playback = int(counts.get("playback") or 0)
    fallback_rate = summary.get("fallback_rate")
    top_recordings = summary.get("top_recordings") or []

    segments: list[str] = []
    if conv:
        segments.append(f"\u5171 {conv} \u8f6e\u5bf9\u8bdd")
    if playback:
        segments.append(f"{playback} \u6b21\u786c\u4ef6\u56de\u653e")
    if isinstance(fallback_rate, (int, float)):
        pct = round(float(fallback_rate) * 100)
        segments.append(f"fallback \u6bd4\u4f8b {pct}%")
    if isinstance(top_recordings, list) and top_recordings:
        names = ", ".join(str(n) for n in top_recordings[:3])
        segments.append(f"\u9ad8\u9891\u52a8\u4f5c\uff1a{names}")
    if not segments:
        return ""
    return "\u3001".join(segments) + "\u3002"


def _section_style_tendency(state: _ReaderState) -> str:
    totals: Counter = Counter()
    fallback_sum = 0.0
    fallback_denom = 0
    for summary in state.summaries:
        hist = summary.get("style_histogram") or {}
        if isinstance(hist, Mapping):
            for style, count in hist.items():
                if isinstance(style, str) and isinstance(count, (int, float)):
                    totals[style] += int(count)
        fallback_rate = summary.get("fallback_rate")
        if isinstance(fallback_rate, (int, float)):
            fallback_sum += float(fallback_rate)
            fallback_denom += 1

    if not totals and fallback_denom == 0:
        return ""

    lines = ["STYLE PATTERNS (last 3 sessions)"]
    if totals:
        total_count = sum(totals.values())
        # Deterministic ordering: count desc, then name asc.
        ordered = sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
        top = ordered[:2]
        fragments = [
            f"{name} ({round((count / total_count) * 100)}%)"
            for name, count in top
        ]
        lines.append("- \u88ab\u56de\u5e94\u6700\u591a\uff1a" + ", ".join(fragments))
    if fallback_denom:
        avg = fallback_sum / fallback_denom
        pct = round(avg * 100)
        lines.append(f"- fallback \u6bd4\u4f8b\uff1a{pct}%")
        if avg > 0.40:
            lines.append("- \u6ce8\u610f\uff1a\u6700\u8fd1 fallback \u6bd4\u4f8b\u504f\u9ad8")
    return "\n".join(lines)


def _format_clock(ts_ms: int) -> str:
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone()
        return dt.strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "??:??"


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "\u2026"


def _section_recent_conversation(state: _ReaderState) -> str:
    convs = [
        e for e in state.recent_events
        if e.get("kind") == "conversation"
    ][-_RECENT_CONVERSATION_LIMIT:]
    if not convs:
        return ""
    lines = [f"RECENT TURNS (\u6700\u8fd1 {len(convs)} \u8f6e)"]
    for idx, event in enumerate(convs, 1):
        payload = event.get("payload") or {}
        user_text = _clip(str(payload.get("user_text") or ""), _CONVERSATION_TEXT_CLIP)
        assistant_text = _clip(
            str(payload.get("assistant_text") or ""),
            _CONVERSATION_TEXT_CLIP,
        )
        style = payload.get("assistant_style")
        assistant_label = f"assistant({style})" if isinstance(style, str) and style else "assistant"
        clock = _format_clock(int(event.get("ts_ms") or 0))
        lines.append(
            f"{idx}. [{clock}] user: {user_text}  \u2192 {assistant_label}: {assistant_text}"
        )
    return "\n".join(lines)


def _section_function_tool_digest(state: _ReaderState) -> str:
    # Count invoke events only to avoid double-counting via result
    # phase; remember the last error for observability.
    invokes = [
        e for e in state.recent_events
        if e.get("kind") == "function_tool"
        and (e.get("payload") or {}).get("phase") == "invoke"
    ][-_TOOL_DIGEST_TAIL_LIMIT:]
    results = [
        e for e in state.recent_events
        if e.get("kind") == "function_tool"
        and (e.get("payload") or {}).get("phase") == "result"
    ][-_TOOL_DIGEST_TAIL_LIMIT:]
    if not invokes:
        return ""

    counts: Counter = Counter()
    for event in invokes:
        payload = event.get("payload") or {}
        tool = payload.get("tool_name")
        if isinstance(tool, str):
            counts[tool] += 1

    by_tool_ok: dict[str, tuple[int, int]] = {}  # tool -> (ok, total)
    last_error: Optional[str] = None
    for event in results:
        payload = event.get("payload") or {}
        tool = payload.get("tool_name")
        ok = bool(payload.get("ok"))
        if not isinstance(tool, str):
            continue
        ok_count, total = by_tool_ok.get(tool, (0, 0))
        total += 1
        if ok:
            ok_count += 1
        else:
            err = payload.get("error")
            if isinstance(err, str) and err.strip():
                last_error = _clip(err.strip(), 60)
        by_tool_ok[tool] = (ok_count, total)

    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    lines = [f"TOOL USAGE (recent {len(invokes)})"]
    for tool, invoke_count in ordered[:_TOOL_DIGEST_TOOL_LIMIT]:
        ok_count, total = by_tool_ok.get(tool, (0, 0))
        if total:
            lines.append(f"- {tool} \u00d7 {invoke_count} (ok: {ok_count}/{total})")
        else:
            lines.append(f"- {tool} \u00d7 {invoke_count}")
    if last_error:
        lines.append(f"- \u6700\u8fd1\u9519\u8bef\uff1a{last_error}")
    return "\n".join(lines)


def _section_playback_digest(state: _ReaderState) -> str:
    playbacks = [
        e for e in state.recent_events
        if e.get("kind") == "playback"
    ][-_PLAYBACK_DIGEST_TAIL_LIMIT:]
    if not playbacks:
        return ""

    rec_counts: Counter = Counter()
    latest_rgb: Optional[tuple[tuple[int, int, int], int]] = None
    fail_count = 0
    for event in playbacks:
        payload = event.get("payload") or {}
        name = payload.get("recording_name")
        if isinstance(name, str) and name:
            rec_counts[name] += 1
        rgb = payload.get("rgb")
        ts_ms = int(event.get("ts_ms") or 0)
        if isinstance(rgb, list) and len(rgb) == 3:
            try:
                triple = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
            except (TypeError, ValueError):
                triple = None
            if triple is not None and (latest_rgb is None or ts_ms > latest_rgb[1]):
                latest_rgb = (triple, ts_ms)
        if payload.get("ok") is False:
            fail_count += 1

    lines = [f"HARDWARE USAGE (recent {len(playbacks)})"]
    if rec_counts:
        top = [name for name, _ in sorted(
            rec_counts.items(), key=lambda pair: (-pair[1], pair[0])
        )[:3]]
        lines.append("- \u6700\u5e38\u64ad\u653e\uff1a" + ", ".join(top))
    if latest_rgb is not None:
        (r, g, b), ts_ms = latest_rgb
        lines.append(
            f"- \u6700\u8fd1\u4e00\u6b21\u706f\u5149\uff1argb({r}, {g}, {b}) @ {_format_clock(ts_ms)}"
        )
    if playbacks and fail_count / len(playbacks) > 0.20:
        lines.append("- \u6ce8\u610f\uff1a\u786c\u4ef6\u56de\u653e\u6700\u8fd1\u6709\u5931\u8d25")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# public entry
# ---------------------------------------------------------------------------


def build_memory_header(
    *,
    user_id: Optional[str] = None,
    budget_tokens: Optional[int] = None,
    now: Optional[datetime] = None,
) -> str:
    """Return the ``<memory>`` block to prepend to a system prompt.

    Behaviour summary (see PROMPT_INTEGRATION.md for the full spec):

    * ``LELAMP_MEMORY_DISABLE`` truthy -> returns ``""``.
    * Fallback tier -> returns ``<memory status="unavailable"/>``.
    * Otherwise composes P0..P5 sections, drops from the bottom
      until the total estimated token count fits the budget, and
      sentence-truncates P1 if the remainder still overflows.
    * Never writes to disk, never raises -- an unexpected error is
      logged and degrades to the fallback marker.
    """

    if _reader_disabled():
        return ""

    try:
        user_dir = user_memory_root(user_id)
        if not user_dir.exists():
            return _FALLBACK_UNAVAILABLE
        state = _collect_state(user_dir)
    except Exception:  # pragma: no cover - purely defensive
        _logger.exception("build_memory_header: unexpected failure; degrading")
        return _FALLBACK_UNAVAILABLE

    if state.tier == "fallback":
        # 边界：voice_agent 还没跑过 session，但灯已经独立陪用户过。
        # 单独渲染 lamp recap，让 R4 记忆在"灯先于语音"的时序下也能闭环。
        lamp_body = _section_lamp_companion_recap(state)
        if lamp_body:
            return _wrap(lamp_body, user_id=user_id, now=now)
        return _FALLBACK_UNAVAILABLE

    sections: list[tuple[str, bool]] = [
        (_section_profile_hint(state), False),
        (_section_session_summary_recent(state), True),
        (_section_lamp_companion_recap(state), True),
        (_section_style_tendency(state), False),
        (_section_recent_conversation(state), False),
        (_section_function_tool_digest(state), False),
        (_section_playback_digest(state), False),
    ]
    sections = [(body, can_truncate) for body, can_truncate in sections if body]
    if not sections:
        return _FALLBACK_UNAVAILABLE

    budget = _budget(budget_tokens)
    # Drop lowest-priority sections first; if the best remaining
    # section still overflows, truncate that section to fit.
    while len(sections) > 1 and sum(estimate_tokens(body) for body, _ in sections) > budget:
        sections.pop()
    used = sum(estimate_tokens(body) for body, _ in sections)
    if used > budget and sections:
        target_index = next(
            (idx for idx, (_, can_truncate) in enumerate(sections) if can_truncate),
            0,
        )
        other_tokens = sum(
            estimate_tokens(body)
            for idx, (body, _) in enumerate(sections)
            if idx != target_index
        )
        allowed = max(0, budget - other_tokens)
        target_body, can_truncate = sections[target_index]
        sections[target_index] = (
            _truncate_by_sentence(target_body, allowed),
            can_truncate,
        )
        sections = [(body, can_truncate) for body, can_truncate in sections if body]
    body = "\n\n".join(body for body, _ in sections)
    return _wrap(body, user_id=user_id, now=now)


def _wrap(body: str, *, user_id: Optional[str], now: Optional[datetime]) -> str:
    when = now if now is not None else datetime.now().astimezone()
    if when.tzinfo is None:
        when = when.astimezone()
    generated_at = when.strftime("%Y-%m-%dT%H:%M:%S%z")
    # Normalize +HHMM -> +HH:MM for readability; strftime %z emits +HHMM.
    if len(generated_at) >= 5 and generated_at[-5] in "+-":
        generated_at = f"{generated_at[:-2]}:{generated_at[-2:]}"
    uid = resolve_user_id(user_id)
    return (
        f'<memory user_id="{uid}" schema="lelamp.memory.v0" '
        f'generated_at="{generated_at}">\n{body}\n</memory>'
    )
