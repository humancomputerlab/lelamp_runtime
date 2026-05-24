"""Deprecated entry point — kept for backward compatibility.

``main.py`` was historically identical to ``smooth_animation.py``. The two
files diverged slightly once ``smooth_animation.py`` started bootstrapping
the motor-bus arbiter, so this module now forwards to ``smooth_animation``
instead of carrying a second copy.

Existing machines may have ``MODE_SCRIPT=main.py`` baked into their
systemd unit or ``pi5_all_in_one.sh`` prompt. Removing this file would
break those deployments, so we keep a thin shim that emits a
``DeprecationWarning`` and delegates.

New deployments should set ``MODE_SCRIPT=smooth_animation.py`` (the default
for ``scripts/pi5_all_in_one.sh`` and ``scripts/pi_setup_max.sh``). Once all
fielded lamps have been migrated this file can be removed.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "lelamp main.py is deprecated; use smooth_animation.py "
    "(set MODE_SCRIPT=smooth_animation.py in your systemd unit or .env)",
    DeprecationWarning,
    stacklevel=2,
)

from smooth_animation import (  # noqa: F401  (re-export for legacy importers)
    LeLamp,
    STARTUP_WARM_RGB,
    entrypoint,
)


if __name__ == "__main__":
    from livekit import agents

    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint, num_idle_processes=1)
    )
