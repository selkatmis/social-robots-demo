"""Shared, hardware-independent conversation engine for ARI.

The v0.4 visitor experience is the default.  The original v0.3 conversation
path remains available to regression callers with ``activities_enabled=False``.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from typing import Any

from ari import prompts
from ari.difference_game import DifferenceGame, INVALID, UNCERTAIN


VALID_PHASES = {
    "STANDBY",
    "INVITATION",
    "INTRODUCTION",
    "ASK_NAME",
    "DAILY_CHECKIN",
    "ACTIVITY_CHOICE",
    "CONVERSATION",
    "GEZELLIG_BABBELEN",
    "ZOEK_DE_VERSCHILLEN",
    "CONTINUE_CHECK",
    "GOODBYE",
}

CONVERSATION_INTENTS = {
    "CONTINUE",
    "TOPIC_CHANGE",
    "UNCLEAR",
    "POSSIBLE_STOP",
    "STOP",
    "URGENT",
}

MAX_REPLY_CHARACTERS = 600
MAX_INVITATION_UNCLEAR = 2
MAX_STOP_CONFIRMATION_UNCLEAR = 2
MAX_CONTINUE_CHECK_UNCLEAR = 2
MAX_ACTIVITY_CHOICE_UNCLEAR = 2
MIN_TOPIC_QUESTIONS = 3
MAX_TOPIC_QUESTIONS = 4

TABLET_MAIN_PAGE = "Main"
TABLET_GAME_PAGE = "Zoek_de_verschillen"


def make_initial_state() -> dict[str, Any]:
    """Create state for one long-running process and no active visitor."""
    return {
        "phase": "STANDBY",
        "conversation_history": [],
        "visitor_details": {},
        "visitor_name": None,
        "current_topic": None,
        "conversation_context": None,
        "pending_stop_confirmation": None,
        "pending_clarification": None,
        "pending_name_confirmation": None,
        "pending_initial_message": None,
        "session_turn_count": 0,
        "invitation_attempts": 0,
        "continue_check_attempts": 0,
        "topic_question_count": 0,
        "used_topic_categories": [],
        "introduction_given": False,
        "daily_checkin_step": None,
        "activity_choice_context": None,
        "activity_choice_attempts": 0,
        "active_activity": None,
        "game_found_count": 0,
        "game_target_count": 3,
        "game_available_count": 0,
        "game_target_reached": False,
        "game_all_found": False,
        "tablet_page": None,
    }


@dataclass(frozen=True)
class ConversationTurn:
    """Ordered output from one engine operation."""

    utterances: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()
    status: str = "waiting_for_input"
    tablet_page: str | None = None
    greeting_wave: bool = False

    @property
    def awaiting_input(self) -> bool:
        return self.status == "waiting_for_input"

    @property
    def is_done(self) -> bool:
        return self.status == "done"


def choose_nonrepeating_response(
    state: dict[str, Any],
    state_key: str,
    responses: tuple[str, ...],
) -> str:
    """Choose prepared speech without immediately repeating the last variant."""
    previous = state.get(state_key)
    choices = tuple(response for response in responses if response != previous)
    response = random.choice(choices or responses)
    state[state_key] = response
    return response


def normalized_words(text: str) -> list[str]:
    """Return case-folded Unicode words for conservative wake recognition."""
    return re.findall(r"[^\W\d_]+", text.casefold(), re.UNICODE)


def matches_stretched_word(spoken_word: str, expected_word: str) -> bool:
    """Match expressive spellings such as ``hallooo`` and ``hoiii``."""
    pattern = "".join(f"{re.escape(character)}+" for character in expected_word)
    return re.fullmatch(pattern, spoken_word) is not None


def contains_loose_word(words: list[str], expected_words: set[str]) -> bool:
    return any(
        spoken == expected or matches_stretched_word(spoken, expected)
        for spoken in words
        for expected in expected_words
    )


def is_wake_greeting(visitor_text: str) -> bool:
    """Recognize a deliberate greeting without waking on arbitrary speech."""
    words = normalized_words(visitor_text)
    if not words:
        return False

    has_name = contains_loose_word(words, prompts.WAKE_NAME_WORDS)
    name_only = len(words) == 1 and has_name
    # A greeting in the first two words is likely addressed to ARI. A greeting
    # embedded later in background speech ("Piet zei gisteren hallo") is not.
    opening_greeting = contains_loose_word(
        words[:2],
        prompts.WAKE_GREETING_WORDS,
    )
    return name_only or opening_greeting


def normalize_for_matching(text: str) -> str:
    return " ".join(normalized_words(text))


def classify_simple_binary_response(visitor_text: str) -> str | None:
    """Handle only unambiguous short yes/no replies without an LLM call."""
    normalized = normalize_for_matching(visitor_text)
    if normalized in prompts.BINARY_YES_RESPONSES:
        return "YES"
    if normalized in prompts.BINARY_NO_RESPONSES:
        return "NO"
    return None


def is_explicit_stop_request(visitor_text: str) -> bool:
    """Catch only unambiguous stop requests before calling the LLM."""
    normalized = normalize_for_matching(visitor_text)
    if normalized in {
        "stop",
        "stoppen",
        "doei",
        "tot ziens",
        "ik ben klaar",
        "ik ga weer",
    }:
        return True

    patterns = (
        r"\bik wil (?:nu )?(?:graag )?stoppen\b",
        r"\bik wil niet meer (?:praten|doorgaan)\b",
        r"\blaten we (?:maar )?stoppen\b",
        r"\b(?:stop|stoppen) met (?:praten|dit gesprek|het gesprek)\b",
        r"\bik (?:moet|ga) (?:nu )?(?:weg|naar huis)\b",
        r"\bik wil (?:dit |het )?gesprek (?:beeindigen|beëindigen|afsluiten)\b",
        r"\b(?:het|dit) is (?:wel )?genoeg geweest\b",
        r"\bvoor mij is het (?:wel )?genoeg\b",
        r"\bik ben (?:nu |wel )?klaar\b",
        r"\bik ga (?:maar )?weer\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def obvious_activity_request(visitor_text: str) -> str | None:
    """Recognize only explicit activity requests before consulting the LLM."""
    normalized = normalize_for_matching(visitor_text)
    if re.search(r"\b(?:niet|geen|nee)\b", normalized):
        # Let the contextual classifier resolve negation and mixed choices.
        return None
    game_patterns = (
        r"\bzoek de verschillen\b",
        r"\bverschillen (?:zoeken|spel(?:en)?)\b",
        r"\b(?:het|dat) spel(?:letje)?\b",
        r"\bspel(?:letje)? spelen\b",
        r"\bverder zoeken\b",
    )
    babbel_patterns = (
        r"\b(?:gezellig )?(?:babbelen|kletsen)\b",
        r"\b(?:gewoon|liever|even) praten\b",
        r"\b(?:ik wil|laten we|we kunnen)(?: graag)? praten\b",
        r"\been praatje\b",
    )
    if any(re.search(pattern, normalized) for pattern in game_patterns):
        return "GAME"
    if any(re.search(pattern, normalized) for pattern in babbel_patterns):
        return "BABBEL"
    return None


def _extract_json_object(raw_response: Any) -> dict[str, Any] | None:
    """Parse one model JSON object and reject prose or malformed output."""
    if not isinstance(raw_response, str):
        return None

    text = raw_response.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
            if text.casefold().startswith("json\n"):
                text = text[5:].strip()

    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _clean_spoken_reply(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    reply = " ".join(value.split()).strip()
    if not reply or len(reply) > MAX_REPLY_CHARACTERS:
        return None
    # These markers add no value in speech and occasionally slip through even
    # with a strict prompt.
    reply = reply.replace("**", "").replace("__", "").replace("`", "")
    if re.search(r"\b(?:je|jij|jou|jouw|jullie)\b", reply, re.IGNORECASE):
        # Keep the spoken register respectful even if a generated reply
        # accidentally ignores the formal-address instructions.
        return None
    if reply.count("?") > 1:
        return None
    return reply.strip() or None


def _clean_topic(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    topic = " ".join(value.split()).strip()
    if not topic or len(topic) > 80:
        return None
    return topic


def _validate_conversation_response(
    raw_response: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one conversation result without exposing private content."""
    parsed = _extract_json_object(raw_response)
    if parsed is None:
        return None, "invalid_json"

    allowed_fields = {"intent", "reply", "topic", "cycle_complete"}
    if set(parsed) - allowed_fields:
        return None, "unexpected_fields"

    intent = parsed.get("intent")
    if not isinstance(intent, str) or intent not in CONVERSATION_INTENTS:
        return None, "invalid_intent"

    raw_reply = parsed.get("reply")
    reply = _clean_spoken_reply(raw_reply)
    if raw_reply is not None and reply is None:
        return None, "invalid_reply"
    if intent in {"CONTINUE", "TOPIC_CHANGE", "UNCLEAR"} and reply is None:
        return None, "missing_reply"
    if reply is not None and _solicits_visitor_name(reply):
        return None, "repeated_name_solicitation"

    raw_topic = parsed.get("topic")
    topic = _clean_topic(raw_topic)
    if raw_topic is not None and topic is None:
        return None, "invalid_topic"
    if intent == "TOPIC_CHANGE" and topic is None:
        return None, "missing_topic"

    cycle_complete = parsed.get("cycle_complete", False)
    if not isinstance(cycle_complete, bool):
        return None, "invalid_cycle_complete"

    if intent == "URGENT" and reply is None:
        reply = prompts.URGENT_FALLBACK
    return {
        "intent": intent,
        "reply": reply,
        "topic": topic,
        "cycle_complete": cycle_complete,
    }, None


