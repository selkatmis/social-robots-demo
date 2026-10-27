"""Editable operator emotes for the existing ARI runtime."""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from robond.core.robot_interface import Gesture

EMOTES_FILE = Path(__file__).with_name("emotes.json")
GESTURES = {
    "wave": Gesture.WAVE, "nod": Gesture.NOD,
    "cheer": Gesture.CHEER, "farewell": Gesture.FAREWELL,
    "greet": Gesture.GREET,
}
# The conversation names the whole exchange; the arm motion behind it is greet.
GESTURE_ALIASES = {"handshake": "greet"}
EXPRESSIONS = {
    "happy", "excited", "neutral", "sad", "surprised",
    "thinking", "confused", "shy", "angry",
}
MAX_STEPS = 128
MAX_SCRIPT_CHARACTERS = 12000
MAX_SPOKEN_CHARACTERS = 400
MAX_WAIT_SECONDS = 10.0
EMOTE_EXPRESSION_SECONDS = 60.0

HANDSHAKE_OFFER = "Zullen we elkaar een hand geven? Steekt u uw hand maar naar mij uit."
HANDSHAKE_DONE = "Aangenaam kennis te maken! Wat fijn dat u er bent."
HANDSHAKE_MISSED = "Geeft niets, hoor. Een andere keer misschien."
HANDSHAKE_SECONDS = 7.0
HANDSHAKE_FACE_SECONDS = 20.0
HAND_DISTANCE_METERS = 0.7


