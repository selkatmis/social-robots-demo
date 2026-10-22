from __future__ import annotations

import argparse
import os
import queue
import threading
import time
from datetime import datetime
from typing import Any, Optional, Union

from shared.mic import ContinuousMic, ContinuousMicError, MicEvent, MicEventType


class Console:
    def __init__(self, debug: bool) -> None:
        self.debug = debug
        self._lock = threading.Lock()

    def write(self, label: str, message: str, *, debug_only: bool = False) -> None:
        if debug_only and not self.debug:
            return
        prefix = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} " if self.debug else ""
        with self._lock:
            print(f"{prefix}[{label}] {message}", flush=True)


def format_status(robot_speaking: bool) -> str:
    if robot_speaking:
        return "Robot: SPEAKING (simulation) | Microphone: STILL LISTENING"
    return "Robot: SILENT (simulation) | Microphone: LISTENING"


class LLMWorker:
    def __init__(self, console: Console, enabled: bool, llm: Any = None) -> None:
        self.console = console
        self.enabled = enabled
        self._queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=4)
        self._thread: Optional[threading.Thread] = None
        self._llm = llm
        self._state_lock = threading.Lock()
        self._stopping = threading.Event()

        if enabled and self._llm is None:
            try:
                from shared.llm import LLM

                self._llm = LLM()
            except Exception as error:
                self.enabled = False
                self.console.write(
                    "LLM",
                    f"Disabled ({error}). Microphone testing continues; "
                    "use --no-llm to suppress this.",
                )

    def start(self) -> None:
        with self._state_lock:
            if not self.enabled or self._stopping.is_set():
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="ari-llm", daemon=True)
            self._thread.start()

    def submit(self, text: str) -> bool:
        if not text:
            return False
        with self._state_lock:
            if not self.enabled or self._stopping.is_set():
                return False
            try:
                self._queue.put_nowait(text)
            except queue.Full:
                self.console.write("LLM", "Request queue full; dropping this turn.")
                return False
        return True

    def begin_shutdown(self) -> None:
        with self._state_lock:
            self._stopping.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def stop(self, timeout: float = 2.0) -> None:
        if not self._stopping.is_set():
            self.begin_shutdown()
        with self._state_lock:
            thread = self._thread
        if thread is None or not thread.is_alive():
            return
        self._queue.put_nowait(None)
        thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            text = self._queue.get()
            if text is None:
                return
            with self._state_lock:
                if self._stopping.is_set():
                    return
                self.console.write("LLM", "Generating response...", debug_only=True)
            started = time.monotonic()
            try:
                answer = self._llm.prompt(text)
            except Exception as error:
                with self._state_lock:
                    if self._stopping.is_set():
                        return
                    self.console.write("LLM ERROR", str(error))
                continue
            latency_ms = (time.monotonic() - started) * 1000
            with self._state_lock:
                if self._stopping.is_set():
                    return
                self.console.write("LLM", str(answer))
                self.console.write(
                    "METRIC",
                    f"LLM response {latency_ms:.0f} ms",
                    debug_only=True,
                )


def parse_device(value: str) -> Union[int, str]:
    try:
        return int(value)
    except ValueError:
        return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Continuously transcribe Dutch microphone speech without a robot."
    )
    parser.add_argument("--list-devices", action="store_true", help="List microphones and exit")
    parser.add_argument("--device", type=parse_device, help="Input device index or name")
    parser.add_argument("--model", default="small", help="Faster-Whisper model (default: small)")
    parser.add_argument("--asr-device", default="cpu", help="Faster-Whisper device (default: cpu)")
    parser.add_argument("--compute-type", default="int8", help="CTranslate2 compute type")
    parser.add_argument("--vad", choices=("silero", "webrtc"), default="silero")
    partials = parser.add_mutually_exclusive_group()
    partials.add_argument(
        "--partials",
        dest="partials",
        action="store_true",
        help="Enable provisional partial transcripts",
    )
    partials.add_argument(
        "--no-partials",
        dest="partials",
        action="store_false",
        help="Disable partial transcripts (default; kept for compatibility)",
    )
    parser.set_defaults(partials=False)
    parser.add_argument(
        "--partial-interval",
        type=float,
        default=2.0,
        help="Seconds between partial transcripts",
    )
    parser.add_argument("--no-llm", action="store_true", help="Do not call shared.llm.LLM")
    parser.add_argument("--debug", action="store_true", help="Show timestamps and timing details")
    parser.add_argument(
        "--robot-speaking",
        action="store_true",
        help="Start with simulated robot-speaking state enabled",
    )
    return parser


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


def get_key() -> Optional[str]:
    if os.name != "nt":
        return None
    import msvcrt

    if not msvcrt.kbhit():
        return None
    return msvcrt.getwch().lower()


