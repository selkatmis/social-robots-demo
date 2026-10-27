"""The robot hub: one page, made for an iPad, that starts ARI, QT and Furhat
with one tap each and drives ARI.

    python -m hub              # on the demo laptop
    python -m hub --port 9000

Open the address it prints on the iPad, on the same wifi as the laptop. Each
robot runs exactly as when you start it by hand (ARI and QT through their
start scripts on Windows), and stopping it from the page is the same as
pressing Ctrl+C in its window, so the robots' own shutdown still runs.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from flask import Flask, Response, jsonify, request
from werkzeug.serving import WSGIRequestHandler, make_server


ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
DEFAULT_PORT = 8080
ARI_UI = "http://127.0.0.1:8765"
LOG_LINES = 400
MAX_LOG_LINE_CHARACTERS = 2000
STOP_WAIT_SECONDS = 45.0
ARI_POLL_SECONDS = 0.7
WINDOWS = os.name == "nt"
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ARI_ACTIONS = {
    "move",
    "standby",
    "greet",
    "say",
    "expression",
    "gesture",
    "emote",
    "stop",
    "volume",
}

# Sends Ctrl+C to every process in another console, the way the operator would
# press it in that window. It runs as its own little process because attaching
# to a console detaches from your own.
CTRL_C_HELPER = r"""
import ctypes, sys, time
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.FreeConsole()
if not kernel32.AttachConsole(int(sys.argv[1])):
    sys.exit(1)
