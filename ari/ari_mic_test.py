"""ARI robot and microphone adapter for the shared conversation engine."""

from __future__ import annotations

import argparse
import os
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from difflib import SequenceMatcher
from typing import Any

from robond.core.listener_backend import ListenerBackend
from robond.core.robot_interface import Expression
from robond.robot.remote import RemoteRobotInterface

from ari.emotes import gesture_for
from ari.engines import MODES, build_engine
from ari.operator_ui import (
    LocalOperatorServer,
    RuntimeCoordinator,
    SpeechLockedRobot,
    UI_HOST,
    UI_PORT,
)
from ari.responses import ConversationEngine, ConversationTurn
from ari.social_control import ARISocialClient, CONTROL_PORT
from shared.config import ARI_IP, REMOTE_PORT
from shared.llm import LLM
from shared.mic import ContinuousMic, ContinuousMicError, MicEventType


COOLDOWN_SECONDS = 0.4
ROBOT_CONNECT_ATTEMPTS = 3
MIC_START_ATTEMPTS = 2
GUARDIAN_IDLE_SECONDS = 60.0

# During the short microphone/speech overlap, acoustic isolation can still let
# part of ARI's own voice through. Keep a brief history and reject only close,
# multi-word matches; short visitor answers and names are never filtered.
ROBOT_ECHO_MEMORY_SECONDS = 8.0
ROBOT_ECHO_MIN_WORDS = 4
ROBOT_ECHO_OVERLAP_SIMILARITY = 0.76
ROBOT_ECHO_RECENT_SIMILARITY = 0.84
ROBOT_ECHO_MAX_UTTERANCES = 8

# ARI's blocking speech request does not expose remaining playback time. Start
# the microphone from an adaptive duration estimate, calibrated against ARI's
# completed utterances. Voice isolation reduces feedback, while the adapter's
# echo guard handles occasional speech that still reaches transcription.
MIC_PRELISTEN_SECONDS = 1.0
SPEECH_TIMING_SAFETY_SECONDS = 0.25
SPEECH_TIMING_INITIAL_BASE_SECONDS = 0.45
SPEECH_TIMING_INITIAL_SECONDS_PER_WORD = 0.40
SPEECH_TIMING_COMMA_PAUSE_SECONDS = 0.12
SPEECH_TIMING_SENTENCE_PAUSE_SECONDS = 0.20
SPEECH_TIMING_EMA_WEIGHT = 0.35
SPEECH_TIMING_MIN_SECONDS_PER_WORD = 0.15
SPEECH_TIMING_MAX_SECONDS_PER_WORD = 1.00

_SPOKEN_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


class TestListener(ListenerBackend):
    """RoboBOND listener placeholder; the laptop microphone supplies input."""

    def listen(self, timeout=10, silence_timeout=None):
        return "[TIMEOUT]"


