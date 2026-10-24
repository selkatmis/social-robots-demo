import argparse
import queue
import time

from robond.core.listener_backend import ListenerBackend
from robond.robot.remote import RemoteRobotInterface

from shared.config import ARI_IP, REMOTE_PORT
from shared.llm import LLM
from shared.mic import ContinuousMic, ContinuousMicError, MicEventType

COOLDOWN_SECONDS = 0.4

class TestListener(ListenerBackend):
    """RoboBOND listener placeholder; input comes from ContinuousMic."""

    def listen(self, timeout=10, silence_timeout=None):
        return "[TIMEOUT]"


def parse_device(value):
    try:
        return int(value)
    except ValueError:
        return value


def parse_args():
    parser = argparse.ArgumentParser(
        description="Turn-based Dutch conversation with ARI."
    )
    parser.add_argument("--host", default=ARI_IP, help="ARI RoboBOND proxy host")
    parser.add_argument("--port", type=int, default=REMOTE_PORT)
    parser.add_argument("--language", default="nl_NL", help="ARI TTS language")
    parser.add_argument(
        "--device",
        type=parse_device,
        help="Krisp/NVIDIA Broadcast input index or name",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="list Windows input devices and exit",
    )
    return parser.parse_args()


def print_devices():
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


def microphone_description(device):
    try:
        import sounddevice as sd

        info = sd.query_devices(device, "input")
        selection = sd.default.device[0] if device is None else device
        return f"{info['name']} (device {selection})"
    except Exception as error:
        selection = "Windows default" if device is None else device
        return f"{selection} (name unavailable: {error})"


def drain_events(turn_events):
    while True:
        try:
            turn_events.get_nowait()
        except queue.Empty:
            return


def wait_for_visitor(turn_events):
    while True:
        try:
            kind, value = turn_events.get(timeout=0.2)
        except queue.Empty:
            continue
        if kind == "error":
            raise ContinuousMicError(value)
        return value


def main(args):
    if args.list_devices:
        print_devices()
        return

    print(f"Connecting to ARI at {args.host}:{args.port}...")
    robot = RemoteRobotInterface(
        host=args.host,
        port=args.port,
        listener=TestListener(),
    )
    robot.set_language(args.language)
    print("ARI connected.")

    llm = LLM(
        system_prompt=(
            "Je bent ARI, een vriendelijke sociale robot. "
            "Antwoord in het Nederlands met maximaal drie korte zinnen. "
            "Gebruik geen emoji's of markdown."
        )
    )

    mic = ContinuousMic(
        device=args.device,
        vad="silero",
        model_size="small",
        language="nl",
        enable_partials=False,
        end_silence_ms=800,
    )
    turn_events = queue.Queue()

    def handle_event(event):
        if event.type == MicEventType.FINAL_TRANSCRIPT:
            text = (event.text or "").strip()
            if text:
                turn_events.put(("transcript", text))
            else:
                print("[LISTENING] No text recognized; still listening.")
        elif event.type == MicEventType.ERROR:
            message = event.data.get("message", "Microphone error")
            print(f"[MIC ERROR] {message}")
            if not event.data.get("recoverable", True):
                turn_events.put(("error", message))

    mic.on("*", handle_event)
    microphone_announced = False

    try:
        while True:
            drain_events(turn_events)
            mic.start()
            if not microphone_announced:
                print(f"Microphone: {microphone_description(args.device)}")
                microphone_announced = True
            print("[LISTENING] Waiting for the visitor...")

            text = wait_for_visitor(turn_events)

            # Stop from the main thread. ContinuousMic discards unfinished audio,
            # ASR tasks, and events, while retaining its loaded models for restart.
            mic.stop()
            print(f"You: {text}")
            print("[THINKING] Generating a response...")

            try:
                response = llm.prompt(text, max_tokens=80)
                print(f"ARI: {response}")
                print("[SPEAKING] ARI is speaking; microphone capture is stopped.")
                robot.say(response)
                print("[SPEAKING] Speech call finished.")
            except Exception as error:
                print(f"[ERROR] {error}")

            time.sleep(COOLDOWN_SECONDS)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        if mic.is_running:
            mic.stop()
        print("Stopped.")


if __name__ == "__main__":
    arguments = parse_args()
    try:
        main(arguments)
    except ContinuousMicError as error:
        raise SystemExit(f"[MIC ERROR] {error}")
