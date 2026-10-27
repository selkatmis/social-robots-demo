"""Emote parser, cleanup and robot-side limits; all ROS/audio calls are mocked."""

import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ari.ari_social_proxy import (
    ARISocialROSBridge, FaceGreetingSmileTracker, MovementDeadman,
    MusicPlayer, FOCUSED_SOCIAL, IDLE_SOCIAL, create_app,
)
from ari.emotes import ActionRunner, load_emotes, parse_emote


def bare_bridge():
    bridge = object.__new__(ARISocialROSBridge)
    bridge._lock = threading.RLock()
    bridge._motion_lock = threading.Lock()
    bridge._motion_abort = threading.Event()
    bridge._motion_generation = 0
    bridge._motion_kind = None
    bridge._stop = threading.Event()
    bridge._movement = MovementDeadman()
    bridge._current_linear = bridge._current_angular = 0.0
    bridge._velocity_was_active = False
    bridge._publish_velocity = MagicMock()
    bridge._music = MagicMock()
    bridge._manual_head = False
    bridge._manual_head_until = 0.0
    bridge._head_pub = MagicMock()
    bridge._head_pub.get_num_connections.return_value = 1
    bridge._disable_neck_service = MagicMock()
    bridge._emergency = False
    bridge._conversation_active = False
    bridge._call_policy = MagicMock(return_value=True)
    bridge._enable_neck = MagicMock(return_value=True)
    bridge._verified_person_frame = lambda _now: "person"
    bridge._update_expression = MagicMock()
    bridge._expression_override = None
    bridge._expression_override_until = 0.0
    bridge._odom_pose = lambda _now: None
    bridge.JointTrajectory = SimpleNamespace
    bridge.JointTrajectoryPoint = SimpleNamespace
    bridge.rospy = SimpleNamespace(Duration=lambda value: value)
    return bridge