kernel32.SetConsoleCtrlHandler(None, True)
ok = kernel32.GenerateConsoleCtrlEvent(0, 0)
time.sleep(1.0)
sys.exit(0 if ok else 2)
"""


def kill_children_with_hub() -> Any:
    """A Windows job that ends the robot processes if the hub window is closed.

    Without it, closing the hub's window would leave the robots running in
    hidden consoles. Best effort: the hub works the same without it.
    """
    if not WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        )
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            kernel32.CloseHandle(job)
            return None
        return kernel32, job
    except Exception as error:
        print(f"[hub] Robots won't stop with the hub window ({error}).")
        return None


def project_python() -> str:
    candidates = (
        ROOT / "venv" / "Scripts" / "python.exe",
        ROOT / "venv" / "bin" / "python",
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def powershell_script(path: str) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / path),
    ]


@dataclass
class Robot:
    key: str
    name: str
    role: str
    windows_command: list[str]
    other_command: list[str]
    ready_markers: tuple[str, ...] = ()
    process: subprocess.Popen | None = None
    status: str = "off"
    detail: str = ""
    started_at: float | None = None
    stop_requested: bool = False
    forced_stop: bool = False
    ready: bool = False
    hint: str | None = None
    log: deque = field(default_factory=lambda: deque(maxlen=LOG_LINES))
    lock: Any = field(default_factory=threading.RLock)
    job: Any = None

    def command(self) -> list[str]:
        return list(self.windows_command if WINDOWS else self.other_command)


def make_robots(python: str) -> dict[str, Robot]:
    robots = [
        Robot(
            "ari",
            "ARI",
            "Gesprek en zoek de verschillen",
            powershell_script(r"ari\start_ari_demo.ps1"),
            [python, "-m", "ari.ari_mic_test", "--ui"],
            ready_markers=("[ARI UI] Open",),
        ),
        Robot(
            "qt",
            "QT",
            "Bijles",
            powershell_script(r"qt\start_qt_demo.ps1"),
            [python, "-m", "qt.qt_demo_test"],
            ready_markers=("[QT READY] STANDBY", "[LISTENING]", "Microphone:"),
        ),
        Robot(
            "furhat",
            "Furhat",
            "Welkom",
            [python, "-m", "furhat.furhat_test"],
            [python, "-m", "furhat.furhat_test"],
        ),
        # Not a robot: puts the new sidecar on ARI, offered on ARI's card
        # when the start script asks for it.
        Robot(
            "ari_update",
            "ARI-sidecar",
            "Nieuwe sidecar op ARI zetten",
            powershell_script(r"ari\ari_save_social.ps1"),
            [python, "-c", "print('Run ari/ari_save_social.ps1 on Windows, or copy the sidecar by hand (ari/README.md).')"],
        ),
    ]
    return {robot.key: robot for robot in robots}


class RobotManager:
    """Starts and stops each robot's own demo process and keeps its output."""

    def __init__(self, robots: dict[str, Robot], *, env: dict[str, str] | None = None) -> None:
        self.robots = robots
        self.env = dict(os.environ if env is None else env)
        self.env.setdefault("PYTHONUNBUFFERED", "1")
        self.env.setdefault("PYTHONIOENCODING", "utf-8")
        self._closing = threading.Event()
        self._lifecycle_lock = threading.RLock()

    def _join_job(self, robot: Robot, process: subprocess.Popen) -> None:
        job_info = kill_children_with_hub()
        if job_info is None:
            return
        kernel32, job = job_info
        try:
            if not kernel32.AssignProcessToJobObject(job, int(process._handle)):
                raise OSError("AssignProcessToJobObject failed")
            robot.job = job_info
        except Exception as error:
            kernel32.CloseHandle(job)
            self._add_line(robot, f"[hub] Procesboom kon niet bewaakt worden: {error}")

    @staticmethod
    def _close_job(robot: Robot) -> None:
        if robot.job is not None:
            kernel32, job = robot.job
            kernel32.CloseHandle(job)
            robot.job = None

    def _add_line(self, robot: Robot, line: str) -> None:
        clean = ANSI.sub("", line).rstrip()[:MAX_LOG_LINE_CHARACTERS]
        if not clean:
            return
        with robot.lock:
            robot.log.append(f"{time.strftime('%H:%M:%S')}  {clean}")
            if any(marker in clean for marker in robot.ready_markers):
                robot.ready = True
            robot.detail = clean[:160]

    def start(self, key: str, extra_env: dict[str, str] | None = None) -> tuple[bool, str | None]:
        robot = self.robots.get(key)
        if robot is None:
            return False, "Onbekende robot."
        with self._lifecycle_lock, robot.lock:
            if self._closing.is_set():
                return False, "De hub wordt afgesloten."
            if robot.process is not None and robot.process.poll() is None:
                return False, f"{robot.name} draait al."
            env = dict(self.env)
            env.update(extra_env or {})
            kwargs: dict[str, Any] = {}
            if WINDOWS:
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 0
                kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
                kwargs["startupinfo"] = startup
            else:
                kwargs["start_new_session"] = True
                # A hub started in the background inherits an ignored Ctrl+C,
                # and so would the robots; give them the normal one back.
                kwargs["preexec_fn"] = lambda: signal.signal(signal.SIGINT, signal.SIG_DFL)
            robot.log.clear()
            robot.ready = False
            robot.hint = None
            robot.stop_requested = False
            robot.forced_stop = False
            robot.detail = "Starten..."
            self._add_line(robot, "$ " + " ".join(robot.command()))
            try:
                robot.process = subprocess.Popen(
                    robot.command(),
                    cwd=str(ROOT),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    **kwargs,
                )
            except OSError as error:
                robot.process = None
                robot.status = "error"
                robot.detail = f"Kon niet starten: {error}"
                self._add_line(robot, robot.detail)
                return False, robot.detail
            self._join_job(robot, robot.process)
            robot.status = "starting"
            robot.started_at = time.time()
            threading.Thread(
                target=self._read_output,
                args=(robot, robot.process),
                name=f"hub-{key}-output",
                daemon=True,
            ).start()
        return True, None

    def _read_output(self, robot: Robot, process: subprocess.Popen) -> None:
        def pump() -> None:
            stream = process.stdout
            if stream is not None:
                for raw in iter(stream.readline, b""):
                    self._add_line(robot, raw.decode("utf-8", errors="replace"))

        # The status follows the process itself, not the pipe: a helper the
        # robot left behind could keep the pipe open after it has stopped.
        reader = threading.Thread(target=pump, name=f"hub-{robot.key}-pipe", daemon=True)
        reader.start()
        code = process.wait()
        # A launcher may have exited while a local helper still owns stdout.
        # Its per-robot job closes those descendants too, even on a crash.
        with robot.lock:
            if robot.process is process:
                self._close_job(robot)
        reader.join(timeout=2.0)
        with robot.lock:
            if robot.process is not process:
                return
            robot.process = None
            robot.ready = False
            if robot.stop_requested or code == 0:
                robot.status = "off"
                robot.detail = (
                    "Geforceerd gestopt; controleer de remote diensten."
                    if robot.forced_stop else "Gestopt."
                )
            else:
                robot.status = "error"
                robot.detail = f"Gestopt met foutcode {code}. {robot.detail}"
                if any("ari_save_social" in line for line in robot.log):
                    robot.hint = "update"
            if robot.key == "ari_update" and code == 0 and "ari" in self.robots:
                self.robots["ari"].hint = None
            robot.started_at = None
            # Preserve the useful error/forced-stop detail in the card.
            detail = robot.detail
            self._add_line(robot, f"[hub] {robot.name} is gestopt (code {code}).")
            robot.detail = detail

    def stop(self, key: str) -> tuple[bool, str | None]:
        robot = self.robots.get(key)
        if robot is None:
            return False, "Onbekende robot."
        with robot.lock:
            process = robot.process
            if process is None or process.poll() is not None:
                return False, f"{robot.name} draait niet."
            if robot.stop_requested:
                robot.forced_stop = True
                try:
                    self._force_stop(process)
                    self._add_line(robot, "[hub] Nog een keer stop: geforceerd gestopt.")
                except Exception as error:
                    robot.status = "error"
                    robot.detail = f"Stoppen mislukt: {error}"
                    robot.stop_requested = False
                    return False, robot.detail
                return True, None
            robot.stop_requested = True
            robot.status = "stopping"
            self._add_line(robot, "[hub] Stoppen zoals met Ctrl+C...")
        threading.Thread(
            target=self._stop_gracefully,
            args=(robot, process),
            name=f"hub-{key}-stop",
            daemon=True,
        ).start()
        return True, None

    def _stop_gracefully(self, robot: Robot, process: subprocess.Popen) -> None:
        try:
            if process.poll() is None:
                self._interrupt(process)
            process.wait(timeout=STOP_WAIT_SECONDS)
        except Exception as error:
            self._add_line(robot, f"[hub] Normaal stoppen mislukte ({error}); geforceerd stoppen...")
            robot.forced_stop = True
            try:
                self._force_stop(process)
                process.wait(timeout=5.0)
            except Exception as force_error:
                with robot.lock:
                    robot.status = "error"
                    robot.detail = f"Stoppen mislukt: {force_error}"
                    robot.stop_requested = False
                    self._add_line(robot, f"[hub] {robot.detail}")

    def _interrupt(self, process: subprocess.Popen) -> None:
        if WINDOWS:
            subprocess.run(
                [sys.executable, "-c", CTRL_C_HELPER, str(process.pid)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=15,
                check=True,
            )
        else:
            os.killpg(process.pid, signal.SIGINT)

    def _force_stop(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if WINDOWS:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                check=False,
                timeout=10,
            )
            if result.returncode and process.poll() is None:
                raise RuntimeError("taskkill kon de procesboom niet stoppen")
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def stop_all(self, timeout: float = STOP_WAIT_SECONDS) -> None:
        with self._lifecycle_lock:
            self._closing.set()
        running = [
            robot
            for robot in self.robots.values()
            if robot.process is not None and robot.process.poll() is None
        ]
        for robot in running:
            print(f"[hub] Stopping {robot.name}...")
            self.stop(robot.key)
        deadline = time.time() + timeout + 5
        for robot in running:
            process = robot.process
            if process is None:
                continue
            try:
                process.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                robot.forced_stop = True
                try:
                    self._force_stop(process)
                    process.wait(timeout=5.0)
                except Exception as error:
                    self._add_line(robot, f"[hub] Stoppen mislukt: {error}")

    def snapshot(self, ari_reachable: bool) -> list[dict[str, Any]]:
        items = []
        for robot in self.robots.values():
            with robot.lock:
                running = robot.process is not None and robot.process.poll() is None
                status = robot.status
                settled = not robot.ready_markers and robot.started_at and time.time() - robot.started_at > 5
                if running and status == "starting" and (
                    robot.ready or settled or (robot.key == "ari" and ari_reachable)
                ):
                    status = robot.status = "running"
                external = robot.key == "ari" and ari_reachable and not running
                items.append({
                    "key": robot.key,
                    "name": robot.name,
                    "role": robot.role,
                    "status": "external" if external else status,
                    "detail": robot.detail,
                    "running": running,
                    "uptime": int(time.time() - robot.started_at) if robot.started_at else None,
                    "tail": list(robot.log)[-4:],
                    "hint": robot.hint,
                })
        return items


class AriLink:
    """Talks to ARI's local operator server, which only listens on this laptop."""

    def __init__(self, base_url: str = ARI_UI) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        # Robot and laptop addresses are local; a system proxy must not get in between.
        self.session.trust_env = False
        self._lock = threading.Lock()
        self._state: dict[str, Any] | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._poll, name="hub-ari-state", daemon=True).start()

    def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                response = self.session.get(f"{self.base_url}/state", timeout=1.0)
                response.raise_for_status()
                state = response.json()
            except Exception:
                state = None
            with self._lock:
                self._state = state
            self._stop.wait(ARI_POLL_SECONDS)

    @property
    def state(self) -> dict[str, Any] | None:
        with self._lock:
            return self._state

    def forward(self, action: str, method: str, payload: dict[str, Any] | None) -> tuple[dict[str, Any], int]:
        try:
            if method == "GET":
                response = self.session.get(f"{self.base_url}/{action}", timeout=3.0)
            else:
                response = self.session.post(f"{self.base_url}/{action}", json=payload or {}, timeout=3.0)
            data = response.json()
            return (data if isinstance(data, dict) else {"ok": False}), response.status_code
        except Exception:
            return {"ok": False, "error": "ARI is niet bereikbaar. Draait ARI al?"}, 503

    def close(self) -> None:
        self._stop.set()


