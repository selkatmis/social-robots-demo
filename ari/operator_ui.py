"""Local-only operator UI and shared runtime coordination for ARI."""

from __future__ import annotations

import threading
import math
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from flask import Flask, Response, jsonify, request
from werkzeug.serving import BaseWSGIServer, WSGIRequestHandler, make_server

from ari.responses import ConversationEngine, ConversationTurn
from ari.emotes import ActionRunner, EXPRESSIONS, GESTURES, load_emotes, parse_emote
from ari.guardian_prompts import STAFF_ON_THE_WAY


UI_HOST = "127.0.0.1"
UI_PORT = 8765
MAX_TRANSCRIPT_ITEMS = 200


class QuietRequestHandler(WSGIRequestHandler):
    """Keep the operator terminal readable while the browser polls state."""

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        del code, size


class RuntimeCoordinator:
    """Coordinate one engine, mic and robot session across CLI and UI threads."""

    def __init__(
        self,
        engine: ConversationEngine,
        robot: Any,
        microphone_input: Any,
        social_control: Any,
        *,
        turn_speaker: Callable[..., None] | None = None,
        speech_timing: Any = None,
        mode: str = "companion",
        idle_seconds: float | None = None,
    ) -> None:
        self.engine = engine
        self.robot = robot
        self.microphone_input = microphone_input
        self.social_control = social_control
        self.mode = mode
        self.idle_seconds = idle_seconds
        self._engine_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._tablet_lock = threading.RLock()
        self._speech_lock = threading.RLock()
        self._operator_gate = threading.Event()
        self._control_lock = threading.RLock()
        self._turn_speaker = turn_speaker
        self._speech_timing = speech_timing
        self._generation = 0
        self._transcript: list[dict[str, str]] = []
        self._phase = "STANDBY"
        self._activity = "LISTENING"
        self._robot_connected = True
        self._tablet_page_sent: str | None = None
        self._volume: int | None = None
        self._alert: dict[str, Any] | None = None
        self._alert_count = 0
        self._last_activity = time.monotonic()
        self._stop_watchdog = threading.Event()
        self.actions = ActionRunner(robot, social_control, self.speak)

    @property
    def generation(self) -> int:
        with self._state_lock:
            return self._generation

    def is_current(self, generation: int) -> bool:
        return generation == self.generation

    def start_engine(self) -> ConversationTurn:
        with self._engine_lock:
            turn = self.engine.start()
        self._set_phase(self.engine.state["phase"])
        self.social_control.set_conversation_active(False)
        if self.idle_seconds:
            threading.Thread(
                target=self._watch_idle,
                name="ari-idle-watch",
                daemon=True,
            ).start()
        return turn

    def handle_transcript(
        self,
        visitor_text: str,
        generation: int,
    ) -> ConversationTurn | None:
        clean = " ".join(str(visitor_text).split())
        with self._state_lock:
            if generation != self._generation:
                return None
            self._last_activity = time.monotonic()
            if clean:
                self._transcript.append({"speaker": "Bezoeker", "text": clean})
                self._transcript = self._transcript[-MAX_TRANSCRIPT_ITEMS:]
            self._activity = "THINKING"
        with self._engine_lock:
            if not self.is_current(generation):
                return None
            turn = self.engine.handle_input(visitor_text)
            turn = self._apply_tablet_request(turn, generation)
        if not self.is_current(generation):
            return None
        self._phase_for_turn(turn, generation)
        return turn

    def prepare_turn(self, turn: ConversationTurn, generation: int) -> bool:
        if not self.is_current(generation):
            return False
        with self._engine_lock:
            turn_after_tablet = self._apply_tablet_request(turn, generation)
        # A game-page failure is normally resolved in handle_transcript before
        # this method.  The initial silent Main request cannot replace speech.
        if turn_after_tablet is not turn and turn.utterances:
            return False
        alert = getattr(turn, "alert", None)
        if alert:
            self._raise_alert(alert)
        farewell = "[PHASE] GOODBYE" in turn.diagnostics
        active = self.engine.state["phase"] != "STANDBY" or farewell or bool(
            turn.utterances and self.actions.busy()
        )
        self.social_control.set_conversation_active(active)
        if not self.is_current(generation):
            self.social_control.set_conversation_active(False)
            return False
        self._phase_for_turn(turn, generation)
        return True

    def _apply_tablet_request(
        self,
        turn: ConversationTurn,
        generation: int,
    ) -> ConversationTurn:
        page = turn.tablet_page
        if not page or page == self._tablet_page_sent:
            return turn
        with self._tablet_lock:
            if not self.is_current(generation):
                return turn
            setter = getattr(self.social_control, "set_tablet_page", None)
            success = bool(setter and setter(page))
            if not self.is_current(generation):
                return turn
            if success:
                self._tablet_page_sent = page
                return turn
            if page == "Zoek_de_verschillen":
                fallback = self.engine.handle_tablet_navigation_failure()
                if turn.greeting_wave and turn.utterances:
                    fallback = replace(
                        fallback,
                        utterances=(turn.utterances[0],) + fallback.utterances,
                        greeting_wave=True,
                    )
                # The fallback requests Main.  Try it once, but its failure is
                # non-fatal because no game is allowed to continue.
                if fallback.tablet_page == "Main" and setter is not None:
                    if bool(setter("Main")):
                        self._tablet_page_sent = "Main"
                return fallback
            return turn

    def finish_turn(self, generation: int) -> None:
        with self._state_lock:
            if generation != self._generation:
                return
            phase = self.engine.state["phase"]
            self._phase = phase
        if phase == "STANDBY":
            self.social_control.set_conversation_active(False)

    def begin_speaking(self, utterance: str, generation: int) -> bool:
        with self._control_lock:
            with self._state_lock:
                if generation != self._generation:
                    return False
                self.add_transcript("ARI", utterance)
                self._activity = "SPEAKING"
                self._last_activity = time.monotonic()
            self.social_control.set_speaking(True)
            if not self.is_current(generation):
                self.social_control.set_speaking(False)
                return False
            return True

    def end_speaking(self, generation: int) -> None:
        with self._state_lock:
            self._last_activity = time.monotonic()
        with self._control_lock:
            self.social_control.set_speaking(False)
        if self.is_current(generation):
            self.set_activity("LISTENING")

    def show_utterance_face(self, action: dict[str, Any], seconds: float) -> None:
        """The face the conversation picked for one line, held while it is spoken."""
        expression = (action or {}).get("expression")
        if expression:
            with self._control_lock:
                self.social_control.set_expression(expression, seconds)

    def begin_listening(self) -> None:
        self.set_activity("LISTENING")

    def wait_for_actions(self, generation: int) -> bool:
        while self.is_current(generation) and self._operator_gate.is_set():
            self.actions.wait(0.1)
        return self.is_current(generation)

    def can_listen(self, generation: int) -> bool:
        return self.is_current(generation) and not self._operator_gate.is_set()

    def deliver_turn(
        self, turn: ConversationTurn, generation: int,
        *, should_continue: Callable[[], bool] | None = None,
    ) -> bool:
        allowed = lambda: self.is_current(generation) and (
            should_continue is None or should_continue()
        )
        while not self._speech_lock.acquire(timeout=0.1):
            if not allowed():
                return False
        try:
            if not allowed() or not self.prepare_turn(turn, generation):
                return False
            if self._turn_speaker is not None:
                self._turn_speaker(
                    turn, self.robot, self.microphone_input, self._speech_timing,
                    should_continue=allowed,
                    on_speech_start=lambda text: self.begin_speaking(text, generation),
                    on_speech_end=lambda: self.end_speaking(generation),
                    on_utterance_face=self.show_utterance_face,
                )
            else:
                # Hardware-free integrations can use the runtime without the
                # microphone adapter. The real adapter supplies speak_turn.
                for index, text in enumerate(turn.utterances):
                    if not allowed() or not self.begin_speaking(text, generation):
                        break
                    try:
                        if index == 0 and turn.greeting_wave and hasattr(self.robot, "coordinated_action"):
                            self.robot.coordinated_action(text, gesture=GESTURES["wave"])
                        else:
                            self.robot.say(text)
                    finally:
                        self.end_speaking(generation)
            self.finish_turn(generation)
            return allowed()
        finally:
            self._speech_lock.release()

    def speak(self, text: str, *, should_continue: Callable[[], bool] | None = None) -> None:
        generation = self.generation
        self.deliver_turn(ConversationTurn((text,)), generation, should_continue=should_continue)

    def _start_operator(self, name: str, target: Callable[[int, threading.Event], None]) -> bool:
        with self._state_lock:
            generation = self._generation
            if self._operator_gate.is_set() or self.actions.busy():
                return False
            self._operator_gate.set()
        ready = threading.Event()
        def run(stopped: threading.Event) -> None:
            ready.wait()
            try:
                if self.is_current(generation) and not stopped.is_set():
                    target(generation, stopped)
            finally:
                self._operator_gate.clear()
        started = self.actions.start(name, run)
        if not started:
            self._operator_gate.clear()
            return False
        try:
            cancel = getattr(self.microphone_input, "cancel_pending", None)
            if cancel is not None:
                cancel()
        finally:
            ready.set()
        return True

    def _raise_alert(self, alert: dict[str, Any]) -> None:
        """Record a request for a person, for the operator page to show."""
        with self._state_lock:
            self._alert_count += 1
            self._alert = {
                "id": self._alert_count,
                "kind": alert.get("kind", "help"),
                "note": alert.get("note") or "",
                "place": alert.get("place"),
                "time": time.strftime("%H:%M"),
            }
            note, kind, place = self._alert["note"], self._alert["kind"], self._alert["place"]
        print(f"[ARI ALERT] {kind}: {note}" + (f" ({place})" if place else ""))

    def ack_alert(self, speak: bool = True) -> bool:
        """Clear the alert and let ARI tell the visitor that someone is coming."""
        with self._state_lock:
            had_alert = self._alert is not None
            self._alert = None
        if had_alert and speak:
            self._start_operator(
                "Melding bevestigen",
                lambda generation, cancel: self.deliver_turn(
                    ConversationTurn((STAFF_ON_THE_WAY,)),
                    generation,
                    should_continue=lambda: not cancel.is_set(),
                ),
            )
        return had_alert

    def handshake(self) -> tuple[bool, str | None]:
        ok = self._start_operator(
            "Hand geven", lambda _generation, cancel: self.actions._run_handshake(cancel)
        )
        return ok, None if ok else "ARI is nog bezig met een operatoractie."

    def operator_say(self, text: str) -> bool:
        clean = " ".join(text.split())
        if not clean or len(clean) > 400:
            return False
        return self._start_operator(
            "Spreken", lambda generation, cancel: self.deliver_turn(
                ConversationTurn((clean,)), generation, should_continue=lambda: not cancel.is_set()
            ),
        )

    def greet(self) -> bool:
        if self.engine.state.get("phase") != "STANDBY":
            return False
        def run(generation: int, cancel: threading.Event) -> None:
            with self._engine_lock:
                if not self.is_current(generation) or cancel.is_set() or self.engine.state.get("phase") != "STANDBY":
                    return
                turn = self.engine.handle_input("Hallo ARI")
            self.deliver_turn(turn, generation, should_continue=lambda: not cancel.is_set())
        return self._start_operator("Begroeten", run)

    def show_expression(self, name: str) -> bool:
        if name not in EXPRESSIONS:
            return False
        setter = getattr(self.social_control, "set_expression", None)
        generation = self.generation
        with self._control_lock:
            return bool(self.is_current(generation) and setter and setter(name, 8.0))

    def play_gesture(self, name: str) -> bool:
        gesture = GESTURES.get(name)
        if gesture is None:
            return False
        def run(_generation: int, cancel: threading.Event) -> None:
            available = self.robot.get_available_gestures()
            if gesture in available and not cancel.is_set():
                self.robot.play_gesture(gesture)
            elif not cancel.is_set():
                raise RuntimeError("Dit gebaar is niet beschikbaar op de robot.")
        return self._start_operator(name, run)

    def play_emote(self, name: str | None = None, script: str | None = None) -> tuple[bool, str | None]:
        try:
            if script is not None:
                steps = parse_emote(script)
                name = "Eigen emote"
            else:
                emotes = load_emotes()
                if name not in emotes:
                    return False, "Onbekende emote."
                steps = parse_emote(emotes[name])
            if not steps:
                return False, "Deze emote heeft geen stappen."
        except ValueError as error:
            return False, str(error)
        ok = self._start_operator(name or "Emote", lambda _generation, cancel: self.actions._run_steps(steps, cancel))
        return ok, None if ok else "ARI is nog bezig met een operatoractie."

    def stop_actions(self) -> None:
        # Invalidate queued speech and in-flight model output, while retaining
        # the visitor's engine/session and difference-game progress.
        with self._state_lock:
            self._generation += 1
            self._activity = "LISTENING"
        self.actions.stop()
        cancel = getattr(self.microphone_input, "cancel_pending", None)
        if cancel is not None:
            cancel()
        try:
            self.robot.stop()
        except Exception:
            pass
        stop = getattr(self.social_control, "stop_actions", None)
        with self._control_lock:
            if stop is not None:
                stop()
            self.social_control.set_speaking(False)
        self.social_control.stop_movement()

    def volume(self, percent: float | None = None) -> int | None:
        setter = getattr(self.social_control, "volume", None)
        result = setter(percent) if setter is not None else None
        if result is not None:
            self._volume = result
        return result

    def _phase_for_turn(
        self,
        turn: ConversationTurn,
        generation: int | None = None,
    ) -> None:
        with self._state_lock:
            if generation is not None and generation != self._generation:
                return
            if "[PHASE] GOODBYE" in turn.diagnostics and turn.utterances:
                self._phase = "GOODBYE"
            else:
                self._phase = self.engine.state["phase"]

    def _set_phase(self, phase: str) -> None:
        with self._state_lock:
            self._phase = phase

    def set_activity(self, activity: str) -> None:
        with self._state_lock:
            self._activity = activity

    def add_transcript(self, speaker: str, text: str) -> None:
        clean = " ".join(str(text).split())
        if not clean:
            return
        with self._state_lock:
            self._transcript.append({"speaker": speaker, "text": clean})
            self._transcript = self._transcript[-MAX_TRANSCRIPT_ITEMS:]

    def reset_to_standby(self) -> None:
        # Invalidate in-flight LLM/speech work before waiting for the engine
        # lock. Its eventual result can therefore never be spoken.
        with self._state_lock:
            self._generation += 1
            self._phase = "STANDBY"
            self._activity = "LISTENING"
            self._transcript = []
            self._alert = None
            self._last_activity = time.monotonic()

        cancel = getattr(self.microphone_input, "cancel_pending", None)
        if cancel is not None:
            cancel()
        self.actions.stop()
        try:
            self.robot.stop()
        except Exception:
            pass
        self.social_control.stop_movement()
        stop = getattr(self.social_control, "stop_actions", None)
        with self._control_lock:
            if stop is not None:
                stop()
            self.social_control.set_speaking(False)
        tablet_setter = getattr(self.social_control, "set_tablet_page", None)
        with self._tablet_lock:
            if tablet_setter is not None and tablet_setter("Main"):
                self._tablet_page_sent = "Main"
        self.social_control.set_conversation_active(False)
        with self._engine_lock:
            self.engine.reset_to_standby()

    def _watch_idle(self) -> None:
        while not self._stop_watchdog.wait(1.0):
            with self._state_lock:
                quiet = time.monotonic() - self._last_activity
                busy = self._activity != "LISTENING" or self._phase == "STANDBY"
            if busy or self.actions.busy() or quiet < self.idle_seconds:
                continue
            print(f"[ARI] No one spoke for {self.idle_seconds:.0f} s; back to standby.")
            self._idle_reset()

    def _idle_reset(self) -> None:
        # Unlike the operator's reset this keeps the transcript and lets a
        # running emote finish; it only forgets the visitor.
        with self._state_lock:
            self._generation += 1
            self._phase = "STANDBY"
            self._activity = "LISTENING"
            self._last_activity = time.monotonic()
        cancel = getattr(self.microphone_input, "cancel_pending", None)
        if cancel is not None:
            cancel()
        self.social_control.set_conversation_active(False)
        with self._engine_lock:
            self.engine.reset_to_standby()
        self._set_phase("STANDBY")
        self.add_transcript("", "Niemand meer aan het woord: terug naar standby.")

    def close(self) -> None:
        self._stop_watchdog.set()

    def move(self, action: str) -> bool:
        social = self.social_control.snapshot()
        if social.get("robot_connected") is False:
            self.social_control.stop_movement()
            return False
        if action != "stop" and self.actions.busy():
            self.stop_actions()
            self.actions.wait(0.2)
            if self.actions.busy():
                return False
        return self.social_control.move(action)

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            state = {
                "connected": self._robot_connected,
                "phase": self._phase,
                "activity": self._activity,
                "transcript": list(self._transcript),
                "generation": self._generation,
                "mode": self.mode,
                "alert": dict(self._alert) if self._alert else None,
            }
        social = self.social_control.snapshot()
        robot_connected = social.get("robot_connected", self._robot_connected)
        movement_available = bool(
            robot_connected and social.get("movement_available", False)
        )
        state.update(
            {
                "connected": robot_connected,
                "control_connected": social.get("control_connected", False),
                "control_error": social.get("control_error"),
                "attention_mode": social.get("attention_mode", "UNKNOWN"),
                "expression": social.get("expression", "unknown"),
                "face_stable": social.get("face_stable", False),
                "movement_available": movement_available,
                "movement_active": social.get("movement_active", False),
                "obstacle_sensor": social.get("obstacle_sensor", "none"),
                "attention_target": social.get("attention_target"),
                "tablet_available": social.get("tablet_available", False),
                "tablet_page": social.get("tablet_page"),
                "tablet_error": social.get("tablet_error"),
                "active_activity": self.engine.state.get("active_activity"),
                "game_found_count": self.engine.state.get("game_found_count", 0),
                "game_target_count": self.engine.state.get("game_target_count", 3),
                "action": self.actions.current,
                "action_error": self.actions.error,
                "emotes": list(load_emotes()),
                "capabilities": social.get("capabilities", []),
                "face_count": social.get("face_count", 0),
                "music_playing": social.get("music_playing", False),
                "sidecar_version": social.get("sidecar_version"),
                "volume": self._volume,
            }
        )
        return state


