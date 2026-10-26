"""Small ROS sidecar for ARI social attention, expressions and manual base control.

Run this on ARI alongside (not instead of) the existing RoboBOND proxy.  It
never publishes gaze targets and never touches the tablet. The head is only
moved directly during an operator emote (such as the dance); attention is
paused for that and comes back on its own a few seconds after the last move.
"""

from __future__ import annotations

import argparse
import array
import logging
import math
import os
import random
import shutil
import signal
import subprocess
import threading
import time
import wave
from typing import Any

from flask import Flask, jsonify, request


# Adjustable behavior and safety parameters.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 7343
SIDECAR_VERSION = "ari-social-v0.4.0-guardian-emotes"
ATTENTION_SERVICE = "/attention_manager/set_policy"
ENABLE_NECK_SERVICE = "/gaze_manager/enable_neck"
DISABLE_NECK_SERVICE = "/gaze_manager/disable_neck"
HEAD_TOPIC = "/head_controller/command"
HEAD_JOINTS = ("head_1_joint", "head_2_joint")
ODOM_TOPIC = "/mobile_base_controller/odom"
VOLUME_PARAM = "/pal/playback_volume"
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

# Operator actions (emotes, handshake, face buttons).
EXPRESSIONS = {
    "neutral",
    "happy",
    "sad",
    "surprised",
    "excited",
    "confused",
    "embarrassed",
    "skeptical",
    "angry",
}
MAX_EXPRESSION_OVERRIDE_SECONDS = 60.0
MAX_HEAD_PAN_DEGREES = 72.0
MAX_HEAD_TILT_UP_DEGREES = 14.0
MAX_HEAD_TILT_DOWN_DEGREES = 24.0
MANUAL_HEAD_HOLD_SECONDS = 3.0
MAX_TURN_DEGREES = 180.0
MAX_DRIVE_METERS = 0.5
MAX_BACKWARD_DRIVE_METERS = 0.3
MOTION_EXTRA_SECONDS = 3.0
ODOM_STALE_SECONDS = 0.5
NEAREST_CONE_DEGREES = 25.0
MIN_VOLUME_PERCENT = 30
MAX_VOLUME_PERCENT = 100
MUSIC_FILES = ("~/dance.wav", "~/StillDRE.wav")
GENERATED_TUNE_PATH = "/tmp/ari_dance_tune.wav"

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


def wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


def yaw_from_quaternion(orientation: Any) -> float:
    q = orientation
    return math.atan2(
        2.0 * (q.w * q.z + q.x * q.y),
        1.0 - 2.0 * (q.y * q.y + q.z * q.z),
    )


