#!/usr/bin/python3
"""Native ROS face tracking and standby behaviour for QTrobot.

Run this node directly on QTPC with ``/usr/bin/python3``. Face detection and
head tracking always remain active. Demo mode only changes what QT does while
no face is visible.
"""

import json
import math
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import cv2
import rospy
from cv_bridge import CvBridge, CvBridgeError
from qt_robot_interface.srv import emotion_show
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import Bool, Float64MultiArray
from std_srvs.srv import SetBool, SetBoolResponse


# ---------------------------------------------------------------------------
# Adjustable parameters -- there are intentionally no command-line arguments.
# ---------------------------------------------------------------------------

# ROS interfaces verified on QTPC.
NODE_NAME = "qt_stand"
CAMERA_TOPIC = "/camera/color/image_raw"
HEAD_COMMAND_TOPIC = "/qt_robot/head_position/command"
HEAD_FEEDBACK_TOPIC = "/qt_robot/joints/state"
EXPRESSION_SERVICE = "/qt_robot/emotion/show"
HEAD_YAW_JOINT_NAME = "HeadYaw"
HEAD_PITCH_JOINT_NAME = "HeadPitch"

# Runtime demo-state integration. A future UI may either call the SetBool
# service or publish std_msgs/Bool to the command topic. The latched state topic
# reports the current value. Tracking never stops when this value changes.
STAND_BUILD_VERSION = "demo-face-grace-12s-v1"
INITIAL_DEMO_ACTIVE = False
DEMO_SET_SERVICE = "/qt_stand/set_demo_active"
DEMO_COMMAND_TOPIC = "/qt_stand/demo_active/set"
DEMO_STATE_TOPIC = "/qt_stand/demo_active"

# Minimal Windows-to-QTPC control bridge. This is deliberately separate from
# the RoboBOND proxy on port 7342: it controls only standby/demo state and does
# not carry robot commands, camera frames, or conversation data.
CONTROL_HTTP_BIND_HOST = "0.0.0.0"
CONTROL_HTTP_PORT = 7343
CONTROL_HTTP_MAX_BODY_BYTES = 1024
CONTROL_HTTP_THREAD_NAME = "qt-stand-control"

# Main-loop and startup timing.
MAIN_LOOP_HZ = 20.0
STARTUP_DELAY_SECONDS = 1.0
HEAD_COMMAND_DECIMAL_PLACES = 3

# ROS buffering and diagnostics.
ROS_QUEUE_SIZE = 1
CAMERA_SUBSCRIBER_BUFFER_BYTES = 2 ** 24
LOG_WARNING_THROTTLE_SECONDS = 5.0
HEAD_FEEDBACK_WARNING_THROTTLE_SECONDS = 10.0

# Installed QT head-controller limits, in degrees. Commands are
# [HeadYaw, HeadPitch]. These limits were verified on this robot.
HEAD_CENTER = (0.0, 0.0)
HEAD_COMMAND_YAW_HARD_LIMITS = (-60.0, 60.0)
HEAD_COMMAND_PITCH_HARD_LIMITS = (-25.0, 25.0)

# Face-following operating envelope and motion tuning. These limits are
# intentionally separate from the smaller idle-scan envelope.
FACE_YAW_LIMITS = (-60.0, 60.0)
FACE_PITCH_LIMITS = (-25.0, 25.0)
FACE_COMMAND_MIN_INTERVAL_SECONDS = 0.55
FACE_COMMAND_MAX_SETTLE_WAIT_SECONDS = 4.0
FACE_COMMAND_SETTLE_TOLERANCE_DEGREES = 2.0
FACE_MIN_TARGET_CHANGE_DEGREES = 4.0
FACE_TARGET_REUSE_TOLERANCE_DEGREES = 3.0
FACE_MOVED_OVERRIDE_DEGREES = 12.0
FACE_DETECTION_FRESH_SECONDS = 0.60

# Initial color-camera field-of-view estimates used only to convert image error
# to an absolute angular correction. Calibrate these values on the installed
# camera if needed; they are not motor-motion or smoothing parameters.
CAMERA_HORIZONTAL_FOV_DEGREES = 69.4
CAMERA_VERTICAL_FOV_DEGREES = 42.5

