"""ARI robot and microphone adapter for the shared conversation engine."""

from __future__ import annotations

import argparse
import queue
import time
from collections.abc import Callable, Sequence
from typing import Any

from robond.core.listener_backend import ListenerBackend
from robond.robot.remote import RemoteRobotInterface

from ari import prompts
from ari.responses import ConversationEngine, ConversationTurn
from shared.config import ARI_IP, REMOTE_PORT
from shared.llm import LLM
from shared.mic import ContinuousMic, ContinuousMicError, MicEventType


COOLDOWN_SECONDS = 0.4
ROBOT_CONNECT_ATTEMPTS = 3
MIC_START_ATTEMPTS = 2


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
    return parser.parse_args(argv)


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
        self._events: queue.Queue[tuple[str, str]] = queue.Queue()
        self._announced = False
        self.microphone.on("*", self._handle_event)

    def _handle_event(self, event: Any) -> None:
        if event.type == MicEventType.FINAL_TRANSCRIPT:
            transcript = (event.text or "").strip()
            if transcript:
                self._events.put(("transcript", transcript))
            else:
                self.console_print(
                    "[LISTENING] No text recognized; please try speaking again."
                )
        elif event.type == MicEventType.ERROR:
            message = event.data.get("message", "Microphone error")
            self.console_print(f"[MIC ERROR] {message}")
            if not event.data.get("recoverable", True):
                self._events.put(("error", message))

    def _drain(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                return

    def stop(self) -> None:
        if self.microphone.is_running:
            self.microphone.stop()
        self._drain()

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

    def listen(self) -> str:
        self._drain()
        self._start()
        if not self._announced:
            self.console_print(
                f"Microphone: {microphone_description(self.microphone.device)}"
            )
            self._announced = True
        self.console_print("[LISTENING] Waiting for the visitor...")

        try:
            while True:
                try:
                    kind, value = self._events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if kind == "error":
                    raise ContinuousMicError(value)

                # Stop capture and discard stale audio before ARI generates or
                # speaks a response, preventing ARI from transcribing itself.
                self.stop()
                self.console_print(f"Bezoeker: {value}")
                return value
        except BaseException:
            self.stop()
            raise

    def close(self) -> None:
        self.stop()
        self.microphone.off("*", self._handle_event)


def speak_turn(
    turn: ConversationTurn,
    robot: Any,
    microphone_input: MicrophoneInput,
    *,
    cooldown_seconds: float = COOLDOWN_SECONDS,
    console_print: Callable[..., None] = print,
) -> None:
    """Speak all ordered engine output before listening for another turn."""
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
    for utterance in turn.utterances:
        console_print(f"ARI: {utterance}")
        console_print("[SPEAKING] ARI is speaking; microphone is stopped.")
        try:
            # The installed ARI proxy's /text request waits on ROS
            # send_goal_and_wait(), so this returns after playback completes.
            robot.say(utterance)
        except Exception as error:
            console_print(f"[ARI SPEECH ERROR] {error}")
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

    microphone_input = MicrophoneInput(microphone)
    try:
        engine = ConversationEngine(LLM(system_prompt=prompts.SYSTEM_PROMPT))
        turn = engine.start()
        while not engine.is_done:
            speak_turn(turn, robot, microphone_input)
            transcript = microphone_input.listen()
            turn = engine.handle_input(transcript)
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
        microphone_input.close()

    print("Stopped. Local microphone resources have been released.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
