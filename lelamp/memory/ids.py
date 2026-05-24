"""Identifier generators and format guards.

Contract anchors:

* ``LIFECYCLE.md#session_id \u751f\u6210`` -- session_id format
* ``SCHEMA.md#\u5171\u540c\u5b57\u6bb5`` -- event_id / invoke_id format

We stay inside the Python stdlib on purpose; the Pi's interpreter ships
with ``uuid`` and ``secrets`` and has no ULID library.  The SCHEMA
contract accepts "ULID \u6216 UUID \u5b57\u7b26\u4e32", so UUID4 hex is sufficient and
avoids pulling a new wheel onto the device.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

AGENT_SESSION_PREFIX = "sess_"
MANUAL_SESSION_PREFIX = "sess_manual_"

# ``sess_<YYYY-MM-DD>_<HH-MM-SS>`` or ``sess_manual_<YYYY-MM-DD>_<HH-MM-SS>``
# with an optional ``-<n>`` suffix reserved for same-second collision
# handling (LIFECYCLE.md scenario C + OPEN_QUESTIONS OQ-2).
SESSION_ID_RE = re.compile(
    r"^sess_(manual_)?"
    r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"
    r"(?:-\d+)?$"
)

_EVENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_INVOKE_ID_RE = re.compile(r"^inv_[0-9a-f]{32}$")


def generate_event_id() -> str:
    """Return a 32-hex event id."""

    return uuid.uuid4().hex


def generate_invoke_id() -> str:
    """Return an ``inv_<32-hex>`` invoke id.

    ``invoke_id`` pairs a ``function_tool`` event with its downstream
    ``playback`` events when the tool kicks off motor / RGB output.  The
    ``inv_`` prefix matches SCHEMA examples and makes log scanning easy.
    """

    return "inv_" + uuid.uuid4().hex


def generate_session_id(
    *,
    manual: bool = False,
    now: Optional[datetime] = None,
) -> str:
    """Generate a session id.

    :param manual: produce a ``sess_manual_*`` id when ``True``.  Agent
        sessions must set this to ``False`` to preserve the prompt-path
        filter contract (manual sessions never enter ``recent_index``).
    :param now: override the wall clock -- handy for deterministic
        tests.  Naive datetimes are assumed to be local time to match
        :meth:`datetime.now`'s default.
    """

    when = now if now is not None else datetime.now().astimezone()
    # When callers pass a UTC-aware datetime we honour that; the
    # filename format is purely cosmetic and does not carry timezone
    # information itself.
    if when.tzinfo is None:
        when = when.astimezone()
    date = when.strftime("%Y-%m-%d")
    time = when.strftime("%H-%M-%S")
    prefix = MANUAL_SESSION_PREFIX if manual else AGENT_SESSION_PREFIX
    return f"{prefix}{date}_{time}"


def is_manual_session(session_id: str) -> bool:
    """Return ``True`` iff ``session_id`` was produced by a standalone writer.

    This is the single chokepoint used by readers / summary aggregators
    to filter manual sessions out of the prompt path (``README.md``
    non-goals and ``PROMPT_INTEGRATION.md`` contract).
    """

    return session_id.startswith(MANUAL_SESSION_PREFIX)


def validate_event_id(event_id: str) -> bool:
    return bool(_EVENT_ID_RE.match(event_id))


def validate_invoke_id(invoke_id: str) -> bool:
    return bool(_INVOKE_ID_RE.match(invoke_id))


def validate_session_id(session_id: str) -> bool:
    return bool(SESSION_ID_RE.match(session_id))


def current_timestamp_ms(*, utc: bool = True) -> int:
    """Return an integer ms timestamp.

    Events persist ``ts_ms`` as UTC ms so cross-session windows compare
    cleanly; the human-readable session_id carries local time for
    operator convenience (``LIFECYCLE.md``).
    """

    if utc:
        return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    return int(datetime.now().astimezone().timestamp() * 1000)
