"""Operator actions and concurrency, with no robot or microphone hardware."""

import json
import queue
import threading
import unittest
from unittest.mock import patch

from ari.ari_mic_test import ListeningCancelled, MicrophoneInput, run_conversation, speak_turn
from ari.difference_game import DifferenceInterpretation
from ari.difference_game_data import REFERENCE_DIFFERENCES
from ari.operator_ui import RuntimeCoordinator, create_app
from ari.responses import ConversationEngine, ConversationTurn
from robond.core.robot_interface import Gesture


class NoLLM:
    def prompt(self, *_args, **_kwargs):
        raise AssertionError("Unexpected model call")


class Robot:
    def __init__(self):
        self.spoken, self.gestures = [], []
        self.stop_count = 0

    def say(self, text):
        self.spoken.append(text)

    def coordinated_action(self, text, gesture=None):
        self.spoken.append(text)
        self.gestures.append(gesture)

    def get_available_gestures(self):
        return [Gesture.WAVE, Gesture.NOD, Gesture.CHEER]

    def play_gesture(self, gesture):
        self.gestures.append(gesture)

    def stop(self):
        self.stop_count += 1


class Mic:
    def __init__(self):
        self.cancelled = threading.Event()

    def stop(self):
        pass

    def cancel_pending(self):
        self.cancelled.set()


class Social:
    def __init__(self):
        self.faces, self.modes, self.pages, self.moves = [], [], [], []
        self.stop_count = 0

    def snapshot(self):
        return {"robot_connected": True, "movement_available": True}

    def set_speaking(self, value):
        self.faces.append(bool(value))

    def set_conversation_active(self, value):
        self.modes.append(bool(value))
        return True

    def set_tablet_page(self, page):
        self.pages.append(page)
        return True

    def stop_movement(self):
        self.moves.append("stop")

    def stop_actions(self):
        self.stop_count += 1

    def set_expression(self, name, seconds):
        self.expression = name, seconds
        return True

    def volume(self, percent=None):
        return int(percent) if percent is not None else 80

    def move(self, action):
        self.moves.append(action)
        return True


def make_runtime(robot=None, engine=None):
    def speaker(*args, **kwargs):
        return speak_turn(*args, **kwargs, cooldown_seconds=0, console_print=lambda *_: None)
    runtime = RuntimeCoordinator(
        engine or ConversationEngine(NoLLM()), robot or Robot(), Mic(), Social(),
        turn_speaker=speaker,
    )
    runtime.start_engine()
    return runtime


