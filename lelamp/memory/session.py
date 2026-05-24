"""Session lifecycle: meta.json two-phase writes + attach_or_create.

Contract anchors:

* ``LIFECYCLE.md#session \u7684\u5b9a\u4e49`` -- session = process lifetime
* ``LIFECYCLE.md#*.meta.json`` -- agent two-phase, manual one-phase
* ``LIFECYCLE.md#\u5f52\u5c5e\u5224\u6d3b\u7684\u5c0f\u5de5\u5177\u51fd\u6570`` -- attach_or_create spec
* ``LIFECYCLE.md#\u6240\u6709\u6062\u590d\u8def\u5f84\u53ef\u91cd\u5165\u5e42\u7b49`` -- idempotency

Only the session's owning writer mutates its meta.json.  The self-check
flow (see :mod:`lelamp.memory.selfcheck`) backfills **summary.json only**;
it never touches meta.json.  This module enforces that by exposing
:func:`write_meta_phase1` and :func:`write_meta_phase2` but no
"repair meta" entry point.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional
from contextlib import contextmanager
import fcntl

from . import ids as _ids
from .writer import MemoryWriter, MemoryWriteError

_logger = logging.getLogger(__name__)

META_SCHEMA = "lelamp.memory.v0.session_meta"
STANDALONE_SOURCE = "standalone_writer"
_FILE_MODE = 0o600
_GIT_REF_TIMEOUT_S = 0.5


class SessionError(RuntimeError):
    """Raised for session-level contract violations."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _local_timezone_name() -> str:
    """Best-effort IANA timezone name (LIFECYCLE.md§timezone)."""

    try:
        p = Path("/etc/timezone")
        if p.exists():
            value = p.read_text(encoding="utf-8").strip()
            if value:
                return value
    except OSError:
        pass
    try:
        link = os.readlink("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in link:
            return link.split(marker, 1)[1]
    except (OSError, ValueError):
        pass
    return time.tzname[0] if time.tzname and time.tzname[0] else "UTC"


def _git_ref(cwd: Optional[Path] = None) -> Optional[str]:
    """Return ``git rev-parse --short HEAD`` or ``None``."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=_GIT_REF_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    ref = result.stdout.strip()
    return ref or None


def _repo_root_hint() -> Optional[Path]:
    """Best-effort repo root for default ``git_ref`` capture.

    The memory store lives under ``$HOME/.lelamp/...`` and is often
    outside the git checkout, so using ``writer.user_dir`` as ``cwd``
    makes ``git_ref`` silently fall back to ``None``.  Prefer the
    source tree containing this module; if that is not a checkout,
    fall back to searching upward from the current working directory.
    """

    seen: set[Path] = set()
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        for candidate in (base, *base.parents):
            if candidate in seen:
                continue
            seen.add(candidate)
            if (candidate / ".git").exists():
                return candidate
    return None


def _pid_alive(pid: int) -> bool:
    """Return ``True`` iff ``pid`` corresponds to a live process we can see.

    ``os.kill(pid, 0)`` is the POSIX canonical liveness probe.  A
    ``PermissionError`` means the process exists but belongs to another
    user -- still counts as alive for attach_or_create purposes (a
    hypothetical dashboard running under another UID must not steal
    the agent session).
    """

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _atomic_write_json(target: Path, data: Mapping[str, Any]) -> None:
    """tmp+rename a JSON aggregate per STORAGE.md atomic contract."""

    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    tmp = target.with_suffix(target.suffix + ".tmp")
    # Atomic within the same filesystem; callers ensure target's parent exists.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)
    # Best-effort parent fsync so the rename survives crash-reboot.
    try:
        dir_fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


@contextmanager
def _flock(lock_path: Path) -> Iterator[None]:
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, _FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _sessions_dir(writer: MemoryWriter) -> Path:
    return writer.user_dir / "sessions"


def _meta_path(writer: MemoryWriter, session_id: str) -> Path:
    return _sessions_dir(writer) / f"{session_id}.meta.json"


def _allocate_session_id(writer: MemoryWriter, base_id: str) -> str:
    """Return a non-colliding session_id, appending ``-1`` / ``-2`` ...

    Must be called under the global flock.  Collision only happens if
    two session starts land in the same wall-clock second (pathological
    under systemd but cheap to handle; contract OQ-2).
    """

    sessions = _sessions_dir(writer)
    candidate = base_id
    suffix = 0
    while (sessions / f"{candidate}.meta.json").exists():
        suffix += 1
        candidate = f"{base_id}-{suffix}"
    return candidate


def _build_phase1_meta(
    *,
    session_id: str,
    user_id: str,
    start_ts_ms: int,
    start_iso: str,
    timezone_name: str,
    pid: Optional[int],
    git_ref: Optional[str],
    model_providers: Iterable[str],
    is_manual: bool,
) -> dict[str, Any]:
    flags: dict[str, Any] = {
        "motor_bus_enabled": None,
        "fluxchi_enabled": False,
    }
    if is_manual:
        flags["source"] = STANDALONE_SOURCE
    return {
        "schema": META_SCHEMA,
        "session_id": session_id,
        "user_id": user_id,
        "start_ts_ms": start_ts_ms,
        "start_ts_iso": start_iso,
        "timezone": timezone_name,
        "pid": pid,
        "git_ref": git_ref,
        "model_providers": list(model_providers),
        "flags": flags,
    }


def _write_meta_phase1_locked(
    writer: MemoryWriter,
    *,
    is_manual: bool,
    base_ts: datetime,
    model_providers: Iterable[str] = (),
    pid_override: Optional[int] = None,
    git_ref_override: Optional[str] = None,
) -> tuple[str, Path, int]:
    """Phase-1 meta write; caller owns the flock.

    Returns ``(session_id, meta_path, start_ts_ms)``.
    """

    _sessions_dir(writer).mkdir(exist_ok=True)
    base_id = _ids.generate_session_id(manual=is_manual, now=base_ts)
    session_id = _allocate_session_id(writer, base_id)

    start_ts_ms = int(base_ts.astimezone(timezone.utc).timestamp() * 1000)
    start_iso = base_ts.astimezone().isoformat()
    tz_name = _local_timezone_name()
    pid = None if is_manual else (pid_override if pid_override is not None else os.getpid())
    git_ref = git_ref_override

    meta = _build_phase1_meta(
        session_id=session_id,
        user_id=writer.user_id,
        start_ts_ms=start_ts_ms,
        start_iso=start_iso,
        timezone_name=tz_name,
        pid=pid,
        git_ref=git_ref,
        model_providers=model_providers,
        is_manual=is_manual,
    )
    meta_path = _meta_path(writer, session_id)
    _atomic_write_json(meta_path, meta)
    return session_id, meta_path, start_ts_ms


# ---------------------------------------------------------------------------
# SessionHandle
# ---------------------------------------------------------------------------


@dataclass
class SessionHandle:
    """Handle for the current process's session.

    ``is_owner`` flips to ``False`` when a writer *attaches* to an
    existing live agent session (scenario A): in that case the
    attaching process must not close the session's summary on exit --
    only the agent process that wrote phase 1 is allowed to finalize
    its own session.  This prevents dashboard / remote_control from
    accidentally racing the agent to summary.json.
    """

    session_id: str
    is_manual: bool
    writer: MemoryWriter
    meta_path: Path
    start_ts_ms: int
    is_owner: bool = True
    _closed: bool = field(default=False, repr=False)

    def set_motor_bus_enabled(self, enabled: Optional[bool]) -> None:
        """Phase-2 meta patch (agent session only).

        Accepts ``True`` / ``False`` / ``None`` -- the three-state
        contract from LIFECYCLE.md.  Manual sessions must leave the
        flag ``null`` forever, so this call errors loudly when invoked
        on one: passing the wrong handle into the wrong lane is a
        caller bug, not something to paper over.
        """

        if self.is_manual:
            raise SessionError(
                "manual sessions do not perform arbiter phase 2; "
                "flags.motor_bus_enabled stays null"
            )
        with _flock(self.writer.user_dir / ".lock"):
            with self.meta_path.open("r", encoding="utf-8") as fh:
                meta = json.load(fh)
            if meta.get("schema") != META_SCHEMA:
                raise SessionError(
                    f"refusing to patch foreign meta schema={meta.get('schema')!r}"
                )
            flags = dict(meta.get("flags") or {})
            flags["motor_bus_enabled"] = enabled
            meta["flags"] = flags
            _atomic_write_json(self.meta_path, meta)

    def close(self, *, end_ts_ms: Optional[int] = None) -> None:
        """Finalize the session: write summary + rebuild recent_index.

        Idempotent: repeat calls are no-ops.  Non-owner handles
        (attached dashboards / CLI) skip the summary + index writes
        entirely so only the agent process owns the close path.

        This method swallows writer-side exceptions to match the
        LIFECYCLE "memory must never kill runtime" rule -- a failed
        close is logged, and the next writer start will pick up the
        pieces via :mod:`lelamp.memory.selfcheck`.
        """

        if self._closed:
            return
        self._closed = True
        if not self.is_owner:
            return
        # Lazy import to avoid a module-level cycle with summary /
        # recent_index, both of which import from this module.
        from .summary import compute_and_write_summary
        from .recent_index import rebuild_recent_index

        try:
            compute_and_write_summary(
                self.writer,
                self.session_id,
                start_ts_ms=self.start_ts_ms,
                end_ts_ms=end_ts_ms,
            )
        except Exception:
            _logger.exception(
                "memory: compute_and_write_summary failed for %s",
                self.session_id,
            )
            return
        try:
            rebuild_recent_index(self.writer)
        except Exception:
            _logger.exception(
                "memory: rebuild_recent_index failed after closing %s",
                self.session_id,
            )

    @property
    def closed(self) -> bool:
        return self._closed


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def start_agent_session(
    writer: MemoryWriter,
    *,
    model_providers: Iterable[str] = (),
    now: Optional[datetime] = None,
    pid: Optional[int] = None,
    git_ref: Optional[str] = None,
) -> SessionHandle:
    """Write phase-1 meta for an agent session.

    This is the entry point that ``smooth_animation.py`` calls before
    ``MotorBusServer.start()`` runs.  ``flags.motor_bus_enabled`` is
    left at ``null`` until :meth:`SessionHandle.set_motor_bus_enabled`
    is invoked with the arbiter outcome.
    """

    base = now if now is not None else datetime.now().astimezone()
    resolved_git_ref = git_ref if git_ref is not None else _git_ref(cwd=_repo_root_hint())
    with _flock(writer.user_dir / ".lock"):
        session_id, meta_path, start_ts_ms = _write_meta_phase1_locked(
            writer,
            is_manual=False,
            base_ts=base,
            model_providers=model_providers,
            pid_override=pid,
            git_ref_override=resolved_git_ref,
        )
    return SessionHandle(
        session_id=session_id,
        is_manual=False,
        writer=writer,
        meta_path=meta_path,
        start_ts_ms=start_ts_ms,
    )


def _iter_meta_candidates(writer: MemoryWriter) -> list[dict[str, Any]]:
    """Load every ``sessions/*.meta.json`` sorted by ``start_ts_ms`` desc."""

    sessions = _sessions_dir(writer)
    if not sessions.exists():
        return []
    loaded: list[dict[str, Any]] = []
    for entry in sessions.glob("*.meta.json"):
        try:
            with entry.open("r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            _logger.warning("skipping unreadable meta %s: %s", entry, exc)
            continue
        if not isinstance(meta, dict):
            continue
        meta["_path"] = entry
        loaded.append(meta)
    loaded.sort(
        key=lambda m: int(m.get("start_ts_ms") or 0),
        reverse=True,
    )
    return loaded


def _find_live_agent_session(metas: Iterable[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """Return the newest meta whose owning agent process is alive."""

    for meta in metas:
        flags = meta.get("flags") or {}
        if flags.get("source") == STANDALONE_SOURCE:
            continue  # manual never eligible as attach target
        pid = meta.get("pid")
        if pid is None:
            continue
        if not isinstance(pid, int):
            continue
        if _pid_alive(pid):
            return meta
    return None


def attach_or_create_session(
    writer: MemoryWriter,
    *,
    now: Optional[datetime] = None,
) -> SessionHandle:
    """Attach to a live agent session or create a fresh manual one.

    Called by dashboard / remote_control writers before every event
    append.  The entire scan + create runs under the global flock so
    two concurrent ``remote_control`` processes cannot race into the
    same ``sess_manual_`` slot.
    """

    base = now if now is not None else datetime.now().astimezone()
    resolved_git_ref = _git_ref(cwd=_repo_root_hint())
    with _flock(writer.user_dir / ".lock"):
        metas = _iter_meta_candidates(writer)
        live = _find_live_agent_session(metas)
        if live is not None:
            # Attach: no meta write, handle is explicitly non-owner so
            # the attaching process won't race the agent at close().
            meta_path = live["_path"]
            return SessionHandle(
                session_id=live["session_id"],
                is_manual=False,
                writer=writer,
                meta_path=meta_path,
                start_ts_ms=int(live.get("start_ts_ms") or 0),
                is_owner=False,
            )
        session_id, meta_path, start_ts_ms = _write_meta_phase1_locked(
            writer,
            is_manual=True,
            base_ts=base,
            git_ref_override=resolved_git_ref,
        )
    return SessionHandle(
        session_id=session_id,
        is_manual=True,
        writer=writer,
        meta_path=meta_path,
        start_ts_ms=start_ts_ms,
        is_owner=True,
    )


def load_meta(writer: MemoryWriter, session_id: str) -> dict[str, Any]:
    """Load a persisted meta.json (test + reader helper)."""

    path = _meta_path(writer, session_id)
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)
