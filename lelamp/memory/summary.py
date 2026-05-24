"""Session summary computation + atomic write.

Contract anchors:

* ``LIFECYCLE.md#*.summary.json`` -- full schema incl. manual shape
* ``LIFECYCLE.md#\u4e3a\u4ec0\u4e48 summary \u4e0d\u662f\u4e8b\u4ef6`` -- summary is derived, rebuildable

Key invariants enforced here:

* ``event_counts`` always carries all 4 ``kind`` keys (0 instead of
  missing).  Downstream readers should be able to do
  ``event_counts['conversation']`` without a ``.get`` dance.
* ``fallback_rate`` is ``None`` when ``event_counts['conversation'] == 0``.
  Playback-only manual sessions always land here -- returning 0.0 would
  wrongly read as "0% fallback" during aggregation.
* ``style_histogram`` is ``{}`` (not ``None``) when there are no
  conversations -- contract says manual shape must be stable.
* ``narrative`` is always ``None`` in v0; the field is present for
  forward-compat only.  Manual sessions never generate a narrative
  (LIFECYCLE.md§"Manual session \u7684\u5dee\u5f02").
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import ids as _ids
from .session import _atomic_write_json, load_meta
from .writer import MemoryWriter

_logger = logging.getLogger(__name__)

SUMMARY_SCHEMA = "lelamp.memory.v0.session_summary"
_TOP_RECORDINGS_K = 3
_EVENT_COUNT_KEYS = (
    "conversation",
    "function_tool",
    "fallback_expression",
    "playback",
)
_CONVERSATION_STYLES = {"excited", "caring", "worried", "sad"}


def summary_path(writer: MemoryWriter, session_id: str) -> Path:
    return writer.user_dir / "sessions" / f"{session_id}.summary.json"


def load_summary(writer: MemoryWriter, session_id: str) -> dict[str, Any]:
    with summary_path(writer, session_id).open("r", encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class _Counters:
    event_counts: dict[str, int]
    style_histogram: dict[str, int]
    fallback_count: int
    first_ts_ms: Optional[int]
    last_ts_ms: Optional[int]
    recordings: dict[str, int]

    @classmethod
    def empty(cls) -> "_Counters":
        return cls(
            event_counts={k: 0 for k in _EVENT_COUNT_KEYS},
            style_histogram={},
            fallback_count=0,
            first_ts_ms=None,
            last_ts_ms=None,
            recordings={},
        )


def _bump_recording(counters: _Counters, name: Optional[str]) -> None:
    if not name:
        return
    counters.recordings[name] = counters.recordings.get(name, 0) + 1


def _accumulate(counters: _Counters, event: Mapping[str, Any]) -> None:
    kind = event.get("kind")
    if kind not in _EVENT_COUNT_KEYS:
        return
    counters.event_counts[kind] = counters.event_counts.get(kind, 0) + 1
    ts_ms = event.get("ts_ms")
    if isinstance(ts_ms, int):
        if counters.first_ts_ms is None or ts_ms < counters.first_ts_ms:
            counters.first_ts_ms = ts_ms
        if counters.last_ts_ms is None or ts_ms > counters.last_ts_ms:
            counters.last_ts_ms = ts_ms

    payload = event.get("payload") or {}

    if kind == "conversation":
        style = payload.get("assistant_style")
        if isinstance(style, str) and style in _CONVERSATION_STYLES:
            counters.style_histogram[style] = counters.style_histogram.get(style, 0) + 1

    elif kind == "fallback_expression":
        counters.fallback_count += 1

    elif kind == "function_tool":
        if payload.get("phase") == "invoke" and payload.get("tool_name") == "play_recording":
            args = payload.get("args") or {}
            if isinstance(args, Mapping):
                _bump_recording(counters, args.get("recording_name"))

    elif kind == "playback":
        _bump_recording(counters, payload.get("recording_name"))


def _events_for_session(writer: MemoryWriter, session_id: str) -> Iterable[Mapping[str, Any]]:
    for event in writer.iter_events():
        if event.get("session_id") == session_id:
            yield event


def _top_recordings(recordings: Mapping[str, int]) -> list[str]:
    # Deterministic tie-break: by count desc, then name asc.
    ordered = sorted(recordings.items(), key=lambda pair: (-pair[1], pair[0]))
    return [name for name, _ in ordered[:_TOP_RECORDINGS_K]]


def compute_summary(
    writer: MemoryWriter,
    session_id: str,
    *,
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
) -> dict[str, Any]:
    """Scan ``events.jsonl`` and build the summary dict.

    ``start_ts_ms`` / ``end_ts_ms`` default to, in order:

    * the provided values, if explicit
    * the meta.json ``start_ts_ms`` for start
    * the first / last event ``ts_ms`` we encountered
    * :func:`_ids.current_timestamp_ms` for end as a last resort

    This layering matters: a session that crashes with zero events
    must still produce a valid summary so selfcheck's idempotent
    rebuild loop doesn't loop forever.
    """

    counters = _Counters.empty()
    for event in _events_for_session(writer, session_id):
        _accumulate(counters, event)

    resolved_start = start_ts_ms
    if resolved_start is None:
        try:
            meta = load_meta(writer, session_id)
            resolved_start = int(meta.get("start_ts_ms") or 0) or None
        except (FileNotFoundError, json.JSONDecodeError):
            resolved_start = None
    if resolved_start is None:
        resolved_start = counters.first_ts_ms
    if resolved_start is None:
        resolved_start = _ids.current_timestamp_ms()

    resolved_end = end_ts_ms
    if resolved_end is None:
        resolved_end = counters.last_ts_ms
    if resolved_end is None:
        resolved_end = _ids.current_timestamp_ms()
    if resolved_end < resolved_start:
        # Clock drift guard: never produce a negative duration.
        resolved_end = resolved_start

    conversations = counters.event_counts["conversation"]
    if conversations == 0:
        # Hard contract: no fallback_rate when the denominator is zero.
        fallback_rate: Optional[float] = None
    else:
        fallback_rate = round(counters.fallback_count / conversations, 4)

    return {
        "schema": SUMMARY_SCHEMA,
        "session_id": session_id,
        "start_ts_ms": resolved_start,
        "end_ts_ms": resolved_end,
        "duration_s": int((resolved_end - resolved_start) / 1000),
        "event_counts": dict(counters.event_counts),
        "style_histogram": dict(counters.style_histogram),
        "fallback_rate": fallback_rate,
        "top_recordings": _top_recordings(counters.recordings),
        # v0 always null: manual is null by design, agent is null because
        # no summariser runs yet (LIFECYCLE.md v0 strong constraint).
        "narrative": None,
    }


def write_summary(
    writer: MemoryWriter,
    session_id: str,
    summary: Mapping[str, Any],
) -> Path:
    """Atomically persist a summary dict to ``sessions/<sid>.summary.json``."""

    if summary.get("schema") != SUMMARY_SCHEMA:
        raise ValueError(
            f"refusing to write summary with schema={summary.get('schema')!r}"
        )
    if summary.get("session_id") != session_id:
        raise ValueError(
            f"summary.session_id mismatch: {summary.get('session_id')!r} vs {session_id!r}"
        )
    _validate_manual_shape(session_id, summary)
    path = summary_path(writer, session_id)
    path.parent.mkdir(exist_ok=True)
    _atomic_write_json(path, summary)
    return path


def compute_and_write_summary(
    writer: MemoryWriter,
    session_id: str,
    *,
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
) -> Path:
    summary = compute_summary(
        writer,
        session_id,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
    )
    return write_summary(writer, session_id, summary)


def _validate_manual_shape(session_id: str, summary: Mapping[str, Any]) -> None:
    """Enforce LIFECYCLE.md §"Manual session summary \u7684\u5408\u6cd5 shape".

    Raises ``ValueError`` so broken callers get loud feedback rather
    than silently shipping a malformed summary that prompt readers
    would need to special-case.
    """

    # All sessions must carry the 4 event_counts keys.
    counts = summary.get("event_counts")
    if not isinstance(counts, dict):
        raise ValueError("event_counts must be a dict")
    missing = [k for k in _EVENT_COUNT_KEYS if k not in counts]
    if missing:
        raise ValueError(f"event_counts missing keys: {missing}")

    if summary.get("style_histogram") is None:
        raise ValueError("style_histogram must be a dict ({} when empty), never null")

    # Manual-only tightenings.
    if _ids.is_manual_session(session_id):
        if summary.get("narrative") is not None:
            raise ValueError("manual session narrative must be null")
        if counts["conversation"] == 0 and summary.get("fallback_rate") is not None:
            raise ValueError(
                "manual/playback-only session with 0 conversations "
                "must have fallback_rate=null, not 0.0"
            )
    # Universal tightening applies to agent too.
    if counts["conversation"] == 0 and summary.get("fallback_rate") is not None:
        raise ValueError(
            "session with 0 conversations must have fallback_rate=null"
        )