def lan_addresses() -> list[str]:
    addresses: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        addresses.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in addresses and not address.startswith("127."):
                addresses.append(address)
    except OSError:
        pass
    return addresses


def create_app(manager: RobotManager, ari: AriLink, urls: list[str]) -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.after_request
    def headers(response: Any) -> Any:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.before_request
    def json_only() -> Any:
        # A plain form post from another site can't set this header without a
        # CORS preflight, which this server never answers.
        if request.method == "POST" and request.path.startswith("/api/"):
            if not request.is_json:
                return jsonify({"ok": False, "error": "JSON verwacht."}), 415
            if not isinstance(request.get_json(silent=True), dict):
                return jsonify({"ok": False, "error": "JSON-object verwacht."}), 400
        return None

    @app.get("/")
    def index() -> Response:
        return Response((HERE / "page.html").read_text(encoding="utf-8"), mimetype="text/html")

    @app.get("/api/state")
    def state() -> Any:
        ari_state = ari.state
        return jsonify(
            {
                "robots": manager.snapshot(ari_state is not None),
                "ari": ari_state,
                "urls": urls,
                "time": time.strftime("%H:%M"),
            }
        )

    @app.post("/api/robots/<key>/start")
    def start(key: str) -> Any:
        ok, error = manager.start(key)
        return jsonify({"ok": ok, "error": error})

    @app.post("/api/robots/<key>/stop")
    def stop(key: str) -> Any:
        ok, error = manager.stop(key)
        return jsonify({"ok": ok, "error": error})

    @app.get("/api/robots/<key>/log")
    def log(key: str) -> Any:
        robot = manager.robots.get(key)
        if robot is None:
            return jsonify({"ok": False, "lines": []}), 404
        with robot.lock:
            return jsonify({"ok": True, "lines": list(robot.log)})

    @app.route("/api/ari/<path:action>", methods=["GET", "POST"])
    def ari_action(action: str) -> Any:
        if action not in ARI_ACTIONS:
            return jsonify({"ok": False, "error": "Onbekende opdracht."}), 404
        if request.method == "GET" and action != "volume":
            return jsonify({"ok": False, "error": "Gebruik POST voor deze opdracht."}), 405
        payload = request.get_json(silent=True) if request.method == "POST" else None
        data, status = ari.forward(action, request.method, payload)
        return jsonify(data), status

    return app


