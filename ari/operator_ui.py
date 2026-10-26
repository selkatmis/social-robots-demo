"""Local-only operator UI and shared runtime coordination for ARI."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from flask import Flask, Response, jsonify, request
from werkzeug.serving import BaseWSGIServer, WSGIRequestHandler, make_server

from ari.emotes import (
    EXPRESSIONS,
    GESTURES,
    ActionRunner,
    gesture_for,
    load_emotes,
    parse_emote,
)
from ari.guardian_prompts import STAFF_ON_THE_WAY
from ari.responses import ConversationEngine, ConversationTurn


UI_HOST = "127.0.0.1"
UI_PORT = 8765
MAX_TRANSCRIPT_ITEMS = 200


class QuietRequestHandler(WSGIRequestHandler):
    """Keep the operator terminal readable while the browser polls state."""

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        del code, size


class SpeechLockedRobot:
    """One utterance at a time, whether it comes from the conversation or the operator."""

    def __init__(self, robot: Any) -> None:
        self._robot = robot
        self._speech_lock = threading.Lock()

    def say(self, text: str) -> None:
        with self._speech_lock:
            self._robot.say(text)

    def coordinated_action(self, text: str, gesture: Any = None, expression: Any = None, speed: float = 0.5) -> None:
        with self._speech_lock:
            self._robot.coordinated_action(text, gesture=gesture, expression=expression, speed=speed)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._robot, name)


def estimate_speech_seconds(text: str) -> float:
    return 0.6 + 0.42 * len(text.split())


class RuntimeCoordinator:
    """Coordinate one engine, mic and robot session across CLI and UI threads."""

    def __init__(
        self,
        engine: ConversationEngine,
        robot: Any,
        microphone_input: Any,
        social_control: Any,
        *,
        mode: str = "companion",
        idle_seconds: float | None = None,
        console_print: Callable[..., None] = print,
    ) -> None:
        self.engine = engine
        self.robot = robot
        self.microphone_input = microphone_input
        self.social_control = social_control
        self.mode = mode
        self.idle_seconds = idle_seconds
        self.console_print = console_print
        self._engine_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._generation = 0
        self._transcript: list[dict[str, str]] = []
        self._phase = "STANDBY"
        self._activity = "LISTENING"
        self._robot_connected = True
        self._alert: dict[str, Any] | None = None
        self._alert_count = 0
        self._last_activity = time.monotonic()
        self._volume: int | None = None
        self._stop_watchdog = threading.Event()
        self.actions = ActionRunner(
            robot,
            social_control,
            self.speak,
            console_print=console_print,
        )

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
        if not self.is_current(generation):
            return None
        self._touch()
        self.add_transcript("Bezoeker", visitor_text)
        self.set_activity("THINKING")
        with self._engine_lock:
            if not self.is_current(generation):
                return None
            turn = self.engine.handle_input(visitor_text)
        if not self.is_current(generation):
            return None
        self._phase_for_turn(turn)
        return turn

    def prepare_turn(self, turn: ConversationTurn, generation: int) -> bool:
        if not self.is_current(generation):
            return False
        farewell = "[PHASE] GOODBYE" in turn.diagnostics
        active = self.engine.state["phase"] != "STANDBY" or farewell
        self.social_control.set_conversation_active(active)
        self._phase_for_turn(turn)
        alert = getattr(turn, "alert", None)
        if alert:
            self._raise_alert(alert)
        return True

    def finish_turn(self, generation: int) -> None:
        if not self.is_current(generation):
            return
        phase = self.engine.state["phase"]
        self._set_phase(phase)
        if phase == "STANDBY":
            self.social_control.set_conversation_active(False)

    def begin_speaking(self, utterance: str, generation: int) -> bool:
        if not self.is_current(generation):
            return False
        self._touch()
        self.add_transcript("ARI", utterance)
        self.set_activity("SPEAKING")
        self.social_control.set_speaking(True)
        return True

    def end_speaking(self, generation: int) -> None:
        self._touch()
        self.social_control.set_speaking(False)
        if self.is_current(generation):
            self.set_activity("LISTENING")

    def show_utterance_face(self, action: dict[str, Any], seconds: float) -> None:
        """The face the conversation picked for one utterance, held while it is spoken."""
        expression = (action or {}).get("expression")
        if expression:
            self.social_control.set_expression(expression, seconds)

    def begin_listening(self) -> None:
        self.set_activity("LISTENING")

    def _phase_for_turn(self, turn: ConversationTurn) -> None:
        if "[PHASE] GOODBYE" in turn.diagnostics and turn.utterances:
            self._set_phase("GOODBYE")
        else:
            self._set_phase(self.engine.state["phase"])

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
        self.social_control.set_speaking(False)
        self.social_control.stop_movement()
        self.social_control.set_conversation_active(False)
        with self._engine_lock:
            self.engine.reset_to_standby()
        # The cancelled listen may have copied the old phase back meanwhile.
        self._set_phase("STANDBY")

    def move(self, action: str) -> bool:
        social = self.social_control.snapshot()
        if social.get("robot_connected") is False:
            self.social_control.stop_movement()
            return False
        if action != "stop" and self.actions.busy():
            # Driving by hand ends a running emote first.
            self.actions.stop()
        return self.social_control.move(action)

    def _touch(self) -> None:
        with self._state_lock:
            self._last_activity = time.monotonic()

    def _watch_idle(self) -> None:
        while not self._stop_watchdog.wait(1.0):
            with self._state_lock:
                quiet = time.monotonic() - self._last_activity
                busy = self._activity != "LISTENING" or self._phase == "STANDBY"
            if busy or self.actions.busy() or quiet < self.idle_seconds:
                continue
            self.console_print(
                f"[ARI] No one spoke for {self.idle_seconds:.0f} s; back to standby."
            )
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

    def _raise_alert(self, alert: dict[str, Any]) -> None:
        with self._state_lock:
            self._alert_count += 1
            self._alert = {
                "id": self._alert_count,
                "kind": alert.get("kind", "help"),
                "note": alert.get("note") or "",
                "place": alert.get("place"),
                "time": time.strftime("%H:%M"),
            }
        self.console_print(
            f"[ARI ALERT] {self._alert['kind']}: {self._alert['note']}"
            + (f" ({self._alert['place']})" if self._alert["place"] else "")
        )

    def ack_alert(self, speak: bool = True) -> bool:
        with self._state_lock:
            had_alert = self._alert is not None
            self._alert = None
        if had_alert and speak:
            self._in_background(lambda: self.speak(STAFF_ON_THE_WAY, expression="happy"))
        return had_alert

    def _in_background(self, target: Callable[[], None]) -> None:
        threading.Thread(target=target, name="ari-operator", daemon=True).start()

    def speak(
        self,
        text: str,
        gesture: str | None = None,
        expression: str | None = None,
    ) -> None:
        """Say one line outside the conversation turn (operator, emote, handshake)."""
        clean = " ".join(str(text).split())
        if not clean:
            return
        self._touch()
        self.add_transcript("ARI", clean)
        mic = self.microphone_input
        note_utterance = getattr(mic, "note_robot_utterance", None)
        set_robot_speaking = getattr(mic, "set_robot_speaking", None)
        if note_utterance is not None:
            note_utterance(clean)
        if set_robot_speaking is not None:
            set_robot_speaking(True)
        if expression:
            self.social_control.set_expression(expression, estimate_speech_seconds(clean) + 1.0)
        self.social_control.set_speaking(True)
        try:
            robot_gesture = gesture_for(gesture)
            if robot_gesture is not None:
                self.robot.coordinated_action(clean, gesture=robot_gesture)
            else:
                self.robot.say(clean)
        except Exception as error:
            self.console_print(f"[ARI SPEECH ERROR] {error}")
        finally:
            self.social_control.set_speaking(False)
            if set_robot_speaking is not None:
                set_robot_speaking(False)
            if note_utterance is not None:
                note_utterance(clean)
            self._touch()

    def operator_say(self, text: str) -> bool:
        clean = " ".join(str(text).split())[:400]
        if not clean:
            return False
        self._in_background(lambda: self.speak(clean))
        return True

    def greet(self) -> bool:
        """Start a conversation as if the visitor said hello."""
        with self._engine_lock:
            if self.engine.state.get("phase") != "STANDBY":
                return False
            turn = self.engine.handle_input("Hallo")
        if not turn.utterances:
            return False
        self._touch()
        self.add_transcript("", "Bediening: bezoeker begroet.")
        self.social_control.set_conversation_active(True)
        self._phase_for_turn(turn)
        actions = getattr(turn, "actions", ())

        def run() -> None:
            for index, utterance in enumerate(turn.utterances):
                action = actions[index] if index < len(actions) else {}
                self.speak(utterance, action.get("gesture"), action.get("expression"))

        self._in_background(run)
        return True

    def show_expression(self, name: str) -> bool:
        if name not in EXPRESSIONS:
            return False
        self._in_background(lambda: self.social_control.set_expression(name, 8.0))
        return True

    def play_gesture(self, name: str) -> bool:
        gesture = GESTURES.get(name)
        if gesture is None:
            return False

        def run() -> None:
            try:
                self.robot.play_gesture(gesture)
            except Exception as error:
                self.console_print(f"[ARI GESTURE] {name} failed: {error}")

        self._in_background(run)
        return True

    def play_emote(self, name: str | None = None, script: str | None = None) -> tuple[bool, str | None]:
        try:
            if script:
                steps = parse_emote(script)
                name = "Eigen emote"
            else:
                emotes = load_emotes()
                if name not in emotes:
                    return False, f"Geen emote met de naam '{name}'."
                steps = parse_emote(emotes[name])
        except ValueError as error:
            return False, f"Stap klopt niet: {error}"
        if not steps:
            return False, "Deze emote heeft geen stappen."
        if not self.actions.play_emote(name or "emote", steps):
            return False, f"ARI is nog bezig met {self.actions.current}."
        return True, None

    def handshake(self) -> tuple[bool, str | None]:
        if not self.actions.handshake():
            return False, f"ARI is nog bezig met {self.actions.current}."
        return True, None

    def stop_actions(self) -> None:
        """The big stop button: no emote, no driving, no music, no speech."""
        self.actions.stop()
        self.social_control.stop_movement()
        try:
            self.robot.stop()
        except Exception:
            pass

    def volume(self, percent: float | None = None) -> int | None:
        value = self.social_control.volume(percent)
        if value is not None:
            with self._state_lock:
                self._volume = value
        return value

    def close(self) -> None:
        self._stop_watchdog.set()
        self.actions.stop()

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            state = {
                "connected": self._robot_connected,
                "phase": self._phase,
                "activity": self._activity,
                "transcript": list(self._transcript),
                "generation": self._generation,
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
                "mode": self.mode,
                "action": self.actions.current,
                "emotes": list(load_emotes()),
                "capabilities": social.get("capabilities") or [],
                "sidecar_version": social.get("sidecar_version"),
                "music_playing": bool(social.get("music_playing")),
                "motion_active": social.get("motion_active"),
                "face_count": social.get("face_count", 0),
            }
        )
        with self._state_lock:
            state["alert"] = dict(self._alert) if self._alert else None
            state["volume"] = self._volume
        return state


def create_app(runtime: RuntimeCoordinator) -> Flask:
    app = Flask(__name__)

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

    def body() -> dict[str, Any]:
        data = request.get_json(force=True, silent=True)
        return data if isinstance(data, dict) else {}

    @app.post("/greet")
    def greet() -> Any:
        ok = runtime.greet()
        return jsonify({"ok": ok, "error": None if ok else "ARI is al in gesprek."})

    @app.post("/say")
    def say() -> Any:
        return jsonify({"ok": runtime.operator_say(str(body().get("text", "")))})

    @app.post("/expression")
    def expression() -> Any:
        return jsonify({"ok": runtime.show_expression(str(body().get("name", "")))})

    @app.post("/gesture")
    def gesture() -> Any:
        return jsonify({"ok": runtime.play_gesture(str(body().get("name", "")))})

    @app.post("/emote")
    def emote() -> Any:
        data = body()
        ok, error = runtime.play_emote(
            name=str(data.get("name") or ""),
            script=str(data.get("script") or ""),
        )
        return jsonify({"ok": ok, "error": error})

    @app.post("/handshake")
    def handshake() -> Any:
        ok, error = runtime.handshake()
        return jsonify({"ok": ok, "error": error})

    @app.post("/stop")
    def stop() -> Any:
        runtime.stop_actions()
        return jsonify({"ok": True})

    @app.post("/alert/ack")
    def ack_alert() -> Any:
        return jsonify({"ok": runtime.ack_alert(bool(body().get("speak", True)))})

    @app.route("/volume", methods=["GET", "POST"])
    def volume() -> Any:
        percent = None
        if request.method == "POST":
            try:
                percent = float(body().get("percent"))
            except (TypeError, ValueError):
                return jsonify({"ok": False, "percent": None})
        value = runtime.volume(percent)
        return jsonify({"ok": value is not None, "percent": value})

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
  </aside>
</main>
<script>
let moveTimer = null;
let heldAction = null;
let refreshInProgress = false;

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
    chip('connection', state.connected ? 'Robot verbonden' : 'Robot niet verbonden', state.connected);
    chip('phase', state.phase); chip('activity', state.activity);
    chip('attention', state.control_connected
      ? 'Aandacht: ' + state.attention_mode
      : 'Aandacht: niet verbonden', state.control_connected);
    chip('expression', 'Gezicht: ' + state.expression, state.expression !== 'unknown' ? true : null);
    renderTranscript(state.transcript || []);
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
