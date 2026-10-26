"""QTrobot microphone demo with phase-synchronised standby behaviour.

The conversation and microphone run on Windows. ``qt/stand.py`` keeps running
on QTPC, where it owns camera processing, face tracking, and idle head motion.
This adapter switches only stand.py's demo-state boolean over its small HTTP
control endpoint; it never starts or stops the tracker.
"""

from __future__ import annotations

import argparse
import json
import queue
import re
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from robond.robot.remote import RemoteRobotInterface

from qt.qt_mic_test import (
    COOLDOWN_SECONDS,
    MIC_START_ATTEMPTS,
    MicrophoneInput,
    QT_HOST,
    QT_PORT,
    ROBOT_CONNECT_ATTEMPTS,
    TestListener,
    microphone_description,
    parse_device,
    print_devices,
)
from qt.responses import TutorEngine, TutorTurn
from shared.llm import LLM
from shared.mic import ContinuousMic, ContinuousMicError


STANDBY_PHASE = "STANDBY"
DONE_PHASE = "DONE"

# stand.py's dedicated control endpoint on QTPC. RoboBOND remains unchanged on
# port 7342; this endpoint carries only {"active": true/false}.
STAND_CONTROL_PORT = 7343
STAND_CONTROL_TIMEOUT_SECONDS = 3.0
STAND_CONTROL_ATTEMPTS = 3
STAND_CONTROL_RETRY_SECONDS = 0.5

# While a visitor conversation is active, return to STANDBY if the microphone
# produces no completed utterance for this long. STANDBY itself keeps listening
# indefinitely for the next visitor.
ACTIVE_CONVERSATION_INACTIVITY_SECONDS = 60.0

# Start capturing the visitor shortly before QT's final utterance completes.
# RoboBOND's /text request reports only completion, not remaining duration, so
# the target time is predicted from text length and continuously calibrated
# against QT's measured speaking time. Krisp voice isolation is expected to
# remove QT's own speech from the Windows microphone input.
MIC_PRELISTEN_SECONDS = 1.2
SPEECH_TIMING_SAFETY_SECONDS = 0.25
SPEECH_TIMING_INITIAL_BASE_SECONDS = 0.45
SPEECH_TIMING_INITIAL_SECONDS_PER_WORD = 0.40
SPEECH_TIMING_COMMA_PAUSE_SECONDS = 0.12
SPEECH_TIMING_SENTENCE_PAUSE_SECONDS = 0.20
SPEECH_TIMING_EMA_WEIGHT = 0.35
SPEECH_TIMING_MIN_SECONDS_PER_WORD = 0.15
SPEECH_TIMING_MAX_SECONDS_PER_WORD = 1.00

_SPOKEN_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


class StandbyControlError(RuntimeError):
    """Raised when Windows cannot synchronize the QTPC standby node."""


class MicrophoneInactivityTimeout(TimeoutError):
    """Raised when an active visitor stops responding for too long."""


