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
ARI_FACE_NAMES = {"thinking": "skeptical", "shy": "embarrassed", "smile": "happy"}


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
            "tablet_available": False,
            "tablet_page": None,
            "tablet_requested_page": None,
            "tablet_error": None,
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

    def set_tablet_page(self, page: str) -> bool:
        """Request one logical installed tablet page through the ROS sidecar."""
        if page not in {"Main", "Zoek_de_verschillen"}:
            return False
        data = self._post("/tablet", {"page": page})
        return bool(data and data.get("command_ok"))

    def has_capability(self, name: str) -> bool:
        return name in (self.snapshot().get("capabilities") or ())

    def _action(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        method: str = "post",
    ) -> dict[str, Any] | None:
        """Call an operator-action route without folding its reply into the state."""
        try:
            if method == "get":
                response = self._requests.get(f"{self.base_url}{path}", timeout=timeout)
            else:
                response = self._requests.post(
                    f"{self.base_url}{path}",
                    json=payload or {},
                    timeout=timeout,
                )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except Exception as error:
            self._console_print(f"[ARI SOCIAL] {path} failed: {error}")
            return None

    def set_expression(self, expression: str, seconds: float = 6.0) -> bool:
        """Hold a face for a while; the sidecar's own face logic resumes after.

        Takes RoboBOND names (happy, thinking, shy, ...); ARI's face calls a
        few of them differently.
        """
        face = ARI_FACE_NAMES.get(expression, expression)
        if self.has_capability("expression_override"):
            data = self._action("/expression", {"expression": face, "seconds": seconds})
            if data and data.get("ok"):
                return True
        # Explicit overrides need a bounded lifetime. Without the sidecar a
        # plain RoboBOND expression could remain stuck after STOP or an emote.
        return False

    def clear_expression(self) -> None:
        if self.has_capability("expression_override"):
            self._action("/expression", {"expression": None, "seconds": 0})

    def head(self, pan: float, tilt: float, duration: float = 0.5) -> bool:
        if not self.has_capability("head"):
            return False
        data = self._action("/head", {"pan": pan, "tilt": tilt, "duration": duration})
        return bool(data and data.get("ok"))

    def turn(self, degrees: float) -> bool:
        if not self.has_capability("turn"):
            return False
        timeout = abs(degrees) / 25.0 + 8.0
        data = self._action("/turn", {"degrees": degrees}, timeout=timeout)
        return bool(data and data.get("ok"))

    def drive(self, meters: float) -> bool:
        if not self.has_capability("drive"):
            return False
        timeout = abs(meters) / 0.1 + 8.0
        data = self._action("/drive", {"meters": meters}, timeout=timeout)
        return bool(data and data.get("ok"))

    def music(self, on: bool) -> bool:
        if not self.has_capability("music"):
            return False
        data = self._action("/music", {"on": bool(on)})
        return bool(data and data.get("ok"))

    def volume(self, percent: float | None = None) -> int | None:
        if not self.has_capability("volume"):
            return None
        if percent is None:
            data = self._action("/volume", method="get")
        else:
            data = self._action("/volume", {"percent": percent})
        return data.get("percent") if data else None

    def nearest(self) -> float | None:
        """Distance to the closest thing ahead, used to notice an offered hand."""
        if not self.has_capability("nearest"):
            return None
        data = self._action("/nearest", method="get", timeout=1.5)
        distance = data.get("distance") if data else None
        return float(distance) if isinstance(distance, (int, float)) else None

    def stop_actions(self) -> None:
        if self.has_capability("stop_actions"):
            self._action("/actions/stop")
        else:
            self.stop_movement()
            self.clear_expression()

    def close(self) -> None:
        self._stop.set()
        self.set_speaking(False)
        self.stop_actions()
        self.stop_movement()
        self.set_tablet_page("Main")
        self.set_conversation_active(False)
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)
        if self._owns_request_session:
            self._requests.close()
