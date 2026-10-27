"""The robot hub: one page, made for an iPad, that shows and drives ARI, QT
and Furhat on whichever laptop they run.

    python -m hub                       # on every demo laptop
    python -m hub --peer 192.168.1.21   # when the wifi blocks the automatic search

Open the address it prints on the iPad, on the same wifi as the laptops.
Hubs on one network find each other, so the page shows every robot, and a tap
on a robot that runs on another laptop goes to the hub on that laptop. A hub
also sees robots someone started by hand on its own laptop. Stopping a robot
from the page is the same as pressing Ctrl+C in its window, so the robots'
own shutdown still runs.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.serving import WSGIRequestHandler, make_server


ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
APP_TAG = "vu-robot-hub"
DEFAULT_PORT = 8080
BEACON_PORT = 47820
BEACON_SECONDS = 2.0
PEER_POLL_SECONDS = 0.7
PEER_ONLINE_SECONDS = 4.0
PEER_FORGET_SECONDS = 60.0
SCAN_SECONDS = 4.0
SCAN_IDLE_SECONDS = 20.0
ARI_UI = "http://127.0.0.1:8765"
LOG_LINES = 400
MAX_LOG_LINE_CHARACTERS = 2000
STOP_WAIT_SECONDS = 45.0
ARI_POLL_SECONDS = 0.7
# ARI's sidecar stops the base 0.6 s after the last drive command, so a late
# one is useless and waiting longer only piles up requests.
MOVE_TIMEOUT_SECONDS = 1.0
WINDOWS = os.name == "nt"
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ROBOT_KEYS = ("ari", "qt", "furhat")
ACTIVE_RANK = {"running": 0, "stopping": 1, "starting": 2, "external": 3}
ARI_ACTIONS = {
    "move",
    "standby",
    "greet",
    "say",
    "expression",
    "gesture",
    "emote",
    "handshake",
    "stop",
    "alert/ack",
    "volume",
}

# How each demo looks in the process list when someone starts it by hand.
HAND_STARTED = {
    "ari": re.compile(r"-m\s+ari\.ari_mic_test\b|ari[\\/]ari_mic_test\.py|start_ari_demo\.ps1", re.I),
    "qt": re.compile(
        r"-m\s+qt\.(qt_demo_test|qt_mic_test)\b|qt[\\/](qt_demo_test|qt_mic_test)\.py|start_qt_demo\.ps1",
        re.I,
    ),
    "furhat": re.compile(
        r"-m\s+furhat\.(furhat_test|furhat|newstart)\b|furhat[\\/](furhat_test|furhat|newstart)\.py",
        re.I,
    ),
}

# Sends Ctrl+C to every process in another console, the way the operator would
# press it in that window. It runs as its own little process because attaching
# to a console detaches from your own. It refuses when that console is the
# hub's own, which would stop the hub too.
CTRL_C_HELPER = r"""
import ctypes, sys, time
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetConsoleWindow.restype = ctypes.c_void_p
kernel32.FreeConsole()
if not kernel32.AttachConsole(int(sys.argv[1])):
    sys.exit(1)
hub_window = int(sys.argv[2]) if len(sys.argv) > 2 else 0
if hub_window and (kernel32.GetConsoleWindow() or 0) == hub_window:
    sys.exit(3)