def load_emotes(path: Path = EMOTES_FILE) -> dict[str, list[str]]:
    """Read fresh for each request, including additions made while running."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    source = data.get("emotes", {}) if isinstance(data, dict) else {}
    if not isinstance(source, dict):
        return {}
    return {
        name: steps for name, steps in source.items()
        if isinstance(name, str) and not name.startswith("__")
        and isinstance(steps, list) and all(isinstance(step, str) for step in steps)
    }


def parse_emote(script: str | list[str]) -> list[tuple[Any, ...]]:
    if isinstance(script, str):
        if len(script) > MAX_SCRIPT_CHARACTERS:
            raise ValueError("De emote is te lang.")
        lines = re.split(r"[;\n]+", script)
    elif isinstance(script, list) and all(isinstance(line, str) for line in script):
        lines = script
    else:
        raise ValueError("Gebruik tekst of een lijst met stappen.")
    if sum(len(line) for line in lines) > MAX_SCRIPT_CHARACTERS:
        raise ValueError("De emote is te lang.")
    steps = []
    for line in lines:
        tokens = line.strip().split()
        if not tokens:
            continue
        kind = tokens[0].lower()
        args = tokens[1:]
        if kind == "say":
            text = " ".join(args)
            if not text or len(text) > MAX_SPOKEN_CHARACTERS:
                raise ValueError("Een gesproken zin moet 1 tot 400 tekens bevatten.")
            steps.append((kind, text))
        elif kind in {"gesture", "expression", "music"}:
            if len(args) != 1:
                raise ValueError(f"Gebruik één waarde bij {kind}.")
            value = args[0].lower()
            allowed = GESTURES if kind == "gesture" else EXPRESSIONS
            if kind == "music":
                if value not in {"on", "off", "aan", "uit"}:
                    raise ValueError("Gebruik music on of music off.")
                steps.append((kind, value in {"on", "aan"}))
            elif value in allowed:
                steps.append((kind, value))
            else:
                raise ValueError(f"Onbekende {kind}: {value}.")
        elif kind in {"head", "turn", "drive", "wait"}:
            counts = {"head": {2, 3}, "turn": {1}, "drive": {1}, "wait": {1}}
            if len(args) not in counts[kind]:
                raise ValueError(f"Onjuist aantal waarden bij {kind}.")
            try:
                numbers = tuple(float(value) for value in args)
            except ValueError:
                raise ValueError(f"Gebruik getallen bij {kind}.") from None
            if not all(math.isfinite(value) for value in numbers):
                raise ValueError("Gebruik eindige getallen.")
            if kind == "head" and len(numbers) == 2:
                numbers += (0.5,)
            if kind == "head" and not (
                -72 <= numbers[0] <= 72 and -24 <= numbers[1] <= 14
                and 0.2 <= numbers[2] <= 5
            ):
                raise ValueError("Hoofd: pan -72..72, tilt -24..14, duur 0.2..5.")
            if kind == "turn" and abs(numbers[0]) > 180:
                raise ValueError("Draai hoogstens 180 graden per stap.")
            if kind == "drive" and not -0.3 <= numbers[0] <= 0.5:
                raise ValueError("Rijd per stap hoogstens 0.5 m vooruit of 0.3 m achteruit.")
            if kind == "wait" and not 0 <= numbers[0] <= MAX_WAIT_SECONDS:
                raise ValueError("Wacht 0 tot 10 seconden per stap.")
            steps.append((kind, *numbers))
        else:
            raise ValueError(f"Onbekende stap: {kind}.")
        if len(steps) > MAX_STEPS:
            raise ValueError("Gebruik hoogstens 128 stappen.")
    return steps


class ActionRunner:
    """One cancellable operator action at a time; each run has its own token."""

    def __init__(self, robot: Any, social: Any, speak: Callable[..., None]) -> None:
        self.robot, self.social, self.speak = robot, social, speak
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._current: str | None = None
        self.error: str | None = None

    @property
    def current(self) -> str | None:
        with self._lock:
            return self._current

    def busy(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def start(self, name: str, target: Callable[[threading.Event], None]) -> bool:
        with self._lock:
            if self.busy():
                return False
            cancel = threading.Event()
            self._cancel = cancel
            self._current, self.error = name, None

            def run() -> None:
                try:
                    target(cancel)
                except Exception as error:
                    self.error = str(error)
                finally:
                    with self._lock:
                        self._current = None

            self._thread = threading.Thread(target=run, name="ari-operator-action", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._cancel.set()

    def wait(self, timeout: float = 0.1) -> None:
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def play_emote(self, name: str, steps: list[tuple[Any, ...]]) -> bool:
        return self.start(name, lambda cancel: self._run_steps(steps, cancel))

    def handshake(self) -> bool:
        return self.start("Hand geven", self._run_handshake)

    def _play_gesture(self, name: str, cancel: threading.Event) -> None:
        gesture = GESTURES[name]
        if gesture in self.robot.get_available_gestures() and not cancel.is_set():
            self.robot.play_gesture(gesture)

    def _run_handshake(self, cancel: threading.Event) -> None:
        self.social.set_expression("happy", HANDSHAKE_FACE_SECONDS)
        try:
            self.speak(HANDSHAKE_OFFER, should_continue=lambda: not cancel.is_set())
            if cancel.is_set():
                return
            arm = threading.Thread(
                target=self._play_gesture,
                args=("greet", cancel),
                name="ari-handshake-arm",
                daemon=True,
            )
            arm.start()
            # Without a live laser or depth image there is nothing to check, so
            # ARI assumes the hand was there rather than sounding let down.
            can_see = self.social.has_capability("nearest") and self.social.snapshot().get(
                "obstacle_sensor"
            ) in {"laser", "depth"}
            found = False
            deadline = time.monotonic() + HANDSHAKE_SECONDS
            while can_see and time.monotonic() < deadline and not cancel.is_set():
                distance = self.social.nearest()
                if distance is not None and distance < HAND_DISTANCE_METERS:
                    found = True
                    break
                cancel.wait(0.25)
            arm.join(timeout=8.0)
            if cancel.is_set():
                return
            if found or not can_see:
                self._play_gesture("nod", cancel)
                self.speak(HANDSHAKE_DONE, should_continue=lambda: not cancel.is_set())
            else:
                self.speak(HANDSHAKE_MISSED, should_continue=lambda: not cancel.is_set())
        finally:
            self.social.stop_actions()

    def _run_steps(self, steps: list[tuple[Any, ...]], cancel: threading.Event) -> None:
        try:
            for kind, *values in steps:
                if cancel.is_set():
                    break
                if kind == "say":
                    self.speak(values[0], should_continue=lambda: not cancel.is_set())
                elif kind == "gesture":
                    gesture = GESTURES[values[0]]
                    available = self.robot.get_available_gestures()
                    if gesture in available and not cancel.is_set():
                        self.robot.play_gesture(gesture)
                elif kind == "expression":
                    self.social.set_expression(values[0], EMOTE_EXPRESSION_SECONDS)
                elif kind == "head":
                    if self.social.head(*values):
                        cancel.wait(values[2])
                    else:
                        raise RuntimeError("Hoofdbeweging is niet beschikbaar.")
                elif kind in {"turn", "drive"}:
                    if not getattr(self.social, kind)(values[0]):
                        raise RuntimeError("Beweging gestopt of niet beschikbaar.")
                elif kind == "music":
                    if not self.social.music(values[0]) and values[0]:
                        raise RuntimeError("Muziek is niet beschikbaar op de robot.")
                elif kind == "wait":
                    cancel.wait(values[0])
        finally:
            self.social.stop_actions()


def gesture_for(name: str | None) -> Gesture | None:
    """Map a conversation gesture name, handshake included, onto an arm motion."""
    key = name or ""
    return GESTURES.get(GESTURE_ALIASES.get(key, key))
