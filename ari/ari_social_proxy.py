"""Small ROS sidecar for ARI social attention, expressions and manual base control.

Run this on ARI alongside (not instead of) the existing RoboBOND proxy.  It
never publishes head or gaze targets and never touches the tablet.
"""

from __future__ import annotations

import argparse
import logging
import math
import signal
import threading
import time
from typing import Any

from flask import Flask, jsonify, request


# Adjustable behavior and safety parameters.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 7343
SIDECAR_VERSION = "ari-social-v0.3.4-brief-face-smile"
ATTENTION_SERVICE = "/attention_manager/set_policy"
ENABLE_NECK_SERVICE = "/gaze_manager/enable_neck"
FACE_TOPIC = "/humans/faces/tracked"
PERSON_TOPIC = "/humans/persons/tracked"
EXPRESSION_TOPIC = "/robot_face/expression"
CMD_VEL_TOPIC = "/mobile_base_controller/cmd_vel"
SCAN_TOPIC = "/scan"
DEPTH_TOPIC = "/torso_front_camera/depth/image_rect_raw"
EMERGENCY_TOPIC = "/power/is_emergency"

FACE_ACQUIRE_SECONDS = 0.8
FACE_LOSS_GRACE_SECONDS = 1.5
FACE_GREETING_SMILE_SECONDS = 2.5
TRACKING_MESSAGE_STALE_SECONDS = 1.0
TARGET_LOSS_GRACE_SECONDS = 2.0
EXPRESSION_REFRESH_SECONDS = 2.0
CONTROL_PERIOD_SECONDS = 0.05
MOVEMENT_DEADMAN_SECONDS = 0.6
SCAN_STALE_SECONDS = 1.0
DEPTH_FOV_DEGREES = 87.0
DEPTH_BAND_TOP_FRACTION = 0.4
DEPTH_BAND_BOTTOM_FRACTION = 0.6
DEPTH_MIN_VALID_METERS = 0.1
DEPTH_MAX_VALID_METERS = 10.0
DEPTH_CHECK_PERIOD_SECONDS = 0.1
FORWARD_CLEARANCE_METERS = 0.45
FORWARD_CONE_DEGREES = 20.0
MAX_FORWARD_METERS_PER_SECOND = 0.18
MAX_BACKWARD_METERS_PER_SECOND = 0.13
MAX_ANGULAR_RADIANS_PER_SECOND = 0.5
LINEAR_RAMP_PER_TICK = 0.015
ANGULAR_RAMP_PER_TICK = 0.045

DISABLED = 0
RANDOM = 1
IDLE_SOCIAL = 2
FOCUSED_SOCIAL = 3
POLICY_NAMES = {
    DISABLED: "DISABLED",
    RANDOM: "RANDOM",
    IDLE_SOCIAL: "IDLE_SOCIAL",
    FOCUSED_SOCIAL: "FOCUSED_SOCIAL",
}
MOVE_COMMANDS = {
    "forward": (MAX_FORWARD_METERS_PER_SECOND, 0.0),
    "back": (-MAX_BACKWARD_METERS_PER_SECOND, 0.0),
    "left": (0.0, MAX_ANGULAR_RADIANS_PER_SECOND),
    "right": (0.0, -MAX_ANGULAR_RADIANS_PER_SECOND),
    "stop": (0.0, 0.0),
}

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class FaceHysteresis:
    """Require a stable acquisition and tolerate brief tracking gaps."""

    def __init__(
        self,
        acquire_seconds: float = FACE_ACQUIRE_SECONDS,
        loss_grace_seconds: float = FACE_LOSS_GRACE_SECONDS,
    ) -> None:
        self.acquire_seconds = acquire_seconds
        self.loss_grace_seconds = loss_grace_seconds
        self.first_seen_at: float | None = None
        self.last_seen_at: float | None = None
        self.stable = False

    def update(self, visible: bool, now: float) -> bool:
        if visible:
            self.last_seen_at = now
            if self.first_seen_at is None:
                self.first_seen_at = now
            if now - self.first_seen_at >= self.acquire_seconds:
                self.stable = True
            return self.stable

        self.first_seen_at = None
        if (
            self.stable
            and self.last_seen_at is not None
            and now - self.last_seen_at < self.loss_grace_seconds
        ):
            return True
        self.stable = False
        return False


