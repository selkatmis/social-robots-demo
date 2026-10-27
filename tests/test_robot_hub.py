"""Robot hub HTTP/process lifecycle, without launching any real process."""

import io
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hub.server import (
    ARI_ACTIONS, HERE, LOG_LINES, MAX_LOG_LINE_CHARACTERS,
    AriLink, Hub, Peers, RobotManager, create_app, make_robots,
)


def make_app(manager, ari, urls=()):
    """A hub with no other laptops, so every request is handled here."""
    identity = {"id": "hub-test", "name": "test", "port": 8080}
    return create_app(Hub(identity, manager, ari, Peers(identity)), list(urls))


class HubTests(unittest.TestCase):
    def manager(self):
        return RobotManager(make_robots("mock-python"), env={"TEST": "yes"})

    def process(self):
        process = MagicMock()
        process.poll.return_value = None
        process.stdout = io.BytesIO(b"mock log\n")
        process.wait.return_value = 0
        return process

    def test_launch_commands_are_existing_fixed_commands(self):
        robots = make_robots("mock-python")
        self.assertEqual(set(robots), {"ari", "qt", "furhat", "ari_update"})
        for name, script in (("ari", "ari/start_ari_demo.ps1"), ("qt", "qt/start_qt_demo.ps1"), ("ari_update", "ari/ari_save_social.ps1")):
            self.assertEqual(Path(robots[name].windows_command[-1]), HERE.parent / script)
        self.assertEqual(robots["furhat"].windows_command, ["mock-python", "-m", "furhat.furhat_test"])

    def test_start_rejects_unknown_and_duplicate_robot_without_extra_process(self):
        manager, process = self.manager(), self.process()
        with patch("hub.server.WINDOWS", False), patch("hub.server.subprocess.Popen", return_value=process) as spawn, patch("hub.server.threading.Thread"), patch.object(manager, "_join_job"):
            self.assertFalse(manager.start("arbitrary-command")[0])
            self.assertTrue(manager.start("ari")[0])
            self.assertFalse(manager.start("ari")[0])
            spawn.assert_called_once()
            self.assertTrue(spawn.call_args.kwargs["start_new_session"])
            self.assertNotIn("shell", spawn.call_args.kwargs)
            self.assertEqual(spawn.call_args.kwargs["env"]["PYTHONUNBUFFERED"], "1")

    def test_process_launch_failure_is_visible(self):
        manager = self.manager()
        with patch("hub.server.WINDOWS", False), patch("hub.server.subprocess.Popen", side_effect=OSError("mock missing executable")):
            self.assertFalse(manager.start("qt")[0])
        card = next(x for x in manager.snapshot(False) if x["key"] == "qt")
        self.assertEqual(card["status"], "error")
        self.assertIn("mock missing executable", card["detail"])

    def test_normal_stop_interrupts_before_waiting_and_does_not_force(self):
        manager, process = self.manager(), self.process()
        robot = manager.robots["ari"]
        robot.process = process
        with patch.object(manager, "_interrupt") as interrupt, patch.object(manager, "_force_stop") as force:
            manager._stop_gracefully(robot, process)
        interrupt.assert_called_once_with(process)
        process.wait.assert_called_once_with(timeout=45.0)
        force.assert_not_called()

    def test_timeout_falls_back_to_force_and_reports_forced_shutdown(self):
        manager, process = self.manager(), self.process()
        robot = manager.robots["ari"]
        robot.process, robot.stop_requested = process, True
        process.wait.side_effect = [subprocess.TimeoutExpired("mock", 45), 0]
        with patch.object(manager, "_interrupt"), patch.object(manager, "_force_stop") as force:
            manager._stop_gracefully(robot, process)
        force.assert_called_once_with(process)
        self.assertTrue(robot.forced_stop)
        process.wait.side_effect = None
        with patch("hub.server.threading.Thread"):
            manager._read_output(robot, process)
        self.assertEqual(robot.status, "off")
        self.assertIn("Geforceerd", robot.detail)

    def test_failed_force_stop_remains_visible_and_can_be_retried(self):
        manager, process = self.manager(), self.process()
        robot = manager.robots["qt"]
        robot.process, robot.stop_requested = process, True
        with patch.object(manager, "_interrupt", side_effect=OSError("mock interrupt failed")), patch.object(manager, "_force_stop", side_effect=OSError("mock force failed")):
            manager._stop_gracefully(robot, process)
        self.assertEqual(robot.status, "error")
        self.assertFalse(robot.stop_requested)
        self.assertIn("mock force failed", robot.detail)

    def test_logs_are_bounded_and_readiness_is_detected(self):
        manager = self.manager()
        robot = manager.robots["ari"]
        for _ in range(LOG_LINES + 10):
            manager._add_line(robot, "x" * (MAX_LOG_LINE_CHARACTERS + 10))
        self.assertEqual(len(robot.log), LOG_LINES)
        self.assertLessEqual(len(robot.log[-1]), MAX_LOG_LINE_CHARACTERS + 10)
        manager._add_line(robot, "[ARI UI] Open http://127.0.0.1:8765")
        self.assertTrue(robot.ready)

    def test_closing_rejects_new_starts_and_external_ari_is_not_owned(self):
        manager = self.manager()
        card = next(x for x in manager.snapshot(True) if x["key"] == "ari")
        self.assertEqual(card["status"], "external")
        self.assertFalse(manager.stop("ari")[0])
        manager.stop_all()
        with patch("hub.server.subprocess.Popen") as spawn:
            self.assertFalse(manager.start("ari")[0])
            spawn.assert_not_called()

    def test_per_robot_job_is_released_with_its_descendants(self):
        manager = self.manager()
        robot = manager.robots["ari"]
        kernel = MagicMock()
        robot.job = kernel, "mock-job"
        manager._close_job(robot)
        kernel.CloseHandle.assert_called_once_with("mock-job")
        self.assertIsNone(robot.job)

    def test_hub_only_forwards_selected_ari_actions_and_valid_json(self):
        manager, ari = self.manager(), MagicMock()
        ari.state = {"phase": "ZOEK_DE_VERSCHILLEN", "game_found_count": 3, "game_target_count": 8}
        ari.forward.return_value = ({"ok": True}, 200)
        client = make_app(manager, ari, ["http://mock:8080"]).test_client()
        for action in ("guardian", "mode", "arbitrary/path"):
            self.assertEqual(client.post("/api/ari/" + action, json={}).status_code, 404)
        self.assertEqual(client.post("/api/ari/say", json=[]).status_code, 400)
        self.assertEqual(client.post("/api/ari/say", data="form").status_code, 415)
        self.assertEqual(client.get("/api/ari/stop").status_code, 405)
        ari.forward.assert_not_called()
        self.assertEqual(client.post("/api/ari/say", json={"text": "Hallo"}).status_code, 200)
        ari.forward.assert_called_once_with("say", "POST", {"text": "Hallo"}, 3.0)
        self.assertEqual(client.get("/api/state").get_json()["ari"]["game_found_count"], 3)
        # The VUmc guide's handshake and staff alert reach ARI as well.
        for action in ("handshake", "alert/ack"):
            self.assertEqual(client.post("/api/ari/" + action, json={}).status_code, 200)
            ari.forward.assert_called_with(action, "POST", {}, 3.0)

    def test_browser_payload_cannot_override_launch_command(self):
        manager, ari = self.manager(), MagicMock()
        ari.state = None
        client = make_app(manager, ari).test_client()
        with patch.object(manager, "start", return_value=(True, None)) as start:
            client.post("/api/robots/ari/start", json={"command": "unexpected", "mode": "guardian"})
            start.assert_called_once_with("ari", {"ARI_MODE": "guardian"})
        # Only the two known conversations can be picked; anything else is ignored.
        with patch.object(manager, "start", return_value=(True, None)) as start:
            client.post("/api/robots/ari/start", json={"mode": "--evil"})
            start.assert_called_once_with("ari", {})

    def test_upstream_errors_keep_their_http_status(self):
        with patch("hub.server.requests.Session") as factory:
            session = factory.return_value
            session.post.return_value.json.return_value = {"ok": False, "error": "ARI is nog bezig"}
            session.post.return_value.status_code = 409
            data, status = AriLink().forward("say", "POST", {"text": "Hallo"})
        self.assertEqual(status, 409)
        self.assertFalse(data["ok"])

    def test_webpage_offers_both_conversations_and_their_controls(self):
        text = (HERE / "page.html").read_text(encoding="utf-8")
        for expected in ("sayForm", "greet", "dance", "volume", "stopAll", "game_found_count", "250",
                         'data-mode="guardian"', 'data-mode="companion"', "handshake", "alert/ack"):
            self.assertIn(expected, text)
        self.assertTrue({"handshake", "alert/ack"} <= ARI_ACTIONS)
        for asset in ("icon.png", "fonts/ubuntu-400.woff2", "fonts/ubuntu-500.woff2", "fonts/ubuntu-700.woff2"):
            self.assertTrue((HERE / "static" / asset).is_file(), asset)


if __name__ == "__main__":
    unittest.main()