# Demo OFF + no face: three alternating side targets, then center. Existing
# tracking conventions use positive yaw for left, negative yaw for right,
# negative pitch for up, and positive pitch for down.
IDLE_YAW_LIMITS = (-45.0, 45.0)
IDLE_PITCH_LIMITS = (-15.0, 15.0)
IDLE_SCAN_INTERVAL_SECONDS = 13.0
IDLE_SCAN_SIDE_MOVEMENTS = 3
IDLE_SCAN_POSE_HOLD_SECONDS = 2.0
IDLE_SCAN_MOVE_TIMEOUT_SECONDS = 5.0
IDLE_SCAN_TARGET_TOLERANCE_DEGREES = 2.0
IDLE_LEFT_YAW_RANGE_DEGREES = (25.0, 45.0)
IDLE_RIGHT_YAW_MAGNITUDE_RANGE_DEGREES = (15.0, 45.0)
IDLE_PITCH_MAGNITUDE_RANGE_DEGREES = (2.5, 10.0)
IDLE_UP_PITCH_SIGN = -1.0
IDLE_DOWN_PITCH_SIGN = 1.0
IDLE_TARGET_DECIMAL_PLACES = 1

# Expressions verified on QTPC.
NEUTRAL_EXPRESSION = "QT/neutral_state_blinking"
SMILE_EXPRESSION = "QT/happy"
SMILE_INTERVAL_SECONDS = (10.0, 20.0)
SMILE_DURATION_SECONDS = 2.0
EXPRESSION_SERVICE_WAIT_SECONDS = 5.0
EXPRESSION_RETRY_SECONDS = 2.0

# Haar face detection. Detection runs below camera rate to leave CPU available
# for the rest of QT's software.
HAAR_CASCADE_PATH = (
    "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
)
FACE_DETECTION_INTERVAL_SECONDS = 0.12
DETECTION_IMAGE_SCALE = 0.5
HAAR_SCALE_FACTOR = 1.1
HAAR_MIN_NEIGHBORS = 4
# The verified 35 px minimum becomes about 18 px after the 0.5 resize.
HAAR_MIN_FACE_SIZE_PIXELS = (20, 20)

# Tracking and target-lock tuning. EMA alpha is the weight of the newest
# detection: smaller values smooth more strongly.
FACE_LOST_GRACE_SECONDS = 2.0
# While a conversation is active, keep the last face pose for ten additional
# seconds. Detection continues throughout this period. A reacquired face
# resumes tracking immediately; only the full timeout returns the head home.
DEMO_FACE_LOST_EXTRA_GRACE_SECONDS = 10.0
FACE_MATCH_MAX_DISTANCE = 0.22
FACE_SWITCH_AREA_RATIO = 1.8
FACE_POSITION_EMA_ALPHA = 0.20
FACE_CENTER_DEAD_ZONE = 0.08

# Flip either value to -1.0 if physical testing shows that axis is reversed.
FACE_YAW_DIRECTION = 1.0
FACE_PITCH_DIRECTION = 1.0


def clamp(value, limits):
    return max(limits[0], min(limits[1], value))


def random_duration(limits):
    return random.uniform(limits[0], limits[1])


def image_error_to_angle(error, field_of_view_degrees):
    """Convert normalized image-center error to a pinhole-camera angle."""
    half_fov_radians = math.radians(field_of_view_degrees) / 2.0
    return math.degrees(
        math.atan(2.0 * error * math.tan(half_fov_radians))
    )