class EmoteTests(unittest.TestCase):
    def test_configured_tablet_pages_publish_manager_identifiers(self):
        class WebGoTo:
            TOUCH_PAGE = 4

        bridge = bare_bridge()
        bridge._tablet_pub = MagicMock()
        bridge._tablet_message_type = WebGoTo
        bridge._tablet_page = None
        bridge._tablet_error = None

        self.assertTrue(bridge.set_tablet_page("Main"))
        self.assertTrue(bridge.set_tablet_page("Zoek_de_verschillen"))
        messages = [call.args[0] for call in bridge._tablet_pub.publish.call_args_list]
        self.assertEqual([message.value for message in messages],
                         ["Main", "Zoek_de_verschillen"])
        self.assertTrue(all(message.type == message.TOUCH_PAGE for message in messages))

        with patch("ari.ari_social_proxy.TABLET_GAME_PAGE_URI", ""):
            self.assertFalse(bridge.set_tablet_page("Zoek_de_verschillen"))
        self.assertEqual(bridge._tablet_pub.publish.call_count, 2)

    def test_real_emotes_parse_and_dance_has_no_linear_drive(self):
        emotes = load_emotes()
        self.assertTrue({"Dans", "Hallo", "Hoera", "Ja", "Nee", "Rondkijken", "Wiebelen"} <= set(emotes))
        for name, script in emotes.items():
            with self.subTest(name=name):
                self.assertTrue(parse_emote(script))
        self.assertNotIn("drive", [step[0] for step in parse_emote(emotes["Dans"])])
        self.assertIn("turn", [step[0] for step in parse_emote(emotes["Dans"])])

    def test_unknown_steps_nonfinite_values_and_out_of_range_motion_are_rejected(self):
        for script in ("unknown 1", "head 0", "turn 0 1", "drive nan", "turn inf", "head 73 0 .5", "head 0 -25 .5", "head 0 0 .1", "drive .51", "drive -.31", "turn 181", "wait 11", "music maybe", "gesture handshake", "say ", "expression made_up"):
            with self.subTest(script=script), self.assertRaises(ValueError):
                parse_emote(script)

    def test_list_and_text_scripts_share_size_and_step_limits(self):
        for script in ("x" * 12001, ["say " + "x" * 400] * 50, ["wait 0"] * 129):
            with self.subTest(kind=type(script)), self.assertRaises(ValueError):
                parse_emote(script)

    def test_emote_file_is_reloaded_and_bad_files_fail_without_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "emotes.json"
            path.write_text(json.dumps({"emotes": {"Een": ["wait 0"]}}), encoding="utf-8")
            self.assertEqual(list(load_emotes(path)), ["Een"])
            path.write_text(json.dumps({"emotes": {"Twee": ["gesture wave"]}}), encoding="utf-8")
            self.assertEqual(list(load_emotes(path)), ["Twee"])
            path.write_text("invalid", encoding="utf-8")
            self.assertEqual(load_emotes(path), {})

    def test_completion_and_failure_both_release_operator_resources(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                social = MagicMock()
                social.turn.return_value = not fail
                spoken = []
                runner = ActionRunner(MagicMock(), social, lambda text, **_kw: spoken.append(text))
                self.assertTrue(runner.play_emote("Test", parse_emote("music on; expression happy; turn 5; say Goedendag!")))
                runner.wait(1)
                self.assertFalse(runner.busy())
                social.stop_actions.assert_called_once()
                self.assertEqual(spoken, [] if fail else ["Goedendag!"])
                self.assertEqual(runner.error is not None, fail)

    def test_cancellation_prevents_remaining_steps_and_old_token_is_not_reused(self):
        social, entered = MagicMock(), threading.Event()
        social.music.side_effect = lambda _value: entered.set() or True
        spoken = []
        runner = ActionRunner(MagicMock(), social, lambda text, **_kw: spoken.append(text))
        runner.play_emote("Lang", parse_emote("music on; wait 10; say Oud"))
        self.assertTrue(entered.wait(1))
        self.assertFalse(runner.play_emote("Tweede", parse_emote("say Te vroeg")))
        runner.stop()
        runner.wait(1)
        self.assertEqual(spoken, [])
        self.assertTrue(runner.play_emote("Nieuw", parse_emote("say Nieuw")))
        runner.wait(1)
        self.assertEqual(spoken, ["Nieuw"])
        self.assertEqual(social.stop_actions.call_count, 2)


class SidecarOperatorTests(unittest.TestCase):
    def test_head_move_temporarily_pauses_pal_and_restores_current_policy(self):
        for active, expected in ((False, IDLE_SOCIAL), (True, FOCUSED_SOCIAL)):
            with self.subTest(active=active):
                bridge = bare_bridge()
                bridge._conversation_active = active
                self.assertTrue(bridge.command_head(999, -999, .01))
                point = bridge._head_pub.publish.call_args.args[0].points[0]
                self.assertAlmostEqual(point.positions[0], 1.2566370614)
                self.assertAlmostEqual(point.positions[1], .4188790205)
                self.assertEqual(point.time_from_start, .2)
                self.assertTrue(bridge._manual_head)
                bridge._update_manual_head(bridge._manual_head_until + 1)
                self.assertFalse(bridge._manual_head)
                self.assertEqual(bridge._call_policy.call_args.args[0], expected)

    def test_missing_pause_service_or_emergency_prevents_head_publish(self):
        for emergency, service in ((False, None), (True, MagicMock())):
            bridge = bare_bridge()
            bridge._emergency, bridge._disable_neck_service = emergency, service
            self.assertFalse(bridge.command_head(10, 0, .5))
            bridge._head_pub.publish.assert_not_called()

    def test_head_publish_failure_restores_pal_immediately(self):
        bridge = bare_bridge()
        bridge._head_pub.publish.side_effect = RuntimeError("mock failure")
        self.assertFalse(bridge.command_head(10, 0, .5))
        self.assertFalse(bridge._manual_head)
        bridge._enable_neck.assert_called_once()

    def test_missing_head_controller_does_not_pause_attention(self):
        bridge = bare_bridge()
        bridge._head_pub.get_num_connections.return_value = 0
        self.assertFalse(bridge.command_head(10, 0, .5))
        bridge._call_policy.assert_not_called()

    def test_pal_restore_retries_after_a_temporary_neck_service_failure(self):
        bridge = bare_bridge()
        bridge._manual_head = True
        bridge._enable_neck.side_effect = [False, True]
        bridge._update_manual_head(10)
        self.assertTrue(bridge._manual_head)
        bridge._call_policy.assert_not_called()
        bridge._update_manual_head(11)
        self.assertFalse(bridge._manual_head)
        bridge._call_policy.assert_called_once_with(IDLE_SOCIAL, "")

    def test_stop_restores_face_neck_and_velocity_even_if_music_stop_fails(self):
        bridge = bare_bridge()
        bridge._manual_head = True
        bridge._expression_override = "sad"
        bridge._music.stop.side_effect = RuntimeError("mock audio failure")
        with self.assertRaises(RuntimeError):
            bridge.stop_actions()
        self.assertIsNone(bridge._expression_override)
        self.assertFalse(bridge._manual_head)
        self.assertTrue(bridge._motion_abort.is_set())
        bridge._publish_velocity.assert_called_with(0.0, 0.0)
        bridge._update_expression.assert_called()

    def test_expression_lease_expires_back_to_speaking_only_rule(self):
        bridge = bare_bridge()
        bridge._conversation_active = True
        bridge._speaking = False
        bridge._face_is_stable = lambda _now: False
        bridge._face_smile = FaceGreetingSmileTracker()
        bridge._faces_at, bridge._face_ids = 0, ()
        with patch("ari.ari_social_proxy.time.monotonic", return_value=10):
            self.assertTrue(bridge.set_expression_override("sad", 2))
        self.assertEqual(bridge._desired_expression(11), "sad")
        self.assertEqual(bridge._desired_expression(13), "neutral")
        bridge._speaking = True
        self.assertEqual(bridge._desired_expression(13), "happy")

    def test_scripted_movement_keeps_unavailability_and_obstacle_checks(self):
        for available, clear, reason in ((False, True, "unavailable"), (True, False, "blocked")):
            with self.subTest(reason=reason):
                bridge = bare_bridge()
                bridge.movement_available = lambda _now: available
                bridge._forward_clear = lambda _now: clear
                result = bridge._blocking_motion("drive", .1, 0, .2)
                self.assertFalse(result["ok"])
                self.assertEqual(result["reason"], reason)
                self.assertEqual(bridge._movement.linear, 0)

    def test_scripted_motion_limits_are_applied_on_robot_side(self):
        bridge = bare_bridge()
        bridge._blocking_motion = MagicMock(return_value={"ok": True, "reason": "done", "progress": .1})
        bridge.drive(50)
        self.assertEqual(bridge._blocking_motion.call_args.args[-1], .5)
        bridge.drive(-50)
        self.assertEqual(bridge._blocking_motion.call_args.args[-1], .3)
        bridge.turn(720)
        self.assertAlmostEqual(bridge._blocking_motion.call_args.args[-1], 3.1415926536)

    def test_nonfinite_http_motion_is_rejected_without_running_it(self):
        bridge = MagicMock()
        client = create_app(bridge).test_client()
        for body in ({"degrees": "nan"}, {"degrees": True}, []):
            self.assertEqual(client.post("/turn", json=body).status_code, 400)
        bridge.turn.assert_not_called()


class MusicTests(unittest.TestCase):
    def test_missing_audio_player_does_not_spawn_a_process(self):
        with patch("ari.ari_social_proxy.shutil.which", return_value=None), patch("ari.ari_social_proxy.subprocess.Popen") as spawn:
            self.assertFalse(MusicPlayer().start())
            spawn.assert_not_called()

    def test_stop_terminates_owned_audio_process_without_restarting_it(self):
        exited = threading.Event()
        class AudioProcess:
            def poll(self):
                return -15 if exited.is_set() else None
            def wait(self, timeout=None):
                if not exited.wait(timeout if timeout is not None else 2):
                    raise subprocess.TimeoutExpired("mock audio", timeout)
                return -15
            def terminate(self):
                exited.set()
        with patch("ari.ari_social_proxy.shutil.which", return_value="mock-player"), patch("ari.ari_social_proxy.os.path.isfile", return_value=True), patch("ari.ari_social_proxy.subprocess.Popen", return_value=AudioProcess()) as spawn:
            player = MusicPlayer()
            self.assertTrue(player.start())
            player.stop()
            self.assertFalse(player.playing)
            self.assertFalse(player._thread.is_alive())
            spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