def _solicits_visitor_name(reply: str) -> bool:
    """Reject a second name request after the dedicated name phase."""
    normalized = normalize_for_matching(reply)
    return any(
        phrase in normalized
        for phrase in (
            "wat is uw naam",
            "hoe heet u",
            "hoe mag ik u noemen",
            "uw naam vertellen",
            "uw naam delen",
            "naam met mij delen",
        )
    )


def _contains_spoken_question(reply: str) -> bool:
    """A question counts only when a question is actually spoken."""
    return "?" in reply


def _without_spoken_questions(reply: str) -> str | None:
    """Keep useful statements while removing over-budget proactive questions."""
    sentences = re.split(r"(?<=[.!?])\s+", reply)
    statements = [sentence for sentence in sentences if "?" not in sentence]
    cleaned = " ".join(statements).strip()
    return cleaned or None


def _clean_name(value: Any, visitor_text: str) -> str | None:
    """Accept a short model-extracted name only when it occurs in the input."""
    if not isinstance(value, str):
        return None
    name = " ".join(value.split()).strip(" .,!?:;\"'")
    if not name or len(name) > 60 or len(name.split()) > 4:
        return None
    if not all(character.isalpha() or character in " -'’" for character in name):
        return None

    name_words = normalized_words(name)
    input_words = normalized_words(visitor_text)
    appears_verbatim = any(
        input_words[index : index + len(name_words)] == name_words
        for index in range(len(input_words) - len(name_words) + 1)
    )
    normalized_name = " ".join(name_words)
    if not normalized_name or not appears_verbatim:
        return None
    if normalized_name in prompts.BINARY_YES_RESPONSES | prompts.BINARY_NO_RESPONSES:
        return None
    return _capitalize_name(name.split())


def _capitalize_name(name_words: list[str]) -> str:
    """Format a provided name naturally without changing its words."""
    particles = {"de", "den", "der", "van", "ter", "ten"}
    formatted: list[str] = []
    for index, word in enumerate(name_words):
        lowered = word.casefold()
        formatted.append(lowered if index and lowered in particles else word.title())
    return " ".join(formatted)