class StandControlHTTPServer(ThreadingHTTPServer):
    """Small HTTP server exposing only health and the demo-state boolean."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, stand):
        self.stand = stand
        super().__init__(address, StandControlRequestHandler)


class StandControlRequestHandler(BaseHTTPRequestHandler):
    """Handle control requests without exposing arbitrary ROS operations."""

    protocol_version = "HTTP/1.1"

    def _send_json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _path(self):
        return urlsplit(self.path).path.rstrip("/") or "/"

    def do_GET(self):
        if self._path() not in {"/health", "/demo"}:
            self._send_json(404, {"ok": False, "error": "not_found"})
            return
        active = self.server.stand._get_demo_active()
        self._send_json(
            200,
            {
                "ok": True,
                "demo_active": active,
                "stand_version": STAND_BUILD_VERSION,
                "standby_face_lost_grace_seconds": FACE_LOST_GRACE_SECONDS,
                "demo_face_lost_grace_seconds": (
                    FACE_LOST_GRACE_SECONDS
                    + DEMO_FACE_LOST_EXTRA_GRACE_SECONDS
                ),
            },
        )

    def do_POST(self):
        self._set_demo_state()

    def do_PUT(self):
        self._set_demo_state()

    def _set_demo_state(self):
        if self._path() != "/demo":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"ok": False, "error": "invalid_length"})
            return
        if not 0 < content_length <= CONTROL_HTTP_MAX_BODY_BYTES:
            self._send_json(400, {"ok": False, "error": "invalid_body_size"})
            return

        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "error": "invalid_json"})
            return
        active = payload.get("active") if isinstance(payload, dict) else None
        if not isinstance(active, bool):
            self._send_json(400, {"ok": False, "error": "active_must_be_boolean"})
            return

        changed = self.server.stand._set_demo_active(active)
        self._send_json(
            200,
            {
                "ok": True,
                "demo_active": active,
                "changed": changed,
                "stand_version": STAND_BUILD_VERSION,
            },
        )

    def log_message(self, format_string, *args):
        rospy.logdebug("Stand control HTTP: " + format_string, *args)


class QTStand:
    """Continuous face tracker with demo-dependent no-face behaviour."""

    def __init__(self):
        self.face_detector = cv2.CascadeClassifier(HAAR_CASCADE_PATH)
        if self.face_detector.empty():
            raise RuntimeError(
                "OpenCV could not load Haar cascade: " + HAAR_CASCADE_PATH
            )

        self.bridge = CvBridge()
        self.image_lock = threading.Lock()
        self.latest_image_message = None
        self.latest_image_sequence = 0
        self.processed_image_sequence = -1

        self.demo_lock = threading.Lock()
        self.demo_active = INITIAL_DEMO_ACTIVE
        self.applied_demo_active = None

        self.head_feedback_lock = threading.Lock()
        self.latest_head_feedback = None

        self.head_publisher = rospy.Publisher(
            HEAD_COMMAND_TOPIC,
            Float64MultiArray,
            queue_size=ROS_QUEUE_SIZE,
        )
        self.demo_state_publisher = rospy.Publisher(
            DEMO_STATE_TOPIC,
            Bool,
            queue_size=ROS_QUEUE_SIZE,
            latch=True,
        )
        self.image_subscriber = rospy.Subscriber(
            CAMERA_TOPIC,
            Image,
            self._on_image,
            queue_size=ROS_QUEUE_SIZE,
            buff_size=CAMERA_SUBSCRIBER_BUFFER_BYTES,
        )
        self.head_feedback_subscriber = rospy.Subscriber(
            HEAD_FEEDBACK_TOPIC,
            JointState,
            self._on_head_feedback,
            queue_size=ROS_QUEUE_SIZE,
        )
        self.demo_command_subscriber = rospy.Subscriber(
            DEMO_COMMAND_TOPIC,
            Bool,
            self._on_demo_command,
            queue_size=ROS_QUEUE_SIZE,
        )
        self.demo_service = rospy.Service(
            DEMO_SET_SERVICE,
            SetBool,
            self._on_set_demo,
        )
        self.emotion_client = rospy.ServiceProxy(
            EXPRESSION_SERVICE,
            emotion_show,
            persistent=False,
        )

        now = time.monotonic()
        self.next_detection_at = now
        self.last_face_seen_at = None
        self.tracked_face = None
        self.face_was_active = False
        self.last_face_command_at = float("-inf")
        self.last_commanded_head = None

        self.idle_scan_targets = []
        self.idle_scan_target_index = 0
        self.idle_scan_commanded_at = None
        self.idle_scan_reached_at = None
        self.next_idle_scan_at = now + IDLE_SCAN_INTERVAL_SECONDS
        self.next_idle_scan_starts_left = random.choice((True, False))
        self.smile_ends_at = None
        self.next_smile_at = now + random_duration(SMILE_INTERVAL_SECONDS)

        self.current_expression = None
        self.last_expression_attempt_at = float("-inf")
        self.shutdown_complete = False
        self.control_server = None
        self.control_server_thread = None

        self._validate_configuration()
        self._start_control_server()

        self.demo_state_publisher.publish(Bool(data=self.demo_active))
        rospy.loginfo("Using Haar cascade: %s", HAAR_CASCADE_PATH)
        rospy.loginfo("Initial demo state: %s", self._demo_label(self.demo_active))

    def _start_control_server(self):
        try:
            self.control_server = StandControlHTTPServer(
                (CONTROL_HTTP_BIND_HOST, CONTROL_HTTP_PORT),
                self,
            )
        except OSError as error:
            raise RuntimeError(
                "Could not start standby control HTTP server on "
                f"{CONTROL_HTTP_BIND_HOST}:{CONTROL_HTTP_PORT}: {error}"
            ) from error
        self.control_server_thread = threading.Thread(
            target=self.control_server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name=CONTROL_HTTP_THREAD_NAME,
            daemon=True,
        )
        self.control_server_thread.start()
        rospy.loginfo(
            "Standby control HTTP listening on %s:%d.",
            CONTROL_HTTP_BIND_HOST,
            CONTROL_HTTP_PORT,
        )

    @staticmethod
    def _demo_label(active):
        return "ON" if active else "OFF"

    @staticmethod
    def _limits_contain(outer, inner):
        return outer[0] <= inner[0] <= inner[1] <= outer[1]

    def _validate_configuration(self):
        if IDLE_SCAN_SIDE_MOVEMENTS < 1:
            raise RuntimeError("IDLE_SCAN_SIDE_MOVEMENTS must be at least 1.")
        if not self._limits_contain(
            HEAD_COMMAND_YAW_HARD_LIMITS,
            IDLE_YAW_LIMITS,
        ) or not self._limits_contain(
            HEAD_COMMAND_PITCH_HARD_LIMITS,
            IDLE_PITCH_LIMITS,
        ):
            raise RuntimeError("Idle limits exceed the configured hard limits.")
        if not self._limits_contain(
            HEAD_COMMAND_YAW_HARD_LIMITS,
            FACE_YAW_LIMITS,
        ) or not self._limits_contain(
            HEAD_COMMAND_PITCH_HARD_LIMITS,
            FACE_PITCH_LIMITS,
        ):
            raise RuntimeError("Face limits exceed the configured hard limits.")
        if not 0.0 < CAMERA_HORIZONTAL_FOV_DEGREES < 180.0 or not (
            0.0 < CAMERA_VERTICAL_FOV_DEGREES < 180.0
        ):
            raise RuntimeError("Camera field-of-view values must be between 0 and 180.")
        if IDLE_LEFT_YAW_RANGE_DEGREES[1] > IDLE_YAW_LIMITS[1]:
            raise RuntimeError("Left idle yaw range exceeds IDLE_YAW_LIMITS.")
        if -IDLE_RIGHT_YAW_MAGNITUDE_RANGE_DEGREES[1] < IDLE_YAW_LIMITS[0]:
            raise RuntimeError("Right idle yaw range exceeds IDLE_YAW_LIMITS.")
        if IDLE_PITCH_MAGNITUDE_RANGE_DEGREES[1] > max(
            abs(IDLE_PITCH_LIMITS[0]),
            abs(IDLE_PITCH_LIMITS[1]),
        ):
            raise RuntimeError("Idle pitch range exceeds IDLE_PITCH_LIMITS.")

    def _on_image(self, message):
        """Keep only the newest frame; detection runs in the main thread."""
        with self.image_lock:
            self.latest_image_message = message
            self.latest_image_sequence += 1

    def _take_latest_image(self):
        with self.image_lock:
            if (
                self.latest_image_message is None
                or self.latest_image_sequence == self.processed_image_sequence
            ):
                return None
            message = self.latest_image_message
            self.processed_image_sequence = self.latest_image_sequence
            return message

    def _on_head_feedback(self, message):
        """Remember actual head angles so startup never commands a jump."""
        try:
            yaw_index = message.name.index(HEAD_YAW_JOINT_NAME)
            pitch_index = message.name.index(HEAD_PITCH_JOINT_NAME)
            yaw = float(message.position[yaw_index])
            pitch = float(message.position[pitch_index])
        except (ValueError, IndexError, TypeError):
            rospy.logwarn_throttle(
                HEAD_FEEDBACK_WARNING_THROTTLE_SECONDS,
                "HeadYaw/HeadPitch are missing from %s.",
                HEAD_FEEDBACK_TOPIC,
            )
            return

        with self.head_feedback_lock:
            self.latest_head_feedback = (yaw, pitch)

    def _initialize_head_from_feedback(self):
        feedback = self._get_head_orientation(use_command_fallback=False)
        if feedback is None:
            rospy.logwarn(
                "No head feedback received during startup; using [0, 0] "
                "until feedback arrives."
            )
            return

        rospy.loginfo(
            "Initial head feedback: [%.1f, %.1f] degrees.",
            feedback[0],
            feedback[1],
        )

    def _get_head_orientation(self, use_command_fallback=True):
        with self.head_feedback_lock:
            feedback = self.latest_head_feedback
        if feedback is not None:
            return (
                clamp(feedback[0], HEAD_COMMAND_YAW_HARD_LIMITS),
                clamp(feedback[1], HEAD_COMMAND_PITCH_HARD_LIMITS),
            )
        if use_command_fallback and self.last_commanded_head is not None:
            return self.last_commanded_head
        if use_command_fallback:
            return HEAD_CENTER
        return None

    def _set_demo_active(self, active):
        active = bool(active)
        with self.demo_lock:
            changed = active != self.demo_active
            self.demo_active = active
        self.demo_state_publisher.publish(Bool(data=active))
        if changed:
            rospy.loginfo("Demo state changed to %s.", self._demo_label(active))
        return changed

    def _get_demo_active(self):
        with self.demo_lock:
            return self.demo_active

    def _on_demo_command(self, message):
        self._set_demo_active(message.data)

    def _on_set_demo(self, request):
        changed = self._set_demo_active(request.data)
        state = self._demo_label(request.data)
        message = "Demo is now " + state if changed else "Demo was already " + state
        return SetBoolResponse(success=True, message=message)

    def _detect_faces(self, now):
        """Return normalized faces as (centre_x, centre_y, area), or None."""
        if now < self.next_detection_at:
            return None
        self.next_detection_at = now + FACE_DETECTION_INTERVAL_SECONDS

        message = self._take_latest_image()
        if message is None:
            return []

        try:
            image = self.bridge.imgmsg_to_cv2(message, desired_encoding="bgr8")
        except CvBridgeError as error:
            rospy.logwarn_throttle(
                LOG_WARNING_THROTTLE_SECONDS,
                "Could not convert camera image: %s",
                error,
            )
            return []

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if DETECTION_IMAGE_SCALE != 1.0:
            gray = cv2.resize(
                gray,
                None,
                fx=DETECTION_IMAGE_SCALE,
                fy=DETECTION_IMAGE_SCALE,
                interpolation=cv2.INTER_AREA,
            )
        gray = cv2.equalizeHist(gray)
        rectangles = self.face_detector.detectMultiScale(
            gray,
            scaleFactor=HAAR_SCALE_FACTOR,
            minNeighbors=HAAR_MIN_NEIGHBORS,
            minSize=HAAR_MIN_FACE_SIZE_PIXELS,
        )

        frame_height, frame_width = gray.shape[:2]
        frame_area = float(frame_width * frame_height)
        return [
            (
                (x + width / 2.0) / frame_width,
                (y + height / 2.0) / frame_height,
                (width * height) / frame_area,
            )
            for x, y, width, height in rectangles
        ]

    def _choose_face(self, faces, now):
        """Choose the largest face while keeping a stable lock when possible."""
        if not faces:
            return None

        largest = max(faces, key=lambda face: face[2])
        lock_is_recent = (
            self.tracked_face is not None
            and self.last_face_seen_at is not None
            and now - self.last_face_seen_at <= FACE_LOST_GRACE_SECONDS
        )
        if not lock_is_recent:
            return largest

        previous_x, previous_y, _ = self.tracked_face
        matched = min(
            faces,
            key=lambda face: math.hypot(
                face[0] - previous_x,
                face[1] - previous_y,
            ),
        )
        match_distance = math.hypot(
            matched[0] - previous_x,
            matched[1] - previous_y,
        )

        # Keep following the matched person unless another face is clearly
        # larger. If the tracked face briefly vanishes, hold the lock instead
        # of immediately jumping to somebody else.
        if match_distance <= FACE_MATCH_MAX_DISTANCE:
            if largest[2] >= matched[2] * FACE_SWITCH_AREA_RATIO:
                return largest
            return matched
        return None

    def _update_face_lock(self, detection, now):
        if detection is None:
            return

        continuing_lock = (
            self.tracked_face is not None
            and self.last_face_seen_at is not None
            and now - self.last_face_seen_at <= FACE_LOST_GRACE_SECONDS
        )
        chosen = self._choose_face(detection, now)
        if chosen is None:
            return

        if not continuing_lock:
            self.tracked_face = chosen
        else:
            alpha = FACE_POSITION_EMA_ALPHA
            self.tracked_face = tuple(
                old + alpha * (new - old)
                for old, new in zip(self.tracked_face, chosen)
            )
        self.last_face_seen_at = now

    def _face_is_active(self, now, demo_active):
        if self.last_face_seen_at is None:
            return False
        grace_seconds = FACE_LOST_GRACE_SECONDS
        if demo_active:
            grace_seconds += DEMO_FACE_LOST_EXTRA_GRACE_SECONDS
        if now - self.last_face_seen_at <= grace_seconds:
            return True
        self.tracked_face = None
        self.last_face_seen_at = None
        return False

    def _publish_head_target(
        self,
        yaw,
        pitch,
        yaw_limits,
        pitch_limits,
        now,
        force=False,
    ):
        """Publish one absolute destination; QT performs the movement."""
        yaw = clamp(float(yaw), yaw_limits)
        pitch = clamp(float(pitch), pitch_limits)
        command = (
            round(clamp(yaw, HEAD_COMMAND_YAW_HARD_LIMITS), HEAD_COMMAND_DECIMAL_PLACES),
            round(
                clamp(pitch, HEAD_COMMAND_PITCH_HARD_LIMITS),
                HEAD_COMMAND_DECIMAL_PLACES,
            ),
        )
        if not force and command == self.last_commanded_head:
            return False

        self.head_publisher.publish(Float64MultiArray(data=list(command)))
        self.last_commanded_head = command
        rospy.logdebug("Absolute head target: [%.1f, %.1f]", *command)
        return True

    def _head_reached(self, target, tolerance):
        orientation = self._get_head_orientation(use_command_fallback=False)
        if orientation is None:
            return False
        return (
            abs(orientation[0] - target[0]) <= tolerance
            and abs(orientation[1] - target[1]) <= tolerance
        )

    def _target_tracked_face(self, now, force=False):
        """Calculate and publish one meaningful absolute face target."""
        if self.tracked_face is None or self.last_face_seen_at is None:
            return False
        if now - self.last_face_seen_at > FACE_DETECTION_FRESH_SECONDS:
            return False
        if (
            not force
            and now - self.last_face_command_at
            < FACE_COMMAND_MIN_INTERVAL_SECONDS
        ):
            return False

        current_yaw, current_pitch = self._get_head_orientation()

        face_x, face_y, _ = self.tracked_face
        horizontal_error = 0.5 - face_x
        vertical_error = face_y - 0.5
        outside_dead_zone = (
            abs(horizontal_error) >= FACE_CENTER_DEAD_ZONE
            or abs(vertical_error) >= FACE_CENTER_DEAD_ZONE
        )
        if not outside_dead_zone:
            # If a previous target is still pulling the head after the face has
            # become centered, replace it once with the current absolute pose.
            if (
                self.last_commanded_head is not None
                and not self._head_reached(
                    self.last_commanded_head,
                    FACE_COMMAND_SETTLE_TOLERANCE_DEGREES,
                )
            ):
                sent = self._publish_head_target(
                    current_yaw,
                    current_pitch,
                    FACE_YAW_LIMITS,
                    FACE_PITCH_LIMITS,
                    now,
                )
                if sent:
                    self.last_face_command_at = now
                return sent
            return False

        # Convert the filtered image error into a camera-relative angle, then
        # add it to live joint feedback to obtain one absolute destination.
        yaw_change = 0.0
        pitch_change = 0.0
        if abs(horizontal_error) >= FACE_CENTER_DEAD_ZONE:
            yaw_change = (
                image_error_to_angle(
                    horizontal_error,
                    CAMERA_HORIZONTAL_FOV_DEGREES,
                )
                * FACE_YAW_DIRECTION
            )
        if abs(vertical_error) >= FACE_CENTER_DEAD_ZONE:
            pitch_change = (
                image_error_to_angle(
                    vertical_error,
                    CAMERA_VERTICAL_FOV_DEGREES,
                )
                * FACE_PITCH_DIRECTION
            )
        target = (
            clamp(current_yaw + yaw_change, FACE_YAW_LIMITS),
            clamp(current_pitch + pitch_change, FACE_PITCH_LIMITS),
        )

        target_change = math.hypot(
            target[0] - current_yaw,
            target[1] - current_pitch,
        )
        if target_change < FACE_MIN_TARGET_CHANGE_DEGREES:
            return False

        previous_target_settled = (
            self.last_commanded_head is None
            or self._head_reached(
                self.last_commanded_head,
                FACE_COMMAND_SETTLE_TOLERANCE_DEGREES,
            )
        )
        replacement_change = float("inf")
        if self.last_commanded_head is not None:
            replacement_change = math.hypot(
                target[0] - self.last_commanded_head[0],
                target[1] - self.last_commanded_head[1],
            )
            if replacement_change < FACE_TARGET_REUSE_TOLERANCE_DEGREES:
                return False

        visitor_moved_substantially = (
            replacement_change >= FACE_MOVED_OVERRIDE_DEGREES
        )
        settle_wait_expired = (
            now - self.last_face_command_at
            >= FACE_COMMAND_MAX_SETTLE_WAIT_SECONDS
        )
        if (
            not force
            and not previous_target_settled
            and not visitor_moved_substantially
            and not settle_wait_expired
        ):
            return False

        sent = self._publish_head_target(
            target[0],
            target[1],
            FACE_YAW_LIMITS,
            FACE_PITCH_LIMITS,
            now,
        )
        if sent:
            self.last_face_command_at = now
        return sent

    def _set_expression(self, expression, now, force=False):
        if not force and expression == self.current_expression:
            return
        if (
            not force
            and now - self.last_expression_attempt_at < EXPRESSION_RETRY_SECONDS
        ):
            return

        self.last_expression_attempt_at = now
        try:
            response = self.emotion_client(name=expression)
        except (rospy.ServiceException, rospy.ROSException) as error:
            rospy.logwarn_throttle(
                LOG_WARNING_THROTTLE_SECONDS,
                "Could not show expression %s: %s",
                expression,
                error,
            )
            return
        if not response.status:
            rospy.logwarn("QT rejected expression: %s", expression)
            return
        self.current_expression = expression

    def _apply_demo_state_change(self, demo_active, now):
        if demo_active == self.applied_demo_active:
            return
        self.applied_demo_active = demo_active
        self._cancel_idle_scan(now, restart_interval=True)
        self.smile_ends_at = None
        self.next_smile_at = now + random_duration(SMILE_INTERVAL_SECONDS)
        self._set_expression(NEUTRAL_EXPRESSION, now)

    def _cancel_idle_scan(self, now, restart_interval):
        was_active = bool(self.idle_scan_targets)
        self.idle_scan_targets = []
        self.idle_scan_target_index = 0
        self.idle_scan_commanded_at = None
        self.idle_scan_reached_at = None
        if restart_interval:
            self.next_idle_scan_at = now + IDLE_SCAN_INTERVAL_SECONDS
        return was_active

    def _command_current_idle_target(self, now):
        target = self.idle_scan_targets[self.idle_scan_target_index]
        self._publish_head_target(
            target[0],
            target[1],
            IDLE_YAW_LIMITS,
            IDLE_PITCH_LIMITS,
            now,
            force=True,
        )
        self.idle_scan_commanded_at = now
        self.idle_scan_reached_at = None

    @staticmethod
    def _random_idle_target(left_side):
        if left_side:
            yaw = random.uniform(*IDLE_LEFT_YAW_RANGE_DEGREES)
        else:
            yaw = -random.uniform(
                *IDLE_RIGHT_YAW_MAGNITUDE_RANGE_DEGREES
            )

        pitch_sign = random.choice(
            (IDLE_UP_PITCH_SIGN, IDLE_DOWN_PITCH_SIGN)
        )
        pitch = pitch_sign * random.uniform(
            *IDLE_PITCH_MAGNITUDE_RANGE_DEGREES
        )
        return (
            round(yaw, IDLE_TARGET_DECIMAL_PLACES),
            round(pitch, IDLE_TARGET_DECIMAL_PLACES),
        )

    def _start_idle_scan(self, now):
        starts_left = self.next_idle_scan_starts_left
        self.next_idle_scan_starts_left = not starts_left
        selected = [
            self._random_idle_target(
                left_side=(starts_left if index % 2 == 0 else not starts_left)
            )
            for index in range(IDLE_SCAN_SIDE_MOVEMENTS)
        ]
        self.idle_scan_targets = selected + [HEAD_CENTER]
        self.idle_scan_target_index = 0
        self.next_idle_scan_at = now + IDLE_SCAN_INTERVAL_SECONDS
        self._command_current_idle_target(now)
        rospy.loginfo("Starting idle look-around sequence: %s", selected)

    def _update_idle_scan(self, now):
        if not self.idle_scan_targets:
            if now >= self.next_idle_scan_at:
                self._start_idle_scan(now)
            return

        target = self.idle_scan_targets[self.idle_scan_target_index]
        returning_to_center = (
            self.idle_scan_target_index == len(self.idle_scan_targets) - 1
        )
        reached = self._head_reached(
            target,
            IDLE_SCAN_TARGET_TOLERANCE_DEGREES,
        )
        move_timed_out = (
            self.idle_scan_commanded_at is not None
            and now - self.idle_scan_commanded_at
            >= IDLE_SCAN_MOVE_TIMEOUT_SECONDS
        )
        if not reached and not move_timed_out:
            return

        if returning_to_center:
            self._cancel_idle_scan(now, restart_interval=True)
            rospy.loginfo("Idle look-around complete; waiting at center.")
            return

        if self.idle_scan_reached_at is None:
            self.idle_scan_reached_at = now
            return
        if now - self.idle_scan_reached_at < IDLE_SCAN_POSE_HOLD_SECONDS:
            return

        self.idle_scan_target_index += 1
        self._command_current_idle_target(now)

    def _run_no_face_demo_off(self, now):
        self._update_idle_scan(now)

        if self.smile_ends_at is not None:
            if now >= self.smile_ends_at:
                self._set_expression(NEUTRAL_EXPRESSION, now)
                if self.current_expression == NEUTRAL_EXPRESSION:
                    self.smile_ends_at = None
                    self.next_smile_at = now + random_duration(
                        SMILE_INTERVAL_SECONDS
                    )
        elif now >= self.next_smile_at:
            self._set_expression(SMILE_EXPRESSION, now)
            if self.current_expression == SMILE_EXPRESSION:
                self.smile_ends_at = now + SMILE_DURATION_SECONDS

    def _run_no_face_demo_on(self, now):
        self._cancel_idle_scan(now, restart_interval=True)
        self._publish_head_target(
            *HEAD_CENTER,
            IDLE_YAW_LIMITS,
            IDLE_PITCH_LIMITS,
            now,
        )
        self.smile_ends_at = None
        self._set_expression(NEUTRAL_EXPRESSION, now)

    def run(self):
        rospy.sleep(STARTUP_DELAY_SECONDS)
        self._initialize_head_from_feedback()
        try:
            rospy.wait_for_service(
                EXPRESSION_SERVICE,
                timeout=EXPRESSION_SERVICE_WAIT_SECONDS,
            )
        except rospy.ROSException:
            rospy.logwarn(
                "Expression service was not ready after %.1f seconds; "
                "the node will keep retrying.",
                EXPRESSION_SERVICE_WAIT_SECONDS,
            )

        now = time.monotonic()
        self._set_expression(NEUTRAL_EXPRESSION, now, force=True)
        self._publish_head_target(
            *HEAD_CENTER,
            IDLE_YAW_LIMITS,
            IDLE_PITCH_LIMITS,
            now,
            force=True,
        )
        rospy.loginfo(
            "QT standby %s started. Face tracking is always active; "
            "demo is %s (face-loss grace %.1fs standby / %.1fs demo).",
            STAND_BUILD_VERSION,
            self._demo_label(self._get_demo_active()),
            FACE_LOST_GRACE_SECONDS,
            FACE_LOST_GRACE_SECONDS + DEMO_FACE_LOST_EXTRA_GRACE_SECONDS,
        )

        rate = rospy.Rate(MAIN_LOOP_HZ)
        while not rospy.is_shutdown():
            now = time.monotonic()
            demo_active = self._get_demo_active()
            self._apply_demo_state_change(demo_active, now)

            detections = self._detect_faces(now)
            self._update_face_lock(detections, now)
            face_active = self._face_is_active(now, demo_active)

            if face_active:
                if not self.face_was_active:
                    self._cancel_idle_scan(now, restart_interval=True)
                    sent = self._target_tracked_face(now, force=True)
                    if not sent:
                        # Supersede any unfinished idle target with one command
                        # for the head's current absolute orientation.
                        current_yaw, current_pitch = self._get_head_orientation()
                        self._publish_head_target(
                            current_yaw,
                            current_pitch,
                            FACE_YAW_LIMITS,
                            FACE_PITCH_LIMITS,
                            now,
                            force=True,
                        )
                        self.last_face_command_at = now
                else:
                    self._target_tracked_face(now)
                # Keep requesting neutral after a transient service failure or
                # a smile-to-face transition that occurred during cooldown.
                self._set_expression(NEUTRAL_EXPRESSION, now)
                if not self.face_was_active:
                    self.smile_ends_at = None
                    rospy.loginfo("Face acquired; continuous tracking active.")
            else:
                if self.face_was_active:
                    self._cancel_idle_scan(now, restart_interval=True)
                    self._publish_head_target(
                        *HEAD_CENTER,
                        IDLE_YAW_LIMITS,
                        IDLE_PITCH_LIMITS,
                        now,
                    )
                    self.next_smile_at = now + random_duration(
                        SMILE_INTERVAL_SECONDS
                    )
                    rospy.loginfo("Face lost; applying no-face standby behaviour.")
                if demo_active:
                    self._run_no_face_demo_on(now)
                else:
                    self._run_no_face_demo_off(now)

            self.face_was_active = face_active
            rate.sleep()

    def shutdown(self):
        if self.shutdown_complete:
            return
        self.shutdown_complete = True
        rospy.loginfo("Returning QTrobot to its centered neutral state.")
        if self.control_server is not None:
            self.control_server.shutdown()
            self.control_server.server_close()
        if self.control_server_thread is not None:
            self.control_server_thread.join(timeout=1.0)
        now = time.monotonic()
        self._set_expression(NEUTRAL_EXPRESSION, now, force=True)
        self.head_publisher.publish(
            Float64MultiArray(data=[HEAD_CENTER[0], HEAD_CENTER[1]])
        )
        time.sleep(0.2)


def main():
    rospy.init_node(NODE_NAME, anonymous=False)
    stand = None
    try:
        stand = QTStand()
        rospy.on_shutdown(stand.shutdown)
        stand.run()
    except RuntimeError as error:
        rospy.logfatal("QT standby could not start: %s", error)
        raise SystemExit(1)
    except rospy.ROSInterruptException:
        pass
    finally:
        if stand is not None:
            stand.shutdown()


if __name__ == "__main__":
    main()
