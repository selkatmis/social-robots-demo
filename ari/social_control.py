"""Laptop client for ARI's optional ROS social-control sidecar."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import requests


CONTROL_PORT = 7343
ROBOND_PORT = 7342
REQUEST_TIMEOUT_SECONDS = 3.0
STATUS_POLL_SECONDS = 1.0


class ARISocialClient:
    """Fail-safe HTTP client; conversation speech never depends on this service."""

    def __init__(
        self,
        host: str,
        port: int = CONTROL_PORT,
        *,
        robond_port: int = ROBOND_PORT,
        request_session: Any = requests,
        expression_fallback: Callable[[str], None] | None = None,
        console_print: Callable[..., None] = print,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.robot_health_url = f"http://{host}:{robond_port}/health"
        self._owns_request_session = request_session is requests
        if self._owns_request_session:
            session = requests.Session()
            # Robot IPs are local-LAN endpoints. Environment proxy settings can
            # otherwise treat port 7342 and 7343 differently on Windows.
            session.trust_env = False
            self._requests = session
        else:
            self._requests = request_session
        self._expression_fallback = expression_fallback
        self._console_print = console_print
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._warned_unavailable = False
        self._conversation_active = False
        self._state: dict[str, Any] = {
            "control_connected": False,
            "control_error": None,
            "robot_connected": False,
            "attention_mode": "UNKNOWN",
            "expression": "unknown",
            "expression_command": "unknown",
            "face_stable": False,
            "movement_available": False,
            "movement_active": False,
            "obstacle_sensor": "none",
            "attention_target": None,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _update(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)

    def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        try:
            response = self._requests.post(
                f"{self.base_url}{path}",
                json=payload or {},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("control response is not an object")
            self._record_success(data)
            return data
        except Exception as error:
            self._record_failure(error)
            return None

    def refresh(self) -> dict[str, Any]:
        try:
            response = self._requests.get(
                f"{self.base_url}/state",
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("control state is not an object")
            self._record_success(data)
        except Exception as error:
            self._record_failure(error)
        try:
            response = self._requests.get(
                self.robot_health_url,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            self._update(robot_connected=bool(response.json().get("ok", True)))
        except Exception:
            self._update(robot_connected=False)
        return self.snapshot()

    def _record_success(self, data: dict[str, Any]) -> None:
        mapped = dict(data)
        mapped["control_connected"] = True
        mapped["control_error"] = None
        if "policy" in data:
            mapped["attention_mode"] = data["policy"]
        self._update(**mapped)
        self._warned_unavailable = False

    def _record_failure(self, error: Exception) -> None:
        self._update(
            control_connected=False,
            control_error=str(error),
            movement_available=False,
            movement_active=False,
        )
        if not self._warned_unavailable:
            self._console_print(
                "[ARI SOCIAL] Optional ROS control sidecar unavailable at "
                f"{self.base_url}: {error}"
            )
            self._warned_unavailable = True

    def start_monitoring(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor,
            name="ari-social-status",
            daemon=True,
        )
        self._monitor_thread.start()

    def _monitor(self) -> None:
        while not self._stop.wait(STATUS_POLL_SECONDS):
            self.refresh()

    def set_conversation_active(self, active: bool) -> bool:
        self._conversation_active = bool(active)
        data = self._post("/mode", {"conversation_active": bool(active)})
        if data is None:
            self._fallback_expression("neutral")
            return False
        return bool(data.get("attention_command_ok", data.get("ok")))

    def set_speaking(self, speaking: bool) -> bool:
        data = self._post("/speaking", {"speaking": bool(speaking)})
        if data is None:
            expression = "happy" if speaking else "neutral"
            self._fallback_expression(expression)
            return False
        return bool(data.get("ok"))

    def _fallback_expression(self, expression: str) -> None:
        if self._expression_fallback is None:
            return
        try:
            self._expression_fallback(expression)
            # RoboBOND accepted the command, but without the ROS observer we
            # cannot claim that this is the currently displayed expression.
            self._update(expression_command=expression)
        except Exception as error:
            self._console_print(f"[ARI EXPRESSION] Fallback failed: {error}")

    def move(self, action: str) -> bool:
        if action not in {"forward", "back", "left", "right", "stop"}:
            return False
        data = self._post("/move", {"action": action})
        return bool(data and data.get("command_ok", data.get("ok")))

    def stop_movement(self) -> None:
        self._post("/move", {"action": "stop"})

    def close(self) -> None:
        self._stop.set()
        self.set_speaking(False)
        self.stop_movement()
        self.set_conversation_active(False)
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)
        if self._owns_request_session:
            self._requests.close()