kernel32.SetConsoleCtrlHandler(None, True)
ok = kernel32.GenerateConsoleCtrlEvent(0, 0)
time.sleep(1.0)
sys.exit(0 if ok else 2)
"""

# Only the processes a demo can run in; CommandLine is what tells them apart.
SCAN_SCRIPT = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='powershell.exe' OR Name='pwsh.exe'" |
  Select-Object ProcessId, ParentProcessId, CommandLine | ConvertTo-Json -Compress
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


def hub_console_window() -> int:
    if not WINDOWS:
        return 0
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        return int(kernel32.GetConsoleWindow() or 0)
    except Exception:
        return 0


HUB_CONSOLE_WINDOW = hub_console_window()


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
    hand_stop_at: float | None = None
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
            "Gids VUmc, gesprek en zoek de verschillen",
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


def parse_windows_processes(text: str) -> list[tuple[int, int, str]]:
    text = text.strip()
    if not text:
        return []
    data = json.loads(text)
    items = [data] if isinstance(data, dict) else data
    processes = []
    for item in items if isinstance(items, list) else []:
        try:
            processes.append(
                (int(item["ProcessId"]), int(item.get("ParentProcessId") or 0), str(item.get("CommandLine") or ""))
            )
        except (KeyError, TypeError, ValueError):
            continue
    return processes


def parse_ps(text: str) -> list[tuple[int, int, str]]:
    processes = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            processes.append((int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else ""))
        except ValueError:
            continue
    return processes


def list_processes() -> list[tuple[int, int, str]] | None:
    """(pid, parent pid, command line) of every process a demo can run in."""
    try:
        if WINDOWS:
            encoded = base64.b64encode(SCAN_SCRIPT.encode("utf-16-le")).decode("ascii")
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                capture_output=True,
                timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS,
            )
            return parse_windows_processes(result.stdout.decode("utf-8", errors="replace"))
        result = subprocess.run(["ps", "-axo", "pid=,ppid=,args="], capture_output=True, timeout=10)
        return parse_ps(result.stdout.decode("utf-8", errors="replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def find_hand_started(processes: list[tuple[int, int, str]], hub_pid: int) -> dict[str, list[int]]:
    """Robot demos in the process list that the hub did not start itself."""
    parents = {pid: parent for pid, parent, _ in processes}

    def from_hub(pid: int) -> bool:
        seen: set[int] = set()
        while pid and pid not in seen:
            if pid == hub_pid:
                return True
            seen.add(pid)
            pid = parents.get(pid, 0)
        return False

    found: dict[str, list[int]] = {}
    for pid, _, command in processes:
        for key, pattern in HAND_STARTED.items():
            if pattern.search(command) and not from_hub(pid):
                found.setdefault(key, []).append(pid)
                break
    for pids in found.values():
        # The top-most process comes first; Ctrl+C goes to the window it runs in.
        pids.sort(key=lambda pid: parents.get(pid) in pids)
    return found


class HandStarted:
    """Keeps a list of the robot demos someone started by hand on this laptop.

    It only looks while a page or another hub is asking, because listing
    processes on Windows costs a PowerShell start every few seconds.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._found: dict[str, list[int]] = {}
        self._wanted = float("-inf")
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.works = True

    def start(self) -> None:
        threading.Thread(target=self._loop, name="hub-hand-started", daemon=True).start()

    def wanted(self) -> None:
        idle = time.monotonic() - self._wanted > SCAN_IDLE_SECONDS
        self._wanted = time.monotonic()
        if idle:
            self._wake.set()

    def pids(self, key: str) -> list[int]:
        with self._lock:
            return list(self._found.get(key, ()))

    def scan(self) -> None:
        processes = list_processes()
        found = {} if processes is None else find_hand_started(processes, os.getpid())
        with self._lock:
            self._found = found
            self.works = processes is not None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if time.monotonic() - self._wanted > SCAN_IDLE_SECONDS:
                self._wake.wait(5.0)
                self._wake.clear()
                continue
            self.scan()
            self._stop.wait(SCAN_SECONDS)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()