def create_app(runtime: RuntimeCoordinator) -> Flask:
    app = Flask(__name__)

    @app.before_request
    def object_body() -> Any:
        if request.method == "POST" and not isinstance(request.get_json(silent=True), dict):
            return jsonify({"ok": False, "error": "JSON-object verwacht."}), 400
        return None

    @app.after_request
    def local_security_headers(response: Any) -> Any:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
        )
        return response

    @app.get("/")
    def index() -> Response:
        return Response(PAGE, mimetype="text/html")

    @app.get("/state")
    def state() -> Any:
        return jsonify(runtime.snapshot())

    @app.post("/move")
    def move() -> Any:
        body = request.get_json(force=True, silent=True) or {}
        action = str(body.get("action", ""))
        ok = runtime.move(action)
        return jsonify({"ok": ok, "movement_available": runtime.snapshot()["movement_available"]})

    @app.post("/standby")
    def standby() -> Any:
        runtime.reset_to_standby()
        return jsonify({"ok": True, "phase": "STANDBY"})

    @app.post("/greet")
    def greet() -> Any:
        ok = runtime.greet()
        return jsonify({"ok": ok, "error": None if ok else "ARI is al in gesprek of bezig."}), 200 if ok else 409

    @app.post("/say")
    def say() -> Any:
        text = request.get_json().get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 400:
            return jsonify({"ok": False, "error": "Typ 1 tot 400 tekens."}), 400
        ok = runtime.operator_say(text)
        return jsonify({"ok": ok, "error": None if ok else "ARI is nog bezig."}), 200 if ok else 409

    @app.post("/expression")
    def expression() -> Any:
        name = request.get_json().get("name")
        if not isinstance(name, str) or name not in EXPRESSIONS:
            return jsonify({"ok": False, "error": "Onbekend gezicht."}), 400
        ok = runtime.show_expression(name)
        return jsonify({"ok": ok, "error": None if ok else "Gezichtsbediening is niet beschikbaar."})

    @app.post("/gesture")
    def gesture() -> Any:
        name = request.get_json().get("name")
        if not isinstance(name, str) or name not in GESTURES:
            return jsonify({"ok": False, "error": "Onbekend gebaar."}), 400
        ok = runtime.play_gesture(name)
        return jsonify({"ok": ok, "error": None if ok else "ARI is nog bezig."}), 200 if ok else 409

    @app.post("/emote")
    def emote() -> Any:
        data = request.get_json()
        name, script = data.get("name"), data.get("script")
        if (name is not None and not isinstance(name, str)) or (script is not None and not isinstance(script, str)):
            return jsonify({"ok": False, "error": "Gebruik tekst voor de emote."}), 400
        ok, error = runtime.play_emote(name, script)
        return jsonify({"ok": ok, "error": error}), 200 if ok else 400

    @app.post("/handshake")
    def handshake() -> Any:
        ok, error = runtime.handshake()
        return jsonify({"ok": ok, "error": error}), 200 if ok else 409

    @app.post("/alert/ack")
    def ack_alert() -> Any:
        speak = request.get_json().get("speak", True)
        if not isinstance(speak, bool):
            return jsonify({"ok": False, "error": "Gebruik true of false voor speak."}), 400
        return jsonify({"ok": runtime.ack_alert(speak)})

    @app.post("/stop")
    def stop() -> Any:
        runtime.stop_actions()
        return jsonify({"ok": True})

    @app.route("/volume", methods=["GET", "POST"])
    def volume() -> Any:
        percent = None
        if request.method == "POST":
            percent = request.get_json().get("percent")
            if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not math.isfinite(percent) or not 30 <= percent <= 100:
                return jsonify({"ok": False, "error": "Volume moet tussen 30 en 100 liggen."}), 400
        result = runtime.volume(percent)
        return jsonify({"ok": result is not None, "percent": result})

    return app