class OperatorTests(unittest.TestCase):
    def finish_action(self, runtime):
        runtime.actions.wait(2)
        self.assertFalse(runtime.actions.busy())
        self.assertIsNone(runtime.actions.error)

    def test_operator_greeting_uses_current_introduction_and_one_wave(self):
        runtime = make_runtime()
        self.assertTrue(runtime.greet())
        self.finish_action(runtime)
        self.assertEqual(runtime.engine.state["phase"], "ASK_NAME")
        self.assertEqual(len(runtime.robot.spoken), 2)
        self.assertIn("sociale robot", runtime.robot.spoken[0])
        self.assertEqual(runtime.robot.gestures, [Gesture.WAVE])
        self.assertFalse(runtime.greet())

    def test_typed_speech_and_gesture_use_existing_robot_interface(self):
        runtime = make_runtime()
        self.assertTrue(runtime.operator_say("  Een momentje alstublieft.  "))
        self.finish_action(runtime)
        self.assertEqual(runtime.robot.spoken, ["Een momentje alstublieft."])
        self.assertEqual(runtime.social_control.faces, [True, False])
        self.assertTrue(runtime.play_gesture("nod"))
        self.finish_action(runtime)
        self.assertEqual(runtime.robot.gestures, [Gesture.NOD])

    def test_automatic_and_operator_speech_are_serialized(self):
        started, release = threading.Event(), threading.Event()
        class BlockingRobot(Robot):
            def say(self, text):
                super().say(text)
                if text == "Automatisch":
                    started.set()
                    release.wait(2)
        runtime = make_runtime(BlockingRobot())
        worker = threading.Thread(target=lambda: runtime.deliver_turn(ConversationTurn(("Automatisch",)), runtime.generation))
        self.addCleanup(release.set)
        worker.start()
        self.assertTrue(started.wait(1))
        self.assertTrue(runtime.operator_say("Bediening"))
        self.assertEqual(runtime.robot.spoken, ["Automatisch"])
        release.set()
        worker.join(2)
        self.finish_action(runtime)
        self.assertEqual(runtime.robot.spoken, ["Automatisch", "Bediening"])

    def test_stop_discards_operator_speech_waiting_for_the_speech_lock(self):
        runtime = make_runtime()
        with runtime._speech_lock:
            self.assertTrue(runtime.operator_say("Mag niet klinken"))
            runtime.stop_actions()
            runtime.actions.wait(1)
            self.assertFalse(runtime.actions.busy())
        self.assertEqual(runtime.robot.spoken, [])
        self.assertFalse(runtime._operator_gate.is_set())

    def test_stop_preserves_game_and_name_while_standby_clears_them(self):
        runtime = make_runtime()
        r = REFERENCE_DIFFERENCES[0]
        runtime.engine.difference_game.evaluate([DifferenceInterpretation(r.difference_id, r.feature, r.picture_a, r.picture_b, .95)])
        runtime.engine.state["visitor_name"] = "Els"
        runtime.engine.state["phase"] = "ZOEK_DE_VERSCHILLEN"
        old = runtime.generation
        runtime.stop_actions()
        self.assertGreater(runtime.generation, old)
        self.assertEqual(runtime.engine.state["visitor_name"], "Els")
        self.assertEqual(runtime.engine.difference_game.score, 1)
        runtime.reset_to_standby()
        self.assertIsNone(runtime.engine.state["visitor_name"])
        self.assertEqual(runtime.engine.difference_game.score, 0)
        self.assertEqual(runtime.social_control.pages[-1], "Main")

    def test_stale_model_reply_is_suppressed_after_operator_reset(self):
        started, release = threading.Event(), threading.Event()
        class BlockingLLM:
            def prompt(self, *_args, **_kwargs):
                started.set()
                release.wait(2)
                return json.dumps({"intent": "CONTINUE", "reply": "Oud antwoord.", "topic": None})
        runtime = make_runtime(engine=ConversationEngine(BlockingLLM()))
        runtime.engine.state["phase"] = "GEZELLIG_BABBELEN"
        results = []
        old = runtime.generation
        worker = threading.Thread(target=lambda: results.append(runtime.handle_transcript("Muziek", old)))
        worker.start()
        self.assertTrue(started.wait(1))
        reset = threading.Thread(target=runtime.reset_to_standby)
        reset.start()
        self.assertTrue(runtime.microphone_input.cancelled.wait(1))
        release.set()
        worker.join(2)
        reset.join(2)
        self.assertEqual(results, [None])
        self.assertFalse(runtime.deliver_turn(ConversationTurn(("Oud antwoord.",)), old))
        self.assertEqual(runtime.robot.spoken, [])

    def test_main_loop_does_not_relabel_an_old_turn_with_the_new_generation(self):
        runtime = make_runtime()
        engine = runtime.engine
        runtime.start_engine = lambda: ConversationTurn(())
        original = runtime.handle_transcript
        def handle(text, generation):
            turn = original(text, generation)
            runtime.reset_to_standby()
            return turn
        runtime.handle_transcript = handle
        class VisitorMic:
            calls = 0
            def listen(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return "Hallo ARI"
                engine.shutdown()
                raise ListeningCancelled()
            def is_probable_robot_echo(self, _text):
                return False
        run_conversation(runtime, VisitorMic())
        self.assertEqual(runtime.robot.spoken, [])

    def test_microphone_does_not_start_while_operator_gate_is_closed(self):
        class Stream:
            is_running = False
            starts = 0
            def on(self, *_args):
                pass
            def start(self):
                self.starts += 1
        stream = Stream()
        mic = MicrophoneInput(stream, console_print=lambda *_: None)
        with self.assertRaises(ListeningCancelled):
            mic.listen(should_continue=lambda: False)
        self.assertEqual(stream.starts, 0)

    def test_cancelled_old_listener_cannot_stop_new_operator_prelisten(self):
        class Stream:
            is_running = False
            def on(self, *_args):
                pass
            def start(self):
                self.is_running = True
            def stop(self):
                self.is_running = False
        stream = Stream()
        mic = MicrophoneInput(stream, console_print=lambda *_: None)
        mic._announced = True
        def event_get(block=True, timeout=None):
            if not block:
                raise queue.Empty()
            mic.prime()
            raise ListeningCancelled()
        with patch.object(mic._events, "get", side_effect=event_get):
            with self.assertRaises(ListeningCancelled):
                mic.listen()
        self.assertTrue(stream.is_running)
        self.assertTrue(mic._primed)
        mic.stop()

    def test_operator_http_endpoints_validate_inputs_and_exclude_unselected_features(self):
        runtime = make_runtime()
        client = create_app(runtime).test_client()
        for path, body in (("/say", {"text": "x" * 401}), ("/emote", {"script": "drive nan"}), ("/gesture", {"name": []}), ("/volume", {"percent": True})):
            with self.subTest(path=path):
                self.assertEqual(client.post(path, json=body).status_code, 400)
        self.assertEqual(client.post("/say", json=[]).status_code, 400)
        self.assertTrue(client.post("/expression", json={"name": "neutral"}).get_json()["ok"])
        self.assertEqual(client.post("/volume", json={"percent": 65}).get_json()["percent"], 65)
        self.assertTrue(client.post("/stop", json={}).get_json()["ok"])
        for path in ("/handshake", "/alert/ack", "/guardian", "/mode"):
            self.assertEqual(client.post(path, json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
