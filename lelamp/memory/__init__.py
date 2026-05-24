"""LeLamp H1 Memory v0.

File-first, per-user, auditable memory layer.  See
``docs/design/h1-memory-v0/`` for the full contract.  This package exposes
three layers:

* Roots & IDs   (``root``, ``ids``) -- directory and identifier contracts
* Writer        (``writer``, ``session``, ``summary``, ``recent_index``,
                 ``selfcheck``) -- the only entities that mutate memory
* Reader        (``reader``) -- pure-read prompt integration

Integration into ``smooth_animation.py`` / ``runtime_bridge.py`` /
``remote_control.py`` is intentionally out of scope for this commit; C0
ships as a pure library so it can be exercised in isolation before we
wire it into the runtime hot path.
"""

from .ids import (
    AGENT_SESSION_PREFIX,
    MANUAL_SESSION_PREFIX,
    SESSION_ID_RE,
    generate_event_id,
    generate_invoke_id,
    generate_session_id,
    is_manual_session,
)
from .reader import build_memory_header
from .runtime import AgentMemoryRuntime, bootstrap_agent_runtime
from .root import (
    DEFAULT_USER_ID,
    ensure_user_memory_root,
    memory_root,
    resolve_user_id,
    user_memory_root,
)

__all__ = [
    "AGENT_SESSION_PREFIX",
    "AgentMemoryRuntime",
    "DEFAULT_USER_ID",
    "MANUAL_SESSION_PREFIX",
    "SESSION_ID_RE",
    "build_memory_header",
    "bootstrap_agent_runtime",
    "ensure_user_memory_root",
    "generate_event_id",
    "generate_invoke_id",
    "generate_session_id",
    "is_manual_session",
    "memory_root",
    "resolve_user_id",
    "user_memory_root",
]
