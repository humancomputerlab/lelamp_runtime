"""events.jsonl append-only writer with flock + fsync.

Contract anchors:

* ``STORAGE.md#events.jsonl`` -- append + fsync under the user-level
  ``.lock`` (``fcntl.flock(LOCK_EX)``), 0600 mode, 2 KiB soft budget
* ``SCHEMA.md`` -- 4 ``kind`` values, common fields, payload validation
* ``STORAGE.md#\u5d29\u6e83\u8bed\u4e49`` -- reader tolerates a malformed trailing line

The writer deliberately has **no** retry / circuit-breaker / async queue:
the critical section is a single locked append (< 1 ms), and crash
recovery is owned by :mod:`lelamp.memory.selfcheck` via the "skip
malformed trailing line" contract.  Integration callers (voice agent,
dashboard, remote_control) decide whether to swallow exceptions from
here -- H1 v0 policy is that a broken memory disk never kills the
runtime, but that wrapping is done at the integration seam, not in
this module.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional

from lelamp.expression_engine import EXPRESSION_STYLE_CHOICES

from . import ids as _ids
from .root import DEFAULT_USER_ID, ensure_user_memory_root, resolve_user_id

_logger = logging.getLogger(__name__)

SCHEMA_VERSION = "lelamp.memory.v0"

KIND_CONVERSATION = "conversation"
KIND_FUNCTION_TOOL = "function_tool"
KIND_FALLBACK_EXPRESSION = "fallback_expression"
KIND_PLAYBACK = "playback"
# R4 relationship-memory pivot (2026-04): the listener side writes one
# of these per harness session so the next voice_agent prompt can see
# "灯陪你呼吸了几次、你打断了几次、最后 stamina 恢复到哪". Not counted
# in summary.py's event_counts — it's a session roll-up, not a turn.
KIND_SESSION_SUMMARY = "session_summary"
KINDS = {
    KIND_CONVERSATION,
    KIND_FUNCTION_TOOL,
    KIND_FALLBACK_EXPRESSION,
    KIND_PLAYBACK,
    KIND_SESSION_SUMMARY,
}

SOURCES = {
    "voice_agent",
    "dashboard",
    "remote_control",
    "auto_expression",
    # Harness consumer on the Pi that maps FluxChi fatigue frames onto
    # lamp actions. Added 2026-04 so session_summary rollups have a
    # legitimate source string; everything else this process does still
    # goes through dashboard/remote_control paths that own their own
    # source values.
    "fluxchi_listener",
}

SESSION_SUMMARY_REASONS = {
    "ws_disconnect",  # harness websocket dropped (Mac / tunnel down)
    "shutdown",       # SIGTERM / KeyboardInterrupt at listener top-level
    "session_end",    # FluxChi backend pushed an explicit end-of-session frame
    "manual",         # CLI / test invocation
}

CONVERSATION_STYLES = {"excited", "caring", "worried", "sad"}

FUNCTION_TOOL_PHASES = {"invoke", "result"}
FUNCTION_TOOL_CALLERS = {"llm", "auto_expression"}
FALLBACK_EXPRESSION_STYLES = frozenset(EXPRESSION_STYLE_CHOICES)

PLAYBACK_ACTIONS = {
    "play",
    "startup",
    "shutdown_pose",
    "light_solid",
    "light_clear",
}
PLAYBACK_INITIATORS = {"dashboard", "remote_control"}

# Soft budget per STORAGE.md; a line over the limit is logged but still written.
MAX_EVENT_SIZE_BYTES = 2048
_CONVERSATION_TEXT_MAX = 2048
_CONVERSATION_TRUNCATE_SUFFIX = "\u2026[truncated]"
_FUNCTION_TOOL_ARGS_MAX_BYTES = 1024
_FILE_MODE = 0o600


class MemoryWriteError(RuntimeError):
    """Raised when a payload fails validation before hitting disk."""


def _dumps(obj: Any) -> str:
    # ``ensure_ascii=False`` keeps Chinese text readable in events.jsonl
    # without ballooning the byte count.  ``separators`` drops whitespace
    # which matters at 10 MiB rotation thresholds.
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _truncate_text(value: str, *, limit: int = _CONVERSATION_TEXT_MAX) -> str:
    if len(value) <= limit:
        return value
    keep = limit - len(_CONVERSATION_TRUNCATE_SUFFIX)
    if keep < 0:
        keep = 0
    return value[:keep] + _CONVERSATION_TRUNCATE_SUFFIX


def _truncate_tool_args(args: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(args)
    serialised = _dumps(payload).encode("utf-8")
    if len(serialised) <= _FUNCTION_TOOL_ARGS_MAX_BYTES:
        return payload
    # Drop the original structure entirely: we keep a marker plus the
    # measured size so auditors can tell *why* a tool call looks thin.
    return {"_truncated": True, "_original_size_bytes": len(serialised)}


class MemoryWriter:
    """Append-only JSONL writer rooted at ``<memory_root>/<user>/``.

    Instances are cheap: all filesystem state is re-opened inside the
    critical section so multi-process writers (agent, dashboard, CLI)
    can all construct their own writer without any cross-process
    bookkeeping beyond the shared ``.lock`` file.
    """

    def __init__(
        self,
        user_id: Optional[str] = None,
        *,
        root: Optional[Path] = None,
    ) -> None:
        self._user_id = resolve_user_id(user_id)
        if root is not None:
            self._user_dir = Path(root)
            self._user_dir.mkdir(parents=True, exist_ok=True)
            (self._user_dir / "sessions").mkdir(exist_ok=True)
            (self._user_dir / "archive").mkdir(exist_ok=True)
        else:
            self._user_dir = ensure_user_memory_root(user_id)
        self._events_path = self._user_dir / "events.jsonl"
        self._lock_path = self._user_dir / ".lock"

    # --- public surface ---------------------------------------------------

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def user_dir(self) -> Path:
        return self._user_dir

    @property
    def events_path(self) -> Path:
        return self._events_path

    def write_conversation(
        self,
        *,
        session_id: str,
        source: str,
        user_text: str,
        assistant_text: str,
        user_text_lang: Optional[str] = None,
        assistant_style: Optional[str] = None,
        turn_duration_ms: Optional[int] = None,
        model_provider: Optional[str] = None,
        model_name: Optional[str] = None,
        ts_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if assistant_style is not None and assistant_style not in CONVERSATION_STYLES:
            raise MemoryWriteError(
                f"invalid assistant_style={assistant_style!r}; "
                f"must be one of {sorted(CONVERSATION_STYLES)} or None"
            )
        payload: dict[str, Any] = {
            "payload_version": 1,
            "user_text": _truncate_text(user_text),
            "assistant_text": _truncate_text(assistant_text),
            "user_text_lang": user_text_lang,
            "assistant_style": assistant_style,
            "turn_duration_ms": turn_duration_ms,
            "model_provider": model_provider,
            "model_name": model_name,
        }
        return self._write(
            kind=KIND_CONVERSATION,
            source=source,
            session_id=session_id,
            payload=payload,
            ts_ms=ts_ms,
            event_id=event_id,
        )

    def write_function_tool(
        self,
        *,
        session_id: str,
        source: str,
        invoke_id: str,
        phase: str,
        tool_name: str,
        args: Mapping[str, Any],
        caller: str,
        duration_ms: Optional[int] = None,
        ok: Optional[bool] = None,
        error: Optional[str] = None,
        ts_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if phase not in FUNCTION_TOOL_PHASES:
            raise MemoryWriteError(
                f"invalid function_tool phase={phase!r}; "
                f"must be one of {sorted(FUNCTION_TOOL_PHASES)}"
            )
        if caller not in FUNCTION_TOOL_CALLERS:
            raise MemoryWriteError(
                f"invalid function_tool caller={caller!r}; "
                f"must be one of {sorted(FUNCTION_TOOL_CALLERS)}"
            )
        if not _ids.validate_invoke_id(invoke_id):
            raise MemoryWriteError(f"invalid invoke_id={invoke_id!r}")
        # For result phase ``ok`` is required; for invoke it is irrelevant.
        if phase == "result" and ok is None:
            raise MemoryWriteError("function_tool result events must set ok=True/False")
        payload: dict[str, Any] = {
            "payload_version": 1,
            "invoke_id": invoke_id,
            "phase": phase,
            "tool_name": tool_name,
            "args": _truncate_tool_args(args),
            "caller": caller,
        }
        if phase == "result":
            payload["duration_ms"] = duration_ms
            payload["ok"] = bool(ok)
            payload["error"] = error
        return self._write(
            kind=KIND_FUNCTION_TOOL,
            source=source,
            session_id=session_id,
            payload=payload,
            ts_ms=ts_ms,
            event_id=event_id,
        )

    def write_fallback_expression(
        self,
        *,
        session_id: str,
        source: str,
        style: str,
        trigger: str,
        linked_conversation_event_id: Optional[str] = None,
        ts_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if not style or not isinstance(style, str):
            raise MemoryWriteError(f"invalid fallback style={style!r}")
        if style not in FALLBACK_EXPRESSION_STYLES:
            raise MemoryWriteError(
                f"invalid fallback style={style!r}; "
                f"must be one of {sorted(FALLBACK_EXPRESSION_STYLES)}"
            )
        payload = {
            "payload_version": 1,
            "style": style,
            "trigger": trigger,
            "linked_conversation_event_id": linked_conversation_event_id,
        }
        return self._write(
            kind=KIND_FALLBACK_EXPRESSION,
            source=source,
            session_id=session_id,
            payload=payload,
            ts_ms=ts_ms,
            event_id=event_id,
        )

    def write_playback(
        self,
        *,
        session_id: str,
        source: str,
        action: str,
        initiator: str,
        recording_name: Optional[str] = None,
        rgb: Optional[Iterable[int]] = None,
        duration_ms: Optional[int] = None,
        ok: bool = True,
        error: Optional[str] = None,
        ts_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if action not in PLAYBACK_ACTIONS:
            raise MemoryWriteError(
                f"invalid playback action={action!r}; "
                f"must be one of {sorted(PLAYBACK_ACTIONS)}"
            )
        if initiator not in PLAYBACK_INITIATORS:
            # Explicit ban on voice_agent_tool: de-dup contract says
            # voice-agent hardware actions are carried by
            # ``function_tool`` only.  A writer that lets this through
            # would double-count into ``fallback_rate``.
            raise MemoryWriteError(
                f"invalid playback initiator={initiator!r}; "
                f"only {sorted(PLAYBACK_INITIATORS)} are accepted -- "
                "voice_agent_tool is explicitly excluded by SCHEMA.md"
            )
        rgb_list: Optional[list[int]] = None
        if rgb is not None:
            rgb_list = [int(c) for c in rgb]
            if len(rgb_list) != 3 or not all(0 <= c <= 255 for c in rgb_list):
                raise MemoryWriteError(f"invalid rgb={rgb_list!r}")
        payload = {
            "payload_version": 1,
            "action": action,
            "recording_name": recording_name,
            "rgb": rgb_list,
            "initiator": initiator,
            "duration_ms": duration_ms,
            "ok": bool(ok),
            "error": error,
        }
        return self._write(
            kind=KIND_PLAYBACK,
            source=source,
            session_id=session_id,
            payload=payload,
            ts_ms=ts_ms,
            event_id=event_id,
        )

    def write_session_summary(
        self,
        *,
        session_id: str,
        source: str,
        reason: str,
        level_counts: Mapping[str, int],
        breath_interrupts: int = 0,
        breath_completed: int = 0,
        manual_fallback_count: int = 0,
        final_stamina: Optional[float] = None,
        final_perclos: Optional[float] = None,
        duration_sec: Optional[float] = None,
        profile_name: Optional[str] = None,
        ts_ms: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a one-off session roll-up written by the FluxChi listener.

        This is deliberately structural (not free-form narrative) so a
        later summariser can compose the R4 "上次我陪你呼吸了 N 次" line
        deterministically. Narrative generation stays out of the writer
        — LIFECYCLE.md still pins ``summary.narrative`` to ``None`` in v0.
        """

        if reason not in SESSION_SUMMARY_REASONS:
            raise MemoryWriteError(
                f"invalid session_summary reason={reason!r}; "
                f"must be one of {sorted(SESSION_SUMMARY_REASONS)}"
            )
        # Normalise level_counts — the listener hands us a possibly-sparse
        # dict and we want the payload readable without .get() gymnastics.
        safe_levels: dict[str, int] = {}
        for name, count in (level_counts or {}).items():
            if not isinstance(name, str):
                raise MemoryWriteError(f"level_counts key must be str, got {type(name)}")
            try:
                safe_levels[name] = int(count)
            except (TypeError, ValueError) as exc:
                raise MemoryWriteError(f"level_counts[{name!r}] not int-coercible: {exc}")
            if safe_levels[name] < 0:
                raise MemoryWriteError(f"level_counts[{name!r}] must be >= 0")
        for fname, value in (
            ("breath_interrupts", breath_interrupts),
            ("breath_completed", breath_completed),
            ("manual_fallback_count", manual_fallback_count),
        ):
            if not isinstance(value, int) or value < 0:
                raise MemoryWriteError(f"{fname} must be a non-negative int, got {value!r}")
        payload: dict[str, Any] = {
            "payload_version": 1,
            "reason": reason,
            "profile_name": profile_name,
            "level_counts": safe_levels,
            "breath_interrupts": int(breath_interrupts),
            "breath_completed": int(breath_completed),
            "manual_fallback_count": int(manual_fallback_count),
            "final_stamina": None if final_stamina is None else float(final_stamina),
            "final_perclos": None if final_perclos is None else float(final_perclos),
            "duration_sec": None if duration_sec is None else float(duration_sec),
        }
        return self._write(
            kind=KIND_SESSION_SUMMARY,
            source=source,
            session_id=session_id,
            payload=payload,
            ts_ms=ts_ms,
            event_id=event_id,
        )

    def iter_events(self) -> Iterator[dict[str, Any]]:
        """Yield events from ``events.jsonl``, skipping a malformed tail.

        STORAGE.md mandates that readers tolerate a partially-written
        final line (SIGKILL mid-append).  We implement that by
        buffering the last line and validating it separately: earlier
        malformed lines are considered disk corruption and propagate
        as ``MemoryWriteError`` to force an fsck, but the last one
        degrades to a warning.
        """

        if not self._events_path.exists():
            return
        with self._events_path.open("r", encoding="utf-8", errors="strict") as fh:
            prev: Optional[str] = None
            for raw in fh:
                if prev is not None:
                    yield _parse_or_raise(prev)
                prev = raw
            if prev is None:
                return
            stripped = prev.rstrip("\n")
            if not stripped:
                return
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError as exc:
                _logger.warning(
                    "skipping malformed trailing line in %s (crash tail?): %s",
                    self._events_path,
                    exc,
                )

    # --- internals --------------------------------------------------------

    def _write(
        self,
        *,
        kind: str,
        source: str,
        session_id: str,
        payload: Mapping[str, Any],
        ts_ms: Optional[int],
        event_id: Optional[str],
    ) -> dict[str, Any]:
        if self._user_id != DEFAULT_USER_ID:
            # Defensive: resolve_user_id already pins this but we guard
            # in case someone passes a pre-resolved override through a
            # future multi-user seam.
            raise MemoryWriteError(
                f"v0 only accepts user_id={DEFAULT_USER_ID!r}, got {self._user_id!r}"
            )
        if kind not in KINDS:
            raise MemoryWriteError(f"unknown kind={kind!r}")
        if source not in SOURCES:
            raise MemoryWriteError(f"unknown source={source!r}")
        if not _ids.validate_session_id(session_id):
            raise MemoryWriteError(f"malformed session_id={session_id!r}")
        if not payload or "payload_version" not in payload:
            raise MemoryWriteError("payload must be non-empty and carry payload_version")

        record: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "event_id": event_id or _ids.generate_event_id(),
            "ts_ms": ts_ms if ts_ms is not None else _ids.current_timestamp_ms(),
            "user_id": self._user_id,
            "session_id": session_id,
            "kind": kind,
            "source": source,
            "payload": dict(payload),
        }
        self._append_raw(record)
        return record

    def _append_raw(self, record: Mapping[str, Any]) -> None:
        line = _dumps(record) + "\n"
        data = line.encode("utf-8")
        if len(data) > MAX_EVENT_SIZE_BYTES:
            _logger.warning(
                "memory event exceeds %d byte budget: %d bytes (kind=%s event_id=%s)",
                MAX_EVENT_SIZE_BYTES,
                len(data),
                record.get("kind"),
                record.get("event_id"),
            )

        with self._locked():
            fd = os.open(
                self._events_path,
                os.O_RDWR | os.O_CREAT | os.O_APPEND,
                _FILE_MODE,
            )
            try:
                file_size = os.fstat(fd).st_size
                if file_size > 0 and os.pread(fd, 1, file_size - 1) != b"\n":
                    os.write(fd, b"\n")
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(
            self._lock_path,
            os.O_CREAT | os.O_RDWR,
            _FILE_MODE,
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _parse_or_raise(line: str) -> dict[str, Any]:
    stripped = line.rstrip("\n")
    if not stripped:
        # Blank line mid-file is abnormal; upstream can recover via fsck
        # but we surface it here so tests catch it.
        raise MemoryWriteError("unexpected blank line in events.jsonl")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise MemoryWriteError(
            f"malformed non-trailing line in events.jsonl: {exc}"
        ) from exc