class RobotManager:
    """Starts and stops each robot's own demo process and keeps its output."""

    def __init__(
        self,
        robots: dict[str, Robot],
        *,
        env: dict[str, str] | None = None,
        hand: HandStarted | None = None,
    ) -> None:
        self.robots = robots
        self.hand = hand
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

    def hand_pids(self, key: str) -> list[int]:
        robot = self.robots.get(key)
        if self.hand is None or robot is None or key not in ROBOT_KEYS:
            return []
        if robot.process is not None and robot.process.poll() is None:
            return []
        return self.hand.pids(key)

    def start(self, key: str, extra_env: dict[str, str] | None = None) -> tuple[bool, str | None]:
        robot = self.robots.get(key)
        if robot is None:
            return False, "Onbekende robot."
        if self.hand_pids(key):
            return False, f"{robot.name} draait al op deze laptop, met de hand gestart."
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
            running = process is not None and process.poll() is None
            if running and robot.stop_requested:
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
            if running:
                robot.stop_requested = True
                robot.status = "stopping"
                self._add_line(robot, "[hub] Stoppen zoals met Ctrl+C...")
        if not running:
            pids = self.hand_pids(key)
            if pids:
                return self._stop_hand_started(robot, pids)
            return False, f"{robot.name} draait niet op deze laptop."
        threading.Thread(
            target=self._stop_gracefully,
            args=(robot, process),
            name=f"hub-{key}-stop",
            daemon=True,
        ).start()
        return True, None

    def _stop_hand_started(self, robot: Robot, pids: list[int]) -> tuple[bool, str | None]:
        now = time.time()
        with robot.lock:
            again = robot.hand_stop_at is not None and now - robot.hand_stop_at < STOP_WAIT_SECONDS
            robot.hand_stop_at = now
        if again:
            for pid in pids:
                self._kill(pid, own_group=False)
            self._add_line(robot, "[hub] Nog een keer stop: geforceerd gestopt.")
            return True, None
        self._add_line(robot, f"[hub] {robot.name} is met de hand gestart; Ctrl+C naar zijn venster gestuurd.")
        code = self._send_ctrl_c(pids[0], own_group=False)
        if code == 0:
            return True, None
        with robot.lock:
            robot.hand_stop_at = None
        if code == 3:
            return False, f"{robot.name} draait in hetzelfde venster als de hub. Stop het daar met Ctrl+C."
        return False, f"Kon {robot.name} niet stoppen. Druk Ctrl+C in zijn eigen venster."

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
        """Ctrl+C for a robot this hub started; raises when it could not be sent."""
        code = self._send_ctrl_c(process.pid, own_group=True)
        if code:
            raise RuntimeError(f"Ctrl+C kon niet verstuurd worden (code {code})")

    def _send_ctrl_c(self, pid: int, *, own_group: bool) -> int:
        """Ctrl+C for the window this process runs in; 0 once it is sent."""
        if WINDOWS:
            result = subprocess.run(
                [sys.executable, "-c", CTRL_C_HELPER, str(pid), str(HUB_CONSOLE_WINDOW)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=15,
                check=False,
            )
            return result.returncode
        try:
            if own_group:
                os.killpg(pid, signal.SIGINT)
            else:
                os.kill(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            return 1
        return 0

    def _kill(self, pid: int, *, own_group: bool) -> bool:
        if WINDOWS:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                check=False,
                timeout=10,
            )
            return result.returncode == 0
        try:
            if own_group:
                os.killpg(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            return False
        return True

    def _force_stop(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if not self._kill(process.pid, own_group=True) and process.poll() is None:
            raise RuntimeError("taskkill kon de procesboom niet stoppen")

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
        now = time.time()
        for robot in self.robots.values():
            pids = self.hand_pids(robot.key)
            with robot.lock:
                running = robot.process is not None and robot.process.poll() is None
                status = robot.status
                settled = not robot.ready_markers and robot.started_at and now - robot.started_at > 5
                if running and status == "starting" and (
                    robot.ready or settled or (robot.key == "ari" and ari_reachable)
                ):
                    status = robot.status = "running"
                by_hand = not running and (bool(pids) or (robot.key == "ari" and ari_reachable))
                if not pids:
                    robot.hand_stop_at = None
                if by_hand:
                    stopping = robot.hand_stop_at is not None and now - robot.hand_stop_at < STOP_WAIT_SECONDS
                    status = "stopping" if stopping else "external"
                items.append({
                    "key": robot.key,
                    "name": robot.name,
                    "role": robot.role,
                    "status": status,
                    "detail": "" if by_hand else robot.detail,
                    "running": running or by_hand,
                    "by_hand": by_hand,
                    "can_stop": running or bool(pids),
                    "uptime": int(now - robot.started_at) if running and robot.started_at else None,
                    "tail": list(robot.log)[-4:],
                    "hint": robot.hint,
                })
        return items


class AriLink:
    """Talks to ARI's local operator server, which only listens on its own laptop."""

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
                self._state = state if isinstance(state, dict) else None
            self._stop.wait(ARI_POLL_SECONDS)

    @property
    def state(self) -> dict[str, Any] | None:
        with self._lock:
            return self._state

    def forward(
        self,
        action: str,
        method: str,
        payload: dict[str, Any] | None,
        timeout: float = 3.0,
    ) -> tuple[dict[str, Any], int]:
        try:
            if method == "GET":
                response = self.session.get(f"{self.base_url}/{action}", timeout=timeout)
            else:
                response = self.session.post(f"{self.base_url}/{action}", json=payload or {}, timeout=timeout)
            data = response.json()
            return (data if isinstance(data, dict) else {"ok": False}), response.status_code
        except Exception:
            return {"ok": False, "error": "ARI is niet bereikbaar. Draait ARI al?"}, 503

    def close(self) -> None:
        self._stop.set()


def split_address(text: str) -> tuple[str, int]:
    text = text.strip().removeprefix("http://").rstrip("/")
    host, _, port = text.rpartition(":") if text.count(":") == 1 else (text, "", "")
    try:
        return host, int(port) if port else DEFAULT_PORT
    except ValueError:
        return host, DEFAULT_PORT


@dataclass
class Peer:
    address: str
    port: int
    source: str
    id: str | None = None
    name: str = ""
    heard_at: float = field(default_factory=time.time)
    ok_at: float = 0.0
    state: dict[str, Any] | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.address}:{self.port}"


class Peers:
    """The hubs on the other laptops.

    Every hub calls out on the wifi every two seconds, and one that hears it
    asks the other for its robots every 0.7 s. A hub given with --peer works
    the same, and a hub that is asked learns about the asker too, so giving
    the address on one of the laptops is enough.
    """

    def __init__(self, identity: dict[str, Any], manual: list[str] | None = None) -> None:
        self.identity = identity
        self._manual = list(manual or [])
        self._lock = threading.Lock()
        self._peers: dict[str, Peer] = {}
        self._own_addresses: set[str] = set()
        self._stop = threading.Event()

    def start(self, search: bool = True) -> None:
        for entry in self._manual:
            address, port = split_address(entry)
            self.add(address, port, "manual")
        if search:
            threading.Thread(target=self._call_out, name="hub-beacon", daemon=True).start()
            threading.Thread(target=self._listen, name="hub-listen", daemon=True).start()

    def add(self, address: Any, port: Any, source: str, hub_id: str | None = None, name: Any = None) -> None:
        if not address or (hub_id and hub_id == self.identity["id"]):
            return
        try:
            port = int(port)
        except (TypeError, ValueError):
            return
        if not 0 < port < 65536:
            return
        key = f"{address}:{port}"
        with self._lock:
            if key in self._own_addresses or self._stop.is_set():
                return
            peer = self._peers.get(key)
            new = peer is None
            if new:
                peer = self._peers[key] = Peer(str(address), port, source)
            if source != "learned":
                peer.heard_at = time.time()
            if hub_id:
                peer.id = hub_id
            if name:
                peer.name = str(name)[:60]
        if new:
            threading.Thread(target=self._poll, args=(key, peer), name=f"hub-peer-{key}", daemon=True).start()

    def _forget(self, key: str, *, own: bool = False) -> None:
        with self._lock:
            self._peers.pop(key, None)
            if own:
                self._own_addresses.add(key)

    def _poll(self, key: str, peer: Peer) -> None:
        session = requests.Session()
        session.trust_env = False
        params = {"from": self.identity["id"], "name": self.identity["name"], "port": self.identity["port"]}
        while not self._stop.is_set():
            try:
                response = session.get(f"{peer.base_url}/api/local/state", params=params, timeout=1.5)
                data = response.json()
                if not isinstance(data, dict) or data.get("app") != APP_TAG:
                    raise ValueError("not a robot hub")
                hub = data.get("hub") if isinstance(data.get("hub"), dict) else {}
                if hub.get("id") == self.identity["id"]:
                    self._forget(key, own=True)
                    break
                with self._lock:
                    peer.id = str(hub.get("id") or "") or None
                    peer.name = str(hub.get("name") or peer.address)[:60]
                    peer.state = data
                    peer.ok_at = time.time()
                for other in data.get("peers") or []:
                    if isinstance(other, dict):
                        self.add(other.get("address"), other.get("port"), "learned")
            except Exception:
                pass
            if peer.source != "manual" and time.time() - max(peer.heard_at, peer.ok_at) > PEER_FORGET_SECONDS:
                self._forget(key)
                break
            self._stop.wait(PEER_POLL_SECONDS)
        session.close()

    def online(self) -> list[Peer]:
        now = time.time()
        with self._lock:
            live = [
                peer
                for peer in self._peers.values()
                if peer.id and peer.state is not None and now - peer.ok_at < PEER_ONLINE_SECONDS
            ]
        # One laptop can answer on two addresses (wifi and cable); show it once.
        best: dict[str, Peer] = {}
        for peer in live:
            if peer.id not in best or peer.ok_at > best[peer.id].ok_at:
                best[peer.id] = peer
        return sorted(best.values(), key=lambda peer: peer.name.lower())

    def find(self, hub_id: str) -> Peer | None:
        return next((peer for peer in self.online() if peer.id == hub_id), None)

    def shareable(self) -> list[dict[str, Any]]:
        return [{"address": peer.address, "port": peer.port} for peer in self.online()]

    def _call_out(self) -> None:
        message = json.dumps({"app": APP_TAG, **self.identity}).encode("utf-8")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while not self._stop.is_set():
            try:
                sock.sendto(message, ("255.255.255.255", BEACON_PORT))
            except OSError:
                pass
            self._stop.wait(BEACON_SECONDS)
        sock.close()

    def _listen(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", BEACON_PORT))
        except OSError as error:
            print(f"[hub] Can't listen for other laptops ({error}); use --peer <address> instead.")
            sock.close()
            return
        sock.settimeout(1.0)
        while not self._stop.is_set():
            try:
                data, sender = sock.recvfrom(4096)
                message = json.loads(data.decode("utf-8"))
            except (socket.timeout, OSError, ValueError):
                continue
            if isinstance(message, dict) and message.get("app") == APP_TAG:
                hub_id = str(message.get("id") or "") or None
                self.add(sender[0], message.get("port"), "beacon", hub_id, message.get("name"))
        sock.close()

    def close(self) -> None:
        self._stop.set()


class MoveGate:
    """Drops a drive command that was overtaken on the way.

    The iPad sends one every 250 ms while an arrow is held. On a busy wifi a
    late "vooruit" could otherwise land after the "stop" and start ARI again.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[str, int] = {}

    def allow(self, payload: dict[str, Any] | None) -> bool:
        payload = payload or {}
        client = str(payload.get("client") or "")[:40]
        seq = payload.get("seq")
        if not client or not isinstance(seq, int):
            return True
        with self._lock:
            if seq <= self._last.get(client, -1):
                return False
            self._last.pop(client, None)
            self._last[client] = seq
            while len(self._last) > 100:
                self._last.pop(next(iter(self._last)))
        return True


class Hub:
    """This laptop's robots plus those of every other hub, as one list."""

    def __init__(
        self,
        identity: dict[str, Any],
        manager: RobotManager,
        ari: AriLink,
        peers: Peers,
        hand: HandStarted | None = None,
    ) -> None:
        self.identity = identity
        self.manager = manager
        self.ari = ari
        self.peers = peers
        self.hand = hand
        self.gate = MoveGate()
        self.targets: dict[str, str] = {}
        self.session = requests.Session()
        self.session.trust_env = False

    @property
    def id(self) -> str:
        return self.identity["id"]

    def wanted(self) -> None:
        if self.hand is not None:
            self.hand.wanted()

    def local_state(self) -> dict[str, Any]:
        ari_state = self.ari.state
        return {
            "app": APP_TAG,
            "hub": self.identity,
            "robots": self.manager.snapshot(ari_state is not None),
            "ari": ari_state,
            "peers": self.peers.shareable(),
            "scan_works": self.hand.works if self.hand is not None else False,
        }

    def view(self) -> dict[str, Any]:
        local = self.local_state()
        hubs = [{"id": self.id, "name": self.identity["name"], "here": True, "robots": local["robots"], "ari": local["ari"]}]
        for peer in self.peers.online():
            state = peer.state or {}
            hubs.append(
                {
                    "id": peer.id,
                    "name": peer.name or peer.address,
                    "here": False,
                    "robots": [robot for robot in state.get("robots") or [] if isinstance(robot, dict)],
                    "ari": state.get("ari") if isinstance(state.get("ari"), dict) else None,
                }
            )
        robots = self._merge(hubs)
        ari_hub = self._ari_hub(hubs, robots)
        return {
            "hubs": [{"id": hub["id"], "name": hub["name"], "here": hub["here"]} for hub in hubs],
            "robots": robots,
            "ari": ari_hub["ari"] if ari_hub else None,
            "ari_hub": {"id": ari_hub["id"], "name": ari_hub["name"], "here": ari_hub["here"]} if ari_hub else None,
        }

    def _merge(self, hubs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged = []
        for key in [robot["key"] for robot in hubs[0]["robots"]]:
            entries = [(hub, robot) for hub in hubs for robot in hub["robots"] if robot.get("key") == key]
            active = sorted(
                ((hub, robot) for hub, robot in entries if robot.get("status") in ACTIVE_RANK),
                key=lambda pair: (ACTIVE_RANK[pair[1]["status"]], not pair[0]["here"]),
            )
            if active:
                hub, robot = active[0]
                self.targets[key] = hub["id"]
            else:
                # A robot that is off shows the laptop it ran on last, where
                # Start will put it again.
                target = self.targets.get(key) or (self.targets.get("ari") if key == "ari_update" else None)
                hub, robot = next(((h, r) for h, r in entries if h["id"] == target), entries[0])
            item = dict(robot)
            item.update(
                hub=hub["id"],
                hub_name=hub["name"],
                here=hub["here"],
                also=[other["name"] for other, _ in active[1:]],
            )
            merged.append(item)
        return merged

    def _ari_hub(self, hubs: list[dict[str, Any]], robots: list[dict[str, Any]]) -> dict[str, Any] | None:
        with_ari = [hub for hub in hubs if hub["ari"]]
        card = next((robot for robot in robots if robot["key"] == "ari"), None)
        return (
            next((hub for hub in with_ari if card and hub["id"] == card["hub"]), None)
            or next((hub for hub in with_ari if hub["here"]), None)
            or (with_ari[0] if with_ari else None)
        )

    def card(self, key: str) -> dict[str, Any] | None:
        return next((robot for robot in self.view()["robots"] if robot["key"] == key), None)

    def forward(
        self,
        hub_id: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = 4.0,
    ) -> tuple[dict[str, Any], int]:
        peer = self.peers.find(hub_id)
        if peer is None:
            return {"ok": False, "error": "Die laptop is niet bereikbaar. Draait de hub daar nog?"}, 503
        try:
            if method == "GET":
                response = self.session.get(f"{peer.base_url}{path}", params=params, timeout=timeout)
            else:
                response = self.session.post(f"{peer.base_url}{path}", json=payload or {}, timeout=timeout)
            data = response.json()
            return (data if isinstance(data, dict) else {"ok": False}), response.status_code
        except Exception:
            return {"ok": False, "error": f"De laptop {peer.name or peer.address} reageert niet."}, 503

    def start_here(self, key: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        extra_env = {}
        if key == "ari" and body.get("mode") in {"guardian", "companion"}:
            extra_env["ARI_MODE"] = body["mode"]
        ok, error = self.manager.start(key, extra_env)
        return {"ok": ok, "error": error}, 200

    def stop_here(self, key: str) -> tuple[dict[str, Any], int]:
        ok, error = self.manager.stop(key)
        return {"ok": ok, "error": error}, 200

    def log_here(self, key: str) -> tuple[dict[str, Any], int]:
        robot = self.manager.robots.get(key)
        if robot is None:
            return {"ok": False, "lines": []}, 404
        with robot.lock:
            lines = list(robot.log)
            hub_runs_it = robot.process is not None and robot.process.poll() is None
        if not hub_runs_it and (self.manager.hand_pids(key) or (key == "ari" and self.ari.state is not None)):
            lines.append(f"[hub] {robot.name} is met de hand gestart. Zijn uitvoer staat in zijn eigen venster.")
        return {"ok": True, "lines": lines}, 200

    def ari_here(self, action: str, method: str, payload: dict[str, Any] | None) -> tuple[dict[str, Any], int]:
        timeout = MOVE_TIMEOUT_SECONDS if action == "move" else 3.0
        return self.ari.forward(action, method, payload, timeout)

    def close(self) -> None:
        self.session.close()


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


def create_app(hub: Hub, urls: list[str]) -> Flask:
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

    def body() -> dict[str, Any]:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def reply(result: tuple[dict[str, Any], int]) -> Any:
        data, status = result
        return jsonify(data), status

    @app.get("/")
    def index() -> Response:
        return Response((HERE / "page.html").read_text(encoding="utf-8"), mimetype="text/html")

    @app.get("/static/<path:name>")
    def static_files(name: str) -> Any:
        response = send_from_directory(HERE / "static", name)
        response.cache_control.max_age = 86400
        return response

    @app.get("/api/state")
    def state() -> Any:
        hub.wanted()
        view = hub.view()
        view.update(urls=urls, time=time.strftime("%H:%M"))
        return jsonify(view)

    @app.post("/api/robots/<key>/start")
    def start(key: str) -> Any:
        data = body()
        target = str(data.get("hub") or hub.id)
        if key in ROBOT_KEYS:
            card = hub.card(key)
            if card and card["running"]:
                return jsonify({"ok": False, "error": f"{card['name']} draait al op {card['hub_name']}."})
            hub.targets[key] = target
        if target == hub.id:
            return reply(hub.start_here(key, data))
        return reply(hub.forward(target, "POST", f"/api/local/robots/{key}/start", {"mode": data.get("mode")}))

    @app.post("/api/robots/<key>/stop")
    def stop(key: str) -> Any:
        card = hub.card(key)
        target = str(body().get("hub") or (card["hub"] if card else hub.id))
        if target == hub.id:
            return reply(hub.stop_here(key))
        return reply(hub.forward(target, "POST", f"/api/local/robots/{key}/stop"))

    @app.get("/api/robots/<key>/log")
    def log(key: str) -> Any:
        target = request.args.get("hub") or hub.id
        if target == hub.id:
            return reply(hub.log_here(key))
        return reply(hub.forward(target, "GET", f"/api/local/robots/{key}/log"))

    @app.route("/api/ari/<path:action>", methods=["GET", "POST"])
    def ari_action(action: str) -> Any:
        if action not in ARI_ACTIONS:
            return jsonify({"ok": False, "error": "Onbekende opdracht."}), 404
        if request.method == "GET" and action != "volume":
            return jsonify({"ok": False, "error": "Gebruik POST voor deze opdracht."}), 405
        payload = body() if request.method == "POST" else None
        if action == "move" and not hub.gate.allow(payload):
            return jsonify({"ok": True, "skipped": True})
        ari_hub = hub.view()["ari_hub"]
        if ari_hub is None or ari_hub["here"]:
            return reply(hub.ari_here(action, request.method, payload))
        timeout = MOVE_TIMEOUT_SECONDS if action == "move" else 4.0
        return reply(hub.forward(ari_hub["id"], request.method, f"/api/local/ari/{action}", payload, timeout=timeout))

    # What the other hubs call. These never pass a request on, so two hubs
    # can't bounce one back and forth.
    @app.get("/api/local/state")
    def local_state() -> Any:
        hub.wanted()
        asker = request.args.get("from")
        if asker:
            hub.peers.add(request.remote_addr, request.args.get("port"), "asked", asker, request.args.get("name"))
        return jsonify(hub.local_state())

    @app.post("/api/local/robots/<key>/start")
    def local_start(key: str) -> Any:
        return reply(hub.start_here(key, body()))

    @app.post("/api/local/robots/<key>/stop")
    def local_stop(key: str) -> Any:
        return reply(hub.stop_here(key))

    @app.get("/api/local/robots/<key>/log")
    def local_log(key: str) -> Any:
        return reply(hub.log_here(key))

    @app.route("/api/local/ari/<path:action>", methods=["GET", "POST"])
    def local_ari(action: str) -> Any:
        if action not in ARI_ACTIONS:
            return jsonify({"ok": False, "error": "Onbekende opdracht."}), 404
        if request.method == "GET" and action != "volume":
            return jsonify({"ok": False, "error": "Gebruik POST voor deze opdracht."}), 405
        payload = body() if request.method == "POST" else None
        if action == "move" and not hub.gate.allow(payload):
            return jsonify({"ok": True, "skipped": True})
        return reply(hub.ari_here(action, request.method, payload))

    return app


class QuietHandler(WSGIRequestHandler):
    """The page polls every second; keep the console for the robots' news."""

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        if str(code).startswith(("4", "5")) and "/state" not in self.path:
            super().log_request(code, size)


def peers_from_env() -> list[str]:
    """HUB_PEERS in shared/.env, for laptops where the automatic search is blocked."""
    value = os.environ.get("HUB_PEERS", "")
    try:
        from dotenv import dotenv_values

        value = value or dotenv_values(ROOT / "shared" / ".env").get("HUB_PEERS") or ""
    except Exception:
        pass
    return [entry for entry in re.split(r"[\s,;]+", value) if entry]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show and drive the robots of every laptop from an iPad.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (all networks by default)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--ari-ui", default=ARI_UI, help="ARI's local operator server")
    parser.add_argument(
        "--peer",
        action="append",
        default=[],
        metavar="ADDRESS",
        help="another laptop's hub, e.g. 192.168.1.21 or 192.168.1.21:8080 (repeat for more)",
    )
    parser.add_argument("--no-search", action="store_true", help="don't look for other hubs on the wifi")
    parser.add_argument("--name", default=None, help="this laptop's name on the page (its computer name by default)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the page on this laptop")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    identity = {
        "id": uuid.uuid4().hex[:12],
        "name": (args.name or socket.gethostname() or "laptop")[:60],
        "port": args.port,
    }
    hand = HandStarted()
    manager = RobotManager(make_robots(project_python()), hand=hand)
    ari = AriLink(args.ari_ui)
    peers = Peers(identity, args.peer + peers_from_env())
    hub = Hub(identity, manager, ari, peers, hand)
    urls = [f"http://{address}:{args.port}" for address in lan_addresses()]
    app = create_app(hub, urls)
    try:
        server = make_server(args.host, args.port, app, threaded=True, request_handler=QuietHandler)
    except OSError as error:
        print(f"[hub] Port {args.port} is not free ({error}). Is the hub already running?")
        return 1
    hand.start()
    ari.start()
    peers.start(search=not args.no_search)

    print(f"\nRobot hub '{identity['name']}' is running. Open one of these on the iPad (same wifi):")
    for url in urls or [f"http://<this-laptop>:{args.port}"]:
        print(f"    {url}")
    print(f"On this laptop: http://127.0.0.1:{args.port}")
    print("Hubs on the other laptops show up on the page by themselves.")
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
        peers.close()
        hand.close()
        hub.close()
        server.server_close()
    print("[hub] Stopped.")
    return 0