def parse_device(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ARI's Dutch conversation with spoken input and output."
    )
    parser.add_argument("--host", default=ARI_IP, help="ARI RoboBOND proxy host")
    parser.add_argument(
        "--port",
        type=int,
        default=REMOTE_PORT,
        help="ARI RoboBOND proxy port",
    )
    parser.add_argument(
        "--language",
        default="nl_NL",
        help="ARI text-to-speech language",
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
    parser.add_argument(
        "--ui",
        action="store_true",
        help="run the local operator interface",
    )
    parser.add_argument(
        "--ui-host",
        default=UI_HOST,
        help="operator UI bind address (localhost by default)",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=UI_PORT,
        help="operator UI port",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=CONTROL_PORT,
        help="optional ARI ROS social-control sidecar port",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=os.environ.get("ARI_MODE") if os.environ.get("ARI_MODE") in MODES else "guardian",
        help="guardian: the VUmc guide; companion: the open conversation demo "
        "(the robot hub passes its choice in ARI_MODE)",
    )
    parser.add_argument(
        "--idle-seconds",
        type=float,
        help="forget the visitor after this long without speech "
        f"(guardian default {GUARDIAN_IDLE_SECONDS:.0f}, companion off; 0 turns it off)",
    )
    return parser.parse_args(argv)


def idle_seconds_for(args: argparse.Namespace) -> float | None:
    value = getattr(args, "idle_seconds", None)
    if value is None:
        return GUARDIAN_IDLE_SECONDS if getattr(args, "mode", "companion") == "guardian" else None
    return value or None


def print_devices() -> None:
    devices = ContinuousMic.list_microphones()
    if not devices:
        print("No input devices found.")
        return
    for device in devices:
        print(
            f"{device.index:>3}: {device.name} "
            f"({device.max_input_channels} input channel(s), "
            f"default {device.default_sample_rate:.0f} Hz)"
        )


def microphone_description(device: int | str | None) -> str:
    try:
        import sounddevice as sd

        info = sd.query_devices(device, "input")
        selection = sd.default.device[0] if device is None else device
        return f"{info['name']} (device {selection})"
    except Exception as error:
        selection = "Windows default" if device is None else device
        return f"{selection} (name unavailable: {error})"


def connect_robot(
    host: str,
    port: int,
    language: str,
    *,
    attempts: int = ROBOT_CONNECT_ATTEMPTS,
    retry_delay: float = 1.5,
    robot_factory: Callable[..., Any] = RemoteRobotInterface,
    console_print: Callable[..., None] = print,
) -> Any:
    """Connect through the ARI RoboBOND proxy used by the legacy demo."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            robot = robot_factory(host=host, port=port, listener=TestListener())
            robot.set_language(language)
            return robot
        except Exception as error:
            last_error = error
            console_print(
                f"[ARI CONNECTION] Attempt {attempt}/{attempts} failed: {error}"
            )
            if attempt < attempts:
                time.sleep(retry_delay)
    raise ConnectionError(
        f"Could not connect to the ARI RoboBOND proxy at {host}:{port}"
    ) from last_error


class ListeningCancelled(Exception):
    """Raised when the operator resets a blocked listening turn."""


class MicrophoneInput:
    """Collect exactly one non-empty final transcript per visitor turn."""

    def __init__(
        self,
        microphone: Any,
        *,
        start_attempts: int = MIC_START_ATTEMPTS,
        console_print: Callable[..., None] = print,
    ) -> None:
        self.microphone = microphone
        self.start_attempts = start_attempts
        self.console_print = console_print
        self._events: queue.Queue[tuple[str, str, bool]] = queue.Queue()
        self._announced = False
        self._primed = False
        self._overlap_utterance_ids: set[int] = set()
        self._echo_lock = threading.RLock()
        self._recent_robot_utterances: deque[tuple[float, str]] = deque(
            maxlen=ROBOT_ECHO_MAX_UTTERANCES
        )
        self.last_transcript_overlapped = False
        self.microphone.on("*", self._handle_event)

    def _handle_event(self, event: Any) -> None:
        if event.type == MicEventType.INTERRUPTION_DETECTED:
            utterance_id = getattr(event, "utterance_id", None)
            if utterance_id is not None:
                self._overlap_utterance_ids.add(utterance_id)
        elif event.type == MicEventType.FINAL_TRANSCRIPT:
            transcript = (event.text or "").strip()
            utterance_id = getattr(event, "utterance_id", None)
            overlapped = utterance_id in self._overlap_utterance_ids
            if utterance_id is not None:
                self._overlap_utterance_ids.discard(utterance_id)
            if transcript:
                self._events.put(("transcript", transcript, overlapped))
            else:
                self.console_print(
                    "[LISTENING] No text recognized; please try speaking again."
                )
        elif event.type == MicEventType.ERROR:
            message = event.data.get("message", "Microphone error")
            self.console_print(f"[MIC ERROR] {message}")
            if not event.data.get("recoverable", True):
                self._events.put(("error", message, False))

    def _drain(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                return

    def stop(self) -> None:
        self._primed = False
        if self.microphone.is_running:
            self.microphone.stop()
        self._drain()

    def cancel_pending(self) -> None:
        """Stop capture and wake a currently blocked listen() call."""
        self.stop()
        self._events.put(("cancel", "", False))

    def set_robot_speaking(self, speaking: bool) -> None:
        """Expose ARI's speech state to the streaming microphone."""
        setter = getattr(self.microphone, "set_robot_speaking", None)
        if setter is not None:
            setter(bool(speaking))

    def note_robot_utterance(
        self,
        utterance: str,
        *,
        now: float | None = None,
    ) -> None:
        normalized = self._normalize_for_echo(utterance)
        if not normalized:
            return
        with self._echo_lock:
            self._recent_robot_utterances.append(
                (time.monotonic() if now is None else now, normalized)
            )

    def is_probable_robot_echo(
        self,
        transcript: str,
        *,
        overlapped: bool | None = None,
        now: float | None = None,
    ) -> bool:
        """Conservatively match a transcript against ARI's recent speech."""
        normalized = self._normalize_for_echo(transcript)
        words = normalized.split()
        if len(words) < ROBOT_ECHO_MIN_WORDS:
            return False

        checked_at = time.monotonic() if now is None else now
        with self._echo_lock:
            while (
                self._recent_robot_utterances
                and checked_at - self._recent_robot_utterances[0][0]
                > ROBOT_ECHO_MEMORY_SECONDS
            ):
                self._recent_robot_utterances.popleft()
            recent = tuple(self._recent_robot_utterances)

        did_overlap = (
            self.last_transcript_overlapped
            if overlapped is None
            else overlapped
        )
        threshold = (
            ROBOT_ECHO_OVERLAP_SIMILARITY
            if did_overlap
            else ROBOT_ECHO_RECENT_SIMILARITY
        )
        for _spoken_at, robot_text in recent:
            if normalized == robot_text:
                return True
            if len(normalized) >= 14 and normalized in robot_text:
                return True
            if SequenceMatcher(None, normalized, robot_text).ratio() >= threshold:
                return True
        return False

    @staticmethod
    def _normalize_for_echo(text: str) -> str:
        return " ".join(_SPOKEN_WORD_RE.findall(text.casefold()))

    def _start(self) -> None:
        last_error: Exception | None = None
        for attempt in range(1, self.start_attempts + 1):
            try:
                self.microphone.start()
                return
            except Exception as error:
                last_error = error
                self.stop()
                self.console_print(
                    f"[MIC ERROR] Start attempt {attempt}/"
                    f"{self.start_attempts} failed: {error}"
                )
                if attempt < self.start_attempts:
                    time.sleep(0.5)
        raise ContinuousMicError("Microphone could not be started") from last_error

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

    def listen(self) -> str:
        # Do not restart or drain a stream primed during ARI's final words: it
        # may already contain the beginning of the visitor's response.
        use_primed_capture = self._primed and self.microphone.is_running
        self._primed = False
        if not use_primed_capture:
            self._drain()
            self._start()
        self._announce_microphone()
        self.console_print("[LISTENING] Waiting for the visitor...")

        try:
            while True:
                try:
                    kind, value, overlapped = self._events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if kind == "error":
                    raise ContinuousMicError(value)
                if kind == "cancel":
                    raise ListeningCancelled()

                # Stop capture and discard stale audio before ARI generates or
                # speaks a response, preventing ARI from transcribing itself.
                self.stop()
                self.last_transcript_overlapped = overlapped
                return value
        except BaseException:
            self.stop()
            raise

    def close(self) -> None:
        self.stop()
        self.microphone.off("*", self._handle_event)


class SpeechDurationEstimator:
    """Estimate blocking ARI speech time and learn from completed utterances."""

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


def _speak_once(robot: Any, utterance: str, gesture: Any = None) -> None:
    if gesture is not None:
        robot.coordinated_action(utterance, gesture=gesture)
    else:
        robot.say(utterance)


def _say_and_measure(
    robot: Any,
    utterance: str,
    timing: SpeechDurationEstimator,
    console_print: Callable[..., None],
    gesture: Any = None,
) -> None:
    started = time.monotonic()
    try:
        _speak_once(robot, utterance, gesture)
    except Exception as error:
        console_print(f"[ARI SPEECH ERROR] {error}")
    finally:
        timing.observe(utterance, time.monotonic() - started)


def _say_with_prelisten(
    robot: Any,
    utterance: str,
    microphone_input: MicrophoneInput,
    timing: SpeechDurationEstimator,
    console_print: Callable[..., None],
    should_continue: Callable[[], bool],
    gesture: Any = None,
) -> None:
    """Speak asynchronously and prime the mic near the predicted ending."""
    completed = threading.Event()
    speech_errors: list[Exception] = []
    started = time.monotonic()

    def run_speech() -> None:
        try:
            _speak_once(robot, utterance, gesture)
        except Exception as error:
            speech_errors.append(error)
        finally:
            completed.set()

    speech_thread = threading.Thread(
        target=run_speech,
        name="ari-speech",
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

    if should_continue():
        microphone_input.prime()
        console_print("[LISTENING] Started listening.")

    # Keep everything captured during the overlap for the following listen().
    completed.wait()
    speech_thread.join()
    timing.observe(utterance, time.monotonic() - started)
    if speech_errors:
        console_print(f"[ARI SPEECH ERROR] {speech_errors[0]}")


def speak_turn(
    turn: ConversationTurn,
    robot: Any,
    microphone_input: MicrophoneInput,
    timing: SpeechDurationEstimator | None = None,
    *,
    cooldown_seconds: float = COOLDOWN_SECONDS,
    console_print: Callable[..., None] = print,
    should_continue: Callable[[], bool] | None = None,
    on_speech_start: Callable[[str], bool | None] | None = None,
    on_speech_end: Callable[[], None] | None = None,
    on_utterance_face: Callable[[dict[str, Any], float], None] | None = None,
) -> None:
    """Speak a turn and pre-listen during its final expected-response prompt."""
    timing = timing or SpeechDurationEstimator()
    should_continue = should_continue or (lambda: True)
    microphone_input.stop()
    post_speech = tuple(
        diagnostic
        for diagnostic in turn.diagnostics
        if diagnostic == "[PHASE] STANDBY" and turn.utterances
    )
    for diagnostic in turn.diagnostics:
        if diagnostic in post_speech:
            continue
        console_print(diagnostic)
    final_index = len(turn.utterances) - 1
    actions = getattr(turn, "actions", ())
    for index, utterance in enumerate(turn.utterances):
        if not should_continue():
            break
        if on_speech_start is not None and on_speech_start(utterance) is False:
            break
        action = actions[index] if index < len(actions) else {}
        gesture = gesture_for(action.get("gesture"))
        if on_utterance_face is not None and action.get("expression"):
            on_utterance_face(action, timing.estimate(utterance) + 1.0)
        note_utterance = getattr(microphone_input, "note_robot_utterance", None)
        set_robot_speaking = getattr(
            microphone_input,
            "set_robot_speaking",
            None,
        )
        if set_robot_speaking is not None:
            set_robot_speaking(True)
        console_print(f"ARI: {utterance}")
        try:
            should_prelisten = (
                turn.awaiting_input
                and "[PHASE] STANDBY" not in turn.diagnostics
                and index == final_index
                and hasattr(microphone_input, "prime")
            )
            if should_prelisten:
                _say_with_prelisten(
                    robot,
                    utterance,
                    microphone_input,
                    timing,
                    console_print,
                    should_continue,
                    gesture,
                )
            else:
                console_print("[SPEAKING] ARI is speaking; microphone is stopped.")
                _say_and_measure(robot, utterance, timing, console_print, gesture)
        finally:
            # Timestamp the memory at playback completion so even a longer ARI
            # utterance remains available while its final audio is transcribed.
            if note_utterance is not None:
                note_utterance(utterance)
            if set_robot_speaking is not None:
                set_robot_speaking(False)
            if on_speech_end is not None:
                on_speech_end()
        if cooldown_seconds > 0:
            time.sleep(cooldown_seconds)
    for diagnostic in post_speech:
        console_print(diagnostic)


def main(args: argparse.Namespace | None = None) -> int:
    args = parse_args() if args is None else args
    if args.list_devices:
        try:
            print_devices()
            return 0
        except ContinuousMicError as error:
            print(f"[MIC ERROR] {error}")
            return 1

    print(f"Connecting to ARI at {args.host}:{args.port}...")
    try:
        robot = connect_robot(args.host, args.port, args.language)
    except Exception as error:
        print(f"[ARI CONNECTION ERROR] {error}")
        return 1
    print("ARI connected.")
    robot = SpeechLockedRobot(robot)
    mode = getattr(args, "mode", "companion")
    print(f"ARI mode: {mode}")

    try:
        microphone = ContinuousMic(
            device=args.device,
            vad="silero",
            model_size="small",
            language="nl",
            hotwords=(
                "ARI VUmc Amsterdam UMC polikliniek"
                if mode == "guardian"
                else "ARI QT FURHAT"
            ),
            enable_partials=False,
            end_silence_ms=800,
        )
    except Exception as error:
        print(f"[MIC ERROR] Microphone setup failed: {error}")
        return 1

    microphone_input = MicrophoneInput(microphone)
    speech_timing = SpeechDurationEstimator()
    social_control: ARISocialClient | None = None
    runtime: RuntimeCoordinator | None = None
    ui_server: LocalOperatorServer | None = None
    try:
        engine = build_engine(mode, LLM)

        def expression_fallback(name: str) -> None:
            robot.set_expression(Expression(name))

        social_control = ARISocialClient(
            args.host,
            getattr(args, "control_port", CONTROL_PORT),
            robond_port=args.port,
            expression_fallback=expression_fallback,
        )
        social_control.refresh()
        social_control.start_monitoring()
        runtime = RuntimeCoordinator(
            engine,
            robot,
            microphone_input,
            social_control,
            mode=mode,
            idle_seconds=idle_seconds_for(args),
        )
        if getattr(args, "ui", False):
            ui_server = LocalOperatorServer(
                runtime,
                getattr(args, "ui_host", UI_HOST),
                getattr(args, "ui_port", UI_PORT),
            )
            ui_server.start()
            print(
                "[ARI UI] Open http://"
                f"{getattr(args, 'ui_host', UI_HOST)}:"
                f"{getattr(args, 'ui_port', UI_PORT)}"
            )

        turn = runtime.start_engine()
        while not engine.is_done:
            turn_generation = runtime.generation
            if runtime.prepare_turn(turn, turn_generation):
                speak_turn(
                    turn,
                    robot,
                    microphone_input,
                    speech_timing,
                    should_continue=lambda: runtime.is_current(turn_generation),
                    on_speech_start=lambda utterance: runtime.begin_speaking(
                        utterance, turn_generation
                    ),
                    on_speech_end=lambda: runtime.end_speaking(turn_generation),
                    on_utterance_face=runtime.show_utterance_face,
                )
                runtime.finish_turn(turn_generation)
            if not runtime.is_current(turn_generation):
                turn = ConversationTurn(())
                continue

            listen_generation = runtime.generation
            runtime.begin_listening()
            listening_cancelled = False
            while runtime.is_current(listen_generation):
                try:
                    transcript = microphone_input.listen()
                except ListeningCancelled:
                    listening_cancelled = True
                    break
                if microphone_input.is_probable_robot_echo(transcript):
                    print("[MIC] ARI's own speech was ignored; listening continues.")
                    runtime.begin_listening()
                    continue
                print(f"Bezoeker: {transcript}")
                break
            else:
                turn = ConversationTurn(())
                continue
            if listening_cancelled:
                turn = ConversationTurn(())
                continue
            turn = runtime.handle_transcript(transcript, listen_generation)
            if turn is None:
                turn = ConversationTurn(())
    except KeyboardInterrupt:
        print("\n[Demo stopped]")
    except ContinuousMicError as error:
        print(f"[MIC ERROR] {error}")
        return 1
    except Exception as error:
        print(f"[CONVERSATION ERROR] {type(error).__name__}: {error}")
        return 1
    finally:
        # Keep the remote RoboBOND proxy alive; release only local mic resources.
        if runtime is not None:
            runtime.reset_to_standby()
            runtime.close()
        if ui_server is not None:
            ui_server.close()
        if social_control is not None:
            social_control.close()
        microphone_input.close()

    print("Stopped. Local microphone resources have been released.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
