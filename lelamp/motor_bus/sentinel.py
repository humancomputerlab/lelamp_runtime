"""Sentinel file: advertises a live motor bus server to other processes.

A process that owns the motor and RGB hardware (the voice agent) writes this
file at startup and removes it at shutdown. Other processes (dashboard, CLI)
read the file to decide whether to proxy actions over HTTP or fall through to
direct hardware access.

The sentinel lives at ``/tmp/lelamp-motor-bus.json`` by default and can be
overridden via ``LELAMP_MOTOR_BUS_SENTINEL``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

DEFAULT_SENTINEL_PATH = "/tmp/lelamp-motor-bus.json"
SENTINEL_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SentinelInfo:
    pid: int
    port: int
    base_url: str
    started_at_ms: int
    version: int = SENTINEL_SCHEMA_VERSION


def sentinel_path() -> Path:
    return Path(os.getenv("LELAMP_MOTOR_BUS_SENTINEL", DEFAULT_SENTINEL_PATH))


def write_sentinel(info: SentinelInfo) -> Path:
    path = sentinel_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(info)))
    tmp.replace(path)
    return path


def read_sentinel() -> Optional[SentinelInfo]:
    path = sentinel_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return SentinelInfo(
            pid=int(data["pid"]),
            port=int(data["port"]),
            base_url=str(data["base_url"]),
            started_at_ms=int(data["started_at_ms"]),
            version=int(data.get("version", 0)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still "alive" from our POV.
        return True
    except OSError:
        return False
    return True


def read_live_sentinel() -> Optional[SentinelInfo]:
    """Return the sentinel only if the process it advertises is still running.

    Callers should treat ``None`` as "no motor bus available; fall back to
    direct hardware access".
    """
    info = read_sentinel()
    if info is None:
        return None
    if info.version != SENTINEL_SCHEMA_VERSION:
        return None
    if not is_process_alive(info.pid):
        return None
    return info


def remove_sentinel() -> None:
    path = sentinel_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