class FaceSmilePulse:
    """Smile once when a stable face appears, then return to neutral."""

    def __init__(self, duration_seconds: float = FACE_GREETING_SMILE_SECONDS) -> None:
        self.duration_seconds = duration_seconds
        self.was_stable = False
        self.active_until = 0.0

    def update(self, stable_face: bool, now: float) -> bool:
        if not stable_face:
            self.was_stable = False
            self.active_until = 0.0
            return False
        if not self.was_stable:
            self.active_until = now + self.duration_seconds
        self.was_stable = True
        return now < self.active_until

    def reset(self) -> None:
        self.was_stable = False
        self.active_until = 0.0


def desired_expression(
    conversation_active: bool,
    speaking: bool,
    face_smile_active: bool,
) -> str:
    """Return the only expression allowed for the current social state."""
    if conversation_active:
        return "happy" if speaking else "neutral"
    return "happy" if face_smile_active else "neutral"


class MovementDeadman:
    """Pure command timeout used by the ROS velocity loop and unit tests."""

    def __init__(self, timeout_seconds: float = MOVEMENT_DEADMAN_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds
        self.linear = 0.0
        self.angular = 0.0
        self.until = 0.0

    def set(self, linear: float, angular: float, now: float) -> None:
        self.linear = linear
        self.angular = angular
        self.until = now + self.timeout_seconds if (linear or angular) else 0.0

    def stop(self) -> None:
        self.linear = 0.0
        self.angular = 0.0
        self.until = 0.0

    def target(self, now: float, available: bool) -> tuple[float, float]:
        if not available or now >= self.until:
            return 0.0, 0.0
        return self.linear, self.angular

    def active(self, now: float) -> bool:
        return now < self.until and bool(self.linear or self.angular)


class ARISocialROSBridge:
    """Own the sidecar's small, explicit ROS surface."""

    def __init__(self) -> None:
        import rospy
        import tf
        from geometry_msgs.msg import Twist
        from hri_msgs.msg import Expression, IdsList
        from sensor_msgs.msg import Image, LaserScan
        from std_msgs.msg import Bool
        from std_srvs.srv import Empty

        try:
            import numpy as np
        except ImportError:
            np = None
            LOGGER.warning(
                "numpy is unavailable; torso depth obstacle checks are disabled"
            )

        try:
            from attention_manager_msgs.srv import SetPolicy
        except ImportError:
            # PAL's 23.12 documentation uses this package name, while the
            # physically inspected ARI reported attention_manager_msgs.
            from attention_manager.srv import SetPolicy

        self.rospy = rospy
        self.Twist = Twist
        self.Expression = Expression
        self._numpy = np
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._face_hysteresis = FaceHysteresis()
        self._face_smile = FaceSmilePulse()
        self._face_smile_active = False
        self._face_ids: tuple[str, ...] = ()
        self._person_ids: tuple[str, ...] = ()
        self._faces_at = 0.0
        self._persons_at = 0.0
        self._scan_at = 0.0
        self._scan: Any = None
        self._depth_at = 0.0
        self._depth: Any = None
        self._depth_checked_at = 0.0
        self._depth_clear: bool | None = None
        self._emergency = False
        self._conversation_active = False
        self._speaking = False
        self._policy = "UNKNOWN"
        self._policy_frame = ""
        self._policy_error: str | None = None
        self._policy_retry_after = 0.0
        self._expression = "unknown"
        self._observed_expression = "unknown"
        self._observed_expression_at = 0.0
        self._expression_published_at = 0.0
        self._attention_target: str | None = None
        self._target_seen_at = 0.0
        self._movement = MovementDeadman()
        self._current_linear = 0.0
        self._current_angular = 0.0
        self._velocity_was_active = False

        rospy.init_node("ari_social_control", anonymous=True)
        self._expression_pub = rospy.Publisher(
            EXPRESSION_TOPIC, Expression, queue_size=1
        )
        self._velocity_pub = rospy.Publisher(CMD_VEL_TOPIC, Twist, queue_size=1)
        rospy.Subscriber(FACE_TOPIC, IdsList, self._on_faces, queue_size=1)
        rospy.Subscriber(PERSON_TOPIC, IdsList, self._on_persons, queue_size=1)
        rospy.Subscriber(
            EXPRESSION_TOPIC,
            Expression,
            self._on_expression,
            queue_size=5,
        )
        rospy.Subscriber(SCAN_TOPIC, LaserScan, self._on_scan, queue_size=1)
        rospy.Subscriber(DEPTH_TOPIC, Image, self._on_depth, queue_size=1)
        rospy.Subscriber(EMERGENCY_TOPIC, Bool, self._on_emergency, queue_size=1)
        self._tf = tf.TransformListener()

        self._attention_service = None
        try:
            rospy.wait_for_service(ATTENTION_SERVICE, timeout=3.0)
            self._attention_service = rospy.ServiceProxy(
                ATTENTION_SERVICE, SetPolicy
            )
        except Exception as error:
            self._policy_error = f"attention service unavailable: {error}"
            LOGGER.warning(self._policy_error)

        self._enable_neck_service = None
        self._neck_enabled = False
        self._neck_error: str | None = None
        try:
            rospy.wait_for_service(ENABLE_NECK_SERVICE, timeout=3.0)
            self._enable_neck_service = rospy.ServiceProxy(
                ENABLE_NECK_SERVICE,
                Empty,
            )
        except Exception as error:
            self._neck_error = f"enable-neck service unavailable: {error}"
            LOGGER.warning(self._neck_error)

        self.set_mode(False)
        self._thread = threading.Thread(
            target=self._loop,
            name="ari-social-control",
            daemon=True,
        )
        self._thread.start()

    def _on_faces(self, message: Any) -> None:
        with self._lock:
            self._face_ids = tuple(str(value) for value in message.ids)
            self._faces_at = time.monotonic()

    def _on_persons(self, message: Any) -> None:
        with self._lock:
            self._person_ids = tuple(str(value) for value in message.ids)
            self._persons_at = time.monotonic()

    def _on_scan(self, message: Any) -> None:
        with self._lock:
            self._scan = message
            self._scan_at = time.monotonic()

    def _on_depth(self, message: Any) -> None:
        with self._lock:
            self._depth = message
            self._depth_at = time.monotonic()

    def _on_expression(self, message: Any) -> None:
        with self._lock:
            self._observed_expression = str(message.expression or "unknown")
            self._observed_expression_at = time.monotonic()

    def _on_emergency(self, message: Any) -> None:
        with self._lock:
            self._emergency = bool(message.data)
            if self._emergency:
                self._movement.stop()

    def _call_policy(self, policy: int, frame: str = "") -> bool:
        if self._attention_service is None:
            return False
        try:
            response = self._attention_service(policy, frame)
            if not bool(response.success):
                raise RuntimeError(
                    getattr(response, "error_msg", "")
                    or "service rejected policy"
                )
        except Exception as error:
            self._policy_error = str(error)
            self._policy_retry_after = time.monotonic() + 5.0
            LOGGER.warning("Could not set %s: %s", POLICY_NAMES[policy], error)
            return False
        self._policy = POLICY_NAMES[policy]
        self._policy_frame = frame
        self._policy_error = None
        self._policy_retry_after = 0.0
        LOGGER.info("Attention policy: %s (frame=%r)", self._policy, frame)
        return True

    def _enable_neck(self) -> bool:
        if getattr(self, "_enable_neck_service", None) is None:
            return False
        try:
            self._enable_neck_service()
        except Exception as error:
            self._neck_enabled = False
            self._neck_error = str(error)
            LOGGER.warning("Could not enable gaze-manager neck control: %s", error)
            return False
        self._neck_enabled = True
        self._neck_error = None
        LOGGER.info("Gaze-manager neck control enabled")
        return True

    def _verified_person_frame(self, now: float) -> str:
        fresh = now - self._persons_at <= TRACKING_MESSAGE_STALE_SECONDS
        person_ids = self._person_ids if fresh else ()
        if self._attention_target in person_ids:
            self._target_seen_at = now
            return self._attention_target or ""
        if (
            self._attention_target
            and now - self._target_seen_at < TARGET_LOSS_GRACE_SECONDS
        ):
            return self._attention_target
        for person_id in person_ids:
            try:
                if self._tf.frameExists(person_id):
                    self._attention_target = person_id
                    self._target_seen_at = now
                    return person_id
            except Exception:
                break
        self._attention_target = None
        return ""

    def set_mode(self, conversation_active: bool) -> bool:
        with self._lock:
            self._conversation_active = bool(conversation_active)
            self._speaking = False
            self._enable_neck()
            if conversation_active:
                target = self._verified_person_frame(time.monotonic())
                success = self._call_policy(FOCUSED_SOCIAL, target)
                if not success:
                    retry_after = getattr(self, "_policy_retry_after", 0.0)
                    self._call_policy(IDLE_SOCIAL, "")
                    self._policy_retry_after = retry_after
                return success
            self._attention_target = None
            return self._call_policy(IDLE_SOCIAL, "")

    def set_speaking(self, speaking: bool) -> None:
        with self._lock:
            self._speaking = bool(speaking)
        self._update_expression(time.monotonic(), force=True)

    def _face_is_stable(self, now: float) -> bool:
        visible = (
            now - self._faces_at <= TRACKING_MESSAGE_STALE_SECONDS
            and bool(self._face_ids)
        )
        return self._face_hysteresis.update(visible, now)

    def _desired_expression(self, now: float) -> str:
        stable_face = self._face_is_stable(now)
        self._face_smile_active = self._face_smile.update(
            stable_face and not self._conversation_active,
            now,
        )
        return desired_expression(
            self._conversation_active,
            self._speaking,
            self._face_smile_active,
        )

    def _update_expression(self, now: float, *, force: bool = False) -> None:
        with self._lock:
            desired = self._desired_expression(now)
            refresh_due = (
                now - self._expression_published_at >= EXPRESSION_REFRESH_SECONDS
            )
            if not force and desired == self._expression and not refresh_due:
                return
            message = self.Expression()
            message.expression = desired
            self._expression_pub.publish(message)
            self._expression = desired
            self._expression_published_at = now

    def _scan_is_fresh(self, now: float) -> bool:
        return self._scan is not None and now - self._scan_at <= SCAN_STALE_SECONDS

    def _depth_is_fresh(self, now: float) -> bool:
        return self._depth is not None and now - self._depth_at <= SCAN_STALE_SECONDS

    def _obstacle_sensor(self, now: float) -> str:
        if self._scan_is_fresh(now):
            return "laser"
        if self._numpy is not None and self._depth_is_fresh(now):
            return "depth"
        return "none"

    def movement_available(self, now: float | None = None) -> bool:
        return (
            not self._emergency
            and self._velocity_pub.get_num_connections() > 0
        )

    def _laser_forward_clear(self) -> bool | None:
        saw_valid_forward_ray = False
        for index, distance in enumerate(self._scan.ranges):
            angle = self._scan.angle_min + index * self._scan.angle_increment
            if abs(math.degrees(angle)) > FORWARD_CONE_DEGREES:
                continue
            if not math.isfinite(distance) or distance <= 0.0:
                continue
            saw_valid_forward_ray = True
            if distance < FORWARD_CLEARANCE_METERS:
                return False
        return True if saw_valid_forward_ray else None

    def _depth_forward_clear(self, now: float) -> bool | None:
        if now - self._depth_checked_at < DEPTH_CHECK_PERIOD_SECONDS:
            return self._depth_clear
        self._depth_checked_at = now
        message = self._depth
        np = self._numpy
        if message is None or np is None:
            self._depth_clear = None
            return None
        if message.encoding in ("16UC1", "mono16"):
            dtype, scale = np.uint16, 0.001
        elif message.encoding == "32FC1":
            dtype, scale = np.float32, 1.0
        else:
            self._depth_clear = None
            return None
        if message.is_bigendian:
            dtype = np.dtype(dtype).newbyteorder(">")
        try:
            row_width = message.step // np.dtype(dtype).itemsize
            image = np.frombuffer(message.data, dtype=dtype).reshape(
                message.height,
                row_width,
            )
            image = image[:, : message.width].astype(np.float32) * scale
        except (TypeError, ValueError):
            self._depth_clear = None
            return None

        top = int(message.height * DEPTH_BAND_TOP_FRACTION)
        bottom = int(message.height * DEPTH_BAND_BOTTOM_FRACTION)
        half_cone = int(
            message.width * FORWARD_CONE_DEGREES / DEPTH_FOV_DEGREES
        )
        center = message.width // 2
        band = image[top:bottom, center - half_cone : center + half_cone]
        values = band[
            np.isfinite(band)
            & (band > DEPTH_MIN_VALID_METERS)
            & (band < DEPTH_MAX_VALID_METERS)
        ]
        self._depth_clear = (
            bool(float(np.min(values)) >= FORWARD_CLEARANCE_METERS)
            if values.size
            else None
        )
        return self._depth_clear

    def _forward_clear(self, now: float) -> bool | None:
        if self._scan_is_fresh(now):
            return self._laser_forward_clear()
        if self._numpy is not None and self._depth_is_fresh(now):
            return self._depth_forward_clear(now)
        return None

    def command_move(self, action: str) -> bool:
        if action not in MOVE_COMMANDS:
            return False
        with self._lock:
            if action == "stop":
                self.stop_movement()
                return True
            now = time.monotonic()
            if not self.movement_available(now):
                self.stop_movement()
                return False
            linear, angular = MOVE_COMMANDS[action]
            if linear > 0.0 and self._forward_clear(now) is False:
                self.stop_movement()
                return False
            self._movement.set(linear, angular, now)
            return True

    def _publish_velocity(self, linear: float, angular: float) -> None:
        message = self.Twist()
        message.linear.x = linear
        message.angular.z = angular
        self._velocity_pub.publish(message)

    def stop_movement(self) -> None:
        with self._lock:
            self._movement.stop()
            self._current_linear = 0.0
            self._current_angular = 0.0
            self._publish_velocity(0.0, 0.0)
            self._velocity_was_active = False

    def _update_movement(self, now: float) -> None:
        with self._lock:
            available = self.movement_available(now)
            if not available or not self._movement.active(now):
                if self._current_linear or self._current_angular:
                    self.stop_movement()
                return
            target_linear, target_angular = self._movement.target(now, available)
            if target_linear > 0.0 and self._forward_clear(now) is False:
                self.stop_movement()
                return
            self._current_linear += clamp(
                target_linear - self._current_linear,
                -LINEAR_RAMP_PER_TICK,
                LINEAR_RAMP_PER_TICK,
            )
            self._current_angular += clamp(
                target_angular - self._current_angular,
                -ANGULAR_RAMP_PER_TICK,
                ANGULAR_RAMP_PER_TICK,
            )
            if abs(self._current_linear) < 0.005 and target_linear == 0.0:
                self._current_linear = 0.0
            if abs(self._current_angular) < 0.01 and target_angular == 0.0:
                self._current_angular = 0.0
            moving = bool(self._current_linear or self._current_angular)
            if moving:
                self._publish_velocity(
                    self._current_linear,
                    self._current_angular,
                )
                self._velocity_was_active = True
            elif self._velocity_was_active:
                self._publish_velocity(0.0, 0.0)
                self._velocity_was_active = False

    def _update_focus(self, now: float) -> None:
        with self._lock:
            if not self._conversation_active:
                return
            target = self._verified_person_frame(now)
            if (
                now >= self._policy_retry_after
                and (
                    self._policy != "FOCUSED_SOCIAL"
                    or target != self._policy_frame
                )
            ):
                self._call_policy(FOCUSED_SOCIAL, target)

    def _loop(self) -> None:
        while not self._stop.wait(CONTROL_PERIOD_SECONDS):
            now = time.monotonic()
            try:
                self._update_expression(now)
                self._update_focus(now)
                self._update_movement(now)
            except Exception as error:
                LOGGER.warning("Social-control update failed: %s", error)
                self.stop_movement()

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            face_stable = self._face_is_stable(now)
            observed_expression = (
                self._observed_expression
                if now - self._observed_expression_at
                <= EXPRESSION_REFRESH_SECONDS * 1.5
                else "unknown"
            )
            return {
                "ok": True,
                "sidecar_version": SIDECAR_VERSION,
                "policy": self._policy,
                "policy_error": self._policy_error,
                "neck_enabled": self._neck_enabled,
                "neck_error": self._neck_error,
                "expression": observed_expression,
                "expression_command": self._expression,
                "conversation_active": self._conversation_active,
                "speaking": self._speaking,
                "face_count": len(self._face_ids)
                if now - self._faces_at <= TRACKING_MESSAGE_STALE_SECONDS
                else 0,
                "face_stable": face_stable,
                "face_smile_active": self._face_smile_active,
                "face_greeting_smile_seconds": FACE_GREETING_SMILE_SECONDS,
                "face_signal": "stable_tracked_faces_approximation",
                "attention_target": self._attention_target,
                "movement_available": self.movement_available(now),
                "movement_active": self._movement.active(now),
                "obstacle_sensor": self._obstacle_sensor(now),
                "base_subscribers": self._velocity_pub.get_num_connections(),
                "emergency": self._emergency,
                "tablet_controlled": False,
            }

    def close(self) -> None:
        self._stop.set()
        self.stop_movement()
        with self._lock:
            self._conversation_active = False
            self._speaking = False
            self._face_ids = ()
            self._face_hysteresis.stable = False
            self._face_hysteresis.first_seen_at = None
            self._face_hysteresis.last_seen_at = None
            self._face_smile.reset()
            self._face_smile_active = False
        self._update_expression(time.monotonic(), force=True)
        self._call_policy(IDLE_SOCIAL, "")
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


def create_app(bridge: Any) -> Flask:
    app = Flask(__name__)

    @app.after_request
    def no_cache(response: Any) -> Any:
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/health", methods=["GET"])
    @app.route("/state", methods=["GET"])
    def state() -> Any:
        return jsonify(bridge.snapshot())

    @app.route("/mode", methods=["POST"])
    def mode() -> Any:
        body = request.get_json(force=True, silent=True) or {}
        active = bool(body.get("conversation_active"))
        success = bridge.set_mode(active)
        result = bridge.snapshot()
        result["attention_command_ok"] = bool(success)
        return jsonify(result)

    @app.route("/speaking", methods=["POST"])
    def speaking() -> Any:
        body = request.get_json(force=True, silent=True) or {}
        bridge.set_speaking(bool(body.get("speaking")))
        return jsonify(bridge.snapshot())

    @app.route("/move", methods=["POST"])
    def move() -> Any:
        body = request.get_json(force=True, silent=True) or {}
        action = str(body.get("action", ""))
        success = bridge.command_move(action)
        result = bridge.snapshot()
        result["command_ok"] = bool(success)
        return jsonify(result)

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ARI social-control ROS sidecar")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        bridge = ARISocialROSBridge()
    except Exception as error:
        LOGGER.error("Cannot initialise ARI ROS controls: %s", error)
        return 1

    def shutdown(signum: int, _frame: Any) -> None:
        LOGGER.info("Stopping ARI social controls (signal %s)", signum)
        bridge.close()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        create_app(bridge).run(host=args.host, port=args.port, threaded=True)
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
