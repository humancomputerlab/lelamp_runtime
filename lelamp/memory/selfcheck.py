"""Writer-side self-check: backfill summaries + rebuild recent_index.

Contract anchors:

* ``LIFECYCLE.md#\u5bb9\u9519\u4e0e\u6062\u590d`` -- summary backfill is writer-only
* ``LIFECYCLE.md#\u91cd\u5efa\u65f6\u673a`` -- index rebuild happens under flock
  before any new meta.json is written
* ``PROMPT_INTEGRATION.md#Reader \u7684\u526f\u4f5c\u7528\u5951\u7ea6`` -- readers never
  rebuild; all "repair disk" paths live here

The flow at every writer start:

1.  Acquire the global ``.lock``.
2.  Walk ``sessions/*.meta.json``; for each with a missing
    ``summary.json`` whose owning pid is not alive (manual sessions
    always match the "not alive" branch because their pid is
    ``null``), compute + write a summary.
3.  If ``recent_index.json`` is missing, unreadable, or older than
    ``events.jsonl``, rebuild it.
4.  Release the lock.

All steps are idempotent so a writer that crashes halfway through
and restarts simply re-runs the same logic -- no "partially repaired"
state is possible.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import ids as _ids
from .recent_index import rebuild_recent_index, recent_index_path
from .session import _flock, _pid_alive
from .summary import compute_and_write_summary, summary_path
from .writer import MemoryWriter

_logger = logging.getLogger(__name__)


@dataclass
class SelfCheckReport:
    """Observable outcome of a self-check pass (used by tests + logs)."""

    summaries_backfilled: list[str] = field(default_factory=list)
    recent_index_rebuilt: bool = False
    stale_reason: Optional[str] = None


def _load_json(path: Path) -> Optional[dict]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _backfill_missing_summaries(writer: MemoryWriter) -> list[str]:
    backfilled: list[str] = []
    sessions_dir = writer.user_dir / "sessions"
    if not sessions_dir.exists():
        return backfilled
    for meta_path in sorted(sessions_dir.glob("*.meta.json")):
        meta = _load_json(meta_path)
        if not meta:
            continue
        session_id = meta.get("session_id")
        if not isinstance(session_id, str):
            continue
        if summary_path(writer, session_id).exists():
            continue
        pid = meta.get("pid")
        # Live session: its own writer will close the session; selfcheck
        # must never try to backfill an in-flight summary.  Manual
        # sessions land here naturally because their pid is ``null``.
        if isinstance(pid, int) and _pid_alive(pid):
            continue
        start_ts_ms = int(meta.get("start_ts_ms") or 0) or None
        try:
            compute_and_write_summary(
                writer,
                session_id,
                start_ts_ms=start_ts_ms,
            )
        except Exception:  # pragma: no cover - purely defensive
            _logger.exception(
                "memory self-check: failed to backfill summary for %s",
                session_id,
            )
            continue
        backfilled.append(session_id)
    return backfilled


def _recent_index_stale(writer: MemoryWriter) -> tuple[bool, Optional[str]]:
    path = recent_index_path(writer)
    if not path.exists():
        return True, "missing"
    idx = _load_json(path)
    if not idx:
        return True, "unreadable"
    built_at = idx.get("built_at_ms")
    if not isinstance(built_at, int):
        return True, "no built_at_ms"
    events_path = writer.events_path
    if not events_path.exists():
        return False, None
    events_mtime_ms = int(events_path.stat().st_mtime * 1000)
    if events_mtime_ms > built_at:
        return True, f"events_mtime_ms={events_mtime_ms} > built_at_ms={built_at}"
    return False, None


def run_selfcheck(writer: MemoryWriter) -> SelfCheckReport:
    """Run backfill + index rebuild under the global flock.

    Returns a :class:`SelfCheckReport` so callers (integration,
    dashboards, tests) can observe what was done.  The function is
    safe to call repeatedly -- each step early-outs when nothing
    needs doing.
    """

    report = SelfCheckReport()
    lock_path = writer.user_dir / ".lock"
    with _flock(lock_path):
        report.summaries_backfilled = _backfill_missing_summaries(writer)
        stale, reason = _recent_index_stale(writer)
        # Backfilling a summary dirties the index, so we always
        # rebuild when anything was backfilled -- cheaper than
        # re-statting events.jsonl after every compute_summary.
        if stale or report.summaries_backfilled:
            if report.summaries_backfilled and not stale:
                reason = "summaries_backfilled"
            try:
                rebuild_recent_index(writer)
            except Exception:  # pragma: no cover - defensive corruption guard
                report.stale_reason = (
                    f"{reason}; rebuild_failed" if reason else "rebuild_failed"
                )
                _logger.exception(
                    "memory self-check: failed to rebuild recent_index",
                    extra={"reason": reason},
                )
            else:
                report.recent_index_rebuilt = True
                report.stale_reason = reason
    return report
