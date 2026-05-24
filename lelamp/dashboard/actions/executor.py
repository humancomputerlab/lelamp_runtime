"""Serialized action execution for dashboard operators."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock, Thread
from typing import Callable

from lelamp.dashboard.runtime_bridge import DashboardActionResult
from lelamp.dashboard.state_store import DashboardStateStore


@dataclass(frozen=True)
class DashboardActionReceipt:
    ok: bool
    action_id: str
    state: str
    message: str
    error: str | None = None
    active_action: str | None = None


class DashboardActionExecutor:
    def __init__(self, store: DashboardStateStore) -> None:
        self._store = store
        self._lock = Lock()
        self._worker: Thread | None = None
        self._active_action: str | None = None

    def submit(
        self,
        action_id: str,
        callback: Callable[[], DashboardActionResult],
        *,
        section: str,
        success_patch: dict[str, object],
    ) -> DashboardActionReceipt:
        with self._lock:
            if self._active_action is not None:
                return DashboardActionReceipt(
                    False,
                    action_id,
                    "busy",
                    "Another action is already running.",
                    error="busy",
                    active_action=self._active_action,
                )

            self._active_action = action_id
            self._store.set_system(status="running", active_action=action_id)
            self._store.patch(section, {"status": "running", "last_result": None})

            worker = Thread(
                target=self._run_action,
                args=(action_id, callback, section, success_patch),
                daemon=True,
            )
            self._worker = worker
            worker.start()

        return DashboardActionReceipt(
            True,
            action_id,
            "running",
            f"{action_id} started.",
            active_action=action_id,
        )

    def current_action(self) -> str | None:
        with self._lock:
            return self._active_action

    def is_busy(self) -> bool:
        with self._lock:
            return self._active_action is not None

    def wait_for_idle(self, timeout: float | None = None) -> bool:
        with self._lock:
            worker = self._worker

        if worker is None:
            return True

        worker.join(timeout=timeout)
        return not worker.is_alive()

    def _run_action(
        self,
        action_id: str,
        callback: Callable[[], DashboardActionResult],
        section: str,
        success_patch: dict[str, object],
    ) -> None:
        system_status = "ready"
        section_patch: dict[str, object]
        error_payload: tuple[str, str, str, str] | None = None

        try:
            result = callback()
            if result.ok:
                section_patch = dict(success_patch, last_result=result.message)
            else:
                system_status = "error"
                section_patch = {"status": "error", "last_result": result.message}
                error_payload = (
                    f"action.{action_id}",
                    result.message,
                    section,
                    "error",
                )
        except Exception as exc:
            system_status = "error"
            message = str(exc)
            section_patch = {"status": "error", "last_result": message}
            error_payload = (
                f"action.{action_id}",
                message,
                section,
                "error",
            )

        with self._lock:
            self._active_action = None
            self._worker = None

            if error_payload is None:
                self._store.resolve_section_errors(section)
            else:
                self._store.record_error(*error_payload)

            self._store.patch(section, section_patch)
            self._store.reconcile_system(
                {"status": system_status, "active_action": None}
            )
