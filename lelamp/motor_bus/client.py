"""Motor bus client: transparent proxy with fallback to direct hardware.

Callers ask for an ``AnimationService``- or ``RGBService``-compatible object
via :func:`build_animation_service` / :func:`build_rgb_service`. These
factories probe the motor bus sentinel; if the agent's server is reachable
they return a proxy that forwards dispatches over HTTP. If the probe fails
they call the supplied ``fallback_factory`` to build a real service that owns
the hardware directly (the existing pre-arbiter behaviour).

The proxy objects are deliberately duck-typed to the subset of the
``AnimationService`` / ``RGBService`` interface that dashboard and
``remote_control`` rely on. They do **not** re-implement ``start()``/
``stop()`` semantics because the real service is already owned by the agent.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from .sentinel import SentinelInfo, read_live_sentinel

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 3.0
DEFAULT_PLAY_TIMEOUT_S = 120.0

REQUIRE_ANY = "any"
REQUIRE_MOTOR = "motor"
REQUIRE_RGB = "rgb"
_VALID_REQUIRES = frozenset({REQUIRE_ANY, REQUIRE_MOTOR, REQUIRE_RGB})

# Loopback traffic must never be sent through a system HTTP proxy. Build a
# dedicated opener with an empty ProxyHandler so requests to 127.0.0.1 always
# hit the local motor bus directly regardless of HTTP_PROXY / http_proxy env.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class MotorBusClientError(RuntimeError):
    """Raised when the proxy fails to reach or dispatch to the motor bus."""


def _post_json(
    base_url: str,
    path: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise MotorBusClientError(f"HTTP {exc.code} on {path}: {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise MotorBusClientError(f"cannot reach motor bus {url}: {exc}") from exc


def _get_json(base_url: str, path: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    try:
        with _opener.open(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise MotorBusClientError(f"HTTP {exc.code} on {path}: {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise MotorBusClientError(f"cannot reach motor bus {url}: {exc}") from exc


class ProxyAnimationService:
    """Minimal ``AnimationService``-shaped facade that dispatches over HTTP.

    Implements just enough to satisfy dashboard ``runtime_bridge.play`` and
    ``runtime_bridge.list_recordings`` plus ``remote_control._handle_play``.
    Staged startup/shutdown choreographies that bypass ``AnimationService``
    are intentionally not supported.
    """

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def start(self) -> None:
        # The real service is owned by the agent process. Nothing to do here;
        # kept for API compatibility with ``AnimationService.start()``.
        pass

    def stop(self, timeout: float = 5.0) -> None:
        pass

    def dispatch(self, event_type: str, payload: Any) -> None:
        if event_type == "play":
            _post_json(self._base_url, "/motor/play", {"recording_name": payload}, timeout=self._timeout)
        elif event_type == "frames":
            frames = [dict(frame) for frame in payload]
            _post_json(
                self._base_url,
                "/motor/frames",
                {"frames": frames},
                timeout=self._timeout,
            )
        elif event_type == "startup":
            _post_json(
                self._base_url,
                "/motor/startup",
                {"recording_name": payload},
                timeout=self._timeout,
            )
        else:
            raise MotorBusClientError(f"unsupported motor event_type: {event_type!r}")

    def get_available_recordings(self) -> list[str]:
        data = _get_json(self._base_url, "/motor/recordings", timeout=self._timeout)
        recordings = data.get("recordings", [])
        return [str(x) for x in recordings]

    def wait_until_playback_complete(self, timeout: float | None = None) -> bool:
        # Bridge to the server-side ``/motor/wait_complete`` so the caller's
        # busy lock stays held until the real AnimationService playback_done
        # event fires. ``timeout=None`` collapses to a generous default that
        # matches dashboard behaviour prior to the proxy.
        effective = DEFAULT_PLAY_TIMEOUT_S if timeout is None else float(timeout)
        try:
            data = _post_json(
                self._base_url,
                "/motor/wait_complete",
                {"timeout": effective},
                # Give the HTTP round-trip a small buffer beyond the server
                # timeout so we don't spuriously error out when playback just
                # finished. Fall back to a floor of 2s for very short timeouts.
                timeout=max(effective + 2.0, 2.0),
            )
        except MotorBusClientError:
            return False
        return bool(data.get("done"))


class ProxyRGBService:
    """Minimal ``RGBService`` facade over HTTP."""

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def start(self) -> None:
        pass

    def stop(self, timeout: float = 5.0) -> None:
        pass

    def handle_event(self, event_type: str, payload: Any) -> None:
        if event_type == "solid":
            red, green, blue = _split_rgb(payload)
            _post_json(
                self._base_url,
                "/rgb/solid",
                {"red": red, "green": green, "blue": blue},
                timeout=self._timeout,
            )
        elif event_type == "paint":
            colors = [list(c) for c in payload]
            _post_json(self._base_url, "/rgb/paint", {"colors": colors}, timeout=self._timeout)
        else:
            raise MotorBusClientError(f"unsupported rgb event_type: {event_type!r}")

    def dispatch(self, event_type: str, payload: Any) -> None:
        self.handle_event(event_type, payload)

    def clear(self) -> None:
        _post_json(self._base_url, "/rgb/clear", timeout=self._timeout)


def _split_rgb(payload: Any) -> tuple[int, int, int]:
    if isinstance(payload, (list, tuple)) and len(payload) == 3:
        return int(payload[0]), int(payload[1]), int(payload[2])
    raise MotorBusClientError(f"solid payload must be 3-tuple, got {payload!r}")


def _probe_health(sentinel: SentinelInfo, *, timeout: float) -> Optional[dict[str, Any]]:
    """Return the parsed ``/health`` body or ``None`` if the server is unreachable."""
    try:
        data = _get_json(sentinel.base_url, "/health", timeout=timeout)
    except MotorBusClientError:
        return None
    if not bool(data.get("ok")):
        return None
    return data


def _health_satisfies(health: dict[str, Any], require: str) -> bool:
    if require == REQUIRE_ANY:
        return True
    if require == REQUIRE_MOTOR:
        # ``motor_ok`` is authoritative on the new server. Fall back to
        # ``animation_error is None`` for older servers that only returned
        # ``ok`` + ``animation_error``.
        if "motor_ok" in health:
            return bool(health["motor_ok"])
        return health.get("animation_error") is None
    if require == REQUIRE_RGB:
        if "rgb_ok" in health:
            return bool(health["rgb_ok"])
        # Legacy field name from the H0 server.
        return bool(health.get("rgb_available", False))
    raise ValueError(f"unknown require={require!r}")


def current_sentinel(
    *,
    require: str = REQUIRE_ANY,
    probe_timeout: float = 1.0,
) -> Optional[SentinelInfo]:
    """Return the live sentinel iff ``/health`` responds and the requested domain is healthy.

    ``require`` selects which hardware domain must be usable via the proxy:
      - ``"any"``: server reachable, no per-domain guarantee.
      - ``"motor"``: server also reports motion as usable.
      - ``"rgb"``: server also reports RGB as usable.

    When the probe fails for the requested domain, this returns ``None`` so
    callers fall through to their direct-hardware fallback path. This keeps
    the pre-arbiter self-recovery behaviour (the agent process failed to open
    the serial port, so let another process try).
    """
    if require not in _VALID_REQUIRES:
        raise ValueError(f"unknown require={require!r}")
    sentinel = read_live_sentinel()
    if sentinel is None:
        return None
    health = _probe_health(sentinel, timeout=probe_timeout)
    if health is None:
        return None
    if not _health_satisfies(health, require):
        return None
    return sentinel


def build_animation_service(
    fallback_factory: Callable[[], Any],
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    probe_timeout: float = 1.0,
) -> Any:
    """Return a motor proxy when the agent's animation service is usable.

    Falls back to ``fallback_factory()`` when the sentinel is missing, the
    server is unreachable, or the server reports ``motor_ok = False`` (which
    means the agent process owns Python but never successfully opened the
    serial port, so a direct-hardware retry may still succeed).
    """
    sentinel = current_sentinel(require=REQUIRE_MOTOR, probe_timeout=probe_timeout)
    if sentinel is not None:
        logger.debug("routing animation service via motor bus at %s", sentinel.base_url)
        return ProxyAnimationService(sentinel.base_url, timeout=timeout)
    return fallback_factory()


def build_rgb_service(
    fallback_factory: Callable[[], Any],
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    probe_timeout: float = 1.0,
) -> Any:
    """Return an RGB proxy when the agent's RGB service is reachable.

    Falls back to ``fallback_factory()`` when the sentinel is missing, the
    server is unreachable, or the server reports ``rgb_ok = False``.
    """
    sentinel = current_sentinel(require=REQUIRE_RGB, probe_timeout=probe_timeout)
    if sentinel is not None:
        logger.debug("routing rgb service via motor bus at %s", sentinel.base_url)
        return ProxyRGBService(sentinel.base_url, timeout=timeout)
    return fallback_factory()