def write_dance_tune(path: str, rate: int = 22050) -> None:
    """Write a short cheerful loop, so the dance has music without a wav file."""
    beat = 60.0 / 124.0
    eighth = beat / 2.0
    melody = (
        72, 76, 79, 76, 72, 76, 79, 84,
        77, 81, 84, 81, 77, 81, 79, 77,
        79, 83, 86, 83, 79, 77, 76, 74,
        76, 72, 76, 79, 84, None, 84, None,
        76, 72, 69, 72, 76, 79, 81, 79,
        77, 81, 77, 72, 77, 81, 84, 81,
        79, 74, 79, 83, 86, 83, 79, 77,
        76, 79, 84, 79, 72, None, 72, None,
    )
    roots = (48, 41, 43, 48, 45, 41, 43, 48)
    total = int(len(melody) * eighth * rate)
    samples = array.array("h", [0]) * total
    noise = random.Random(7)

    def frequency(note: int) -> float:
        return 440.0 * 2.0 ** ((note - 69) / 12.0)

    def add_note(start: float, length: float, note: int, level: float, square: bool) -> None:
        first = int(start * rate)
        count = int(length * rate)
        step = frequency(note) / rate
        phase = 0.0
        for index in range(count):
            position = first + index
            if position >= total:
                break
            attack = min(1.0, index / 200.0)
            release = min(1.0, (count - index) / 600.0)
            if square:
                wave_value = 1.0 if phase < 0.5 else -1.0
            else:
                wave_value = 4.0 * abs(phase - 0.5) - 1.0
            value = samples[position] + int(level * attack * release * wave_value)
            samples[position] = max(-32000, min(32000, value))
            phase = (phase + step) % 1.0

    for index, note in enumerate(melody):
        if note is not None:
            add_note(index * eighth, eighth * 0.9, note, 4200, True)
    for bar, root in enumerate(roots):
        for quarter, offset in enumerate((0, 7, 12, 7)):
            add_note((bar * 4 + quarter) * beat, beat * 0.8, root + offset, 7000, False)
    for quarter in range(len(melody) // 2):
        first = int(quarter * beat * rate)
        for index in range(int(0.12 * rate)):
            position = first + index
            if position >= total:
                break
            kick = math.sin(2 * math.pi * (60.0 + 90.0 * math.exp(-index / 300.0)) * index / rate)
            value = samples[position] + int(9000 * math.exp(-index / 1500.0) * kick)
            samples[position] = max(-32000, min(32000, value))
        off_beat = int((quarter * beat + eighth) * rate)
        for index in range(int(0.03 * rate)):
            position = off_beat + index
            if position >= total:
                break
            hat = noise.uniform(-1.0, 1.0) * 1800 * math.exp(-index / 120.0)
            samples[position] = max(-32000, min(32000, samples[position] + int(hat)))

    temporary = f"{path}.part"
    with wave.open(temporary, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.tobytes())
    os.replace(temporary, path)


class MusicPlayer:
    """Loops dance music through ARI's speaker while an emote asks for it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Any = None
        self.source: str | None = None

    @staticmethod
    def find_source() -> str | None:
        for candidate in MUSIC_FILES:
            path = os.path.expanduser(candidate)
            if os.path.isfile(path):
                return path
        if not os.path.isfile(GENERATED_TUNE_PATH):
            write_dance_tune(GENERATED_TUNE_PATH)
        return GENERATED_TUNE_PATH

    @staticmethod
    def player_command(path: str) -> list[str] | None:
        if shutil.which("paplay"):
            return ["paplay", "--volume=65536", path]
        if shutil.which("aplay"):
            return ["aplay", "-q", path]
        return None

    @property
    def playing(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and not self._stop.is_set()

    def start(self) -> bool:
        with self._lock:
            if self.playing:
                return True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="ari-dance-music",
                daemon=True,
            )
            self._thread.start()
        return True

    def _loop(self) -> None:
        try:
            path = self.find_source()
        except Exception as error:
            LOGGER.warning("No dance music available: %s", error)
            return
        command = self.player_command(path) if path else None
        if command is None:
            LOGGER.warning("Neither paplay nor aplay is installed; dancing without music")
            return
        self.source = path
        while not self._stop.is_set():
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            code = self._process.wait()
            self._process = None
            if self._stop.is_set():
                break
            if code:
                LOGGER.warning("%s exited with %s; stopping the music", command[0], code)
                break

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            process = self._process
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=1.0)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)


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

        try:
            from nav_msgs.msg import Odometry
            from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
        except ImportError as error:
            Odometry = JointTrajectory = JointTrajectoryPoint = None
            LOGGER.warning("Head moves and odometry are off: %s", error)

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
        self.JointTrajectory = JointTrajectory
        self.JointTrajectoryPoint = JointTrajectoryPoint
        self._expression_override: str | None = None
        self._expression_override_until = 0.0
        self._manual_head = False
        self._manual_head_until = 0.0
        self._odom: Any = None
        self._odom_at = 0.0
        self._motion_lock = threading.Lock()
        self._motion_abort = threading.Event()
        self._motion_kind: str | None = None
        self._music = MusicPlayer()

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
        self._head_pub = None
        if JointTrajectory is not None:
            self._head_pub = rospy.Publisher(
                HEAD_TOPIC, JointTrajectory, queue_size=1
            )
        if Odometry is not None:
            rospy.Subscriber(ODOM_TOPIC, Odometry, self._on_odom, queue_size=1)
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

        # Not every PAL release has this one; without it the head moves of an
        # emote still work, the gaze manager just gets paused through attention.
        self._disable_neck_service = None
        try:
            rospy.wait_for_service(DISABLE_NECK_SERVICE, timeout=1.0)
            self._disable_neck_service = rospy.ServiceProxy(
                DISABLE_NECK_SERVICE,
                Empty,
            )
        except Exception:
            LOGGER.info("No %s service; emote head moves only pause attention", DISABLE_NECK_SERVICE)

        threading.Thread(
            target=self._prepare_music,
            name="ari-dance-music-prepare",
            daemon=True,
        ).start()

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

    def _on_odom(self, message: Any) -> None:
        with self._lock:
            self._odom = message
            self._odom_at = time.monotonic()

    def _prepare_music(self) -> None:
        # Writing the fallback tune takes a second or two, so do it before the
        # first dance instead of during it.
        try:
            MusicPlayer.find_source()
        except Exception as error:
            LOGGER.warning("Could not prepare the dance tune: %s", error)

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
            if getattr(self, "_manual_head", False):
                # An emote owns the head right now; attention for this mode is
                # switched on when the emote's head hold ends.
                return True
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
        override = getattr(self, "_expression_override", None)
        if override and now < getattr(self, "_expression_override_until", 0.0):
            return override
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
        abort = getattr(self, "_motion_abort", None)
        if abort is not None:
            # The arrow pad and the stop button always win over an emote's turn.
            abort.set()
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
            if not self._conversation_active or getattr(self, "_manual_head", False):
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

    def set_expression_override(self, expression: str | None, seconds: float) -> bool:
        """Show one face for a while (operator button, emote, ARI's reply)."""
        name = str(expression or "").strip().lower()
        with self._lock:
            if not name or seconds <= 0:
                self._expression_override = None
                self._expression_override_until = 0.0
            elif name in EXPRESSIONS:
                self._expression_override = name
                self._expression_override_until = time.monotonic() + clamp(
                    seconds, 0.5, MAX_EXPRESSION_OVERRIDE_SECONDS
                )
            else:
                return False
        self._update_expression(time.monotonic(), force=True)
        return True

    def _disable_neck(self) -> None:
        service = getattr(self, "_disable_neck_service", None)
        if service is None:
            return
        try:
            service()
            self._neck_enabled = False
        except Exception as error:
            LOGGER.warning("Could not pause gaze-manager neck control: %s", error)

    def command_head(self, pan: float, tilt: float, duration: float) -> bool:
        if self._head_pub is None:
            return False
        pan = clamp(pan, -MAX_HEAD_PAN_DEGREES, MAX_HEAD_PAN_DEGREES)
        tilt = clamp(tilt, -MAX_HEAD_TILT_DOWN_DEGREES, MAX_HEAD_TILT_UP_DEGREES)
        duration = clamp(duration, 0.2, 5.0)
        with self._lock:
            now = time.monotonic()
            if not self._manual_head:
                self._manual_head = True
                self._call_policy(DISABLED, "")
                self._disable_neck()
            self._manual_head_until = now + duration + MANUAL_HEAD_HOLD_SECONDS
            message = self.JointTrajectory()
            message.joint_names = list(HEAD_JOINTS)
            point = self.JointTrajectoryPoint()
            # head_2_joint is flipped on ARI: a positive angle tips the nose down.
            point.positions = [math.radians(pan), math.radians(-tilt)]
            point.time_from_start = self.rospy.Duration(duration)
            message.points = [point]
            self._head_pub.publish(message)
        return True

    def _update_manual_head(self, now: float) -> None:
        with self._lock:
            if not self._manual_head or now < self._manual_head_until:
                return
            self._manual_head = False
            self._enable_neck()
            if self._conversation_active:
                self._call_policy(FOCUSED_SOCIAL, self._verified_person_frame(now))
            else:
                self._attention_target = None
                self._call_policy(IDLE_SOCIAL, "")

    def _odom_pose(self, now: float) -> tuple[float, float, float] | None:
        message = self._odom
        if message is None or now - self._odom_at > ODOM_STALE_SECONDS:
            return None
        pose = message.pose.pose
        return (
            pose.position.x,
            pose.position.y,
            yaw_from_quaternion(pose.orientation),
        )

    def _blocking_motion(
        self,
        kind: str,
        linear: float,
        angular: float,
        goal: float,
    ) -> dict[str, Any]:
        """Turn (goal in radians) or drive (goal in meters) through the guarded velocity loop."""
        if not self._motion_lock.acquire(timeout=0.5):
            return {"ok": False, "progress": 0.0, "reason": "busy"}
        speed = linear if kind == "drive" else angular
        direction = 1.0 if speed > 0 else -1.0
        progress = 0.0
        reason = "done"
        try:
            self._motion_abort.clear()
            with self._lock:
                # Start from standstill so the previous step's speed can't
                # count towards this one.
                self.stop_movement()
                self._motion_kind = kind
                last_pose = self._odom_pose(time.monotonic())
            last_time = time.monotonic()
            deadline = last_time + goal / abs(speed) + MOTION_EXTRA_SECONDS
            while progress < goal:
                now = time.monotonic()
                if self._motion_abort.is_set():
                    reason = "stopped"
                    break
                if now >= deadline:
                    reason = "timeout"
                    break
                with self._lock:
                    if not self.movement_available(now):
                        reason = "unavailable"
                        break
                    if linear > 0.0 and self._forward_clear(now) is False:
                        reason = "blocked"
                        break
                    self._movement.set(linear, angular, now)
                if self._stop.wait(CONTROL_PERIOD_SECONDS):
                    reason = "stopped"
                    break
                now = time.monotonic()
                with self._lock:
                    pose = self._odom_pose(now)
                    velocity = (
                        self._current_linear if kind == "drive" else self._current_angular
                    )
                if pose is not None and last_pose is not None:
                    if kind == "turn":
                        progress += direction * wrap_angle(pose[2] - last_pose[2])
                    else:
                        dx = pose[0] - last_pose[0]
                        dy = pose[1] - last_pose[1]
                        progress += direction * (
                            dx * math.cos(pose[2]) + dy * math.sin(pose[2])
                        )
                else:
                    progress += direction * velocity * (now - last_time)
                last_pose = pose
                last_time = now
        finally:
            with self._lock:
                if self._movement.linear == linear and self._movement.angular == angular:
                    self._movement.stop()
                self._motion_kind = None
            self._motion_lock.release()
        return {"ok": reason == "done", "progress": max(0.0, progress), "reason": reason}

    def turn(self, degrees: float) -> dict[str, Any]:
        degrees = clamp(degrees, -MAX_TURN_DEGREES, MAX_TURN_DEGREES)
        if abs(degrees) < 1.0:
            return {"ok": True, "turned": 0.0, "reason": "done"}
        angular = math.copysign(MAX_ANGULAR_RADIANS_PER_SECOND, degrees)
        result = self._blocking_motion("turn", 0.0, angular, math.radians(abs(degrees)))
        turned = math.copysign(math.degrees(result["progress"]), degrees)
        return {"ok": result["ok"], "turned": round(turned, 1), "reason": result["reason"]}

    def drive(self, meters: float) -> dict[str, Any]:
        meters = clamp(meters, -MAX_BACKWARD_DRIVE_METERS, MAX_DRIVE_METERS)
        if abs(meters) < 0.01:
            return {"ok": True, "driven": 0.0, "reason": "done"}
        linear = (
            MAX_FORWARD_METERS_PER_SECOND
            if meters > 0
            else -MAX_BACKWARD_METERS_PER_SECOND
        )
        result = self._blocking_motion("drive", linear, 0.0, abs(meters))
        driven = math.copysign(result["progress"], meters)
        return {"ok": result["ok"], "driven": round(driven, 3), "reason": result["reason"]}

    def set_music(self, on: bool) -> bool:
        if on:
            return self._music.start()
        self._music.stop()
        return False

    def volume(self, percent: float | None = None) -> int | None:
        if percent is not None:
            value = int(clamp(round(percent), MIN_VOLUME_PERCENT, MAX_VOLUME_PERCENT))
            self.rospy.set_param(VOLUME_PARAM, value)
        try:
            return int(self.rospy.get_param(VOLUME_PARAM))
        except Exception:
            return None

    def _laser_nearest(self) -> float | None:
        nearest = None
        for index, distance in enumerate(self._scan.ranges):
            angle = self._scan.angle_min + index * self._scan.angle_increment
            if abs(math.degrees(angle)) > NEAREST_CONE_DEGREES:
                continue
            if not math.isfinite(distance) or distance <= 0.0:
                continue
            if nearest is None or distance < nearest:
                nearest = distance
        return nearest

    def _depth_nearest(self) -> float | None:
        message = self._depth
        np = self._numpy
        if message is None or np is None:
            return None
        if message.encoding in ("16UC1", "mono16"):
            dtype, scale = np.uint16, 0.001
        elif message.encoding == "32FC1":
            dtype, scale = np.float32, 1.0
        else:
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
            return None
        top = int(message.height * DEPTH_BAND_TOP_FRACTION)
        bottom = int(message.height * DEPTH_BAND_BOTTOM_FRACTION)
        half_cone = int(message.width * NEAREST_CONE_DEGREES / DEPTH_FOV_DEGREES)
        center = message.width // 2
        band = image[top:bottom, max(0, center - half_cone) : center + half_cone]
        values = band[
            np.isfinite(band)
            & (band > DEPTH_MIN_VALID_METERS)
            & (band < DEPTH_MAX_VALID_METERS)
        ]
        return float(np.min(values)) if values.size else None

    def nearest_ahead(self) -> tuple[float | None, str]:
        """Closest thing in front of ARI, used to notice a hand for a handshake."""
        now = time.monotonic()
        with self._lock:
            if self._scan_is_fresh(now):
                return self._laser_nearest(), "laser"
            if self._numpy is not None and self._depth_is_fresh(now):
                return self._depth_nearest(), "depth"
        return None, "none"

    def _loop(self) -> None:
        while not self._stop.wait(CONTROL_PERIOD_SECONDS):
            now = time.monotonic()
            try:
                self._update_expression(now)
                self._update_manual_head(now)
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
                "expression_override": self._expression_override
                if now < self._expression_override_until
                else None,
                "manual_head": self._manual_head,
                "motion_active": self._motion_kind,
                "music_playing": self._music.playing,
                "head_available": self._head_pub is not None,
                "odometry": self._odom_pose(now) is not None,
                "capabilities": [
                    "expression_override",
                    "head",
                    "turn",
                    "drive",
                    "music",
                    "volume",
                    "nearest",
                ],
            }

    def close(self) -> None:
        self._stop.set()
        self._motion_abort.set()
        self._music.stop()
        self.stop_movement()
        with self._lock:
            self._expression_override = None
            self._manual_head = False
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

    def number(body: dict[str, Any], key: str, default: float) -> float:
        try:
            value = float(body.get(key, default))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    @app.route("/expression", methods=["POST"])
    def expression() -> Any:
        body = request.get_json(force=True, silent=True) or {}
        success = bridge.set_expression_override(
            body.get("expression"),
            number(body, "seconds", 5.0),
        )
        return jsonify({"ok": bool(success), "expression": body.get("expression")})

    @app.route("/head", methods=["POST"])
    def head() -> Any:
        body = request.get_json(force=True, silent=True) or {}
        success = bridge.command_head(
            number(body, "pan", 0.0),
            number(body, "tilt", 0.0),
            number(body, "duration", 0.5),
        )
        return jsonify({"ok": bool(success)})

    @app.route("/turn", methods=["POST"])
    def turn() -> Any:
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(bridge.turn(number(body, "degrees", 0.0)))

    @app.route("/drive", methods=["POST"])
    def drive() -> Any:
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(bridge.drive(number(body, "meters", 0.0)))

    @app.route("/music", methods=["POST"])
    def music() -> Any:
        body = request.get_json(force=True, silent=True) or {}
        playing = bridge.set_music(bool(body.get("on")))
        return jsonify({"ok": True, "playing": bool(playing)})

    @app.route("/volume", methods=["GET", "POST"])
    def volume() -> Any:
        percent = None
        if request.method == "POST":
            body = request.get_json(force=True, silent=True) or {}
            percent = number(body, "percent", 80.0)
        try:
            current = bridge.volume(percent)
        except Exception as error:
            return jsonify({"ok": False, "percent": None, "error": str(error)})
        return jsonify({"ok": True, "percent": current})

    @app.route("/nearest", methods=["GET"])
    def nearest() -> Any:
        distance, sensor = bridge.nearest_ahead()
        return jsonify({"ok": True, "distance": distance, "sensor": sensor})

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