class ConversationEngine:
    """Small phase-based conversation brain shared by all ARI adapters."""

    def __init__(
        self,
        llm: Any,
        *,
        state: dict[str, Any] | None = None,
        activities_enabled: bool = True,
        difference_game: DifferenceGame | None = None,
    ) -> None:
        self.llm = llm
        self._state = make_initial_state() if state is None else state
        self._activities_enabled = bool(activities_enabled)
        self.difference_game = difference_game or DifferenceGame()
        self._pending_tablet_page: str | None = None
        self._pending_greeting_wave = False
        self._started = False
        self._entry_pending = True
        self._shutdown = False

    @property
    def state(self) -> dict[str, Any]:
        """Expose state for diagnostics and tests, not adapter decisions."""
        return self._state

    @property
    def is_done(self) -> bool:
        """Normal goodbyes return to STANDBY; only shutdown ends the engine."""
        return self._shutdown

    def start(self) -> ConversationTurn:
        if self._started:
            raise RuntimeError("ConversationEngine.start() may only be called once")
        self._started = True
        utterances: list[str] = []
        diagnostics: list[str] = []
        self._entry_pending = True
        self._settle(utterances, diagnostics)
        return self._turn(utterances, diagnostics)

    def handle_input(self, visitor_text: str) -> ConversationTurn:
        if not self._started:
            raise RuntimeError("Call start() before handle_input()")
        if self.is_done:
            return self._turn((), ())
        if self._entry_pending:
            raise RuntimeError("The current conversation phase has not been entered")

        utterances: list[str] = []
        diagnostics: list[str] = []
        text = visitor_text.strip()
        phase = self._state["phase"]

        if phase == "STANDBY":
            self._handle_standby(text, utterances, diagnostics)
        elif phase == "INVITATION":
            self._handle_invitation(text, utterances, diagnostics)
        elif phase == "INTRODUCTION":
            raise RuntimeError("INTRODUCTION does not accept visitor input")
        elif phase == "ASK_NAME":
            self._handle_ask_name(text, utterances, diagnostics)
        elif phase == "DAILY_CHECKIN":
            self._handle_daily_checkin(text, utterances, diagnostics)
        elif phase == "ACTIVITY_CHOICE":
            self._handle_activity_choice(text, utterances, diagnostics)
        elif phase == "CONVERSATION":
            self._handle_conversation(text, utterances, diagnostics)
        elif phase == "GEZELLIG_BABBELEN":
            self._handle_babbel(text, utterances, diagnostics)
        elif phase == "ZOEK_DE_VERSCHILLEN":
            self._handle_difference_game(text, utterances, diagnostics)
        elif phase == "CONTINUE_CHECK":
            self._handle_continue_check(text, utterances, diagnostics)
        elif phase == "GOODBYE":
            raise RuntimeError("GOODBYE does not accept visitor input")
        else:
            raise RuntimeError(f"Unknown conversation phase: {phase}")

        self._settle(utterances, diagnostics)
        return self._turn(utterances, diagnostics)

    def shutdown(self) -> ConversationTurn:
        """End the local engine explicitly; visitor goodbyes do not call this."""
        self._clear_visitor_session()
        self._state["phase"] = "STANDBY"
        self._entry_pending = False
        self._request_tablet_page(TABLET_MAIN_PAGE)
        self._shutdown = True
        return self._turn((), ())

    def reset_to_standby(self) -> ConversationTurn:
        """Forget the current visitor without stopping the long-running engine."""
        self._clear_visitor_session()
        self._state["phase"] = "STANDBY"
        self._entry_pending = False
        self._request_tablet_page(TABLET_MAIN_PAGE)
        return self._turn(
            (),
            ("[OPERATOR] Session reset", "[PHASE] STANDBY"),
        )

    def handle_tablet_navigation_failure(self) -> ConversationTurn:
        """Leave a just-entered game safely when its page could not be shown."""
        if self._state.get("phase") != "ZOEK_DE_VERSCHILLEN":
            return self._turn((), ())
        utterances = [
            "Ik kan het spel nu niet veilig op het scherm tonen. "
            "We kunnen wel gezellig babbelen."
        ]
        diagnostics = ["[TABLET] GAME_PAGE_UNAVAILABLE"]
        history = self._state.get("conversation_history", [])
        if history and history[-1].get("role") == "ari" and (
            history[-1].get("content") in prompts.GAME_INTRODUCTIONS
            or str(history[-1].get("content", "")).startswith(
                "Prima, dan zoeken we verder"
            )
        ):
            history.pop()
        self._state.pop("game_has_started", None)
        self._state["active_activity"] = None
        self._state["activity_choice_context"] = "game_unavailable"
        self._append_history("ari", utterances[0])
        self._set_phase("ACTIVITY_CHOICE", diagnostics)
        self._settle(utterances, diagnostics)
        return self._turn(utterances, diagnostics)

    def _turn(self, utterances: Any, diagnostics: Any) -> ConversationTurn:
        turn = ConversationTurn(
            utterances=tuple(utterances),
            diagnostics=tuple(diagnostics),
            status="done" if self.is_done else "waiting_for_input",
            tablet_page=self._pending_tablet_page,
            greeting_wave=self._pending_greeting_wave,
        )
        self._pending_tablet_page = None
        self._pending_greeting_wave = False
        return turn

    def _set_phase(self, phase: str, diagnostics: list[str]) -> None:
        if phase not in VALID_PHASES:
            raise ValueError(f"Unknown conversation phase: {phase}")
        self._state["phase"] = phase
        self._entry_pending = True
        diagnostics.append(f"[PHASE] {phase}")
        if self._activities_enabled:
            keep_game_visible = phase == "ZOEK_DE_VERSCHILLEN" or (
                phase == "ACTIVITY_CHOICE"
                and self._state.get("activity_choice_context") == "after_game"
            )
            desired_page = (
                TABLET_GAME_PAGE
                if keep_game_visible and self.difference_game.ready
                else TABLET_MAIN_PAGE
            )
            self._request_tablet_page(desired_page, diagnostics)

    def _request_tablet_page(
        self,
        page: str,
        diagnostics: list[str] | None = None,
    ) -> None:
        if not self._activities_enabled:
            return
        if self._state.get("tablet_page") == page:
            return
        self._state["tablet_page"] = page
        self._pending_tablet_page = page
        if diagnostics is not None:
            diagnostics.append(f"[TABLET] {page}")

    def _settle(
        self,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        while self._entry_pending and not self.is_done:
            self._entry_pending = False
            phase_before = self._state["phase"]
            self._enter_current_phase(utterances, diagnostics)
            if self._state["phase"] == phase_before:
                break

    def _enter_current_phase(
        self,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        phase = self._state["phase"]
        if phase == "STANDBY":
            # Deliberately silent: both adapters immediately keep listening.
            self._request_tablet_page(TABLET_MAIN_PAGE, diagnostics)
            return
        if phase == "INTRODUCTION":
            self._pending_greeting_wave = True
            introduction = choose_nonrepeating_response(
                self._state,
                "last_introduction_response",
                prompts.INTRODUCTION_RESPONSES,
            )
            self._state["introduction_given"] = True
            utterances.append(introduction)
            self._append_history("ari", introduction)
            requested_activity = self._state.pop("pending_requested_activity", None)
            if requested_activity is not None:
                diagnostics.append(f"[ACTIVITY] {requested_activity}")
                self._start_activity(requested_activity, diagnostics)
                return
            self._set_phase("ASK_NAME", diagnostics)
            return
        if phase == "INVITATION":
            invitation = choose_nonrepeating_response(
                self._state,
                "last_invitation_response",
                prompts.INVITATION_RESPONSES,
            )
            self._state["active_invitation"] = invitation
            utterances.append(invitation)
            return
        if phase == "ASK_NAME":
            question = choose_nonrepeating_response(
                self._state,
                "last_name_question",
                prompts.ASK_NAME_QUESTIONS,
            )
            self._state["active_name_question"] = question
            utterances.append(question)
            return
        if phase == "DAILY_CHECKIN":
            self._enter_daily_checkin(utterances)
            return
        if phase == "ACTIVITY_CHOICE":
            self._enter_activity_choice(utterances)
            return
        if phase == "CONVERSATION":
            if self._state.pop("skip_conversation_opening", False):
                return
            if not self._state.get("current_topic"):
                self._start_topic(self._choose_new_topic(), diagnostics)
            opening = self._generate_topic_opening(diagnostics)
            if opening is None:
                topic = self._state["current_topic"]
                opening = f"Laten we het over {topic} hebben. Wat komt er als eerste bij u op?"
            utterances.append(opening)
            self._append_history("ari", opening)
            if _contains_spoken_question(opening):
                self._state["topic_question_count"] += 1
            return
        if phase == "GEZELLIG_BABBELEN":
            self._enter_babbel(utterances, diagnostics)
            return
        if phase == "ZOEK_DE_VERSCHILLEN":
            self._enter_difference_game(utterances, diagnostics)
            return
        if phase == "CONTINUE_CHECK":
            question = choose_nonrepeating_response(
                self._state,
                "last_continue_check_question",
                prompts.CONTINUE_CHECK_QUESTIONS,
            )
            self._state["active_continue_check"] = question
            utterances.append(question)
            self._append_history("ari", question)
            return
        if phase == "GOODBYE":
            self._enter_goodbye(utterances, diagnostics)
            return
        raise RuntimeError(f"Unknown conversation phase: {phase}")

    def _enter_goodbye(
        self,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        declined = self._state.get("pending_goodbye_kind") == "decline"
        visitor_name = self._state.get("visitor_name")
        if visitor_name and not declined:
            template = choose_nonrepeating_response(
                self._state,
                "last_named_goodbye_response",
                prompts.NAMED_GOODBYE_RESPONSES,
            )
            farewell = template.format(name=visitor_name)
        else:
            responses = (
                prompts.DECLINE_RESPONSES if declined else prompts.GOODBYE_RESPONSES
            )
            state_key = "last_decline_response" if declined else "last_goodbye_response"
            farewell = choose_nonrepeating_response(self._state, state_key, responses)
        utterances.append(farewell)

        # Build the farewell first, then remove every visitor-specific value.
        self._clear_visitor_session()
        self._set_phase("STANDBY", diagnostics)

    def _clear_visitor_session(self) -> None:
        self._pending_greeting_wave = False
        self._state["conversation_history"] = []
        self._state["visitor_details"] = {}
        self._state["visitor_name"] = None
        self._state["current_topic"] = None
        self._state["conversation_context"] = None
        self._state["pending_stop_confirmation"] = None
        self._state["pending_clarification"] = None
        self._state["pending_name_confirmation"] = None
        self._state["pending_initial_message"] = None
        self._state["session_turn_count"] = 0
        self._state["invitation_attempts"] = 0
        self._state["continue_check_attempts"] = 0
        self._state["topic_question_count"] = 0
        self._state["used_topic_categories"] = []
        self._state["introduction_given"] = False
        self._state["daily_checkin_step"] = None
        self._state["activity_choice_context"] = None
        self._state["activity_choice_attempts"] = 0
        self._state["active_activity"] = None
        self.difference_game.reset()
        self._sync_game_state()
        self._state.pop("active_invitation", None)
        self._state.pop("active_name_question", None)
        self._state.pop("ask_name_unclear_count", None)
        self._state.pop("active_continue_check", None)
        self._state.pop("pending_goodbye_kind", None)
        self._state.pop("skip_conversation_opening", None)
        self._state.pop("babbel_transition", None)
        self._state.pop("continue_same_topic", None)
        self._state.pop("game_has_started", None)
        self._state.pop("game_target_offer_done", None)
        self._state.pop("pending_requested_activity", None)
        self._state.pop("requested_babbel_topic", None)
        self._state.pop("active_activity_question", None)

    def _handle_standby(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        del utterances
        if not is_wake_greeting(visitor_text):
            return
        self._clear_visitor_session()
        if self._activities_enabled:
            requested_activity = obvious_activity_request(visitor_text)
            if requested_activity is not None:
                self._state["pending_requested_activity"] = requested_activity
        diagnostics.append("[STANDBY] Greeting recognized")
        self._set_phase(
            "INTRODUCTION" if self._activities_enabled else "INVITATION",
            diagnostics,
        )

    def _handle_invitation(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        if is_explicit_stop_request(visitor_text):
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("decline", diagnostics)
            return

        if not visitor_text:
            utterances.append(prompts.INVITATION_CLARIFICATION)
            return

        binary = classify_simple_binary_response(visitor_text)
        if binary == "YES":
            decision = "ACCEPT"
        elif binary == "NO":
            decision = "DECLINE"
        else:
            decision = self._classify_invitation(visitor_text, diagnostics)
        if decision == "ACCEPT":
            self._set_phase("ASK_NAME", diagnostics)
            return
        if decision == "ACCEPT_WITH_CONTENT":
            # Preserve meaningful content attached to the acceptance instead
            # of replacing it with a generic opening question.
            self._state["pending_initial_message"] = visitor_text
            self._set_phase("ASK_NAME", diagnostics)
            return
        if decision == "DECLINE":
            self._begin_goodbye("decline", diagnostics)
            return
        if decision == "URGENT":
            diagnostics.append("[INTENT] URGENT")
            utterances.append(prompts.URGENT_FALLBACK)
            self._clear_visitor_session()
            self._set_phase("STANDBY", diagnostics)
            return

        self._state["invitation_attempts"] += 1
        diagnostics.append("[INTENT] UNCLEAR")
        if self._state["invitation_attempts"] >= MAX_INVITATION_UNCLEAR:
            self._begin_goodbye("decline", diagnostics)
            return
        utterances.append(prompts.INVITATION_CLARIFICATION)

    def _classify_invitation(
        self,
        visitor_text: str,
        diagnostics: list[str],
    ) -> str:
        try:
            result = self.llm.prompt(
                f"{prompts.INVITATION_CLASSIFIER}\n\n"
                f"Vraag van ARI: {self._state.get('active_invitation')!r}\n"
                f"Bezoeker zegt: {visitor_text!r}",
                max_tokens=10,
                temperature=0.0,
            )
        except Exception:
            diagnostics.append("[LLM] Invitation classifier unavailable")
            return "UNCLEAR"

        label = result.strip().upper() if isinstance(result, str) else "UNCLEAR"
        valid_labels = {
            "ACCEPT",
            "ACCEPT_WITH_CONTENT",
            "DECLINE",
            "URGENT",
            "UNCLEAR",
        }
        return label if label in valid_labels else "UNCLEAR"

    def _handle_ask_name(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        if is_explicit_stop_request(visitor_text):
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("decline", diagnostics)
            return

        if self._activities_enabled:
            requested_activity = obvious_activity_request(visitor_text)
            if requested_activity is not None:
                names_self = re.search(
                    r"\b(?:ik heet|mijn naam is|noem mij|noemt u mij)\b",
                    normalize_for_matching(visitor_text),
                )
                if names_self:
                    self._state["pending_requested_activity"] = requested_activity
                else:
                    diagnostics.append(f"[ACTIVITY] {requested_activity}")
                    utterances.append("Dat is prima; uw naam hoeft u niet te noemen.")
                    self._state["pending_name_confirmation"] = None
                    self._state.pop("ask_name_unclear_count", None)
                    self._start_activity(requested_activity, diagnostics)
                    return

        pending_name = self._state.get("pending_name_confirmation")
        if pending_name:
            binary = classify_simple_binary_response(visitor_text)
            if binary == "YES":
                self._state["visitor_name"] = pending_name
                self._state["pending_name_confirmation"] = None
                diagnostics.append("[NAME] CONFIRMED")
                acknowledgement_template = choose_nonrepeating_response(
                    self._state,
                    "last_name_acknowledgement",
                    prompts.NAME_ACKNOWLEDGEMENTS,
                )
                utterances.append(acknowledgement_template.format(name=pending_name))
                self._finish_name_phase(utterances, diagnostics)
                return
            if binary == "NO":
                self._state["pending_name_confirmation"] = None
                diagnostics.append("[NAME] NOT_CONFIRMED")
                utterances.append(prompts.ASK_NAME_WITHOUT_NAME)
                self._finish_name_phase(utterances, diagnostics)
                return
            # A correction such as "Ik heet Semi" is classified normally below.
            self._state["pending_name_confirmation"] = None

        parsed = self._classify_name_response(visitor_text, diagnostics)
        intent = parsed.get("intent") if parsed else "UNCLEAR"
        if intent == "URGENT":
            diagnostics.append("[INTENT] URGENT")
            utterances.append(prompts.URGENT_FALLBACK)
            self._clear_visitor_session()
            self._set_phase("STANDBY", diagnostics)
            return
        if intent == "STOP":
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return

        if intent == "NAME":
            name = _clean_name(parsed.get("name"), visitor_text)
            if name:
                self._state["visitor_name"] = name
                diagnostics.append("[NAME] NAME")
                acknowledgement_template = choose_nonrepeating_response(
                    self._state,
                    "last_name_acknowledgement",
                    prompts.NAME_ACKNOWLEDGEMENTS,
                )
                acknowledgement = acknowledgement_template.format(name=name)
                utterances.append(acknowledgement)
                self._finish_name_phase(utterances, diagnostics)
                return

        if intent == "CONFIRM_NAME":
            candidate = _clean_name(parsed.get("name"), visitor_text)
            if candidate:
                clarification = prompts.NAME_CONFIRMATION.format(name=candidate)
                self._state["pending_name_confirmation"] = candidate
                self._state["active_name_question"] = clarification
                diagnostics.append("[NAME] CONFIRMATION")
                utterances.append(clarification)
                return

        if intent == "REFUSE":
            diagnostics.append("[NAME] REFUSE")
            utterances.append(
                choose_nonrepeating_response(
                    self._state,
                    "last_name_refusal_response",
                    prompts.NAME_REFUSAL_RESPONSES,
                )
            )
            self._finish_name_phase(utterances, diagnostics)
            return

        # As in the QT tutor, one unclear answer receives one focused retry.
        # A second unclear answer continues without a name and without falsely
        # labelling the visitor's response as a refusal.
        diagnostics.append("[NAME] UNCLEAR")
        unclear_count = self._state.get("ask_name_unclear_count", 0) + 1
        self._state["ask_name_unclear_count"] = unclear_count
        if unclear_count == 1:
            self._state["active_name_question"] = prompts.ASK_NAME_CLARIFICATION
            utterances.append(prompts.ASK_NAME_CLARIFICATION)
            return

        utterances.append(prompts.ASK_NAME_WITHOUT_NAME)
        self._finish_name_phase(utterances, diagnostics)

    def _classify_name_response(
        self,
        visitor_text: str,
        diagnostics: list[str],
    ) -> dict[str, Any] | None:
        if not visitor_text:
            return {"intent": "UNCLEAR", "name": None}
        try:
            result = self.llm.prompt(
                f"{prompts.ASK_NAME_INSTRUCTIONS}\n\n"
                f"Vraag van ARI: {self._state.get('active_name_question')!r}\n"
                f"Bezoeker zegt: {visitor_text!r}",
                max_tokens=50,
                temperature=0.0,
            )
        except Exception:
            diagnostics.append("[LLM] Name classifier unavailable")
            return None

        parsed = _extract_json_object(result)
        if parsed is None or parsed.get("intent") not in {
            "NAME",
            "CONFIRM_NAME",
            "REFUSE",
            "UNCLEAR",
            "STOP",
            "URGENT",
        }:
            diagnostics.append("[LLM] Malformed name response")
            return None
        return parsed

    def _finish_name_phase(
        self,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        self._state.pop("ask_name_unclear_count", None)
        self._state.pop("active_name_question", None)
        self._state["pending_name_confirmation"] = None
        initial_message = self._state.pop("pending_initial_message", None)
        if self._activities_enabled:
            requested_activity = self._state.pop("pending_requested_activity", None)
            if requested_activity is not None:
                diagnostics.append(f"[ACTIVITY] {requested_activity}")
                self._start_activity(requested_activity, diagnostics)
                return
            self._set_phase("DAILY_CHECKIN", diagnostics)
            return
        if initial_message:
            self._state["skip_conversation_opening"] = True
            self._set_phase("CONVERSATION", diagnostics)
            self._handle_conversation(initial_message, utterances, diagnostics)
            return
        self._set_phase("CONVERSATION", diagnostics)

    # ------------------------------------------------------------------
    # ARI v0.4 visitor experience

    def _enter_daily_checkin(self, utterances: list[str]) -> None:
        if self._state.get("daily_checkin_step") is not None:
            return
        self._state["daily_checkin_step"] = "WELLBEING"
        question = choose_nonrepeating_response(
            self._state,
            "last_daily_wellbeing_question",
            prompts.DAILY_WELLBEING_QUESTIONS,
        )
        utterances.append(question)
        self._append_history("ari", question)

    def _handle_daily_checkin(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        if is_explicit_stop_request(visitor_text):
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return

        requested_activity = obvious_activity_request(visitor_text)
        if requested_activity is not None:
            self._append_history("visitor", visitor_text)
            diagnostics.append(f"[ACTIVITY] {requested_activity}")
            self._start_activity(requested_activity, diagnostics)
            return

        if not visitor_text:
            utterances.append(prompts.EMPTY_INPUT_RESPONSE)
            return

        parsed = self._classify_daily_checkin(visitor_text, diagnostics)
        intent = parsed.get("intent") if parsed else "UNCLEAR"
        if intent == "STOP":
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return
        if intent == "URGENT":
            diagnostics.append("[INTENT] URGENT")
            utterances.append(prompts.URGENT_FALLBACK)
            self._clear_visitor_session()
            self._set_phase("STANDBY", diagnostics)
            return
        if intent in {"BABBEL", "GAME"}:
            self._append_history("visitor", visitor_text)
            diagnostics.append(f"[ACTIVITY] {intent}")
            self._start_activity(intent, diagnostics)
            return
        if intent == "UNCLEAR":
            reply = (
                "Pardon, ik heb u niet goed verstaan. "
                "Wilt u het nog eens rustig zeggen?"
            )
            utterances.append(reply)
            self._remember_exchange(visitor_text, reply)
            return

        reply = _clean_spoken_reply(parsed.get("reply") if parsed else None)
        reply = _without_spoken_questions(reply or "") or (
            "Dank u dat u dat vertelt."
        )
        utterances.append(reply)
        self._remember_exchange(visitor_text, reply)

        step = self._state.get("daily_checkin_step")
        plans_mentioned = bool(parsed and parsed.get("plans_mentioned"))
        if (
            step == "WELLBEING"
            and intent == "NORMAL"
            and not plans_mentioned
        ):
            self._state["daily_checkin_step"] = "PLANS"
            question = choose_nonrepeating_response(
                self._state,
                "last_daily_plans_question",
                prompts.DAILY_PLANS_QUESTIONS,
            )
            utterances.append(question)
            self._append_history("ari", question)
            return

        # A plan was already supplied, the plans question was answered, or the
        # visitor preferred not to discuss the check-in.  Do not ask again.
        self._state["daily_checkin_step"] = "DONE"
        self._state["activity_choice_context"] = "initial"
        self._set_phase("ACTIVITY_CHOICE", diagnostics)

    def _classify_daily_checkin(
        self,
        visitor_text: str,
        diagnostics: list[str],
    ) -> dict[str, Any] | None:
        try:
            result = self.llm.prompt(
                f"{prompts.DAILY_CHECKIN_INSTRUCTIONS}\n\n"
                f"STAP: {self._state.get('daily_checkin_step')}\n"
                f"GESPREK TOT NU TOE:\n{self._history_for_prompt()}\n\n"
                f"BEZOEKER: {visitor_text}",
                max_tokens=110,
                temperature=0.2,
            )
        except Exception:
            diagnostics.append("[LLM] Daily check-in unavailable")
            return None

        parsed = _extract_json_object(result)
        allowed = {"intent", "reply", "plans_mentioned"}
        intents = {
            "NORMAL",
            "UNCOMFORTABLE",
            "DISTRESS",
            "URGENT",
            "BABBEL",
            "GAME",
            "STOP",
            "UNCLEAR",
        }
        if (
            parsed is None
            or set(parsed) != allowed
            or parsed.get("intent") not in intents
            or not isinstance(parsed.get("plans_mentioned"), bool)
        ):
            diagnostics.append("[LLM] Malformed daily check-in response")
            return None
        return parsed

    def _enter_activity_choice(self, utterances: list[str]) -> None:
        context = self._state.get("activity_choice_context") or "initial"
        if context == "after_babbel":
            choices = prompts.BABBEL_BOUNDARY_QUESTIONS
        elif context == "after_game":
            choices = prompts.GAME_BOUNDARY_QUESTIONS
        elif context in {"game_complete", "game_stopped"}:
            choices = prompts.GAME_FINISHED_QUESTIONS
        elif context == "game_unavailable":
            choices = prompts.GAME_UNAVAILABLE_CHOICE_QUESTIONS
        else:
            choices = prompts.ACTIVITY_CHOICE_QUESTIONS
        question = choose_nonrepeating_response(
            self._state,
            f"last_activity_choice_{context}",
            choices,
        )
        if context == "after_game":
            remaining = self.difference_game.next_target_count - self.difference_game.score
            question = question.format(remaining=remaining)
        self._state["active_activity_question"] = question
        utterances.append(question)
        self._append_history("ari", question)

    def _handle_activity_choice(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        if is_explicit_stop_request(visitor_text):
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return
        if not visitor_text:
            utterances.append(prompts.ACTIVITY_CHOICE_CLARIFICATION)
            return

        requested = obvious_activity_request(visitor_text)
        binary = classify_simple_binary_response(visitor_text)
        context = self._state.get("activity_choice_context")
        normalized = normalize_for_matching(visitor_text)
        if requested == "BABBEL" and re.search(
            r"\b(?:hetzelfde|zelfde|hierover|daarover|dit onderwerp)\b", normalized
        ):
            decision = {"activity": "BABBEL", "same_topic": True, "topic": None}
        elif requested == "BABBEL" and " over " in f" {normalized} ":
            decision = self._classify_activity_choice(visitor_text, diagnostics)
        elif requested is not None:
            decision = {
                "activity": requested,
                "same_topic": False,
                "topic": None,
            }
        elif binary is not None and context == "after_game":
            if binary == "YES":
                decision = {"activity": "GAME", "same_topic": False, "topic": None}
            else:
                self._state["activity_choice_context"] = "game_stopped"
                self._enter_activity_choice(utterances)
                return
        elif binary is not None and context in {"game_complete", "game_stopped"}:
            decision = {
                "activity": "BABBEL" if binary == "YES" else "STOP",
                "same_topic": False,
                "topic": None,
            }
        elif binary is not None:
            # A yes/no answer cannot select one of two or three options.
            decision = None
        else:
            decision = self._classify_activity_choice(visitor_text, diagnostics)

        label = decision.get("activity") if decision else "UNCLEAR"
        diagnostics.append(f"[ACTIVITY] {label}")
        if label == "URGENT":
            utterances.append(prompts.URGENT_FALLBACK)
            self._clear_visitor_session()
            self._set_phase("STANDBY", diagnostics)
            return
        if label in {"STOP", "DECLINE"}:
            self._begin_goodbye(
                "decline" if label == "DECLINE" else "conversation",
                diagnostics,
            )
            return
        if label == "CHOOSE_FOR_ME":
            game_can_be_chosen = (
                self.difference_game.ready
                and not self.difference_game.all_found
                and self._state.get("activity_choice_context") != "game_unavailable"
            )
            label = random.choice(("BABBEL", "GAME")) if game_can_be_chosen else "BABBEL"
            diagnostics.append(f"[ACTIVITY] CHOSEN_{label}")
        if label in {"BABBEL", "GAME"}:
            self._state["activity_choice_attempts"] = 0
            self._append_history("visitor", visitor_text)
            if label == "BABBEL":
                same_topic = bool(decision and decision.get("same_topic"))
                requested_topic = _clean_topic(decision.get("topic")) if decision else None
                self._state["continue_same_topic"] = same_topic
                self._state["requested_babbel_topic"] = requested_topic
            self._start_activity(label, diagnostics)
            return

        self._state["activity_choice_attempts"] += 1
        utterances.append(prompts.ACTIVITY_CHOICE_CLARIFICATION)
        self._remember_exchange(visitor_text, prompts.ACTIVITY_CHOICE_CLARIFICATION)

    def _classify_activity_choice(
        self,
        visitor_text: str,
        diagnostics: list[str],
    ) -> dict[str, Any] | None:
        try:
            result = self.llm.prompt(
                f"{prompts.ACTIVITY_CHOICE_INSTRUCTIONS}\n\n"
                f"CONTEXT: {self._state.get('activity_choice_context')}\n"
                f"HUIDIG ONDERWERP: {self._state.get('current_topic')}\n"
                f"VRAAG VAN ARI: {self._state.get('active_activity_question')!r}\n"
                f"BEZOEKER: {visitor_text!r}",
                max_tokens=75,
                temperature=0.0,
            )
        except Exception:
            diagnostics.append("[LLM] Activity classifier unavailable")
            return None
        parsed = _extract_json_object(result)
        labels = {
            "BABBEL",
            "GAME",
            "CHOOSE_FOR_ME",
            "STOP",
            "DECLINE",
            "URGENT",
            "UNCLEAR",
        }
        if (
            parsed is None
            or set(parsed) != {"activity", "same_topic", "topic"}
            or parsed.get("activity") not in labels
            or not isinstance(parsed.get("same_topic"), bool)
            or (
                parsed.get("topic") is not None
                and not isinstance(parsed.get("topic"), str)
            )
        ):
            diagnostics.append("[LLM] Malformed activity response")
            return None
        if parsed["activity"] != "BABBEL":
            parsed["same_topic"] = False
            parsed["topic"] = None
        return parsed

    def _start_activity(self, activity: str, diagnostics: list[str]) -> None:
        if activity == "BABBEL":
            previous = self._state.get("active_activity")
            self._state["active_activity"] = "GEZELLIG_BABBELEN"
            self._state["activity_choice_context"] = None
            self._state["babbel_transition"] = (
                "Natuurlijk, dan praten we gezellig verder."
                if previous == "ZOEK_DE_VERSCHILLEN"
                else "Graag, dan gaan we gezellig babbelen."
            )
            self._set_phase("GEZELLIG_BABBELEN", diagnostics)
            return
        if activity == "GAME":
            if self.difference_game.all_found:
                self._state["activity_choice_context"] = "game_complete"
                self._set_phase("ACTIVITY_CHOICE", diagnostics)
                return
            if self.difference_game.target_reached:
                self.difference_game.advance_round()
                self._sync_game_state()
            self._state["active_activity"] = "ZOEK_DE_VERSCHILLEN"
            self._state["activity_choice_context"] = None
            self._set_phase("ZOEK_DE_VERSCHILLEN", diagnostics)
            return
        raise ValueError(f"Unknown activity: {activity}")

    def _enter_babbel(
        self,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        transition = self._state.pop("babbel_transition", None)
        if transition:
            utterances.append(transition)
            self._append_history("ari", transition)

        same_topic = bool(self._state.pop("continue_same_topic", False))
        requested_topic = self._state.pop("requested_babbel_topic", None)
        if same_topic and self._state.get("current_topic"):
            topic = self._state["current_topic"]
        else:
            topic = requested_topic or self._choose_new_topic()
        self._start_topic(topic, diagnostics)
        self._state["continue_same_topic"] = same_topic
        opening = self._generate_topic_opening(diagnostics)
        if opening is None:
            opening = f"Laten we het over {topic} hebben. Wat komt er als eerste bij u op?"
        utterances.append(opening)
        self._append_history("ari", opening)
        if _contains_spoken_question(opening):
            self._state["topic_question_count"] += 1

    def _handle_babbel(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        if is_explicit_stop_request(visitor_text):
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return
        if obvious_activity_request(visitor_text) == "GAME":
            self._append_history("visitor", visitor_text)
            diagnostics.append("[ACTIVITY] GAME")
            self._start_activity("GAME", diagnostics)
            return
        self._handle_conversation(visitor_text, utterances, diagnostics)

    def _enter_difference_game(
        self,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        self._sync_game_state()
        if not self.difference_game.ready:
            utterances.append(prompts.GAME_UNAVAILABLE)
            self._append_history("ari", prompts.GAME_UNAVAILABLE)
            self._state["active_activity"] = None
            self._state["activity_choice_context"] = "game_unavailable"
            self._set_phase("ACTIVITY_CHOICE", diagnostics)
            return

        if self._state.get("game_has_started"):
            introduction = "Prima, dan zoeken we verder. Ziet u nog een verschil?"
        else:
            introduction = choose_nonrepeating_response(
                self._state,
                "last_game_introduction",
                prompts.GAME_INTRODUCTIONS,
            )
            self._state["game_has_started"] = True
        utterances.append(introduction)
        self._append_history("ari", introduction)

    def _handle_difference_game(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        normalized = normalize_for_matching(visitor_text)
        if re.search(r"\b(?:stoppen met|genoeg van) (?:dit |het )?spel(?:letje)?\b", normalized):
            self._state["activity_choice_context"] = "game_stopped"
            utterances.append("Natuurlijk, we kunnen het spel hierbij laten.")
            self._set_phase("ACTIVITY_CHOICE", diagnostics)
            return
        if is_explicit_stop_request(visitor_text):
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return
        if obvious_activity_request(visitor_text) == "BABBEL":
            self._append_history("visitor", visitor_text)
            diagnostics.append("[ACTIVITY] BABBEL")
            self._start_activity("BABBEL", diagnostics)
            return
        if not visitor_text:
            utterances.append(prompts.EMPTY_INPUT_RESPONSE)
            return
        if not self.difference_game.ready:
            utterances.append(prompts.GAME_UNAVAILABLE)
            self._state["activity_choice_context"] = "game_unavailable"
            self._set_phase("ACTIVITY_CHOICE", diagnostics)
            return

        if any(phrase in normalized for phrase in (
            "ik weet het niet", "geen idee", "ik zie het niet", "ik kan niets vinden",
            "ik zie geen verschil", "ik weet geen verschil", "een hint", "help",
        )) or normalized in {"hint", "weet ik niet", "weet niet", "hulp"}:
            self._give_game_hint(visitor_text, utterances, diagnostics)
            return

        if self.difference_game.needs_side_clarification(visitor_text):
            reply = "Wilt u het verschil met links en rechts beschrijven?"
            utterances.append(reply)
            self._remember_exchange(visitor_text, reply)
            diagnostics.append("[GAME] PICTURE_LABELS_UNVERIFIED")
            return

        raw = self.difference_game.interpret_known_observation(visitor_text)
        if raw is None:
            try:
                raw = self.llm.prompt(
                    self.difference_game.interpretation_prompt(visitor_text),
                    max_tokens=1800,
                    temperature=0.0,
                )
            except Exception:
                diagnostics.append("[LLM] Difference interpreter unavailable")
                raw = None
        intent = self.difference_game.response_intent(raw)
        if intent == "STOP":
            self._begin_goodbye("conversation", diagnostics)
            return
        if intent == "BABBEL":
            self._append_history("visitor", visitor_text)
            self._start_activity("BABBEL", diagnostics)
            return
        if intent == "URGENT":
            utterances.append(prompts.URGENT_FALLBACK)
            self._clear_visitor_session()
            self._set_phase("STANDBY", diagnostics)
            return
        if intent == "DONT_KNOW":
            self._give_game_hint(visitor_text, utterances, diagnostics)
            return
        result = self.difference_game.evaluate_llm_output(raw)
        self._sync_game_state()

        if result.status == "matched":
            count = len(result.newly_found_ids)
            if count == 1:
                reply = choose_nonrepeating_response(
                    self._state,
                    "last_game_correct_response",
                    prompts.GAME_CORRECT_RESPONSES,
                )
            else:
                reply = f"Ja, dat zijn {count} verschillende goede vondsten."
            diagnostics.append(f"[GAME] +{count} ({result.score}/{result.available_count})")
            if result.all_found or result.target_reached:
                if result.all_found:
                    reply += " Daarmee hebben we alle beschikbare verschillen gevonden."
                else:
                    reply += f" We hebben er nu {result.score} gevonden."
                utterances.append(reply)
                self._remember_exchange(visitor_text, reply)
                self._state["activity_choice_context"] = (
                    "game_complete" if result.all_found else "after_game"
                )
                self._set_phase("ACTIVITY_CHOICE", diagnostics)
                return
            reply += " Ziet u nog een verschil?"
        elif result.status == "already_found":
            reply = choose_nonrepeating_response(
                self._state,
                "last_game_repeat_response",
                prompts.GAME_REPEAT_RESPONSES,
            )
            diagnostics.append("[GAME] DUPLICATE")
        else:
            invalid_output = any(d.status == INVALID for d in result.decisions)
            if not invalid_output and self.difference_game.record_mistake():
                self._give_game_hint(visitor_text, utterances, diagnostics)
                return
            self._sync_game_state()
            uncertain_output = invalid_output or any(
                d.status == UNCERTAIN for d in result.decisions
            )
            reply = choose_nonrepeating_response(
                self._state,
                "last_game_clarification_response" if uncertain_output else "last_game_incorrect_response",
                prompts.GAME_CLARIFICATION_RESPONSES if uncertain_output else prompts.GAME_INCORRECT_RESPONSES,
            )
            diagnostics.append("[GAME] UNVERIFIED")
        utterances.append(reply)
        self._remember_exchange(visitor_text, reply)

    def _give_game_hint(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        reassurance = choose_nonrepeating_response(
            self._state, "last_game_hint_reassurance", prompts.GAME_HINT_REASSURANCES
        )
        reply = f"{reassurance} {self.difference_game.next_hint()}"
        diagnostics.append("[GAME] HINT")
        utterances.append(reply)
        self._remember_exchange(visitor_text, reply)
        self._sync_game_state()

    def _sync_game_state(self) -> None:
        self._state["game_found_count"] = self.difference_game.score
        self._state["game_target_count"] = self.difference_game.target_count
        self._state["game_available_count"] = self.difference_game.available_count
        self._state["game_target_reached"] = self.difference_game.target_reached
        self._state["game_all_found"] = self.difference_game.all_found
        self._state["game_found_ids"] = sorted(self.difference_game.found_ids)
        self._state["game_mistakes_since_hint"] = self.difference_game.mistakes_since_hint

    def _handle_conversation(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        if is_explicit_stop_request(visitor_text):
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return

        if not visitor_text:
            utterances.append(prompts.EMPTY_INPUT_RESPONSE)
            return

        if self._state.get("pending_stop_confirmation"):
            self._handle_stop_confirmation(visitor_text, utterances, diagnostics)
            return

        resolving_clarification = (
            self._state.pop("pending_clarification", None) is not None
        )
        questions_before = self._state.get("topic_question_count", 0)
        previous_topic = self._state.get("current_topic")
        generated = self._generate_conversation_response(visitor_text, diagnostics)
        if generated is None:
            reply = prompts.CONVERSATION_FALLBACK
            utterances.append(reply)
            self._remember_exchange(visitor_text, reply)
            return

        intent = generated["intent"]
        reply = generated["reply"]
        topic = generated["topic"]
        cycle_complete = generated["cycle_complete"]

        if intent == "STOP":
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return
        if intent == "POSSIBLE_STOP":
            diagnostics.append("[INTENT] POSSIBLE_STOP")
            reply = prompts.STOP_CONFIRMATION_QUESTION
            self._state["pending_stop_confirmation"] = {
                "question": reply,
                "unclear_attempts": 0,
            }
        elif intent == "UNCLEAR":
            diagnostics.append("[INTENT] UNCLEAR")
            # The model may use the immediate context to formulate one careful
            # interpretation (for example, "Bedoelt u bolognese?"). Keep that
            # clarification instead of replacing it with a generic repeat
            # request. It is deliberately tracked outside the topic budget.
            reply = reply or prompts.CONVERSATION_CLARIFICATION
            self._state["pending_clarification"] = {"question": reply}
            cycle_complete = False
        elif intent == "TOPIC_CHANGE":
            diagnostics.append("[INTENT] TOPIC_CHANGE")
        elif intent == "URGENT":
            diagnostics.append("[INTENT] URGENT")
            # Python owns the conservative safety wording. Do not allow a
            # generated diagnosis or treatment to pass through here.
            reply = prompts.URGENT_FALLBACK

        topic_changed = False
        if topic is not None and (
            intent == "TOPIC_CHANGE" or self._state.get("current_topic") is None
        ):
            topic_changed = self._topics_differ(previous_topic, topic)
            if topic_changed or previous_topic is None:
                self._start_topic(topic, diagnostics)
                questions_before = 0

        is_topic_reply = intent in {"CONTINUE", "TOPIC_CHANGE"}
        asks_question = is_topic_reply and _contains_spoken_question(reply)

        # Once four topic questions have already been heard, Python refuses a
        # fifth proactive question even if the model accidentally generated it.
        # Preserve any answer or explanation that came before that question.
        if not topic_changed and questions_before >= MAX_TOPIC_QUESTIONS and asks_question:
            diagnostics.append("[TOPIC] Question budget reached")
            reply = _without_spoken_questions(reply) or prompts.TOPIC_CYCLE_CLOSING_FALLBACK
            asks_question = False
            cycle_complete = True

        if (
            not topic_changed
            and questions_before >= MAX_TOPIC_QUESTIONS
            and intent == "CONTINUE"
            and not asks_question
        ):
            cycle_complete = True

        utterances.append(reply)
        self._remember_exchange(visitor_text, reply)

        if asks_question:
            self._state["topic_question_count"] += 1

        # Complete only after the visitor has answered the third or fourth
        # question. A just-spoken fourth question still deserves an answer.
        if (
            not topic_changed
            and intent == "CONTINUE"
            and self._state.get("pending_stop_confirmation") is None
            and not resolving_clarification
            and questions_before >= MIN_TOPIC_QUESTIONS
            and not asks_question
            and cycle_complete
        ):
            if self._activities_enabled:
                self._state["activity_choice_context"] = "after_babbel"
                self._set_phase("ACTIVITY_CHOICE", diagnostics)
            else:
                self._set_phase("CONTINUE_CHECK", diagnostics)

    def _generate_conversation_response(
        self,
        visitor_text: str,
        diagnostics: list[str],
    ) -> dict[str, Any] | None:
        history = self._history_for_prompt()
        last_ari_reply = next(
            (
                item.get("content", "")
                for item in reversed(self._state.get("conversation_history", []))
                if item.get("role") == "ari"
            ),
            "(nog geen eerdere reactie)",
        )
        current_topic = self._state.get("current_topic") or "nog niet bepaald"
        visitor_name = self._state.get("visitor_name") or "niet gegeven"
        topic_questions = self._state.get("topic_question_count", 0)
        if topic_questions >= MAX_TOPIC_QUESTIONS:
            budget_guidance = (
                "Het maximum is bereikt. Stel geen nieuwe onderwerpvraag. "
                "Reageer eerst passend op de bezoeker. Geef daarna een korte, "
                "betrouwbare bijdrage of een observatie uit diens eigen woorden "
                "en gebruik cycle_complete=true zodra er een natuurlijke pauze is."
            )
        elif topic_questions >= MIN_TOPIC_QUESTIONS:
            budget_guidance = (
                "De minimale cycluslengte is bereikt. Rond bij voorkeur af met "
                "een korte relevante mededeling zonder vraag en gebruik dan "
                "cycle_complete=true. Stel alleen nog een vierde vraag als die "
                "werkelijk voortbouwt op nieuwe informatie van de bezoeker."
            )
        else:
            budget_guidance = (
                "De cyclus is nog bezig. Reageer natuurlijk; een vervolgvraag is "
                "alleen nodig wanneer die bij de uitspraak past."
            )
        prompt = (
            f"{prompts.CONVERSATION_RESPONSE_INSTRUCTIONS}\n\n"
            f"HUIDIG ONDERWERP: {current_topic}\n\n"
            f"OPTIONELE NAAM VAN DEZE BEZOEKER: {visitor_name}\n"
            f"UITGESPROKEN ONDERWERPVRAGEN IN DEZE CYCLUS: "
            f"{topic_questions} van maximaal {MAX_TOPIC_QUESTIONS}\n"
            f"STRUCTUURINSTRUCTIE: {budget_guidance}\n\n"
            f"DIRECT VOORAFGAANDE REACTIE VAN ARI:\n{last_ari_reply}\n\n"
            f"GESPREKSGESCHIEDENIS VAN DEZE BEZOEKER:\n{history}\n\n"
            f"NIEUWE UITSPRAAK VAN DE BEZOEKER:\n{visitor_text}"
        )
        try:
            result = self.llm.prompt(
                prompt,
                max_tokens=180,
                temperature=0.45,
            )
        except Exception:
            diagnostics.append("[LLM] Conversation response unavailable")
            return None

        validated, reason = _validate_conversation_response(result)
        if validated is not None:
            return validated

        # Keep the existing high-level diagnostic for operators while adding a
        # reason code that contains neither the transcript nor model output.
        diagnostics.append("[LLM] Malformed conversation response")
        diagnostics.append(f"[LLM] Conversation validation failed: {reason}")
        retry_prompt = f"{prompt}\n\n{prompts.CONVERSATION_FORMAT_RETRY}"
        try:
            retry_result = self.llm.prompt(
                retry_prompt,
                max_tokens=180,
                temperature=0.0,
            )
        except Exception:
            diagnostics.append("[LLM] Conversation response retry unavailable")
            return None

        validated, reason = _validate_conversation_response(retry_result)
        if validated is None:
            diagnostics.append("[LLM] Conversation response retry malformed")
            diagnostics.append(
                f"[LLM] Conversation retry validation failed: {reason}"
            )
            return None

        diagnostics.append("[LLM] Conversation response recovered")
        return validated

    def _choose_new_topic(self) -> str:
        current = normalize_for_matching(self._state.get("current_topic") or "")
        previous = normalize_for_matching(
            self._state.get("last_topic_category") or ""
        )
        used = {
            normalize_for_matching(topic)
            for topic in self._state.get("used_topic_categories", [])
        }
        available = [
            topic
            for topic in prompts.TOPIC_CATEGORIES
            if normalize_for_matching(topic) != current
            and normalize_for_matching(topic) != previous
            and normalize_for_matching(topic) not in used
        ]
        if not available:
            available = [
                topic
                for topic in prompts.TOPIC_CATEGORIES
                if normalize_for_matching(topic) != current
                and normalize_for_matching(topic) != previous
            ]
        return random.choice(available or prompts.TOPIC_CATEGORIES)

    @staticmethod
    def _topics_differ(first: str | None, second: str | None) -> bool:
        if first is None or second is None:
            return first != second
        return normalize_for_matching(first) != normalize_for_matching(second)

    def _start_topic(self, topic: str, diagnostics: list[str]) -> None:
        self._state["current_topic"] = topic
        self._state["topic_question_count"] = 0
        self._state["continue_check_attempts"] = 0
        if topic in prompts.TOPIC_CATEGORIES:
            self._state["last_topic_category"] = topic
            used = self._state.setdefault("used_topic_categories", [])
            if topic not in used:
                used.append(topic)
        diagnostics.append(f"[TOPIC] {topic}")

    def _generate_topic_opening(
        self,
        diagnostics: list[str],
    ) -> str | None:
        topic = self._state.get("current_topic")
        same_topic = self._state.pop("continue_same_topic", False)
        history = self._history_for_prompt()
        prompt = (
            f"{prompts.TOPIC_OPENING_INSTRUCTIONS}\n\n"
            f"GEKOZEN ONDERWERP: {topic}\n"
            f"NIEUWE CYCLUS OVER HETZELFDE ONDERWERP: "
            f"{'ja; kies een ander aspect' if same_topic else 'nee'}\n\n"
            f"EERDERE GESPREKSGESCHIEDENIS:\n{history}"
        )
        try:
            result = self.llm.prompt(
                prompt,
                max_tokens=100,
                temperature=0.55,
            )
        except Exception:
            diagnostics.append("[LLM] Topic opening unavailable")
            return None

        parsed = _extract_json_object(result)
        reply = _clean_spoken_reply(parsed.get("reply")) if parsed else None
        normalized_reply = normalize_for_matching(reply or "")
        unsupported_opening = (
            "ik hoor dat" in normalized_reply
            or "blijkbaar" in normalized_reply
            or (
                normalize_for_matching(topic or "") == "hobby s"
                and (
                    "niet meer" in normalized_reply
                    or "vroeger wel" in normalized_reply
                )
            )
        )
        if reply is None or not _contains_spoken_question(reply):
            diagnostics.append("[LLM] Malformed topic opening")
            return None
        if unsupported_opening:
            diagnostics.append("[LLM] Unsupported topic opening")
            return None
        return reply

    def _handle_continue_check(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        if is_explicit_stop_request(visitor_text):
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return

        binary = classify_simple_binary_response(visitor_text)
        if binary == "YES":
            decision = {"decision": "NEW_TOPIC", "topic": None}
        elif binary == "NO":
            decision = {"decision": "STOP", "topic": None}
        else:
            decision = self._classify_continue_check(visitor_text, diagnostics)

        label = decision.get("decision") if decision else "UNCLEAR"
        requested_topic = _clean_topic(decision.get("topic")) if decision else None
        diagnostics.append(f"[CONTINUE] {label}")

        if label == "STOP":
            self._begin_goodbye("conversation", diagnostics)
            return
        if label == "URGENT":
            diagnostics.append("[INTENT] URGENT")
            utterances.append(prompts.URGENT_FALLBACK)
            self._remember_exchange(visitor_text, prompts.URGENT_FALLBACK)
            return
        if label in {"NEW_TOPIC", "SAME_TOPIC"}:
            self._append_history("visitor", visitor_text)
            previous_topic = self._state.get("current_topic")
            same_topic = label == "SAME_TOPIC"
            if same_topic:
                topic = requested_topic or previous_topic or self._choose_new_topic()
            else:
                topic = requested_topic or self._choose_new_topic()
                if not self._topics_differ(previous_topic, topic):
                    same_topic = True
            self._start_topic(topic, diagnostics)
            self._state["continue_same_topic"] = same_topic
            self._set_phase("CONVERSATION", diagnostics)
            return

        self._state["continue_check_attempts"] += 1
        if self._state["continue_check_attempts"] >= MAX_CONTINUE_CHECK_UNCLEAR:
            self._begin_goodbye("decline", diagnostics)
            return
        utterances.append(prompts.CONTINUE_CHECK_CLARIFICATION)
        self._remember_exchange(visitor_text, prompts.CONTINUE_CHECK_CLARIFICATION)

    def _classify_continue_check(
        self,
        visitor_text: str,
        diagnostics: list[str],
    ) -> dict[str, Any] | None:
        history = self._history_for_prompt()
        try:
            result = self.llm.prompt(
                f"{prompts.CONTINUE_CHECK_INSTRUCTIONS}\n\n"
                f"HUIDIG ONDERWERP: {self._state.get('current_topic')}\n\n"
                f"GESPREK TOT NU TOE:\n{history}\n\n"
                f"ANTWOORD VAN DE BEZOEKER: {visitor_text}",
                max_tokens=60,
                temperature=0.0,
            )
        except Exception:
            diagnostics.append("[LLM] Continue classifier unavailable")
            return None

        parsed = _extract_json_object(result)
        if parsed is None or parsed.get("decision") not in {
            "NEW_TOPIC",
            "SAME_TOPIC",
            "STOP",
            "UNCLEAR",
            "URGENT",
        }:
            diagnostics.append("[LLM] Malformed continue response")
            return None
        return parsed

    def _handle_stop_confirmation(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        if is_explicit_stop_request(visitor_text):
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return

        binary = classify_simple_binary_response(visitor_text)
        if binary == "YES":
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return
        if binary == "NO":
            reply = "Natuurlijk, we kunnen rustig verder praten."
            self._state["pending_stop_confirmation"] = None
            utterances.append(reply)
            self._remember_exchange(visitor_text, reply)
            return

        history = self._history_for_prompt()
        try:
            result = self.llm.prompt(
                f"{prompts.STOP_CONFIRMATION_INSTRUCTIONS}\n\n"
                f"GESPREK TOT NU TOE:\n{history}\n\n"
                f"LAATSTE ANTWOORD VAN DE BEZOEKER: {visitor_text}",
                max_tokens=100,
                temperature=0.2,
            )
        except Exception:
            diagnostics.append("[LLM] Stop confirmation unavailable")
            diagnostics.append("[INTENT] UNCLEAR")
            self._handle_unclear_stop_confirmation(
                visitor_text,
                utterances,
                diagnostics,
            )
            return

        parsed = _extract_json_object(result)
        decision = parsed.get("decision") if parsed else None
        if decision == "STOP":
            diagnostics.append("[INTENT] STOP")
            self._begin_goodbye("conversation", diagnostics)
            return
        if decision == "CONTINUE":
            reply = _clean_spoken_reply(parsed.get("reply"))
            if reply is None:
                reply = "Natuurlijk, we kunnen rustig verder praten."
            self._state["pending_stop_confirmation"] = None
            utterances.append(reply)
            self._remember_exchange(visitor_text, reply)
            return
        if decision == "URGENT":
            diagnostics.append("[INTENT] URGENT")
            self._state["pending_stop_confirmation"] = None
            utterances.append(prompts.URGENT_FALLBACK)
            self._remember_exchange(visitor_text, prompts.URGENT_FALLBACK)
            return

        diagnostics.append("[INTENT] UNCLEAR")
        self._handle_unclear_stop_confirmation(
            visitor_text,
            utterances,
            diagnostics,
        )

    def _handle_unclear_stop_confirmation(
        self,
        visitor_text: str,
        utterances: list[str],
        diagnostics: list[str],
    ) -> None:
        pending = self._state["pending_stop_confirmation"]
        pending["unclear_attempts"] = pending.get("unclear_attempts", 0) + 1
        if pending["unclear_attempts"] >= MAX_STOP_CONFIRMATION_UNCLEAR:
            self._begin_goodbye("decline", diagnostics)
            return

        utterances.append(prompts.STOP_CONFIRMATION_CLARIFICATION)
        self._remember_exchange(
            visitor_text,
            prompts.STOP_CONFIRMATION_CLARIFICATION,
        )

    def _begin_goodbye(self, kind: str, diagnostics: list[str]) -> None:
        self._state["pending_goodbye_kind"] = kind
        self._set_phase("GOODBYE", diagnostics)

    def _append_history(self, role: str, content: str) -> None:
        self._state["conversation_history"].append(
            {"role": role, "content": content}
        )

    def _remember_exchange(self, visitor_text: str, ari_reply: str) -> None:
        self._append_history("visitor", visitor_text)
        self._append_history("ari", ari_reply)
        self._state["session_turn_count"] += 1

    def _history_for_prompt(self) -> str:
        history = self._state.get("conversation_history", [])
        if not history:
            return "(nog geen eerdere gespreksbeurten)"
        labels = {"visitor": "Bezoeker", "ari": "ARI"}
        return "\n".join(
            f"{labels.get(item.get('role'), 'Onbekend')}: {item.get('content', '')}"
            for item in history
        )


__all__ = [
    "ConversationEngine",
    "ConversationTurn",
    "MAX_TOPIC_QUESTIONS",
    "MIN_TOPIC_QUESTIONS",
    "is_explicit_stop_request",
    "is_wake_greeting",
    "make_initial_state",
]