class LocalOperatorServer:
    def __init__(
        self,
        runtime: RuntimeCoordinator,
        host: str = UI_HOST,
        port: int = UI_PORT,
    ) -> None:
        self.host = host
        self.port = port
        self.app = create_app(runtime)
        self._server: BaseWSGIServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._server = make_server(
            self.host,
            self.port,
            self.app,
            threaded=True,
            request_handler=QuietRequestHandler,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="ari-operator-ui",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)


PAGE = r"""<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARI bediening</title>
<style>
:root { color-scheme: light; --ink:#17212b; --muted:#64748b; --line:#dbe3ea;
  --blue:#155eef; --blue-soft:#eef4ff; --red:#b42318; --bg:#f5f7fa; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.45 system-ui,sans-serif; }
main { max-width:1080px; margin:0 auto; padding:24px; display:grid;
  grid-template-columns:minmax(0,1fr) 300px; gap:20px; }
.card { background:white; border:1px solid var(--line); border-radius:18px;
  box-shadow:0 8px 25px rgba(23,33,43,.06); padding:20px; }
h1 { margin:0 0 16px; font-size:25px; }
h2 { margin:0 0 14px; font-size:16px; }
.status { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; }
.chip { border-radius:999px; padding:6px 10px; background:#edf1f5; font-size:13px; }
.chip.good { color:#067647; background:#ecfdf3; }
.chip.bad { color:var(--red); background:#fef3f2; }
.hidden { display:none; }
#log { height:560px; overflow:auto; border-top:1px solid var(--line); padding-top:10px; }
.entry { margin:12px 0; padding:12px 14px; border-radius:13px; max-width:88%; }
.entry.visitor { background:#f0f2f5; }
.entry.ari { background:var(--blue-soft); margin-left:auto; }
.speaker { font-size:12px; font-weight:700; color:var(--muted); margin-bottom:3px; }
.controls { display:grid; grid-template-columns:repeat(3,72px); justify-content:center; gap:9px; }
button { border:0; border-radius:13px; min-height:58px; padding:10px; font:600 16px system-ui;
  cursor:pointer; background:#e8edf3; color:var(--ink); touch-action:none; user-select:none; }
button:active { transform:scale(.97); }
button.arrow { font-size:28px; }
button.stop { color:var(--red); }
button.primary { width:100%; margin-top:20px; color:white; background:var(--blue); }
button:disabled { opacity:.35; cursor:not-allowed; }
.hint { color:var(--muted); font-size:13px; margin-top:12px; }
.operator { margin-top:20px; border-top:1px solid var(--line); padding-top:16px; }
.operator input, .operator textarea, .operator select { width:100%; box-sizing:border-box; margin:6px 0; padding:10px; font:inherit; }
.operator button { min-height:40px; margin:3px; font-size:14px; }
.operator .row { display:flex; flex-wrap:wrap; gap:4px; }
@media(max-width:760px){ main{grid-template-columns:1fr;padding:12px} #log{height:420px} }
</style>
</head>
<body><main>
  <section class="card">
    <h1>ARI gesprek</h1>
    <div class="status">
      <span id="connection" class="chip">Verbinden…</span>
      <span id="phase" class="chip">STANDBY</span>
      <span id="activity" class="chip">LISTENING</span>
      <span id="attention" class="chip">Aandacht: onbekend</span>
      <span id="expression" class="chip">Gezicht: onbekend</span>
      <span id="game" class="chip hidden"></span>
    </div>
    <div id="log" aria-live="polite"></div>
  </section>
  <aside class="card">
    <h2>Handmatig bewegen</h2>
    <div class="controls">
      <span></span><button class="arrow" data-move="forward" aria-label="Vooruit">↑</button><span></span>
      <button class="arrow" data-move="left" aria-label="Links draaien">←</button>
      <button class="stop" data-move="stop" aria-label="Stop bewegen">■</button>
      <button class="arrow" data-move="right" aria-label="Rechts draaien">→</button>
      <span></span><button class="arrow" data-move="back" aria-label="Achteruit">↓</button><span></span>
    </div>
    <p id="moveHint" class="hint">Beweging is alleen beschikbaar met de veilige robot-sidecar.</p>
    <button id="standby" class="primary">Terug naar standby</button>
    <div class="operator">
      <h2>Operatorbediening</h2>
      <div class="row"><button id="greet">Begroet bezoeker</button><button id="stopActions" class="stop">STOP acties</button></div>
      <form id="sayForm"><input id="sayText" maxlength="400" placeholder="Laat ARI iets zeggen" aria-label="Tekst voor ARI"><button type="submit">Zeg</button></form>
      <div id="phrases" class="row"></div>
      <h2>Gezicht en gebaren</h2>
      <div id="faces" class="row"></div>
      <div class="row"><button data-gesture="wave">Zwaaien</button><button data-gesture="nod">Knikken</button><button data-gesture="cheer">Juichen</button></div>
      <h2>Emotes</h2><div id="emotes" class="row"></div>
      <details><summary>Eigen emote</summary><textarea id="emoteScript" rows="3" placeholder="gesture wave&#10;say Goedendag!&#10;wait 1"></textarea><button id="runScript">Speel af</button></details>
      <label for="volume">Volume: <span id="volumeText">--</span></label>
      <input id="volume" type="range" min="30" max="100" step="5" value="80">
      <p id="operatorStatus" class="hint" aria-live="polite"></p>
    </div>
  </aside>
</main>
<script>
let moveTimer = null;
let heldAction = null;
let refreshInProgress = false;
let volumeLoaded = false;

async function post(path, body) {
  try {
    const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body || {})});
    return await response.json();
  } catch (_) { return {ok:false}; }
}
function sendMove(action) { return post('/move', {action}); }
function startMove(action) {
  if (action === 'stop') { stopMove(); return; }
  if (heldAction === action) return;
  stopMove(); heldAction = action; sendMove(action);
  moveTimer = setInterval(() => sendMove(action), 250);
}
function stopMove() {
  if (moveTimer) clearInterval(moveTimer);
  moveTimer = null; heldAction = null; sendMove('stop');
}
for (const button of document.querySelectorAll('[data-move]')) {
  const action = button.dataset.move;
  button.addEventListener('pointerdown', event => { event.preventDefault(); startMove(action); });
  for (const name of ['pointerup','pointerleave','pointercancel'])
    button.addEventListener(name, event => { event.preventDefault(); stopMove(); });
  button.addEventListener('contextmenu', event => event.preventDefault());
}
const keys = {ArrowUp:'forward',ArrowDown:'back',ArrowLeft:'left',ArrowRight:'right'};
document.addEventListener('keydown', event => {
  if (['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) return;
  if (keys[event.key]) { event.preventDefault(); if (!event.repeat) startMove(keys[event.key]); }
});
document.addEventListener('keyup', event => {
  if (keys[event.key]) { event.preventDefault(); stopMove(); }
});
window.addEventListener('blur', stopMove);
window.addEventListener('pagehide', stopMove);
document.addEventListener('visibilitychange', () => { if (document.hidden) stopMove(); });
document.getElementById('standby').addEventListener('click', async () => {
  stopMove(); await post('/standby'); await refresh();
});

async function operatorAction(path, body) {
  const result = await post(path, body);
  document.getElementById('operatorStatus').textContent = result.ok ? 'Opdracht ontvangen.' : (result.error || 'Opdracht is niet beschikbaar.');
  return result;
}
document.getElementById('greet').onclick = () => operatorAction('/greet');
document.getElementById('stopActions').onclick = () => { stopMove(); operatorAction('/stop'); };
document.getElementById('sayForm').onsubmit = async event => {
  event.preventDefault(); const input = document.getElementById('sayText');
  if ((await operatorAction('/say', {text:input.value})).ok) input.value = '';
};
for (const [label, text] of [['Welkom','Goedendag, welkom bij onze stand!'],['Momentje','Een momentje alstublieft.'],['Tot ziens','Dank u wel en tot ziens!']]) {
  const button = document.createElement('button'); button.textContent = label;
  button.onclick = () => operatorAction('/say', {text}); document.getElementById('phrases').append(button);
}
for (const [name,label] of [['happy','Blij'],['neutral','Neutraal'],['sad','Verdrietig'],['surprised','Verrast'],['thinking','Denkend'],['excited','Enthousiast']]) {
  const button = document.createElement('button'); button.textContent = label;
  button.onclick = () => operatorAction('/expression', {name}); document.getElementById('faces').append(button);
}
for (const button of document.querySelectorAll('[data-gesture]')) button.onclick = () => operatorAction('/gesture', {name:button.dataset.gesture});
document.getElementById('runScript').onclick = () => operatorAction('/emote', {script:document.getElementById('emoteScript').value});
document.getElementById('volume').onchange = event => {
  document.getElementById('volumeText').textContent = event.target.value + '%';
  operatorAction('/volume', {percent:Number(event.target.value)});
};
document.getElementById('volume').oninput = event => {
  document.getElementById('volumeText').textContent = event.target.value + '%';
};

function chip(id, text, good) {
  const element = document.getElementById(id); element.textContent = text;
  element.className = 'chip ' + (good === true ? 'good' : good === false ? 'bad' : '');
}
function renderTranscript(items) {
  const log = document.getElementById('log');
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 50;
  log.replaceChildren();
  for (const item of items) {
    const entry = document.createElement('div');
    entry.className = 'entry ' + (item.speaker === 'ARI' ? 'ari' : 'visitor');
    const speaker = document.createElement('div'); speaker.className = 'speaker';
    speaker.textContent = item.speaker;
    const text = document.createElement('div'); text.textContent = item.text;
    entry.append(speaker, text); log.append(entry);
  }
  if (atBottom) log.scrollTop = log.scrollHeight;
}
async function refresh() {
  if (refreshInProgress) return;
  refreshInProgress = true;
  try {
    const state = await (await fetch('/state', {cache:'no-store'})).json();
    if (state.control_connected && !volumeLoaded) {
      volumeLoaded = true;
      fetch('/volume').then(response => response.json()).then(data => {
        if (typeof data.percent === 'number') {
          document.getElementById('volume').value = data.percent;
          document.getElementById('volumeText').textContent = data.percent + '%';
        }
      }).catch(() => {});
    }
    if (!state.control_connected) volumeLoaded = false;
    chip('connection', state.connected ? 'Robot verbonden' : 'Robot niet verbonden', state.connected);
    chip('phase', state.phase); chip('activity', state.activity);
    chip('attention', state.control_connected
      ? 'Aandacht: ' + state.attention_mode
      : 'Aandacht: niet verbonden', state.control_connected);
    chip('expression', 'Gezicht: ' + state.expression, state.expression !== 'unknown' ? true : null);
    const game = document.getElementById('game');
    if (state.active_activity === 'ZOEK_DE_VERSCHILLEN') {
      game.textContent = 'Zoek de verschillen: ' + state.game_found_count +
        ' van ' + state.game_target_count + ' gevonden';
      game.className = 'chip';
    } else {
      game.className = 'chip hidden';
    }
    renderTranscript(state.transcript || []);
    const emotes = document.getElementById('emotes');
    const names = JSON.stringify(state.emotes || []);
    if (emotes.dataset.names !== names) {
      emotes.dataset.names = names; emotes.replaceChildren();
      for (const name of state.emotes || []) {
        const button = document.createElement('button'); button.textContent = name;
        button.onclick = () => operatorAction('/emote', {name}); emotes.append(button);
      }
    }
    if (state.action_error) document.getElementById('operatorStatus').textContent = state.action_error;
    for (const button of document.querySelectorAll('[data-move]'))
      button.disabled = !state.movement_available && button.dataset.move !== 'stop';
    document.getElementById('moveHint').textContent = state.movement_available
      ? (state.obstacle_sensor === 'none'
          ? 'Houd ingedrukt om te bewegen. Geen obstakelsensor actief; rijd voorzichtig.'
          : 'Houd ingedrukt om te bewegen. Obstakelcontrole: ' + state.obstacle_sensor + '.')
      : 'Beweging is veilig uitgeschakeld: de robot-sidecar of basisaandrijving is niet beschikbaar.';
  } catch (_) {
    chip('connection', 'Lokale UI niet verbonden', false);
    stopMove();
  } finally {
    refreshInProgress = false;
  }
}
setInterval(refresh, 1000); refresh();
</script></body></html>"""
