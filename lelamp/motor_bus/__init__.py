"""Motor bus arbiter: single-owner coordination for /dev/ttyACM0 and /dev/leds0.

Three entry points can write to motor/LED hardware:

1. Agent process (``smooth_animation.py console``), long-lived, systemd-managed.
   Holds ``/dev/ttyACM0`` and ``/dev/leds0`` for the lifetime of the process.
2. Dashboard (``uv run -m lelamp.dashboard.api``). Historically builds a fresh
   ``AnimationService`` per action, which re-opens the serial port.
3. Remote control CLI (``uv run -m lelamp.remote_control play ...``). Transient
   per-invocation, also re-opens the serial port.

Without arbitration they collide: when the agent is alive it already owns the
serial port, so any AnimationService that dashboard or CLI try to ``start()``
will fail to ``robot.connect()``.

H0 solution (this module):
  - Agent process starts a loopback FastAPI server (``MotorBusServer``) that
    proxies its long-lived services.
  - Agent writes a sentinel file (``SENTINEL_PATH``) containing ``pid`` +
    ``base_url`` so other processes can discover it.
  - Dashboard and CLI check the sentinel before touching hardware directly:
      * sentinel present + pid alive + ``/health`` reachable → route via proxy
      * otherwise → fall back to the existing direct-hardware code path

H0 only proxies motion ``play``/``startup`` dispatches and RGB ``solid``/
``paint``/``clear``. Staged startup/shutdown choreographies that bypass
``AnimationService`` (direct ``robot.bus.write("Torque_Enable", ...)`` in
``remote_control._handle_startup`` / ``_handle_shutdown``) are **not** in
scope here; the proxy surface keeps them as no-agent-only commands.
"""

from __future__ import annotations

from .sentinel import (
    DEFAULT_SENTINEL_PATH,
    SentinelInfo,
    read_live_sentinel,
    read_sentinel,
    remove_sentinel,
    sentinel_path,
    write_sentinel,
)

__all__ = [
    "DEFAULT_SENTINEL_PATH",
    "SentinelInfo",
    "read_live_sentinel",
    "read_sentinel",
    "remove_sentinel",
    "sentinel_path",
    "write_sentinel",
]
