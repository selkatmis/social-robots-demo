"""ARI's emotes and handshake, run from the laptop like FOAF's in hackathon2026-main.

An emote is a list of small steps (head, turn, gesture, say, ...) from
``emotes.json``. Steps that need the social sidecar are skipped when it is not
there, so an emote never takes the conversation down with it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from robond.core.robot_interface import Expression, Gesture


EMOTES_FILE = Path(__file__).with_name("emotes.json")
GESTURES = {
    "wave": Gesture.WAVE,
    "nod": Gesture.NOD,
    "cheer": Gesture.CHEER,
    "greet": Gesture.GREET,
    "handshake": Gesture.GREET,
    "farewell": Gesture.FAREWELL,
}
EXPRESSIONS = {
    "happy",
    "excited",
    "neutral",
    "sad",
    "surprised",
    "thinking",
    "confused",
    "shy",
    "angry",
}
STEP_KINDS = ("head", "turn", "drive", "gesture", "expression", "say", "music", "wait")
EMOTE_EXPRESSION_SECONDS = 60.0
MAX_WAIT_SECONDS = 10.0

HANDSHAKE_OFFER = "Zullen we elkaar een hand geven? Steekt u uw hand maar naar mij uit."
HANDSHAKE_DONE = "Aangenaam kennis te maken! Wat fijn dat u er bent."
HANDSHAKE_MISSED = "Geeft niets, hoor. Een andere keer misschien."
HANDSHAKE_SECONDS = 7.0
HAND_DISTANCE_METERS = 0.7

Speak = Callable[..., None]


_last_load_error: str | None = None


def load_emotes(path: Path = EMOTES_FILE) -> dict[str, list[str]]:
    """Read the file fresh each time, so edits show up without a restart."""
    global _last_load_error
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        # The operator page asks for this every second; say it once.
        if str(error) != _last_load_error:
            print(f"[ARI EMOTES] {path.name} could not be read: {error}")
            _last_load_error = str(error)
        return {}
    _last_load_error = None
    emotes = data.get("emotes", data) if isinstance(data, dict) else {}
    return {
        name: steps
        for name, steps in emotes.items()
        if not name.startswith("__") and isinstance(steps, list)
    }


def parse_emote(script: str | list[str]) -> list[tuple[Any, ...]]:
    """Turn 'turn 30; say hallo' or a list of such lines into checked steps."""
    lines = script if isinstance(script, list) else re.split(r"[;\n]+", script)
    steps: list[tuple[Any, ...]] = []
    for line in lines:
        tokens = str(line).strip().split()
        if not tokens:
            continue
        kind = tokens[0].lower()
        try:
            if kind == "say":
                text = " ".join(tokens[1:]).strip()
                if text:
                    steps.append(("say", text))
            elif kind == "gesture":
                name = tokens[1].lower()
                if name not in GESTURES:
                    raise ValueError(f"unknown gesture '{name}'")
                steps.append(("gesture", name))
            elif kind == "expression":
                name = tokens[1].lower()
                if name not in EXPRESSIONS:
                    raise ValueError(f"unknown expression '{name}'")
                steps.append(("expression", name))
            elif kind == "music":
                steps.append(("music", tokens[1].lower() in {"on", "aan", "true", "1", "yes"}))
            elif kind in {"turn", "drive"}:
                steps.append((kind, float(tokens[1])))
            elif kind == "wait":
                steps.append(("wait", min(MAX_WAIT_SECONDS, max(0.0, float(tokens[1])))))
            elif kind == "head":
                duration = float(tokens[3]) if len(tokens) > 3 else 0.5
                steps.append(("head", float(tokens[1]), float(tokens[2]), duration))
            else:
                raise ValueError(f"'{tokens[0]}' (known: {', '.join(STEP_KINDS)})")
        except IndexError:
            raise ValueError(f"'{line}' is missing a value") from None
    return steps


class ActionRunner:
    """Plays one emote or handshake at a time, stoppable from the operator page."""

    def __init__(
        self,
        robot: Any,
        social: Any,
        speak: Speak,
        *,
        console_print: Callable[..., None] = print,
    ) -> None:
        self.robot = robot
        self.social = social
        self.speak = speak
        self.console_print = console_print
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current: str | None = None
        self._gestures: list[Gesture] | None = None

    @property
    def current(self) -> str | None:
        with self._lock:
            return self._current

    def busy(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _start(self, name: str, target: Callable[[], None]) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._current = name

            def run() -> None:
                try:
                    target()
                except Exception as error:
                    self.console_print(f"[ARI ACTION] {name} failed: {error}")
                finally:
                    with self._lock:
                        self._current = None

            self._thread = threading.Thread(target=run, name="ari-action", daemon=True)
            self._thread.start()
        return True

    def play_emote(self, name: str, steps: list[tuple[Any, ...]]) -> bool:
        return self._start(name, lambda: self._run_steps(name, steps))

    def handshake(self) -> bool:
        return self._start("Hand geven", self._run_handshake)

    def stop(self) -> None:
        self._stop.set()
        try:
            self.social.stop_movement()
            self.social.music(False)
        except Exception:
            pass

    def _available_gestures(self) -> list[Gesture]:
        if self._gestures is None:
            try:
                self._gestures = list(self.robot.get_available_gestures())
            except Exception:
                self._gestures = list(GESTURES.values())
        return self._gestures

    def _play_gesture(self, name: str) -> None:
        gesture = GESTURES[name]
        if gesture not in self._available_gestures():
            self.console_print(f"[ARI EMOTE] ARI has no '{name}' gesture; skipped")
            return
        self.robot.play_gesture(gesture)

    def _run_steps(self, name: str, steps: list[tuple[Any, ...]]) -> None:
        self.console_print(f"[ARI EMOTE] {name}")
        set_face = False
        try:
            for step in steps:
                if self._stop.is_set():
                    break
                kind, values = step[0], step[1:]
                if kind == "head":
                    if self.social.head(*values):
                        self._stop.wait(values[2])
                elif kind == "turn":
                    self.social.turn(values[0])
                elif kind == "drive":
                    self.social.drive(values[0])
                elif kind == "gesture":
                    self._play_gesture(values[0])
                elif kind == "expression":
                    self.social.set_expression(values[0], EMOTE_EXPRESSION_SECONDS)
                    set_face = True
                elif kind == "say":
                    self.speak(values[0])
                elif kind == "music":
                    self.social.music(bool(values[0]))
                elif kind == "wait":
                    self._stop.wait(values[0])
        finally:
            # Never leave the music playing or a face stuck after an emote.
            self.social.music(False)
            if set_face:
                self.social.clear_expression()
            if self._stop.is_set():
                self.social.stop_movement()

    def _run_handshake(self) -> None:
        self.social.set_expression("happy", 20.0)
        try:
            self.speak(HANDSHAKE_OFFER)
            if self._stop.is_set():
                return
            gesture_thread = threading.Thread(
                target=self._play_gesture,
                args=("greet",),
                name="ari-handshake-arm",
                daemon=True,
            )
            gesture_thread.start()
            # Without a live laser or depth image there is nothing to check, so
            # ARI just assumes the hand was there rather than sounding let down.
            can_see = self.social.has_capability("nearest") and self.social.snapshot().get(
                "obstacle_sensor"
            ) in {"laser", "depth"}
            found = False
            deadline = time.monotonic() + HANDSHAKE_SECONDS
            while can_see and time.monotonic() < deadline and not self._stop.is_set():
                distance = self.social.nearest()
                if distance is not None and distance < HAND_DISTANCE_METERS:
                    found = True
                    break
                self._stop.wait(0.25)
            gesture_thread.join(timeout=8.0)
            if self._stop.is_set():
                return
            if found or not can_see:
                self.speak(HANDSHAKE_DONE, gesture="nod", expression="happy")
            else:
                self.speak(HANDSHAKE_MISSED, expression="neutral")
        finally:
            self.social.clear_expression()


def gesture_for(name: str | None) -> Gesture | None:
    return GESTURES.get(name or "")


def expression_for(name: str | None) -> Expression | None:
    try:
        return Expression(name) if name else None
    except ValueError:
        return None
