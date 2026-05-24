"""Memory directory + user_id resolution.

Contract anchors (``docs/design/h1-memory-v0/STORAGE.md``):

* Default root is ``$HOME/.lelamp/memory/``.
* ``LELAMP_MEMORY_ROOT`` env var overrides the root (used by tests and by
  operators who want the memory store on an external mount).
* ``resolve_user_id()`` in v0 always returns ``"default"``.  Multi-user
  attribution is explicitly a non-goal -- see ``README.md#\u975e\u76ee\u6807``.
* All directories created by this module are ``0o700`` to match the
  "single-user Pi" threat model documented in ``STORAGE.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_USER_ID = "default"
_ROOT_ENV = "LELAMP_MEMORY_ROOT"
_DIR_MODE = 0o700


def memory_root() -> Path:
    """Return the memory root path, honouring ``LELAMP_MEMORY_ROOT``.

    The directory is **not** created here; callers that need it on disk
    should go through :func:`ensure_user_memory_root` so the per-user
    layout is materialised atomically.
    """

    override = os.environ.get(_ROOT_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".lelamp" / "memory"


def resolve_user_id(user_id: str | None = None) -> str:
    """Return the effective user_id.

    v0 pins this to ``DEFAULT_USER_ID`` regardless of input so any
    caller that prematurely plumbs a real user identifier still writes
    into the correct single-user tree.  When multi-user support lands
    (see ``OPEN_QUESTIONS.md``) this function is the only seam that
    needs to change.
    """

    if user_id is None:
        return DEFAULT_USER_ID
    return DEFAULT_USER_ID


def user_memory_root(user_id: str | None = None) -> Path:
    """Path to ``<root>/<user>/`` without touching the filesystem."""

    return memory_root() / resolve_user_id(user_id)


def ensure_user_memory_root(user_id: str | None = None) -> Path:
    """Create the per-user memory tree on demand and return its path.

    The tree is::

        <root>/<user>/
            sessions/
            archive/

    All directories are created with ``0o700``.  The call is idempotent;
    callers may invoke it on every writer start.
    """

    base = user_memory_root(user_id)
    for sub in (base, base / "sessions", base / "archive"):
        sub.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    return base