class QuietHandler(WSGIRequestHandler):
    """The page polls every second; keep the console for the robots' news."""

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        if str(code).startswith(("4", "5")) and "/api/state" not in self.path:
            super().log_request(code, size)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the robots and drive ARI from an iPad.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (all networks by default)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--ari-ui", default=ARI_UI, help="ARI's local operator server")
    parser.add_argument("--no-browser", action="store_true", help="don't open the page on this laptop")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manager = RobotManager(make_robots(project_python()))
    ari = AriLink(args.ari_ui)
    ari.start()
    urls = [f"http://{address}:{args.port}" for address in lan_addresses()]
    app = create_app(manager, ari, urls)
    try:
        server = make_server(args.host, args.port, app, threaded=True, request_handler=QuietHandler)
    except OSError as error:
        print(f"[hub] Port {args.port} is not free ({error}). Is the hub already running?")
        return 1

    print("\nRobot hub is running. Open one of these on the iPad (same wifi):")
    for url in urls or [f"http://<this-laptop>:{args.port}"]:
        print(f"    {url}")
    print(f"On this laptop: http://127.0.0.1:{args.port}")
    print("Ctrl+C stops the hub and every robot it started.\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[hub] Stopping...")
    finally:
        manager.stop_all()
        ari.close()
        server.server_close()
    print("[hub] Stopped.")
    return 0