def main() -> int:
    args = build_parser().parse_args()
    if args.list_devices:
        try:
            print_devices()
        except ContinuousMicError as error:
            print(f"[MIC ERROR] {error}")
            return 1
        return 0

    console = Console(args.debug)
    llm_worker = LLMWorker(console, enabled=not args.no_llm)
    shutdown_started = threading.Event()
    shutdown_lock = threading.Lock()

    mic = ContinuousMic(
        device=args.device,
        vad=args.vad,
        model_size=args.model,
        asr_device=args.asr_device,
        compute_type=args.compute_type,
        enable_partials=args.partials,
        partial_interval_seconds=args.partial_interval,
    )
    mic.set_robot_speaking(args.robot_speaking)

    def handle_event(event: MicEvent) -> None:
        with shutdown_lock:
            if shutdown_started.is_set():
                return
            if event.type == MicEventType.SPEECH_STARTED:
                console.write("VAD", "Speech started", debug_only=True)
                console.write(
                    "METRIC",
                    "speech start confirmed after "
                    f"{event.data['onset_confirmation_ms']:.0f} ms",
                    debug_only=True,
                )
            elif event.type == MicEventType.SPEECH_PAUSED:
                console.write(
                    "VAD",
                    f"Temporary pause ({event.data['silence_ms']:.0f} ms)",
                    debug_only=True,
                )
            elif event.type == MicEventType.SPEECH_RESUMED:
                console.write("VAD", "Speech resumed", debug_only=True)
            elif event.type == MicEventType.SPEECH_ENDED:
                details = f" (silence: {event.data['silence_ms']:.0f} ms)"
                console.write("VAD", f"Speech ended{details}", debug_only=True)
                console.write(
                    "METRIC",
                    "end detection "
                    f"{event.data['speech_end_detection_latency_ms']:.0f} ms; "
                    f"reason={event.data['reason']}",
                    debug_only=True,
                )
            elif event.type == MicEventType.PARTIAL_TRANSCRIPT:
                console.write("ASR PARTIAL", event.text or "")
                console.write(
                    "METRIC",
                    f"partial ASR {event.data['transcription_latency_ms']:.0f} ms; "
                    f"queue {event.data['asr_queue_wait_ms']:.0f} ms "
                    f"(revision {event.data['revision']})",
                    debug_only=True,
                )
            elif event.type == MicEventType.FINAL_TRANSCRIPT:
                if event.text:
                    console.write("ASR FINAL", event.text)
                    llm_worker.submit(event.text)
                else:
                    console.write("ASR FINAL", "No text recognized.")
                console.write(
                    "METRIC",
                    f"final ASR {event.data['transcription_latency_ms']:.0f} ms; "
                    f"queue {event.data['asr_queue_wait_ms']:.0f} ms; "
                    "speech-end-to-text "
                    f"{event.data['speech_end_to_transcript_ms']:.0f} ms; "
                    f"dropped audio frames {event.data['dropped_audio_frames']}; "
                    f"dropped ASR tasks {event.data['dropped_asr_tasks']}",
                    debug_only=True,
                )
                console.write(
                    "STATUS",
                    format_status(mic.robot_speaking),
                    debug_only=True,
                )
            elif event.type == MicEventType.INTERRUPTION_DETECTED:
                console.write(
                    "INTERRUPTION",
                    "Speech detected while robot was speaking (simulation). "
                    "This is unverified without acoustic echo cancellation.",
                )
            elif event.type == MicEventType.ERROR:
                console.write(
                    "MIC ERROR",
                    f"{event.data['message']} [{event.data['stage']}]"
                    + (f": {event.data['exception']}" if "exception" in event.data else ""),
                )

    mic.on("*", handle_event)
    llm_worker.start()
    exit_code = 0
    try:
        mic.start()
        with shutdown_lock:
            console.write("STATUS", format_status(mic.robot_speaking))
        console.write(
            "CONTROL",
            "Press R to toggle simulated robot speech, Q to quit, or Ctrl+C to stop.",
        )
        while mic.is_running:
            key = get_key()
            if key == "r":
                with shutdown_lock:
                    mic.set_robot_speaking(not mic.robot_speaking)
                    console.write("STATUS", format_status(mic.robot_speaking))
            elif key == "q":
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    except ContinuousMicError as error:
        console.write("MIC ERROR", str(error))
        exit_code = 1
    finally:
        with shutdown_lock:
            shutdown_started.set()
            llm_worker.begin_shutdown()
            stopping_mic = mic.is_running
            if stopping_mic:
                console.write("MIC", "Stopping...")
        try:
            mic.stop()
        except Exception as error:
            exit_code = 1
            console.write(
                "MIC ERROR",
                f"Shutdown failed ({type(error).__name__}): {error}",
            )
        else:
            if stopping_mic:
                console.write("MIC", "Stopped.")
        try:
            llm_worker.stop()
        except Exception as error:
            exit_code = 1
            console.write(
                "LLM ERROR",
                f"Shutdown failed ({type(error).__name__}): {error}",
            )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