class DemoMicrophoneInput(MicrophoneInput):
    """Microphone input supporting pre-listening and an inactivity deadline."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._primed = False

    def _announce_microphone(self) -> None:
        if not self._announced:
            self.console_print(
                f"Microphone: {microphone_description(self.microphone.device)}"
            )
            self._announced = True

    def prime(self) -> None:
        """Begin capture now and preserve early speech for the next listen()."""
        self._drain()
        self._start()
        self._primed = True
        self._announce_microphone()

    def stop(self) -> None:
        self._primed = False
        super().stop()

    def listen(self, timeout_seconds: float | None = None) -> str:
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        # prime() may already have captured the start of the visitor's answer.
        # In that case, restarting or draining here would lose precisely the
        # audio this adapter is intended to retain.
        use_primed_capture = self._primed and self.microphone.is_running
        self._primed = False
        if not use_primed_capture:
            self._drain()
            self._start()
        self._announce_microphone()

        if timeout_seconds is None:
            self.console_print("[LISTENING] Waiting for the child...")
            deadline = None
        else:
            self.console_print(
                "[LISTENING] Waiting for the child "
                f"({timeout_seconds:.0f}s inactivity timeout)..."
            )
            deadline = time.monotonic() + timeout_seconds

        try:
            while True:
                if deadline is None:
                    queue_timeout = 0.2
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise MicrophoneInactivityTimeout(
                            "no completed utterance for "
                            f"{timeout_seconds:.0f} seconds"
                        )
                    queue_timeout = min(0.2, remaining)
                try:
                    kind, value = self._events.get(timeout=queue_timeout)
                except queue.Empty:
                    continue
                if kind == "error":
                    raise ContinuousMicError(value)

                self.stop()
                self.console_print(f"Kind: {value}")
                return value
        except BaseException:
            self.stop()
            raise


class SpeechDurationEstimator:
    """Estimate blocking QT speech time and learn from completed utterances."""

    def __init__(self) -> None:
        self.seconds_per_word = SPEECH_TIMING_INITIAL_SECONDS_PER_WORD

    @staticmethod
    def _parts(text: str) -> tuple[int, float]:
        word_count = max(1, len(_SPOKEN_WORD_RE.findall(text)))
        pause_seconds = (
            text.count(",") * SPEECH_TIMING_COMMA_PAUSE_SECONDS
            + sum(text.count(mark) for mark in ".!?")
            * SPEECH_TIMING_SENTENCE_PAUSE_SECONDS
        )
        return word_count, pause_seconds

    def estimate(self, text: str) -> float:
        word_count, pause_seconds = self._parts(text)
        return (
            SPEECH_TIMING_INITIAL_BASE_SECONDS
            + word_count * self.seconds_per_word
            + pause_seconds
        )

    def observe(self, text: str, elapsed_seconds: float) -> None:
        word_count, pause_seconds = self._parts(text)
        observed = (
            elapsed_seconds
            - SPEECH_TIMING_INITIAL_BASE_SECONDS
            - pause_seconds
        ) / word_count
        observed = max(
            SPEECH_TIMING_MIN_SECONDS_PER_WORD,
            min(SPEECH_TIMING_MAX_SECONDS_PER_WORD, observed),
        )
        weight = SPEECH_TIMING_EMA_WEIGHT
        self.seconds_per_word = (
            (1.0 - weight) * self.seconds_per_word + weight * observed
        )


def _say_and_measure(
    robot: Any,
    utterance: str,
    timing: SpeechDurationEstimator,
    console_print: Callable[..., None],
) -> None:
    started = time.monotonic()
    try:
        robot.say(utterance)
    except Exception as error:
        console_print(f"[QT SPEECH ERROR] {error}")
    finally:
        timing.observe(utterance, time.monotonic() - started)


def _say_with_prelisten(
    robot: Any,
    utterance: str,
    microphone_input: DemoMicrophoneInput,
    timing: SpeechDurationEstimator,
    console_print: Callable[..., None],
) -> None:
    """Speak asynchronously and prime the mic near the predicted ending."""
    completed = threading.Event()
    speech_errors: list[Exception] = []
    started = time.monotonic()

    def run_speech() -> None:
        try:
            robot.say(utterance)
        except Exception as error:
            speech_errors.append(error)
        finally:
            completed.set()

    speech_thread = threading.Thread(
        target=run_speech,
        name="qt-speech",
        daemon=True,
    )
    speech_thread.start()

    estimated_seconds = timing.estimate(utterance)
    start_after_seconds = max(
        0.0,
        estimated_seconds
        - MIC_PRELISTEN_SECONDS
        - SPEECH_TIMING_SAFETY_SECONDS,
    )
    completed.wait(timeout=start_after_seconds)

    microphone_input.prime()
    console_print("[LISTENING] Started listening.")

    # Preserve any visitor audio captured during this wait. listen() will use
    # the already-running stream without clearing its event queue.
    completed.wait()
    speech_thread.join()
    timing.observe(utterance, time.monotonic() - started)
    if speech_errors:
        console_print(f"[QT SPEECH ERROR] {speech_errors[0]}")


def speak_turn(
    turn: TutorTurn,
    robot: Any,
    microphone_input: DemoMicrophoneInput,
    timing: SpeechDurationEstimator,
    *,
    cooldown_seconds: float = COOLDOWN_SECONDS,
    console_print: Callable[..., None] = print,
) -> None:
    """Speak a turn and pre-listen during its final expected-response prompt."""
    microphone_input.stop()
    for diagnostic in turn.diagnostics:
        console_print(diagnostic)

    final_index = len(turn.utterances) - 1
    for index, utterance in enumerate(turn.utterances):
        console_print(f"QT: {utterance}")
        should_prelisten = turn.awaiting_input and index == final_index
        if should_prelisten:
            console_print(
                "[SPEAKING] QT is speaking; early microphone overlap is armed."
            )
            _say_with_prelisten(
                robot,
                utterance,
                microphone_input,
                timing,
                console_print,
            )
        else:
            console_print("[SPEAKING] QT is speaking; microphone is stopped.")
            _say_and_measure(robot, utterance, timing, console_print)
        if cooldown_seconds > 0:
            time.sleep(cooldown_seconds)


class StandbyController:
    """Reliable client for stand.py's narrow demo-state HTTP interface."""

    def __init__(
        self,
        host: str,
        port: int = STAND_CONTROL_PORT,
        *,
        timeout: float = STAND_CONTROL_TIMEOUT_SECONDS,
        attempts: int = STAND_CONTROL_ATTEMPTS,
        retry_delay: float = STAND_CONTROL_RETRY_SECONDS,
        console_print: Callable[..., None] = print,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.attempts = attempts
        self.retry_delay = retry_delay
        self.console_print = console_print
        self._last_active: bool | None = None

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                details = error.read().decode("utf-8", errors="replace")
            except Exception:
                details = ""
            suffix = f": {details}" if details else ""
            raise StandbyControlError(
                f"stand.py returned HTTP {error.code}{suffix}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise StandbyControlError(
                f"cannot reach stand.py at {self.base_url}: {error}"
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise StandbyControlError(
                "stand.py returned an invalid JSON response"
            ) from error

        if not isinstance(response_payload, dict) or not response_payload.get("ok"):
            raise StandbyControlError(
                f"stand.py rejected the request: {response_payload!r}"
            )
        return response_payload

    def _with_retries(
        self,
        operation: Callable[[], dict[str, Any]],
        description: str,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                return operation()
            except StandbyControlError as error:
                last_error = error
                self.console_print(
                    f"[STANDBY CONTROL] {description} attempt "
                    f"{attempt}/{self.attempts} failed: {error}"
                )
                if attempt < self.attempts:
                    time.sleep(self.retry_delay)
        raise StandbyControlError(
            f"{description} failed after {self.attempts} attempts"
        ) from last_error

    def connect(self) -> bool:
        """Check the remote node and adopt its reported current state."""
        response = self._with_retries(
            lambda: self._request("GET", "/health"),
            "health check",
        )
        active = response.get("demo_active")
        if not isinstance(active, bool):
            raise StandbyControlError("stand.py health response has no boolean state")
        self._last_active = active
        return active

    def set_demo_active(self, active: bool, *, force: bool = False) -> None:
        """Switch active/standby behaviour without stopping face tracking."""
        active = bool(active)
        if not force and active == self._last_active:
            return
        response = self._with_retries(
            lambda: self._request("PUT", "/demo", {"active": active}),
            "demo-state update",
        )
        reported = response.get("demo_active")
        if reported != active:
            raise StandbyControlError(
                f"stand.py reported unexpected demo state: {reported!r}"
            )
        self._last_active = active
        label = "DEMO" if active else "STANDBY"
        self.console_print(f"[QT MODE] {label}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Dutch QT math tutor with microphone input and continuous "
            "QTPC standby/face tracking."
        )
    )
    parser.add_argument(
        "--device",
        type=parse_device,
        help="laptop microphone index or name (Windows default when omitted)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="list available Windows input devices and exit",
    )
    parser.add_argument("--host", default=QT_HOST, help="QT RoboBOND gateway host")
    parser.add_argument(
        "--port",
        type=int,
        default=QT_PORT,
        help="QT RoboBOND gateway port",
    )
    parser.add_argument(
        "--stand-host",
        help="QTPC host for stand.py control (defaults to --host)",
    )
    parser.add_argument(
        "--stand-port",
        type=int,
        default=STAND_CONTROL_PORT,
        help="stand.py control port",
    )
    return parser.parse_args(argv)


def connect_robot(
    host: str,
    port: int,
    *,
    attempts: int = ROBOT_CONNECT_ATTEMPTS,
    retry_delay: float = 1.5,
    robot_factory: Callable[..., Any] = RemoteRobotInterface,
    console_print: Callable[..., None] = print,
) -> Any:
    """Connect without overriding the face expression owned by stand.py."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            robot = robot_factory(host=host, port=port, listener=TestListener())
            robot.set_language("nl")
            return robot
        except Exception as error:
            last_error = error
            console_print(
                f"[QT CONNECTION] Attempt {attempt}/{attempts} failed: {error}"
            )
            if attempt < attempts:
                time.sleep(retry_delay)
    raise ConnectionError(
        f"Could not connect to the QT RoboBOND proxy at {host}:{port}"
    ) from last_error


def phase_uses_standby(phase: str) -> bool:
    return phase in {STANDBY_PHASE, DONE_PHASE}


def prepare_turn_mode(controller: StandbyController, phase: str) -> None:
    """Enter DEMO before active speech; leave farewell speech undisturbed."""
    if not phase_uses_standby(phase):
        controller.set_demo_active(True)


def finish_turn_mode(controller: StandbyController, phase: str) -> None:
    """Enter STANDBY only after any final/farewell utterance has completed."""
    if phase_uses_standby(phase):
        controller.set_demo_active(False)


def main(args: argparse.Namespace | None = None) -> int:
    args = parse_args() if args is None else args
    if args.list_devices:
        try:
            print_devices()
            return 0
        except ContinuousMicError as error:
            print(f"[MIC ERROR] {error}")
            return 1

    stand_host = args.stand_host or args.host
    standby = StandbyController(stand_host, args.stand_port)
    try:
        current_state = standby.connect()
        print(
            f"Connected to stand.py at {standby.base_url} "
            f"(demo_active={current_state})."
        )
        standby.set_demo_active(False, force=True)
    except StandbyControlError as error:
        print(f"[STANDBY CONTROL ERROR] {error}")
        print(
            "Deploy and restart the updated qt/stand.py on QTPC, then try again."
        )
        return 1

    print(f"Connecting to QT at {args.host}:{args.port}...")
    try:
        robot = connect_robot(args.host, args.port)
    except Exception as error:
        print(f"[QT CONNECTION ERROR] {error}")
        return 1
    print("QT connected.")

    try:
        microphone = ContinuousMic(
            device=args.device,
            vad="silero",
            model_size="medium",
            language="nl",
            hotwords="ARI QT FURHAT",
            enable_partials=False,
            end_silence_ms=800,
        )
    except Exception as error:
        print(f"[MIC ERROR] Microphone setup failed: {error}")
        return 1

    microphone_input = DemoMicrophoneInput(
        microphone,
        start_attempts=MIC_START_ATTEMPTS,
    )
    speech_timing = SpeechDurationEstimator()
    exit_code = 0
    try:
        llm = LLM()
        engine = TutorEngine(llm)
        turn = engine.start()
        while True:
            phase = str(engine.state["phase"])
            prepare_turn_mode(standby, phase)
            speak_turn(
                turn,
                robot,
                microphone_input,
                speech_timing,
                cooldown_seconds=COOLDOWN_SECONDS,
            )
            finish_turn_mode(standby, phase)
            if engine.is_done:
                break
            inactivity_timeout = (
                None
                if phase_uses_standby(phase)
                else ACTIVE_CONVERSATION_INACTIVITY_SECONDS
            )
            try:
                transcript = microphone_input.listen(
                    timeout_seconds=inactivity_timeout
                )
            except MicrophoneInactivityTimeout:
                print(
                    "[INACTIVITY] No visitor response for "
                    f"{ACTIVE_CONVERSATION_INACTIVITY_SECONDS:.0f} seconds; "
                    "returning to STANDBY."
                )
                standby.set_demo_active(False)
                engine = TutorEngine(llm)
                turn = engine.start()
                continue
            turn = engine.handle_input(transcript)
    except KeyboardInterrupt:
        print("\n[Demo stopped]")
    except StandbyControlError as error:
        print(f"[STANDBY CONTROL ERROR] {error}")
        exit_code = 1
    except ContinuousMicError as error:
        print(f"[MIC ERROR] {error}")
        exit_code = 1
    except Exception as error:
        print(f"[TUTOR ERROR] {type(error).__name__}: {error}")
        exit_code = 1
    finally:
        # Leave face tracking running and return only its no-visitor behaviour
        # to STANDBY. Never stop the remote RoboBOND proxy.
        microphone_input.close()
        try:
            standby.set_demo_active(False, force=True)
        except StandbyControlError as error:
            print(f"[STANDBY RESTORE WARNING] {error}")

    print("[Demo finished]")
    print("Stopped. Local microphone resources have been released.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
