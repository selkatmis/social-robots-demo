import argparse
import queue
import threading
import time
from dataclasses import dataclass

import requests
from robond.core.listener_backend import ListenerBackend
from robond.core.robot_interface import Expression
from robond.robot.remote import RemoteRobotInterface

from shared.llm import LLM
from shared.mic import ContinuousMic, MicEventType


QT_HOST = "10.96.21.245"
QT_PORT = 7342
QT_STOP_URL = f"http://{QT_HOST}:{QT_PORT}/behaviour/speech"
STOP_TIMEOUT_SECONDS = 2.0


class TestListener(ListenerBackend):
    def listen(self, timeout=10, silence_timeout=None):
        return "[TIMEOUT]"


@dataclass
class InterruptedUtterance:
    generation: int
    stop_finished: bool = False
    stop_accepted: bool = False
    final_received: bool = False
    final_text: str = ""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Talk with QT through the continuous Dutch microphone pipeline."
    )
    parser.add_argument(
        "--barge-in",
        action="store_true",
        help="stop QT when speech is detected during QT playback",
    )
    return parser.parse_args()


def main(*, barge_in=False):
    print("Connecting to QT...")
    robot = RemoteRobotInterface(
        host=QT_HOST,
        port=QT_PORT,
        listener=TestListener(),
    )
    robot.set_expression(Expression.HAPPY)
    print("QT connected.")

    llm = LLM(
        system_prompt=(
            "Je bent QT, een vriendelijke sociale robot. "
            "Antwoord in het Nederlands met maximaal drie korte zinnen. "
            "Gebruik geen emoji's of markdown."
        )
    )

    mic = ContinuousMic(
        model_size="small",
        language="nl",
        enable_partials=False,
        end_silence_ms=800,
    )

    conversation_queue = queue.Queue(maxsize=3)
    stopping = threading.Event()
    state_lock = threading.Lock()
    playback_utterances = set()
    interrupted_utterances = {}
    stop_results = {}
    speech_generation = 0
    robot_speaking = False

    def enqueue_request(text):
        try:
            conversation_queue.put_nowait(text)
            return True
        except queue.Full:
            print("[WARNING] Conversation queue full.")
            return False

    def reconcile_interruption(utterance_id):
        with state_lock:
            interrupted = interrupted_utterances.get(utterance_id)
            if (
                interrupted is None
                or not interrupted.stop_finished
                or not interrupted.final_received
            ):
                return
            interrupted_utterances.pop(utterance_id, None)

        if interrupted.stop_accepted and interrupted.final_text:
            enqueue_request(interrupted.final_text)
        elif interrupted.final_text:
            print(
                "[INFO] Not sending interrupted speech because the QT stop "
                "request failed."
            )

    def request_speech_stop(generation):
        with state_lock:
            still_current = robot_speaking and speech_generation == generation

        accepted = False
        if not still_current:
            accepted = True
            print("[QT] Speech already finished; no stop request sent.")
        else:
            print("[QT] Requesting speech stop.")
            try:
                response = requests.delete(
                    QT_STOP_URL,
                    timeout=STOP_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                accepted = True
                print(
                    "[QT] Stop request accepted "
                    "(audible stop is not independently verified)."
                )
            except requests.RequestException as error:
                print(f"[QT] Stop request failed: {error}")

        with state_lock:
            stop_results[generation] = accepted
            matching_ids = []
            for utterance_id, interrupted in interrupted_utterances.items():
                if interrupted.generation == generation:
                    interrupted.stop_finished = True
                    interrupted.stop_accepted = accepted
                    matching_ids.append(utterance_id)

        for utterance_id in matching_ids:
            reconcile_interruption(utterance_id)

    def handle_event(event):
        if event.type == MicEventType.SPEECH_STARTED:
            if event.data.get("robot_speaking"):
                with state_lock:
                    playback_utterances.add(event.utterance_id)

        elif event.type == MicEventType.INTERRUPTION_DETECTED:
            print("[INTERRUPTION] Speech detected during QT playback.")
            with state_lock:
                playback_utterances.add(event.utterance_id)

            if not barge_in:
                print("This could be your voice, another person, or QT's speaker.")
                return

            start_stop_worker = False
            with state_lock:
                if event.utterance_id in interrupted_utterances:
                    return

                generation = speech_generation
                speech_was_active = robot_speaking
                if not speech_was_active:
                    interrupted_utterances[event.utterance_id] = InterruptedUtterance(
                        generation=generation,
                        stop_finished=True,
                        stop_accepted=True,
                    )
                else:
                    known_result = stop_results.get(generation)
                    interrupted_utterances[event.utterance_id] = InterruptedUtterance(
                        generation=generation,
                        stop_finished=known_result is not None,
                        stop_accepted=known_result is True,
                    )
                    if generation not in stop_results:
                        stop_results[generation] = None
                        start_stop_worker = True

            if not speech_was_active:
                print("[QT] Speech already finished; no stop request sent.")
            elif start_stop_worker:
                threading.Thread(
                    target=request_speech_stop,
                    args=(generation,),
                    name="qt-speech-stop",
                    daemon=True,
                ).start()

        elif event.type == MicEventType.FINAL_TRANSCRIPT:
            text = (event.text or "").strip()
            with state_lock:
                heard_during_playback = event.utterance_id in playback_utterances
                playback_utterances.discard(event.utterance_id)
                interrupted = interrupted_utterances.get(event.utterance_id)
                if interrupted is not None:
                    interrupted.final_received = True
                    interrupted.final_text = text

            if interrupted is not None:
                if text:
                    print(f"You: {text}")
                reconcile_interruption(event.utterance_id)
                return

            if not text:
                return

            print(f"You: {text}")
            if heard_during_playback:
                print("[INFO] Not sending speech heard during QT playback to LLM.")
                return

            enqueue_request(text)

        elif event.type == MicEventType.ERROR:
            print(f"[MIC ERROR] {event.data.get('message')}")
            if (
                event.utterance_id is not None
                and event.data.get("stage") in {"asr_final", "asr_queue"}
            ):
                with state_lock:
                    playback_utterances.discard(event.utterance_id)
                    interrupted = interrupted_utterances.pop(
                        event.utterance_id,
                        None,
                    )
                if interrupted is not None:
                    print(
                        "[BARGE-IN] Interrupted utterance could not reach Whisper."
                    )

    def say(text):
        nonlocal robot_speaking, speech_generation

        with state_lock:
            speech_generation += 1
            generation = speech_generation
            robot_speaking = True
        mic.set_robot_speaking(True)
        print("[QT] Starting speech.")
        try:
            robot.say(text)
        finally:
            with state_lock:
                if speech_generation == generation:
                    robot_speaking = False
            mic.set_robot_speaking(False)
            print("[QT] Speech call finished.")

    def conversation_worker():
        while not stopping.is_set():
            try:
                text = conversation_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            try:
                response = llm.prompt(text, max_tokens=80)
                print(f"QT: {response}")
                say(response)
            except Exception as error:
                print(f"[ERROR] {error}")
            finally:
                conversation_queue.task_done()

    mic.on("*", handle_event)
    worker = threading.Thread(target=conversation_worker, daemon=True)

    try:
        mic.start()
        worker.start()
        if barge_in:
            print(
                "Barge-in enabled through the existing RoboBOND "
                "DELETE /behaviour/speech endpoint."
            )
        print("Microphone listening. Press Ctrl+C to stop.")

        while True:
            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stopping.set()
        mic.stop()
        worker.join(timeout=2)
        if worker.is_alive():
            print("[WARNING] Conversation worker did not stop within two seconds.")
        else:
            print("Stopped.")


if __name__ == "__main__":
    arguments = parse_args()
    main(barge_in=arguments.barge_in)
