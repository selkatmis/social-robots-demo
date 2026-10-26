"""ARI as the guide in the VUmc entrance hall: way-finding and practical help.

Same interface as :class:`ari.responses.ConversationEngine`, so the mic, text
and operator adapters can run either one. Phases:

STANDBY -> HELP <-> ANYTHING_ELSE -> GOODBYE -> STANDBY

Python owns the safety wording, waking up and saying goodbye; the LLM writes
the answers from the hospital information in ``vumc_info.txt``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from ari import guardian_prompts as prompts
from ari.responses import (
    ConversationTurn,
    _clean_name,
    _clean_spoken_reply,
    _extract_json_object,
    _without_spoken_questions,
    contains_loose_word,
    is_explicit_stop_request,
    normalize_for_matching,
    normalized_words,
)
from ari.prompts import WAKE_GREETING_WORDS, WAKE_NAME_WORDS


PHASES = {"STANDBY", "HELP", "ANYTHING_ELSE", "GOODBYE"}
INTENTS = {"ANSWER", "HELPER", "ESCORT", "URGENT", "UNCLEAR", "DONE", "STOP"}
REPLY_EXPRESSIONS = {"happy", "neutral", "sad", "surprised", "thinking"}
REPLY_GESTURES = {"nod", "wave", "handshake"}
MAX_HISTORY_ITEMS = 14
MAX_UNCLEAR_IN_A_ROW = 2


@dataclass(frozen=True)
class GuardianTurn(ConversationTurn):
    """A turn plus a face/gesture per utterance and an optional staff alert."""

    actions: tuple[dict[str, str | None], ...] = ()
    alert: dict[str, Any] | None = None


def is_help_request(visitor_text: str) -> bool:
    """A clear request for help near the start of what someone says."""
    words = normalized_words(visitor_text)[:10]
    if not words:
        return False
    if words[0] in prompts.HELP_FIRST_WORDS:
        return True
    joined = f" {' '.join(words)} "
    if any(f" {opener} " in joined for opener in prompts.HELP_OPENERS):
        return True
    return "ik" in words[:3] and "kwijt" in words


def greeting_only(visitor_text: str) -> bool:
    """True for "hallo" or "goedemorgen ARI", false once a question follows."""
    words = normalized_words(visitor_text)
    if not words:
        return False
    rest = [
        word
        for word in words
        if not contains_loose_word([word], prompts_wake_words())
    ]
    return len(rest) <= 1


def prompts_wake_words() -> set[str]:
    return set(WAKE_GREETING_WORDS) | set(WAKE_NAME_WORDS) | {"robot", "daar", "dag"}


def starts_with_greeting(visitor_text: str) -> bool:
    words = normalized_words(visitor_text)
    if not words:
        return False
    if len(words) == 1 and contains_loose_word(words, set(WAKE_NAME_WORDS)):
        return True
    return contains_loose_word(words[:2], set(WAKE_GREETING_WORDS))


def looks_urgent(visitor_text: str) -> bool:
    normalized = f" {normalize_for_matching(visitor_text)} "
    return any(f" {hint}" in normalized for hint in prompts.URGENT_HINTS)


def _starts_with_any(visitor_text: str, openers: tuple[str, ...]) -> bool:
    normalized = normalize_for_matching(visitor_text)
    return any(
        normalized == opener or normalized.startswith(f"{opener} ")
        for opener in openers
    )


def wants_no_more_help(visitor_text: str) -> bool:
    words = normalized_words(visitor_text)
    return (
        bool(words)
        and len(words) <= 8
        and "?" not in visitor_text
        and _starts_with_any(visitor_text, prompts.NO_MORE_HELP_OPENERS)
    )


def wants_more_help(visitor_text: str) -> bool:
    words = normalized_words(visitor_text)
    return bool(words) and len(words) <= 3 and _starts_with_any(visitor_text, prompts.YES_OPENERS)


def spoken_now(now: datetime) -> str:
    """Date and time as context, e.g. 'dinsdag 27 oktober 2026, 10:42 uur'."""
    day = prompts.DAY_NAMES[now.weekday()]
    month = prompts.MONTH_NAMES[now.month - 1]
    if now.hour < 12:
        part = "'s ochtends"
    elif now.hour < 18:
        part = "'s middags"
    else:
        part = "'s avonds"
    return f"{day} {now.day} {month} {now.year}, {now:%H:%M} uur ({part})"


def _clean_short_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    return text[:limit] if text else None


def validate_help_response(
    raw_response: Any,
    visitor_text: str,
) -> tuple[dict[str, Any] | None, str | None]:
    parsed = _extract_json_object(raw_response)
    if parsed is None:
        return None, "invalid_json"
    intent = parsed.get("intent")
    if intent not in INTENTS:
        return None, "invalid_intent"
    raw_reply = parsed.get("reply")
    reply = _clean_spoken_reply(raw_reply)
    if raw_reply not in (None, "") and reply is None:
        return None, "invalid_reply"
    if intent in {"ANSWER", "UNCLEAR", "DONE"} and reply is None:
        return None, "missing_reply"
    expression = parsed.get("expression")
    gesture = parsed.get("gesture")
    return {
        "intent": intent,
        "reply": reply,
        "place": _clean_short_text(parsed.get("place"), 80),
        "name": _clean_name(parsed.get("name"), visitor_text),
        "note": _clean_short_text(parsed.get("note"), 160),
        "expression": expression if expression in REPLY_EXPRESSIONS else None,
        "gesture": gesture if gesture in REPLY_GESTURES else None,
    }, None


class GuardianEngine:
    """Phase-based helper for one visitor at a time, sharing ARI's adapters."""

    def __init__(
        self,
        llm: Any,
        *,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.llm = llm
        self.clock = clock
        self._state: dict[str, Any] = {"phase": "STANDBY"}
        self._clear_visitor()
        self._started = False
        self._shutdown = False

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    @property
    def is_done(self) -> bool:
        return self._shutdown

    def start(self) -> GuardianTurn:
        if self._started:
            raise RuntimeError("GuardianEngine.start() may only be called once")
        self._started = True
        return GuardianTurn(())

    def shutdown(self) -> GuardianTurn:
        self._clear_visitor()
        self._state["phase"] = "STANDBY"
        self._shutdown = True
        return GuardianTurn((), (), status="done")

    def reset_to_standby(self) -> GuardianTurn:
        self._clear_visitor()
        self._state["phase"] = "STANDBY"
        return GuardianTurn((), ("[OPERATOR] Session reset", "[PHASE] STANDBY"))

    def handle_input(self, visitor_text: str) -> GuardianTurn:
        if not self._started:
            raise RuntimeError("Call start() before handle_input()")
        if self.is_done:
            return GuardianTurn((), (), status="done")
        turn = _TurnBuilder()
        text = visitor_text.strip()
        phase = self._state["phase"]
        if phase == "STANDBY":
            self._handle_standby(text, turn)
        elif phase == "HELP":
            self._handle_help(text, turn)
        elif phase == "ANYTHING_ELSE":
            self._handle_anything_else(text, turn)
        else:
            raise RuntimeError(f"Unknown guardian phase: {phase}")
        return turn.build()

    def _set_phase(self, phase: str, turn: _TurnBuilder) -> None:
        if phase not in PHASES:
            raise ValueError(f"Unknown guardian phase: {phase}")
        self._state["phase"] = phase
        turn.diagnostics.append(f"[PHASE] {phase}")

    def _clear_visitor(self) -> None:
        self._state.update(
            {
                "conversation_history": [],
                "visitor_name": None,
                "last_place": None,
                "unclear_in_a_row": 0,
            }
        )

    def _choose(self, key: str, options: tuple[str, ...]) -> str:
        previous = self._state.get(key)
        choices = [option for option in options if option != previous] or list(options)
        choice = random.choice(choices)
        self._state[key] = choice
        return choice

    def _remember(self, role: str, content: str) -> None:
        history = self._state["conversation_history"]
        history.append({"role": role, "content": content})
        del history[:-MAX_HISTORY_ITEMS]

    def _say(
        self,
        turn: _TurnBuilder,
        text: str,
        *,
        expression: str | None = None,
        gesture: str | None = None,
    ) -> None:
        turn.say(text, expression, gesture)
        self._remember("ari", text)

    def _handle_standby(self, text: str, turn: _TurnBuilder) -> None:
        greeted = starts_with_greeting(text)
        if greeted and greeting_only(text):
            self._clear_visitor()
            turn.diagnostics.append("[STANDBY] Greeting recognized")
            self._set_phase("HELP", turn)
            self._say(
                turn,
                self._choose("last_greeting", prompts.GREETINGS),
                expression="happy",
                gesture="wave",
            )
            return
        if greeted or is_help_request(text):
            self._clear_visitor()
            turn.diagnostics.append("[STANDBY] Request recognized")
            self._set_phase("HELP", turn)
            self._say(
                turn,
                self._choose("last_introduction", prompts.SHORT_INTRODUCTIONS),
                expression="happy",
                gesture="wave",
            )
            self._handle_help(text, turn)

    def _handle_anything_else(self, text: str, turn: _TurnBuilder) -> None:
        if is_explicit_stop_request(text) or wants_no_more_help(text):
            self._remember("visitor", text)
            self._goodbye(turn)
            return
        self._set_phase("HELP", turn)
        if wants_more_help(text):
            self._remember("visitor", text)
            self._say(turn, prompts.MORE_HELP, expression="happy")
            return
        self._handle_help(text, turn)

    def _handle_help(self, text: str, turn: _TurnBuilder) -> None:
        if is_explicit_stop_request(text):
            self._remember("visitor", text)
            turn.diagnostics.append("[INTENT] STOP")
            self._goodbye(turn)
            return
        if not text:
            self._say(turn, prompts.EMPTY_INPUT, expression="neutral")
            return

        result = self._ask_llm(text, turn)
        self._remember("visitor", text)
        if result is None:
            if looks_urgent(text):
                result = {"intent": "URGENT", "reply": None, "note": text}
            else:
                self._say(turn, prompts.LLM_FALLBACK, expression="neutral")
                return

        intent = result["intent"]
        turn.diagnostics.append(f"[INTENT] {intent}")
        if result.get("name") and not self._state.get("visitor_name"):
            self._state["visitor_name"] = result["name"]
            turn.diagnostics.append("[NAME] NAME")
        if result.get("place"):
            self._state["last_place"] = result["place"]

        if intent == "URGENT":
            # Python owns the wording here; no generated diagnosis gets spoken.
            self._state["unclear_in_a_row"] = 0
            self._say(turn, prompts.URGENT_REPLY, expression="neutral")
            turn.raise_alert("urgent", result.get("note") or text, None)
            return
        if intent == "HELPER":
            self._state["unclear_in_a_row"] = 0
            self._say(
                turn,
                result.get("reply") or prompts.HELPER_FALLBACK,
                expression=result.get("expression") or "neutral",
            )
            turn.raise_alert("help", result.get("note") or text, result.get("place"))
            return
        if intent == "ESCORT":
            self._state["unclear_in_a_row"] = 0
            place = result.get("place") or self._state.get("last_place")
            self._say(
                turn,
                result.get("reply") or prompts.ESCORT_FALLBACK,
                expression=result.get("expression") or "happy",
            )
            turn.raise_alert("escort", result.get("note") or text, place)
            return
        if intent == "STOP":
            self._goodbye(turn)
            return
        if intent == "UNCLEAR":
            self._state["unclear_in_a_row"] += 1
            if self._state["unclear_in_a_row"] >= MAX_UNCLEAR_IN_A_ROW:
                self._state["unclear_in_a_row"] = 0
                self._say(turn, prompts.OFFER_HELPER, expression="neutral")
            else:
                self._say(turn, result["reply"], expression="neutral")
            return

        self._state["unclear_in_a_row"] = 0
        reply = result["reply"]
        if intent == "DONE":
            # Python asks the follow-up question itself, so drop any from the model.
            reply = _without_spoken_questions(reply)
        if reply:
            self._say(
                turn,
                reply,
                expression=result.get("expression") or "happy",
                gesture=result.get("gesture"),
            )
        if intent == "DONE":
            self._set_phase("ANYTHING_ELSE", turn)
            self._say(turn, self._choose("last_anything_else", prompts.ANYTHING_ELSE), expression="happy")

    def _goodbye(self, turn: _TurnBuilder) -> None:
        self._set_phase("GOODBYE", turn)
        name = self._state.get("visitor_name")
        if name:
            farewell = self._choose("last_named_goodbye", prompts.NAMED_GOODBYES).format(name=name)
        else:
            farewell = self._choose("last_goodbye", prompts.GOODBYES)
        turn.say(farewell, "happy", "wave")
        self._clear_visitor()
        self._set_phase("STANDBY", turn)

    def _context(self, text: str) -> str:
        history = self._state["conversation_history"]
        lines = [
            f"{'Bezoeker' if item['role'] == 'visitor' else 'ARI'}: {item['content']}"
            for item in history
        ]
        return (
            f"NU: {spoken_now(self.clock())}\n"
            f"PLAATS: {prompts.PLACE}\n"
            f"NAAM VAN DE BEZOEKER: {self._state.get('visitor_name') or 'onbekend'}\n"
            f"LAATST BESPROKEN PLEK: {self._state.get('last_place') or 'geen'}\n\n"
            f"GESPREK TOT NU TOE:\n{chr(10).join(lines) or '(nog niets gezegd)'}\n\n"
            f"NIEUWSTE UITSPRAAK VAN DE BEZOEKER:\n{text}"
        )

    def _ask_llm(self, text: str, turn: _TurnBuilder) -> dict[str, Any] | None:
        prompt = f"{prompts.HELP_INSTRUCTIONS}\n\n{self._context(text)}"
        for attempt, extra in enumerate(("", f"\n\n{prompts.FORMAT_RETRY}")):
            try:
                raw = self.llm.prompt(
                    prompt + extra,
                    max_tokens=220,
                    temperature=0.3 if attempt == 0 else 0.0,
                )
            except Exception:
                turn.diagnostics.append("[LLM] Guardian response unavailable")
                return None
            result, reason = validate_help_response(raw, text)
            if result is not None:
                if attempt:
                    turn.diagnostics.append("[LLM] Guardian response recovered")
                return result
            turn.diagnostics.append(f"[LLM] Guardian validation failed: {reason}")
        return None


class _TurnBuilder:
    def __init__(self) -> None:
        self.utterances: list[str] = []
        self.actions: list[dict[str, str | None]] = []
        self.diagnostics: list[str] = []
        self.alert: dict[str, Any] | None = None

    def say(self, text: str, expression: str | None, gesture: str | None) -> None:
        self.utterances.append(text)
        self.actions.append({"expression": expression, "gesture": gesture})

    def raise_alert(self, kind: str, note: str, place: str | None) -> None:
        self.alert = {"kind": kind, "note": note, "place": place}
        self.diagnostics.append(f"[ALERT] {kind}: {note}" + (f" ({place})" if place else ""))

    def build(self) -> GuardianTurn:
        return GuardianTurn(
            utterances=tuple(self.utterances),
            diagnostics=tuple(self.diagnostics),
            actions=tuple(self.actions),
            alert=self.alert,
        )


__all__ = [
    "GuardianEngine",
    "GuardianTurn",
    "is_help_request",
    "looks_urgent",
    "spoken_now",
    "validate_help_response",
]
