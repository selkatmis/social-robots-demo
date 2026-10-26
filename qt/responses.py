import json
import random
import re
from dataclasses import dataclass

import qt.prompts as template


# Keep these easy to tune during text-only conversation tests.
MAX_WRONG_OR_UNCLEAR = 3  # Existing limit for division exercises.
MAX_TURNS_PER_QUESTION = 8
SMALL_ERROR_MAX = 2  # A first multiplication answer within 2 of the correct value is "close".
MAX_EXPLANATION_TURNS = 5
MAX_STOP_CONFIRMATION_UNCLEAR = 2
MAX_OFF_TOPIC_RESPONSES = 3
MAX_UNRECOGNIZED_NUMBER_ATTEMPTS = 3
MIN_CHAT_TOPIC_QUESTIONS = 3
MAX_CHAT_TOPIC_QUESTIONS = 4
MAX_CHAT_REPLY_CHARACTERS = 500
AVAILABLE_TOPICS = ("KEREN", "DELEN")
DIRECT_SUBJECT_SWITCHES = {
    "WISSEL_KEREN": "KEREN",
    "WISSEL_DELEN": "DELEN",
}
SUBJECT_CHANGE_INTENTS = {"ANDER_ONDERWERP", *DIRECT_SUBJECT_SWITCHES}
CHAT_INTENTS = {
    "CONTINUE",
    "DIRECT_ANSWER",
    "TOPIC_CHANGE",
    "SWITCH_REKENEN",
    "SWITCH_KEREN",
    "SWITCH_DELEN",
    "UNCLEAR",
    "SENSITIVE",
    "STOP",
}

# Match common Dutch spoken-number answers deterministically, without asking
# the LLM to judge whether an answer is mathematically correct.
NUMBER_WORDS = {
    word: value for value, word in template.DUTCH_NUMBER_NAMES.items()
}
NUMBER_WORDS["een"] = 1


def dutch_number(value, *, capitalize=False):
    """Return the prepared Dutch word for a number in the curriculum."""
    word = template.DUTCH_NUMBER_NAMES[value]
    return word.capitalize() if capitalize else word


def make_progressive_addition(groups, group_size):
    """Build a short, spoken walkthrough ending before the final addition."""
    if groups < 5:
        return ()

    group_word = dutch_number(group_size)
    lines = ["Laten we het stap voor stap uitrekenen!"]

    completed_groups = 3
    running_total = completed_groups * group_size
    first_sum = " plus ".join([group_word] * completed_groups).capitalize()
    lines.append(f"{first_sum} is {dutch_number(running_total)}.")

    # Keep the final group for the child. The varied one- and two-group steps
    # keep the spoken explanation short without hiding any arithmetic.
    groups_before_final = groups - 1
    remaining = groups_before_final - completed_groups

    if remaining:
        completed_groups += 1
        running_total += group_size
        remaining -= 1
        lines.append(
            f"Nog {group_word} erbij is {dutch_number(running_total)}."
        )

    while remaining >= 2:
        completed_groups += 2
        running_total += 2 * group_size
        remaining -= 2
        lines.append(
            f"Dan doen we er twee groepjes van {group_word} bij. "
            f"Dan hebben we {dutch_number(running_total)}!"
        )

    if remaining == 1:
        completed_groups += 1
        running_total += group_size
        lines.append(
            f"Nog {group_word} erbij maakt {dutch_number(running_total)}."
        )

    expected_total = groups_before_final * group_size
    if completed_groups != groups_before_final or running_total != expected_total:
        raise AssertionError("Progressive multiplication totals are inconsistent")

    lines.append(
        f"En nu nog één keer {group_word} erbij. "
        "Hoeveel hebben we dan samen?"
    )
    return tuple(lines)


def make_multiplication_exercise(groups, group_size, difficulty):
    """Build all dialogue data for one multiplication exercise."""
    answer = groups * group_size
    completed_groups = groups - 1
    counting_start = completed_groups * group_size
    groups_spoken = f"{dutch_number(groups)} groepjes van {dutch_number(group_size)}"
    addition = " plus ".join([str(group_size)] * groups)
    addition_spoken = " plus ".join(
        [dutch_number(group_size)] * groups
    ).capitalize()
    counting_lead_in = "... ".join(
        dutch_number(value)
        for value in range(counting_start + 1, answer)
    ).capitalize()
    progressive_addition = make_progressive_addition(groups, group_size)

    if completed_groups == 1:
        known_total = ""
    else:
        known_total = (
            f"{dutch_number(completed_groups, capitalize=True)} groepjes van "
            f"{dutch_number(group_size)} zijn {dutch_number(counting_start)}. "
        )

    if groups == 2:
        group_breakdown = (
            f"één groepje van {dutch_number(group_size)} en nog een groepje "
            f"van {dutch_number(group_size)}"
        )
    else:
        group_breakdown = groups_spoken

    return {
        "difficulty": difficulty,
        "groups": groups,
        "group_size": group_size,
        "question": f"Wat is {groups} keer {group_size}?",
        "expression": f"{groups} keer {group_size}",
        "answer": answer,
        "hint": f"Denk aan {groups_spoken}.",
        "addition": addition,
        "addition_spoken": addition_spoken,
        "answer_spoken": dutch_number(answer),
        "multiplication_spoken": (
            f"{dutch_number(groups)} keer {dutch_number(group_size)}"
        ),
        "groups_spoken": groups_spoken,
        "group_breakdown": group_breakdown,
        "counting_known_total": known_total,
        "counting_start": dutch_number(counting_start),
        "counting_amount": dutch_number(group_size),
        "counting_lead_in": counting_lead_in,
        "counting_previous": dutch_number(answer - 1),
        "explanation_hint": f"Tel {groups_spoken} één voor één bij elkaar op.",
        "progressive_addition": progressive_addition,
        "progressive_final_question": (
            progressive_addition[-1] if progressive_addition else None
        ),
        "progressive_previous_total": dutch_number(counting_start),
        "progressive_final_hint": (
            f"We waren bij {dutch_number(counting_start)}. "
            f"Tel er nog {dutch_number(group_size)} bij."
        ),
    }


EASY_MULTIPLICATION_FACTS = (
    (2, 3),
    (2, 4),
    (2, 5),
    (3, 2),
    (3, 3),
    (3, 4),
    (3, 5),
    (4, 2),
    (4, 3),
    (4, 4),
    (5, 2),
    (5, 3),
    (5, 4),
)
HARD_MULTIPLICATION_FACTS = (
    (6, 4),
    (7, 3),
    (8, 5),
    (6, 6),
    (7, 4),
    (9, 3),
)
MULTIPLICATION_EXERCISES = tuple(
    make_multiplication_exercise(*fact, difficulty="easy")
    for fact in EASY_MULTIPLICATION_FACTS
) + tuple(
    make_multiplication_exercise(*fact, difficulty="hard")
    for fact in HARD_MULTIPLICATION_FACTS
)


def counted_object(count, singular, plural):
    """Return a natural Dutch number-and-object phrase."""
    noun = singular if count == 1 else plural
    return f"{dutch_number(count)} {noun}"


EASY_DIVISION_OBJECTS = (
    ("koekje", "koekjes"),
    ("snoepje", "snoepjes"),
    ("chocoladereepje", "chocoladereepjes"),
    ("speelgoedauto", "speelgoedauto's"),
    ("fidget spinner", "fidget spinners"),
)
HARD_DIVISION_OBJECTS = (
    ("appel", "appels"),
    ("banaan", "bananen"),
    ("aardbei", "aardbeien"),
    ("sinaasappel", "sinaasappels"),
)


def make_division_round_shares(answer, difficulty):
    """Split one child's quotient into short, manageable sharing rounds."""
    if answer == 1:
        return (1,)

    middle_total = answer - 2
    max_middle_share = 2 if difficulty == "easy" else 3
    if middle_total == 0:
        middle_shares = ()
    else:
        middle_rounds = (
            middle_total + max_middle_share - 1
        ) // max_middle_share
        smallest_share, larger_rounds = divmod(middle_total, middle_rounds)
        middle_shares = tuple(
            smallest_share + (1 if index < larger_rounds else 0)
            for index in range(middle_rounds)
        )

    shares = (1, *middle_shares, 1)
    if sum(shares) != answer or any(share <= 0 for share in shares):
        raise AssertionError("Division sharing rounds are inconsistent")
    return shares


def make_first_sharing_question(divisor, singular, plural):
    """Describe giving one object to each child without a long recital."""
    if divisor <= 3:
        sentences = []
        for child_number in range(1, divisor + 1):
            if child_number == divisor and divisor > 1:
                sentences.append(
                    f"En kind {dutch_number(child_number)} krijgt ook een {singular}."
                )
            else:
                sentences.append(
                    f"Kind {dutch_number(child_number)} krijgt een {singular}."
                )
        distribution = " ".join(sentences)
        return f"{distribution} Hoeveel {plural} hebben we nu nog over?"

    return template.DIVISION_FIRST_GROUP_QUESTION.format(
        child_count=dutch_number(divisor),
        one_object=f"één {singular}",
        round_objects=counted_object(divisor, singular, plural),
        objects=plural,
    )


def make_division_exercise(
    dividend,
    divisor,
    difficulty,
    object_singular,
    object_plural,
):
    """Build deterministic interactive sharing rounds for one division fact."""
    if dividend <= 0 or divisor <= 0 or dividend % divisor:
        raise ValueError("Division exercises require a positive whole-number answer")

    answer = dividend // divisor
    sharing_question = (
        f"Je hebt {dutch_number(dividend)} {object_plural} en "
        f"{dutch_number(divisor)} kinderen. Hoeveel {object_plural} "
        "krijgt ieder kind?"
    )

    multiplication = (
        f"{dutch_number(divisor, capitalize=True)} keer "
        f"{dutch_number(answer)} is {dutch_number(dividend)}"
    )
    division = (
        f"{dutch_number(dividend)} gedeeld door {dutch_number(divisor)} "
        f"is {dutch_number(answer)}"
    )

    intro = template.DIVISION_EXPLANATION_INTRO.format(
        objects=object_plural,
        dividend_objects=counted_object(
            dividend,
            object_singular,
            object_plural,
        ),
        children=f"{dutch_number(divisor)} kinderen",
    )
    rounds = []
    each_before = 0
    remaining_before = dividend
    round_shares = make_division_round_shares(answer, difficulty)

    for index, share in enumerate(round_shares):
        round_object_count = divisor * share
        each_after = each_before + share
        remaining_after = remaining_before - round_object_count
        is_first = index == 0
        is_final = index == len(round_shares) - 1

        values = {
            "answer": counted_object(answer, object_singular, object_plural),
            "answer_number": dutch_number(answer),
            "division": division,
            "each_before": counted_object(
                each_before,
                object_singular,
                object_plural,
            ),
            "each_before_number": dutch_number(each_before),
            "each_before_number_capitalized": dutch_number(
                each_before,
                capitalize=True,
            ),
            "each_total": counted_object(
                each_after,
                object_singular,
                object_plural,
            ),
            "each_total_number": dutch_number(each_after),
            "multiplication": multiplication,
            "objects": object_plural,
            "remaining": counted_object(
                remaining_after,
                object_singular,
                object_plural,
            ),
            "remaining_before": counted_object(
                remaining_before,
                object_singular,
                object_plural,
            ),
            "remaining_before_number": dutch_number(remaining_before),
            "round_objects": counted_object(
                round_object_count,
                object_singular,
                object_plural,
            ),
            "round_objects_number": dutch_number(round_object_count),
            "share": counted_object(share, object_singular, object_plural),
            "share_number": dutch_number(share),
        }

        if is_first:
            question = make_first_sharing_question(
                divisor,
                object_singular,
                object_plural,
            )
            correct_answer = remaining_after
            success = template.DIVISION_REMAINING_SUCCESS.format(**values)
            hint = template.DIVISION_REMAINING_HINT.format(**values)
            reveal = template.DIVISION_REMAINING_REVEAL.format(**values)
        elif is_final:
            question = template.DIVISION_FINAL_ROUND_QUESTION.format(**values)
            correct_answer = each_after
            success = template.DIVISION_FINAL_SUCCESS.format(**values)
            hint = template.DIVISION_EACH_TOTAL_HINT.format(**values)
            reveal = template.DIVISION_FINAL_REVEAL.format(**values)
        else:
            question = template.DIVISION_NEXT_ROUND_QUESTION.format(**values)
            correct_answer = each_after
            success_template = (
                template.DIVISION_PENULTIMATE_SUCCESS
                if index == len(round_shares) - 2
                else template.DIVISION_ROUND_SUCCESS
            )
            success = success_template.format(**values)
            hint = template.DIVISION_EACH_TOTAL_HINT.format(**values)
            reveal = template.DIVISION_EACH_TOTAL_REVEAL.format(**values)

        rounds.append({
            "step": index,
            "distributed_before": dividend - remaining_before,
            "distributed_after": dividend - remaining_after,
            "remaining_before": remaining_before,
            "remaining_after": remaining_after,
            "each_before": each_before,
            "each_after": each_after,
            "share_each": share,
            "correct_answer": correct_answer,
            "question": question,
            "hint": hint,
            "success": success,
            "reveal": reveal,
            "is_final": is_final,
        })
        each_before = each_after
        remaining_before = remaining_after

    if each_before != answer or remaining_before != 0:
        raise AssertionError("Division sharing plan does not finish evenly")

    return {
        "difficulty": difficulty,
        "question": f"Wat is {dividend} gedeeld door {divisor}?",
        "expression": f"{dividend} gedeeld door {divisor}",
        "answer": answer,
        "answer_spoken": dutch_number(answer),
        "dividend": dividend,
        "divisor": divisor,
        "object_singular": object_singular,
        "object_plural": object_plural,
        "hint": sharing_question,
        "sharing_question": sharing_question,
        "sharing_intro": intro,
        "sharing_rounds": tuple(rounds),
        "multiplication_connection": multiplication,
        "division_spoken": division,
    }


EASY_DIVISION_FACTS = (
    (6, 2),
    (8, 2),
    (9, 3),
    (10, 2),
    (12, 3),
    (12, 4),
    (15, 3),
    (16, 4),
    (20, 4),
)
HARD_DIVISION_FACTS = (
    (24, 4),
    (28, 4),
    (30, 5),
    (32, 4),
    (35, 5),
    (36, 6),
    (40, 5),
)
DIVISION_EXERCISES = tuple(
    make_division_exercise(
        dividend,
        divisor,
        "easy",
        *EASY_DIVISION_OBJECTS[0],
    )
    for dividend, divisor in EASY_DIVISION_FACTS
) + tuple(
    make_division_exercise(
        dividend,
        divisor,
        "hard",
        *HARD_DIVISION_OBJECTS[0],
    )
    for dividend, divisor in HARD_DIVISION_FACTS
)


def extract_answer_number(child_text):
    """Return one unambiguous numeric answer (digit or Dutch number word).

    This is deliberately conservative: multiple numbers or no recognizable
    number are treated as unclear, rather than asking an LLM to do arithmetic.
    """
    words = re.findall(r"\d+|[^\W\d_]+", child_text.casefold(), re.UNICODE)
    numbers = []

    for word in words:
        if word.isdecimal():
            numbers.append(int(word))
        elif word in NUMBER_WORDS:
            # In longer sentences 'een' is commonly an article, not the number 1.
            if word == "een" and len(words) > 1:
                continue
            numbers.append(NUMBER_WORDS[word])

    return numbers[0] if len(numbers) == 1 else None


def is_simple_numeric_answer(child_text):
    """Recognize a bare number, including a child's hesitant '?' or '!'."""
    text = child_text.casefold().strip().strip("?!., \t\r\n")
    return text.isdecimal() or text in NUMBER_WORDS


def looks_like_numeric_answer_expression(child_text):
    """Recognize a number in wording that clearly presents it as an answer."""
    if extract_answer_number(child_text) is None:
        return False
    tokens = re.findall(r"\d+|[^\W\d_]+", child_text.casefold(), re.UNICODE)
    return len(tokens) <= 3 or any(
        word in {"antwoord", "uitkomst", "volgens", "denk"} for word in tokens
    )


def is_unclear_number_attempt(child_text, response_type):
    """Separate likely recognition trouble from wrong and off-topic answers."""
    if response_type in {"ONVERSTAANBAAR_ANTWOORD", "ONBEKEND"}:
        return True
    if response_type != "OFF_TOPIC":
        return False

    words, _ = normalized_words(child_text)
    has_known_tangent = any(
        word in template.PLAYFUL_OFF_TOPIC_ACKNOWLEDGEMENTS for word in words
    )
    return 0 < len(words) <= 3 and not has_known_tangent


def multiplication_fact_key(exercise):
    """Treat reversed multiplication operands as the same underlying fact."""
    return tuple(sorted((exercise["groups"], exercise["group_size"])))


def exercise_for_topic(state, topic, *, difficulty=None):
    """Draw a random unused exercise, avoiding equivalent repetition."""
    if topic == "KEREN":
        eligible = [
            index
            for index, exercise in enumerate(MULTIPLICATION_EXERCISES)
            if difficulty is None or exercise["difficulty"] == difficulty
        ]
        remaining = state.get("remaining_multiplication_indices", [])
        candidates = [index for index in remaining if index in eligible]
        if not candidates:
            for index in eligible:
                if index not in remaining:
                    remaining.append(index)
            candidates = [index for index in remaining if index in eligible]

        previous = state.get("last_multiplication_index")
        previous_key = state.get("last_multiplication_fact_key")
        if previous_key is None and previous is not None:
            previous_key = multiplication_fact_key(
                MULTIPLICATION_EXERCISES[previous]
            )

        non_repeating = [
            index
            for index in candidates
            if multiplication_fact_key(MULTIPLICATION_EXERCISES[index])
            != previous_key
        ]
        if non_repeating:
            candidates = non_repeating
        elif previous_key is not None:
            # A partially consumed legacy pool can contain only the reversed
            # form of the preceding fact. Refill with other facts instead of
            # asking, for example, 3 × 4 immediately after 4 × 3.
            alternatives = [
                index
                for index in eligible
                if multiplication_fact_key(MULTIPLICATION_EXERCISES[index])
                != previous_key
            ]
            for index in alternatives:
                if index not in remaining:
                    remaining.append(index)
            candidates = alternatives

        # When the last three unused exercises are A×B, B×A, and one other
        # fact, choose one orientation now. The other fact can then separate
        # the two orientations, preserving the full no-repeat exercise pool.
        if len(candidates) == 3:
            fact_counts = {}
            for index in candidates:
                key = multiplication_fact_key(MULTIPLICATION_EXERCISES[index])
                fact_counts[key] = fact_counts.get(key, 0) + 1
            paired_keys = {key for key, count in fact_counts.items() if count > 1}
            if len(paired_keys) == 1:
                candidates = [
                    index
                    for index in candidates
                    if multiplication_fact_key(MULTIPLICATION_EXERCISES[index])
                    in paired_keys
                ]

        selected = random.choice(candidates)
        remaining.remove(selected)
        state["remaining_multiplication_indices"] = remaining
        state["last_multiplication_index"] = selected
        state["last_multiplication_fact_key"] = multiplication_fact_key(
            MULTIPLICATION_EXERCISES[selected]
        )
        return MULTIPLICATION_EXERCISES[selected]

    if topic == "DELEN":
        eligible = [
            index
            for index, exercise in enumerate(DIVISION_EXERCISES)
            if difficulty is None or exercise["difficulty"] == difficulty
        ]
        remaining = state.get("remaining_division_indices", [])
        candidates = [index for index in remaining if index in eligible]
        if not candidates:
            for index in eligible:
                if index not in remaining:
                    remaining.append(index)
            candidates = [index for index in remaining if index in eligible]

        previous = state.get("last_division_index")
        non_repeating = [index for index in candidates if index != previous]
        if non_repeating:
            candidates = non_repeating

        selected = random.choice(candidates)
        remaining.remove(selected)
        state["remaining_division_indices"] = remaining
        state["last_division_index"] = selected
        base_exercise = DIVISION_EXERCISES[selected]
        object_choices = (
            EASY_DIVISION_OBJECTS
            if base_exercise["difficulty"] == "easy"
            else HARD_DIVISION_OBJECTS
        )
        object_singular, object_plural = random.choice(object_choices)
        return make_division_exercise(
            base_exercise["dividend"],
            base_exercise["divisor"],
            base_exercise["difficulty"],
            object_singular,
            object_plural,
        )

    raise ValueError(f"Unknown topic: {topic}")


def choose_correct_multiplication_response(state):
    """Choose praise without repeating the response used last time."""
    previous = state.get("last_correct_response")
    choices = tuple(
        response
        for response in template.CORRECT_MULTIPLICATION_RESPONSES
        if response != previous
    )
    response = random.choice(choices)
    state["last_correct_response"] = response
    return response


def choose_correct_division_response(state):
    """Choose varied praise for a correct division answer."""
    previous = state.get("last_correct_division_response")
    choices = tuple(
        response
        for response in template.CORRECT_DIVISION_RESPONSES
        if response != previous
    )
    response = random.choice(choices)
    state["last_correct_division_response"] = response
    return response


def validated_classifier_label(result, allowed_labels):
    """Return one permitted LLM label, failing closed on malformed output."""
    if not isinstance(result, str):
        return "ONBEKEND"
    label = result.strip().upper()
    return label if label in allowed_labels else "ONBEKEND"


def _extract_json_object(raw_response):
    """Parse one strict JSON object, tolerating a single fenced wrapper."""
    if not isinstance(raw_response, str):
        return None
    text = raw_response.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _clean_spoken_reply(value):
    if not isinstance(value, str):
        return None
    reply = " ".join(value.split()).strip()
    if not reply or len(reply) > MAX_CHAT_REPLY_CHARACTERS:
        return None
    reply = reply.replace("**", "").replace("__", "").replace("`", "")
    if reply.count("?") > 1:
        return None
    return reply.strip() or None


def _clean_chat_topic(value):
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    topic = " ".join(value.split()).strip()
    return topic if 0 < len(topic) <= 80 else None


def _contains_spoken_question(reply):
    return "?" in reply


def _without_spoken_questions(reply):
    sentences = re.split(r"(?<=[.!?])\s+", reply)
    statements = [sentence for sentence in sentences if "?" not in sentence]
    cleaned = " ".join(statements).strip()
    return cleaned or None


def reply_requests_private_information(reply):
    """Reject generated questions that solicit identifying child data."""
    normalized = normalized_words(reply)[1]
    if normalized in {
        "hoe heet je",
        "hoe heet jij",
        "wat is je naam",
        "wat is jouw naam",
        "hoe oud ben je",
    }:
        return True
    private_terms = (
        "wat is je adres",
        "wat is jouw adres",
        "waar woon je",
        "waar kom je vandaan",
        "in welke plaats woon je",
        "wat is je postcode",
        "telefoonnummer",
        "mobiele nummer",
        "hoe heet je school",
        "welke school",
        "waar staat je school",
        "gebruikersnaam",
        "social media",
        "wachtwoord",
        "contactgegevens",
    )
    return any(term in normalized for term in private_terms)


def contains_private_information(child_text):
    """Avoid retaining obvious contact or exact-location disclosures."""
    normalized = normalized_words(child_text)[1]
    private_markers = (
        "mijn adres",
        "ik woon op",
        "ik woon aan",
        "ik woon in",
        "mijn postcode",
        "mijn telefoonnummer",
        "mijn mobiele nummer",
        "mijn school heet",
        "mijn gebruikersnaam",
        "mijn wachtwoord",
    )
    dutch_phone_number = re.search(
        r"(?<!\d)(?:(?:\+|00)31[ -]?|0)[1-9](?:[ -]?\d){7,9}(?!\d)",
        child_text,
    ) is not None
    email_like = re.search(r"\b\S+@\S+\.\S+\b", child_text) is not None
    return dutch_phone_number or email_like or any(
        marker in normalized for marker in private_markers
    )


def is_sensitive_disclosure(child_text):
    """Conservatively identify disclosures that need a trusted adult."""
    normalized = normalized_words(child_text)[1]
    words = set(normalized.split())
    sensitive_words = {
        "zelfmoord",
        "zelfbeschadiging",
        "drugs",
        "alcohol",
        "roken",
        "mishandeld",
        "mishandeling",
        "misbruikt",
        "misbruik",
        "seks",
        "zwanger",
        "politiek",
        "religie",
    }
    sensitive_phrases = (
        "wil niet meer leven",
        "mezelf pijn",
        "mezelf snijden",
        "in mezelf snijden",
        "doet mij pijn",
        "slaat mij",
        "bang om naar huis",
        "niemand mag het weten",
        "geheim houden",
        "welke medicijnen",
        "wat voor behandeling",
        "problemen thuis",
        "ruzie thuis",
        "geldproblemen",
        "ik ben eenzaam",
        "ik voel me eenzaam",
        "ik ben heel verdrietig",
    )
    return bool(words & sensitive_words) or any(
        phrase in normalized for phrase in sensitive_phrases
    )


def contains_unsafe_generated_content(reply):
    """Fail closed if generated dialogue introduces a restricted subject."""
    normalized = normalized_words(reply)[1]
    unsafe_terms = (
        "seks",
        "drugs",
        "alcohol",
        "roken",
        "zelfbeschadiging",
        "zelfmoord",
        "politiek",
        "verkiezingen",
        "religie",
        "geweld",
        "mishandeling",
        "misbruik",
        "volwassen relatie",
        "medische diagnose",
        "medische behandeling",
        "diagnose stellen",
        "gezinsinkomen",
        "problemen thuis",
    )
    return any(term in normalized for term in unsafe_terms)


def chat_opening_repeats_introduction(reply):
    """Reject a generated opening that greets or introduces QT again."""
    words, normalized = normalized_words(reply)
    starts_with_greeting = bool(words) and contains_loose_word(
        words[:1],
        template.WAKE_GREETING_WORDS,
    )
    return starts_with_greeting or "ik ben qt" in normalized


def _validate_chat_response(raw_response):
    parsed = _extract_json_object(raw_response)
    if parsed is None:
        return None, "invalid_json"
    required_fields = {"intent", "reply", "topic", "cycle_complete"}
    if set(parsed) != required_fields:
        return None, "invalid_fields"

    intent = parsed.get("intent")
    if not isinstance(intent, str) or intent not in CHAT_INTENTS:
        return None, "invalid_intent"

    raw_reply = parsed.get("reply")
    reply = _clean_spoken_reply(raw_reply)
    if raw_reply is not None and reply is None:
        return None, "invalid_reply"
    if intent in {"CONTINUE", "DIRECT_ANSWER", "TOPIC_CHANGE", "UNCLEAR"}:
        if reply is None:
            return None, "missing_reply"
    if reply is not None:
        if reply_requests_private_information(reply):
            return None, "private_solicitation"
        if contains_unsafe_generated_content(reply):
            return None, "unsafe_generated_content"

    raw_topic = parsed.get("topic")
    topic = _clean_chat_topic(raw_topic)
    if raw_topic is not None and topic is None:
        return None, "invalid_topic"
    if intent == "TOPIC_CHANGE" and topic is None:
        return None, "missing_topic"
    if topic is not None and contains_unsafe_generated_content(topic):
        return None, "unsafe_topic"

    cycle_complete = parsed.get("cycle_complete", False)
    if not isinstance(cycle_complete, bool):
        return None, "invalid_cycle_complete"
    return {
        "intent": intent,
        "reply": reply,
        "topic": topic,
        "cycle_complete": cycle_complete,
    }, None


def classify_activity_response(llm, child_text, question=None):
    """Classify the child's activity choice without guessing from a bare yes."""
    _, normalized = normalized_words(child_text)
    if normalized in {"ja", "jaa", "ja hoor", "yes", "oke", "oké", "prima"}:
        return "ONBEKEND"
    result = llm.prompt(
        f"{template.INSTRUCTIONS}\n"
        f"{template.ACTIVITY_CHOICE_CLASSIFIER}\n"
        f"Vraag van QT: {(question or template.ACTIVITY_CHOICE_QUESTIONS[0])!r}\n"
        f"Kind zegt: {child_text!r}",
        max_tokens=15,
        temperature=0.0,
    )
    return validated_classifier_label(
        result,
        {
            "REKENEN",
            "KEREN",
            "DELEN",
            "KLETSEN",
            "KLETSEN_ZELFDE",
            "KIES_JIJ",
            "STOP",
            "ONBEKEND",
        },
    )


def classify_choice_response(llm, child_text, question=None):
    """Classify a topic choice in the context of QT's actual question."""
    active_question = question or template.CHOICE_QUESTIONS[0]
    _, normalized = normalized_words(child_text)
    if normalized in {
        "ja",
        "jaa",
        "ja hoor",
        "yes",
        "is goed",
        "prima",
        "oke",
        "oké",
    }:
        return "ONBEKEND"
    result = llm.prompt(
        f"{template.INSTRUCTIONS}\n"
        f"{template.CHOICE_CLASSIFIER_1}\n"
        f"Huidige fase: CHOICE\n"
        f"Vraag van QT: {active_question!r}\n"
        f"Kind zegt: {child_text!r}",
        max_tokens=15,
        temperature=0.0,
    )
    return validated_classifier_label(
        result,
        {"KEREN", "DELEN", "KIES_JIJ", "STOP", "ONBEKEND"},
    )


def normalized_words(child_text):
    """Return lowercase words and their normalized spoken form."""
    words = re.findall(r"[^\W\d_]+", child_text.casefold(), re.UNICODE)
    return words, " ".join(words)


def matches_stretched_word(spoken_word, expected_word):
    """Match expressive repetitions such as 'hallooo' against 'hallo'."""
    stretched_pattern = "".join(
        f"{re.escape(character)}+" for character in expected_word
    )
    return re.fullmatch(stretched_pattern, spoken_word) is not None


def contains_loose_word(words, expected_words):
    """Match exact or naturally stretched forms from a prepared vocabulary."""
    return any(
        spoken == expected or matches_stretched_word(spoken, expected)
        for spoken in words
        for expected in expected_words
    )


def is_wake_greeting(child_text):
    """Recognize a greeting directed at QT without involving the LLM."""
    words, normalized = normalized_words(child_text)
    has_name = (
        contains_loose_word(words, template.WAKE_NAME_WORDS) or "q t" in normalized
    )
    has_greeting = contains_loose_word(words, template.WAKE_GREETING_WORDS)

    # Speech recognition often hears the robot's name as "cutie".  Its name
    # alone is a deliberate wake-up.  A short, ordinary greeting also wakes
    # QT so a child does not need to know or pronounce the robot's name.
    name_only = (
        len(words) == 1 and contains_loose_word(words, template.WAKE_NAME_WORDS)
    ) or normalized == "q t"
    short_greeting = has_greeting and len(words) <= 3
    return name_only or (has_name and has_greeting) or short_greeting


def classify_standby_input(child_text):
    """Recognize the few social intents QT supports while waiting."""
    words, normalized = normalized_words(child_text)
    word_set = set(words)
    padded = f" {normalized} "

    wants_practice = (
        bool(word_set & template.STANDBY_PRACTICE_ACTIVITY_WORDS)
        and bool(word_set & template.STANDBY_PRACTICE_INTENT_WORDS)
    ) or any(
        phrase in padded
        for phrase in template.STANDBY_PRACTICE_PHRASES
    )
    wants_chat = bool(word_set & template.STANDBY_CHAT_ACTIVITY_WORDS) and bool(
        word_set & template.STANDBY_PRACTICE_INTENT_WORDS
    )
    asks_about_qt = any(
        phrase in padded
        for phrase in (
            " wat doe jij hier ",
            " wat doe je hier ",
            " wat kun jij ",
            " wat kan jij ",
            " wat kun je ",
            " wie ben jij ",
            " wie ben je ",
        )
    )
    short_goodbye = (
        bool(word_set & template.STANDBY_GOODBYE_WORDS) and len(words) <= 4
    ) or normalized in {"tot ziens", "tot de volgende keer"}

    if wants_chat:
        return "CHAT"
    if wants_practice:
        return "PRACTICE"
    if asks_about_qt:
        return "ABOUT"
    if short_goodbye:
        return "GOODBYE"
    if is_wake_greeting(child_text):
        return "GREETING"
    return None


def classify_confirmation_response(llm, *, phase, question, child_text):
    """Classify agreement using the phase and exact preceding question."""
    result = llm.prompt(
        f"{template.INSTRUCTIONS}\n"
        f"{template.CONFIRMATION_CLASSIFIER}\n"
        f"Huidige fase: {phase}\n"
        f"Vraag van QT: {question!r}\n"
        f"Kind zegt: {child_text!r}",
        max_tokens=8,
        temperature=0.0,
    )
    return validated_classifier_label(result, {"YES", "NO", "ONBEKEND"})


def _capitalize_name(name_words):
    """Apply readable capitalization without relying on an external service."""
    particles = {"de", "den", "der", "van", "ter", "ten"}
    formatted = []
    for index, word in enumerate(name_words):
        lowered = word.casefold()
        formatted.append(lowered if index and lowered in particles else word.title())
    return " ".join(formatted)


def _validated_name_candidate(candidate, child_text):
    """Validate a proposed name without relying on an accepted-name list."""
    if not isinstance(candidate, str):
        return None
    candidate = candidate.strip().strip(" \t\r\n.,!?;:\"“”")
    name_words = re.findall(
        r"[^\W\d_]+(?:[-'][^\W\d_]+)*",
        candidate,
        re.UNICODE,
    )
    if not 1 <= len(name_words) <= 3 or len(candidate) > 40:
        return None

    extracted_spoken = normalized_words(candidate)[1]
    non_name_answers = {
        "ja",
        "jaa",
        "ja hoor",
        "yes",
        "nee",
        "nee hoor",
        "oke",
        "oké",
        "prima",
        "hallo",
        "hoi",
        "rekenen",
        "kletsen",
        "stop",
        "stoppen",
        "olifant",
        "tijger",
    }
    if not extracted_spoken or extracted_spoken in non_name_answers:
        return None

    # The candidate must occur in the child's own words, so an LLM can never
    # invent a plausible-looking name that was not actually supplied.
    child_spoken = f" {normalized_words(child_text)[1]} "
    if f" {extracted_spoken} " not in child_spoken:
        return None
    return _capitalize_name(name_words)


def _contextual_name_candidate(child_text):
    """Recover clear name-shaped answers in the explicit ASK_NAME context."""
    text = child_text.strip()
    explicit_patterns = (
        r"^ik\s+(?:heet|het)\s+(.+?)\s*[.!?]*$",
        r"^mijn\s+naam\s+is\s+(.+?)\s*[.!?]*$",
        r"^(?:je\s+mag\s+me|noem\s+me)\s+(.+?)\s*[.!?]*$",
    )
    for pattern in explicit_patterns:
        match = re.fullmatch(pattern, text, flags=re.IGNORECASE | re.UNICODE)
        if match:
            return _validated_name_candidate(match.group(1), child_text)

    # Keyboard input preserves capitalization, making a single title-cased
    # word a strong contextual signal. Lowercase ASR output still goes through
    # the LLM classifier and the same validation below.
    if re.fullmatch(r"[^\W\d_]+(?:[-'][^\W\d_]+)*[.!?]?", text, re.UNICODE):
        candidate = text.rstrip(".!?")
        if candidate[:1].isupper():
            return _validated_name_candidate(candidate, child_text)
    return None


def classify_name_response(llm, child_text, question=None):
    """Interpret a name answer through strict JSON and validate it in Python."""
    active_question = question or template.ASK_NAME_QUESTION
    _, normalized = normalized_words(child_text)
    ambiguous_answer = normalized in {
        "ja",
        "jaa",
        "ja hoor",
        "yes",
        "oke",
        "oké",
        "prima",
    }
    refusal = any(
        phrase in f" {normalized} "
        for phrase in (
            " liever niet ",
            " vertel ik liever niet ",
            " wil ik niet vertellen ",
            " dat zeg ik niet ",
            " geen naam ",
        )
    )
    contextual_candidate = _contextual_name_candidate(child_text)
    result = llm.prompt(
        f"{template.INSTRUCTIONS}\n"
        f"{template.NAME_RESPONSE_CLASSIFIER}\n"
        f"Huidige fase: ASK_NAME\n"
        f"Vraag van QT: {active_question!r}\n"
        f"Kind zegt: {child_text!r}",
        max_tokens=45,
        temperature=0.0,
    )
    if refusal:
        return "REFUSE", None
    if ambiguous_answer:
        return "UNCLEAR", None
    if not isinstance(result, str):
        return (
            ("NAME", contextual_candidate)
            if contextual_candidate is not None
            else ("UNCLEAR", None)
        )
    try:
        parsed = json.loads(result.strip())
    except (json.JSONDecodeError, TypeError):
        return (
            ("NAME", contextual_candidate)
            if contextual_candidate is not None
            else ("UNCLEAR", None)
        )
    if not isinstance(parsed, dict):
        return (
            ("NAME", contextual_candidate)
            if contextual_candidate is not None
            else ("UNCLEAR", None)
        )

    if contextual_candidate is not None:
        return "NAME", contextual_candidate

    intent = parsed.get("intent")
    name = parsed.get("name")
    if intent in {"REFUSE", "UNCLEAR"}:
        if name is not None:
            return "UNCLEAR", None
        return intent, None
    if intent != "NAME" or not isinstance(name, str):
        return "UNCLEAR", None

    validated_name = _validated_name_candidate(name, child_text)
    if validated_name is None:
        return "UNCLEAR", None
    return "NAME", validated_name


def classify_stop_request(child_text):
    """Return EXPLICIT, UNCERTAIN, or None for a possible stop request."""
    words = re.findall(r"[^\W\d_]+", child_text.casefold(), re.UNICODE)
    word_set = set(words)
    normalized = " ".join(words)
    padded = f" {normalized} "

    mentions_stop = bool(word_set & template.STOP_CONFIRMATION_STOP_WORDS) or any(
        phrase in padded
        for phrase in (
            " wil niet meer ",
            " geen zin meer ",
            " genoeg geweest ",
            " wil naar huis ",
            " moet naar huis ",
            " ben klaar ",
            " ik ga weer ",
            " ik ga weg ",
            " wil mijn mama ",
            " wil mama ",
            " wil mijn papa ",
            " wil papa ",
        )
    )
    mentions_continue = bool(
        word_set & template.STOP_CONFIRMATION_CONTINUE_WORDS
    ) or any(
        phrase in padded
        for phrase in (
            " nog een keer ",
            " verder wil ",
        )
    )
    uncertain = (
        "misschien" in word_set
        or "twijfel" in word_set
        or " weet niet of " in padded
        or normalized.startswith("zal ik ")
    )

    if uncertain and (mentions_stop or mentions_continue):
        return "UNCERTAIN"
    if mentions_stop:
        return "EXPLICIT"
    return None


def is_negative_feeling(child_text):
    """Recognize frustration that does not itself request stopping."""
    words, normalized = normalized_words(child_text)
    word_set = set(words)
    padded = f" {normalized} "
    negative_description = (
        bool(word_set & {"saai", "stom", "vervelend"})
        or re.search(r"\bniet(?: \w+){0,2} leuk\b", normalized) is not None
        or re.search(r"\bniet(?: \w+){0,2} fijn\b", normalized) is not None
    )
    return negative_description or any(
        phrase in padded
        for phrase in (
            " lukt me niet ",
            " lukt mij niet ",
        )
    )


def classify_stop_confirmation_response(llm, child_text, question=None):
    """Classify a stop confirmation as YES, NO, or ONBEKEND."""
    if classify_stop_request(child_text) == "EXPLICIT":
        return "YES"
    return classify_confirmation_response(
        llm,
        phase="STOP_CONFIRMATION",
        question=question or template.STOP_CONFIRMATION_QUESTION,
        child_text=child_text,
    )


def choose_nonrepeating_response(state, state_key, responses):
    """Choose prepared dialogue without repeating the previous variant."""
    previous = state.get(state_key)
    choices = tuple(response for response in responses if response != previous)
    response = random.choice(choices or tuple(responses))
    state[state_key] = response
    return response


def playful_off_topic_acknowledgement(state, child_text):
    """Acknowledge a harmless tangent without opening a new conversation."""
    words, _ = normalized_words(child_text)
    for word in words:
        prepared = template.PLAYFUL_OFF_TOPIC_ACKNOWLEDGEMENTS.get(word)
        if prepared:
            return prepared
    return choose_nonrepeating_response(
        state,
        "last_off_topic_acknowledgement",
        template.OFF_TOPIC_ACKNOWLEDGEMENTS,
    )


def clear_math_activity(state):
    """Clear only the current mathematics work, retaining visitor context."""
    state["topic"] = None
    state["exercise"] = None
    state.pop("explanation_solved", None)
    state.pop("harder_second_question", None)
    state.pop("multiplication_question_number", None)
    state.pop("division_question_number", None)
    state.pop("division_explanation_progress", None)
    state.pop("question_progress", None)
    state.pop("multiplication_explanation_progress", None)
    state.pop("stop_confirmation", None)


def return_to_choice(state):
    clear_math_activity(state)
    state["phase"] = "CHOICE"


def return_to_standby(state):
    """Reset one visitor's lesson while preserving exercise shuffle history."""
    active_keys = (
        "topic",
        "exercise",
        "explanation_solved",
        "harder_second_question",
        "multiplication_question_number",
        "division_question_number",
        "division_explanation_progress",
        "question_progress",
        "multiplication_explanation_progress",
        "stop_confirmation",
        "invitation_unclear",
        "after_practice_unclear",
        "active_choice_question",
        "active_invitation_greeting",
        "active_ask_name_question",
        "ask_name_unclear_count",
        "neutral_choice_question",
        "name_used_in_practice_session",
        "activity",
        "pending_activity",
        "pending_math_topic",
        "name_phase_complete",
        "introduction_given",
        "activity_choice_context",
        "active_activity_question",
        "chat_history",
        "current_chat_topic",
        "last_chat_topic_category",
        "used_chat_topics",
        "chat_question_count",
        "chat_pending_clarification",
        "chat_opening_request",
        "continue_same_chat_topic",
    )
    for key in active_keys:
        state.pop(key, None)
    state["topic"] = None
    state["exercise"] = None
    state["child_name"] = None
    state["phase"] = "STANDBY"


def return_to_after_practice(state):
    """Clear lesson-local state before asking about another session."""
    return_to_choice(state)
    state["phase"] = "AFTER_PRACTICE"


def classify_child_response(llm, question, child_text, current_topic=None):
    """Use Python for bare numbers and the LLM only for other intentions."""
    if is_simple_numeric_answer(child_text):
        response_type = "ANTWOORD"
    else:
        result = llm.prompt(
            f"{template.INSTRUCTIONS}\n"
            f"{template.QUESTION_1_CLASSIFIER}\n"
            f"Huidig rekenonderwerp: {current_topic or 'ONBEKEND'}\n"
            f"Rekensom: {question}\n"
            f"Kind zegt: {child_text!r}",
            max_tokens=15,
            temperature=0.0,
        )
        response_type = validated_classifier_label(
            result,
            {
                "ANTWOORD",
                "HULP",
                "HERHAAL",
                "WISSEL_KEREN",
                "WISSEL_DELEN",
                "WISSEL_KLETSEN",
                "SLA_OVER",
                "ANDER_ONDERWERP",
                "ONVERSTAANBAAR_ANTWOORD",
                "OFF_TOPIC",
                "STOP",
                "ONBEKEND",
            },
        )
        # Keep genuine non-answer intents. Recover a deterministic number when
        # the label is uncertain, or when the wording clearly presents that
        # number as an answer despite an OFF_TOPIC misclassification.
        has_number = extract_answer_number(child_text) is not None
        if has_number and (
            response_type in {"ONBEKEND", "ONVERSTAANBAAR_ANTWOORD"}
            or (
                response_type == "OFF_TOPIC"
                and looks_like_numeric_answer_expression(child_text)
            )
        ):
            response_type = "ANTWOORD"

    return response_type


def generate_supportive_feedback(
    llm,
    feedback_instructions,
    *,
    question,
    child_text,
    hint=None,
):
    """Let the language model phrase feedback; Python controls correctness."""
    context = [
        f"Rekensom: {question}",
        f"Poging van het kind: {child_text!r}",
        f"Voorwerp voor het voorbeeld: {random.choice(template.FEEDBACK_EXAMPLE_OBJECTS)}",
    ]
    if hint is not None:
        context.append(f"Leerhint: {hint}")

    feedback = llm.prompt(
        f"{template.INSTRUCTIONS}\n"
        f"{template.SUPPORTIVE_FEEDBACK}\n"
        f"{feedback_instructions}\n"
        + "\n".join(context),
        max_tokens=60,
        temperature=0.2,
    ).strip()
    return keep_first_question(feedback)


def keep_first_question(feedback):
    """Discard anything the LLM adds after the required question."""
    feedback = re.sub(r"([.!?])(?=[^\s.!?])", r"\1 ", feedback.strip())
    question_end = feedback.find("?")
    if question_end == -1:
        return feedback
    return feedback[:question_end + 1].strip()


def generate_final_explanation(llm, *, addition, expression, answer):
    """Explain the known answer gently after the easier question also failed."""
    return llm.prompt(
        f"{template.INSTRUCTIONS}\n"
        f"{template.FINAL_EXPLANATION}\n"
        f"Vereenvoudigde som: {addition}\n"
        f"Oorspronkelijke som: {expression}\n"
        f"Rekenverband: {addition} is {answer}, dus {expression} is {answer}.",
        max_tokens=60,
        temperature=0.2,
    ).strip()


def go_to_keren_explanation(state, *, solved, qualifies_for_harder=False):
    state["explanation_solved"] = solved
    state["harder_second_question"] = qualifies_for_harder
    state["phase"] = "KEREN_EXPLANATION"


def go_to_division_explanation(state, *, solved, qualifies_for_harder=False):
    state.pop("division_explanation_progress", None)
    state["explanation_solved"] = solved
    state["harder_second_question"] = qualifies_for_harder
    state["phase"] = "DIVISION_EXPLANATION"


# Compatibility alias for older research scripts. Live conversations choose a
# non-repeating variant from the dialogue templates.
CHOICE_QUESTION = template.CHOICE_QUESTIONS[0]


def make_initial_state():
    """Create the private mutable state used by one tutor conversation."""
    return {
        "phase": "STANDBY",
        "outer_conversation": True,
        "child_name": None,
        "name_phase_complete": False,
        "activity": None,
        "pending_activity": None,
        "pending_math_topic": None,
        "topic": None,
        "exercise": None,
        "chat_history": [],
        "current_chat_topic": None,
        "chat_question_count": 0,
        "used_chat_topics": [],
        "remaining_multiplication_indices": [],
        "last_multiplication_index": None,
        "last_multiplication_fact_key": None,
        "last_correct_response": None,
        "remaining_division_indices": [],
        "last_division_index": None,
        "last_correct_division_response": None,
    }


@dataclass(frozen=True)
class TutorTurn:
    """Ordered output produced after one engine operation."""

    utterances: tuple[str, ...]
    diagnostics: tuple[str, ...] = ()
    status: str = "waiting_for_input"

    @property
    def awaiting_input(self):
        return self.status == "waiting_for_input"

    @property
    def is_done(self):
        return self.status == "done"


class TutorEngine:
    """Stateful, interface-independent QT activity engine.

    The engine owns activity transitions, mathematics decisions, and child-safe
    chat cycles. It never performs terminal, microphone, or robot I/O.
    """

    def __init__(self, llm, *, state=None):
        self.llm = llm
        self._state = make_initial_state() if state is None else state
        self._started = False
        self._entry_pending = True

    @property
    def is_done(self):
        return self._state["phase"] == "DONE"

    @property
    def state(self):
        """Expose state for diagnostics and legacy tests, not adapter decisions."""
        return self._state

    def start(self):
        if self._started:
            raise RuntimeError("TutorEngine.start() may only be called once")
        self._started = True
        self._entry_pending = True
        utterances = []
        self._settle(utterances)
        return self._turn(utterances)

    def start_current_phase(self, *, follow_transitions=False):
        """Enter a supplied legacy state without introducing alternate logic."""
        if self._started:
            raise RuntimeError("This TutorEngine has already been started")
        self._started = True
        self._entry_pending = True
        utterances = []
        self._settle(utterances, follow_transitions=follow_transitions)
        return self._turn(utterances)

    def handle_input(self, child_text, *, follow_transitions=True):
        if not self._started:
            raise RuntimeError("Call start() before handle_input()")
        if self.is_done:
            return self._turn(())
        if self._entry_pending:
            raise RuntimeError("The current tutor phase has not been entered")

        child_text = child_text.strip()
        utterances = []
        diagnostics = []
        phase = self._state["phase"]

        if phase == "STANDBY":
            self._handle_standby(child_text, utterances, diagnostics)
        elif phase == "ACTIVITY_CHOICE":
            self._handle_activity_choice(child_text, utterances, diagnostics)
        elif phase == "INVITATION":
            self._handle_invitation(child_text, utterances, diagnostics)
        elif phase == "ASK_NAME":
            self._handle_ask_name(child_text, utterances, diagnostics)
        elif phase == "CHOICE":
            self._handle_choice(child_text, utterances, diagnostics)
        elif phase == "QUESTION_1":
            self._handle_question(child_text, utterances, diagnostics)
        elif phase == "KEREN_EXPLANATION":
            self._handle_multiplication_explanation(
                child_text,
                utterances,
                diagnostics,
            )
        elif phase == "DIVISION_EXPLANATION":
            self._handle_division_explanation(
                child_text,
                utterances,
                diagnostics,
            )
        elif phase == "STOP_CONFIRMATION":
            self._handle_stop_confirmation(
                child_text,
                utterances,
                diagnostics,
            )
        elif phase == "AFTER_PRACTICE":
            self._handle_after_practice(child_text, utterances, diagnostics)
        elif phase == "KLETSEN":
            self._handle_chat(child_text, utterances, diagnostics)
        else:
            raise RuntimeError(f"Unknown conversation phase: {phase}")

        if follow_transitions:
            self._settle(utterances)
        return self._turn(utterances, diagnostics)

    def _enter_activity_choice(self, utterances):
        state = self._state
        if not state.get("introduction_given"):
            introduction = choose_nonrepeating_response(
                state,
                "last_introduction_variant",
                template.INTRODUCTION_VARIANTS,
            )
            utterances.extend(introduction)
            state["introduction_given"] = True
            state["active_activity_question"] = introduction[-1]
            if state.get("pending_activity"):
                if state.get("name_phase_complete"):
                    self._start_selected_activity(utterances)
                else:
                    self._set_phase("ASK_NAME")
            return

        context = state.pop("activity_choice_context", None)
        questions = (
            template.CHAT_CYCLE_CHOICE_QUESTIONS
            if context == "CHAT_END"
            else template.ACTIVITY_CHOICE_QUESTIONS
        )
        question = choose_nonrepeating_response(
            state,
            "last_activity_choice_question",
            questions,
        )
        state["active_activity_question"] = question
        utterances.append(question)

    def _handle_activity_choice(self, child_text, utterances, diagnostics):
        active_question = self._state.get(
            "active_activity_question",
            template.ACTIVITY_CHOICE_QUESTIONS[0],
        )
        if self._intercept_stop_request(
            child_text,
            utterances,
            diagnostics,
            resume_phase="ACTIVITY_CHOICE",
            resume_utterances=(active_question,),
        ):
            return

        choice = classify_activity_response(
            self.llm,
            child_text,
            question=active_question,
        )
        diagnostics.append(f"[ACTIVITY] {choice}")
        if choice == "STOP":
            self._finish_stop(utterances)
            return
        if choice == "KIES_JIJ":
            choice = random.choice(("REKENEN", "KLETSEN"))
        if choice == "ONBEKEND":
            utterances.append(template.ACTIVITY_CHOICE_CLARIFICATION)
            self._state["active_activity_question"] = (
                template.ACTIVITY_CHOICE_CLARIFICATION
            )
            return

        self._queue_activity_choice(choice, child_text, utterances)

    def _queue_activity_choice(self, choice, child_text, utterances):
        state = self._state
        state.pop("active_activity_question", None)
        if choice in {"REKENEN", "KEREN", "DELEN"}:
            state["pending_activity"] = "REKENEN"
            state["pending_math_topic"] = (
                choice if choice in AVAILABLE_TOPICS else None
            )
        else:
            state["pending_activity"] = "KLETSEN"
            state["continue_same_chat_topic"] = choice == "KLETSEN_ZELFDE"
            state["chat_opening_request"] = child_text

        if not state.get("name_phase_complete"):
            self._set_phase("ASK_NAME")
            return
        self._start_selected_activity(utterances)

    def _start_selected_activity(self, utterances, *, announce=True):
        state = self._state
        activity = state.pop("pending_activity", None) or "REKENEN"
        if activity == "KLETSEN":
            self._start_chat_activity(utterances, announce=announce)
            return

        state["activity"] = "REKENEN"
        state.pop("continue_same_chat_topic", None)
        state.pop("chat_opening_request", None)
        math_topic = state.pop("pending_math_topic", None)
        if math_topic in AVAILABLE_TOPICS:
            self._start_topic_session(math_topic, utterances)
            return

        clear_math_activity(state)
        if announce:
            utterances.append(template.MATH_ACTIVITY_START)
        state["neutral_choice_question"] = True
        self._set_phase("CHOICE")

    def _start_chat_activity(self, utterances, *, announce=True):
        state = self._state
        clear_math_activity(state)
        state["activity"] = "KLETSEN"
        state.pop("pending_activity", None)
        state.pop("pending_math_topic", None)
        if announce:
            utterances.append(template.CHAT_ACTIVITY_START)
        self._set_phase("KLETSEN")

    def _enter_chat(self, utterances):
        state = self._state
        same_topic = state.pop("continue_same_chat_topic", False)
        opening_request = state.get("chat_opening_request")
        if opening_request and contains_private_information(opening_request):
            utterances.append(template.CHAT_PRIVATE_INFORMATION_RESPONSE)
            state.pop("chat_opening_request", None)
        elif opening_request and is_sensitive_disclosure(opening_request):
            utterances.append(template.CHAT_SENSITIVE_RESPONSE)
            state.pop("chat_opening_request", None)
        current_topic = state.get("current_chat_topic")
        if same_topic and current_topic is not None:
            # This is a fresh 3-4 question cycle about a different aspect of
            # the same subject, not a continuation of the exhausted budget.
            self._start_chat_topic(current_topic)
        else:
            topic = self._choose_new_chat_topic()
            self._start_chat_topic(topic)
        generated_opening = self._generate_chat_opening(same_topic=same_topic)
        if generated_opening is None:
            topic = state["current_chat_topic"]
            opening = template.CHAT_TOPIC_OPENING_FALLBACKS.get(
                topic,
                "Waar wil jij graag over kletsen?",
            )
        else:
            opening, actual_topic = generated_opening
            if actual_topic != state.get("current_chat_topic"):
                self._start_chat_topic(actual_topic)
        utterances.append(opening)
        self._append_chat_history("qt", opening)
        if _contains_spoken_question(opening):
            state["chat_question_count"] = state.get("chat_question_count", 0) + 1

    def _choose_new_chat_topic(self):
        state = self._state
        current = (state.get("current_chat_topic") or "").casefold()
        previous = (state.get("last_chat_topic_category") or "").casefold()
        used = {
            topic.casefold() for topic in state.get("used_chat_topics", [])
        }
        available = [
            topic
            for topic in template.CHAT_TOPIC_CATEGORIES
            if topic.casefold() not in {current, previous}
            and topic.casefold() not in used
        ]
        if not available:
            available = [
                topic
                for topic in template.CHAT_TOPIC_CATEGORIES
                if topic.casefold() not in {current, previous}
            ]
        return random.choice(available or template.CHAT_TOPIC_CATEGORIES)

    def _start_chat_topic(self, topic):
        state = self._state
        state["current_chat_topic"] = topic
        state["chat_question_count"] = 0
        state.pop("chat_pending_clarification", None)
        if topic in template.CHAT_TOPIC_CATEGORIES:
            state["last_chat_topic_category"] = topic
            used = state.setdefault("used_chat_topics", [])
            if topic not in used:
                used.append(topic)

    def _generate_chat_opening(self, *, same_topic=False):
        state = self._state
        topic = state.get("current_chat_topic")
        request = state.pop("chat_opening_request", None) or "(geen specifieke wens)"
        prompt = (
            f"{template.INSTRUCTIONS}\n"
            f"{template.CHAT_TOPIC_OPENING_INSTRUCTIONS}\n\n"
            f"GEKOZEN ONDERWERP: {topic}\n"
            f"WENS VAN HET KIND BIJ DE ACTIVITEITSKEUZE: {request}\n"
            f"NIEUWE CYCLUS OVER HETZELFDE ONDERWERP: "
            f"{'ja; kies een ander aspect' if same_topic else 'nee'}\n\n"
            f"GESPREK TOT NU TOE:\n{self._chat_history_for_prompt()}"
        )
        try:
            result = self.llm.prompt(
                prompt,
                max_tokens=100,
                temperature=0.55,
            )
        except Exception:
            return None
        parsed = _extract_json_object(result)
        reply = _clean_spoken_reply(parsed.get("reply")) if parsed else None
        actual_topic = _clean_chat_topic(parsed.get("topic")) if parsed else None
        actual_topic = actual_topic or topic
        if (
            reply is None
            or not _contains_spoken_question(reply)
            or reply_requests_private_information(reply)
            or contains_unsafe_generated_content(reply)
            or chat_opening_repeats_introduction(reply)
            or actual_topic is None
            or contains_unsafe_generated_content(actual_topic)
        ):
            return None
        return reply, actual_topic

    def _handle_chat(self, child_text, utterances, diagnostics):
        """Handle one safe, bounded conversational turn."""
        state = self._state

        if not child_text:
            diagnostics.append("[CHAT] UNCLEAR_EMPTY")
            utterances.append(template.CHAT_CLARIFICATION)
            return

        # Do not put private or sensitive disclosures in the short history or
        # send them to the model. Safety takes priority over the legacy stop
        # phrase matcher: for example, "ik wil niet meer leven" must never
        # receive a routine goodbye. A literal stop request is still honored
        # immediately after the fixed, non-intrusive safety response.
        if contains_private_information(child_text):
            diagnostics.append("[CHAT] PRIVATE_INFORMATION")
            utterances.append(template.CHAT_PRIVATE_INFORMATION_RESPONSE)
            return
        if is_sensitive_disclosure(child_text):
            diagnostics.append("[CHAT] SENSITIVE")
            utterances.append(template.CHAT_SENSITIVE_RESPONSE)
            words, _ = normalized_words(child_text)
            if set(words) & template.STOP_CONFIRMATION_STOP_WORDS:
                self._finish_stop(utterances)
            return

        # A clear request to end the interaction always wins over every
        # activity or topic transition proposed by the language model.
        if classify_stop_request(child_text) == "EXPLICIT":
            diagnostics.append("[CHAT] STOP")
            self._finish_stop(utterances)
            return

        response, validation_error = self._generate_chat_response(child_text)
        if response is None:
            diagnostics.append(f"[CHAT] FALLBACK:{validation_error}")
            utterances.append(template.CHAT_RESPONSE_FALLBACK)
            self._finish_chat_topic()
            return

        intent = response["intent"]
        diagnostics.append(f"[CHAT] {intent}")
        if intent == "STOP":
            self._finish_stop(utterances)
            return
        if intent in {"SWITCH_REKENEN", "SWITCH_KEREN", "SWITCH_DELEN"}:
            self._switch_chat_to_math(intent, utterances)
            return
        if intent == "SENSITIVE":
            utterances.append(template.CHAT_SENSITIVE_RESPONSE)
            return
        if intent == "UNCLEAR":
            utterances.append(response["reply"] or template.CHAT_CLARIFICATION)
            return

        self._append_chat_history("child", child_text)
        if intent == "TOPIC_CHANGE":
            self._start_chat_topic(response["topic"])

        reply = response["reply"]
        question_count_before = state.get("chat_question_count", 0)
        is_proactive_question = (
            intent in {"CONTINUE", "TOPIC_CHANGE"}
            and reply is not None
            and _contains_spoken_question(reply)
        )

        # Once four proactive questions have been answered, Python removes any
        # extra model-generated question and turns this into a natural closing
        # statement. Direct answers and clarifications never consume budget.
        if (
            is_proactive_question
            and question_count_before >= MAX_CHAT_TOPIC_QUESTIONS
        ):
            reply = _without_spoken_questions(reply)
            is_proactive_question = False
            response["cycle_complete"] = True
        if reply is None:
            reply = template.CHAT_CYCLE_CLOSING_FALLBACK

        utterances.append(reply)
        self._append_chat_history("qt", reply)
        if is_proactive_question:
            state["chat_question_count"] = question_count_before + 1

        question_count = state.get("chat_question_count", 0)
        natural_rest = (
            intent == "CONTINUE"
            and question_count >= MIN_CHAT_TOPIC_QUESTIONS
            and not _contains_spoken_question(reply)
        )
        if not _contains_spoken_question(reply) and (
            question_count >= MAX_CHAT_TOPIC_QUESTIONS
            or (response["cycle_complete"] and question_count >= MIN_CHAT_TOPIC_QUESTIONS)
            or natural_rest
        ):
            self._finish_chat_topic()

    def _generate_chat_response(self, child_text):
        """Generate and validate one JSON chat action, retrying format once."""
        state = self._state
        question_count = state.get("chat_question_count", 0)
        budget_note = (
            "Er zijn al vier proactieve vragen gesteld. Stel nu geen nieuwe vraag."
            if question_count >= MAX_CHAT_TOPIC_QUESTIONS
            else (
                f"Er zijn {question_count} van maximaal "
                f"{MAX_CHAT_TOPIC_QUESTIONS} proactieve vragen gesteld."
            )
        )
        base_prompt = (
            f"{template.INSTRUCTIONS}\n"
            f"{template.CHAT_RESPONSE_INSTRUCTIONS}\n\n"
            f"HUIDIG ONDERWERP: {state.get('current_chat_topic')}\n"
            f"VRAGEN IN DEZE CYCLUS: {question_count}\n"
            f"BUDGETREGEL: {budget_note}\n\n"
            f"GESPREK TOT NU TOE:\n{self._chat_history_for_prompt()}\n\n"
            f"KIND ZEGT NU: {child_text!r}"
        )
        last_error = "generation_error"
        for attempt in range(2):
            prompt = base_prompt
            if attempt:
                prompt = f"{base_prompt}\n\n{template.CHAT_FORMAT_RETRY}"
            try:
                raw_response = self.llm.prompt(
                    prompt,
                    max_tokens=180,
                    temperature=0.45 if attempt == 0 else 0.1,
                )
            except Exception:
                last_error = "generation_error"
                continue
            response, last_error = _validate_chat_response(raw_response)
            if response is not None:
                return response, None
        return None, last_error

    def _append_chat_history(self, role, text):
        """Keep a small in-memory window for this visitor only."""
        if role not in {"child", "qt"} or not isinstance(text, str):
            return
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            return
        history = self._state.setdefault("chat_history", [])
        history.append({"role": role, "text": cleaned[:300]})
        del history[:-10]

    def _chat_history_for_prompt(self):
        history = self._state.get("chat_history", [])[-10:]
        if not history:
            return "(nog geen eerdere gespreksbeurten)"
        labels = {"child": "Kind", "qt": "QT"}
        return "\n".join(
            f"{labels.get(item.get('role'), 'QT')}: {item.get('text', '')}"
            for item in history
        )

    def _finish_chat_topic(self):
        self._state["activity_choice_context"] = "CHAT_END"
        self._set_phase("ACTIVITY_CHOICE")

    def _switch_chat_to_math(self, intent, utterances):
        utterances.append(template.CHAT_TO_MATH_RESPONSE)
        self._state["activity"] = "REKENEN"
        if intent == "SWITCH_KEREN":
            self._start_topic_session("KEREN", utterances, announce=False)
        elif intent == "SWITCH_DELEN":
            self._start_topic_session("DELEN", utterances, announce=False)
        else:
            clear_math_activity(self._state)
            self._state["neutral_choice_question"] = True
            self._set_phase("CHOICE")

    def _turn(self, utterances, diagnostics=()):
        return TutorTurn(
            utterances=tuple(utterances),
            diagnostics=tuple(diagnostics),
            status="done" if self.is_done else "waiting_for_input",
        )

    def _set_phase(self, phase):
        self._state["phase"] = phase
        self._entry_pending = phase != "DONE"

    def _settle(self, utterances, *, follow_transitions=True):
        while self._entry_pending and not self.is_done:
            self._entry_pending = False
            phase_before = self._state["phase"]
            self._enter_current_phase(utterances)
            if not follow_transitions or self._state["phase"] == phase_before:
                break

    def _enter_current_phase(self, utterances):
        phase = self._state["phase"]
        if phase == "STANDBY":
            # STANDBY deliberately produces no speech.  The adapters will
            # immediately listen for a greeting and keep the process alive.
            return
        if phase == "ACTIVITY_CHOICE":
            self._enter_activity_choice(utterances)
        elif phase == "INVITATION":
            greeting = choose_nonrepeating_response(
                self._state,
                "last_invitation_greeting",
                template.INVITATION_GREETING_RESPONSES,
            )
            self._state["active_invitation_greeting"] = greeting
            utterances.append(greeting)
        elif phase == "ASK_NAME":
            pending_activity = self._state.get("pending_activity")
            if pending_activity == "KLETSEN":
                question_pool = template.ASK_NAME_CHAT_QUESTIONS
            elif pending_activity == "REKENEN":
                question_pool = template.ASK_NAME_MATH_QUESTIONS
            else:
                question_pool = template.ASK_NAME_QUESTIONS
            question = choose_nonrepeating_response(
                self._state,
                "last_ask_name_question",
                question_pool,
            )
            self._state["active_ask_name_question"] = question
            utterances.append(question)
        elif phase == "CHOICE":
            question_pool = (
                template.NEUTRAL_CHOICE_QUESTIONS
                if self._state.pop("neutral_choice_question", False)
                else template.CHOICE_QUESTIONS
            )
            question = choose_nonrepeating_response(
                self._state,
                "last_choice_question",
                question_pool,
            )
            self._state["active_choice_question"] = question
            utterances.append(question)
        elif phase == "QUESTION_1":
            self._enter_question(utterances)
        elif phase == "KEREN_EXPLANATION":
            self._enter_multiplication_explanation(utterances)
        elif phase == "DIVISION_EXPLANATION":
            self._enter_division_explanation(utterances)
        elif phase == "STOP_CONFIRMATION":
            confirmation = self._state.get("stop_confirmation")
            if confirmation is None:
                raise RuntimeError("Missing stop-confirmation context")
            utterances.append(confirmation["question"])
        elif phase == "AFTER_PRACTICE":
            utterances.append(template.AFTER_PRACTICE_QUESTION)
        elif phase == "KLETSEN":
            self._enter_chat(utterances)
        else:
            raise RuntimeError(f"Unknown conversation phase: {phase}")

    def _finish_stop(self, utterances, *, fallback=False):
        if self._state.get("child_name"):
            utterances.append(self._make_named_farewell())
        else:
            if fallback:
                responses = template.STOP_UNCLEAR_FALLBACK_RESPONSES
            elif self._state.get("activity") == "KLETSEN":
                responses = template.CHAT_FAREWELL_RESPONSES
            else:
                responses = template.STOP_FAREWELL_RESPONSES
            utterances.append(
                choose_nonrepeating_response(
                    self._state,
                    "last_stop_farewell",
                    responses,
                )
            )
        self._state.pop("stop_confirmation", None)
        return_to_standby(self._state)
        self._entry_pending = False

    def _make_named_farewell(self):
        """Build the farewell before STANDBY clears the visitor's name."""
        prefixes = (
            template.PERSONALIZED_CHAT_FAREWELL_PREFIXES
            if self._state.get("activity") == "KLETSEN"
            else template.PERSONALIZED_FAREWELL_PREFIXES
        )
        prefix = choose_nonrepeating_response(
            self._state,
            "last_named_farewell_prefix",
            prefixes,
        )
        ending = choose_nonrepeating_response(
            self._state,
            "last_named_farewell_ending",
            template.PERSONALIZED_FAREWELL_ENDINGS,
        )
        return f"{prefix}, {self._state['child_name']}! {ending}"

    def _choose_correct_praise(self, topic):
        """Use the name at most once in each two-question practice session."""
        child_name = self._state.get("child_name")
        if child_name and not self._state.get("name_used_in_practice_session"):
            prefix = choose_nonrepeating_response(
                self._state,
                "last_personalized_praise_prefix",
                template.PERSONALIZED_PRAISE_PREFIXES,
            )
            self._state["name_used_in_practice_session"] = True
            return f"{prefix}, {child_name}!"
        if topic == "KEREN":
            return choose_correct_multiplication_response(self._state)
        return choose_correct_division_response(self._state)

    def _handle_standby(self, child_text, utterances, diagnostics):
        """Remain silent until someone deliberately greets QT."""
        standby_intent = classify_standby_input(child_text)
        if standby_intent is None:
            return
        diagnostic_name = {
            "GREETING": "Greeting",
            "PRACTICE": "Practice request",
            "CHAT": "Chat request",
            "ABOUT": "Question",
            "GOODBYE": "Goodbye",
        }[standby_intent]
        diagnostics.append(f"[STANDBY] {diagnostic_name} recognized")

        if standby_intent == "PRACTICE":
            self._state["pending_activity"] = "REKENEN"
            words, _ = normalized_words(child_text)
            if any(word in {"keren", "tafels", "keersommen"} for word in words):
                self._state["pending_math_topic"] = "KEREN"
            elif any(word in {"delen", "verdelen", "deelsommen"} for word in words):
                self._state["pending_math_topic"] = "DELEN"
            self._set_phase("ACTIVITY_CHOICE")
        elif standby_intent == "CHAT":
            self._state["pending_activity"] = "KLETSEN"
            self._state["chat_opening_request"] = child_text
            self._set_phase("ACTIVITY_CHOICE")
        elif standby_intent == "ABOUT":
            self._set_phase("ACTIVITY_CHOICE")
        elif standby_intent == "GOODBYE":
            utterances.append(
                choose_nonrepeating_response(
                    self._state,
                    "last_standby_goodbye",
                    template.STANDBY_GOODBYE_RESPONSES,
                )
            )
        else:
            self._set_phase("ACTIVITY_CHOICE")

    def _handle_invitation(self, child_text, utterances, diagnostics):
        """Ask for agreement before entering the mathematics tutor."""
        active_greeting = self._state.get("active_invitation_greeting")
        if active_greeting is None:
            raise RuntimeError("Missing generated invitation greeting")
        active_question = (
            template.INVITATION_CLARIFICATION
            if self._state.get("invitation_unclear")
            else active_greeting
        )
        if self._intercept_stop_request(
            child_text,
            utterances,
            diagnostics,
            resume_phase="INVITATION",
            resume_utterances=(active_question,),
        ):
            return

        decision = classify_confirmation_response(
            self.llm,
            phase="INVITATION",
            question=active_question,
            child_text=child_text,
        )
        diagnostics.append(f"[CLASSIFIER] {decision}")
        if decision == "YES":
            self._state.pop("invitation_unclear", None)
            self._set_phase("ASK_NAME")
            return
        if decision == "NO":
            utterances.append(
                choose_nonrepeating_response(
                    self._state,
                    "last_invitation_decline",
                    template.INVITATION_DECLINE_RESPONSES,
                )
            )
            return_to_standby(self._state)
            self._entry_pending = False
            return

        self._state["invitation_unclear"] = True
        utterances.append(template.INVITATION_CLARIFICATION)

    def _handle_ask_name(self, child_text, utterances, diagnostics):
        """Remember an optional name locally for only the current visitor."""
        active_question = (
            template.ASK_NAME_CLARIFICATION
            if self._state.get("ask_name_unclear_count", 0)
            else self._state.get("active_ask_name_question")
        )
        if active_question is None:
            raise RuntimeError("Missing active name question")
        if self._intercept_stop_request(
            child_text,
            utterances,
            diagnostics,
            resume_phase="ASK_NAME",
            resume_utterances=(active_question,),
        ):
            return

        decision, child_name = classify_name_response(
            self.llm,
            child_text,
            question=active_question,
        )
        diagnostics.append(f"[NAME] {decision}")
        if decision == "NAME":
            self._state["child_name"] = child_name
            self._state.pop("ask_name_unclear_count", None)
            self._state.pop("active_ask_name_question", None)
            acknowledgement = choose_nonrepeating_response(
                self._state,
                "last_name_acknowledgement",
                template.NAME_ACKNOWLEDGEMENT_RESPONSES,
            )
            utterances.append(acknowledgement.format(name=child_name))
            self._state["name_phase_complete"] = True
            # The contextual name question already introduced the selected
            # activity. Suppress a second "Dan gaan we..." announcement.
            self._start_selected_activity(utterances, announce=False)
            return

        if decision == "REFUSE":
            self._continue_without_name(utterances)
            return

        unclear_count = self._state.get("ask_name_unclear_count", 0) + 1
        self._state["ask_name_unclear_count"] = unclear_count
        if unclear_count == 1:
            utterances.append(template.ASK_NAME_CLARIFICATION)
            return
        self._continue_without_name(utterances)

    def _continue_without_name(self, utterances):
        pending_activity = self._state.get("pending_activity")
        self._state["child_name"] = None
        self._state["name_phase_complete"] = True
        self._state.pop("ask_name_unclear_count", None)
        self._state.pop("active_ask_name_question", None)
        if pending_activity == "KLETSEN":
            transition = template.ASK_NAME_WITHOUT_NAME_CHAT
        elif pending_activity == "REKENEN":
            transition = template.ASK_NAME_WITHOUT_NAME_MATH
        else:
            transition = template.ASK_NAME_WITHOUT_NAME
        utterances.append(transition)
        self._start_selected_activity(utterances, announce=False)

    def _handle_after_practice(self, child_text, utterances, diagnostics):
        """Offer mathematics, chatting, or a graceful end at a round boundary."""
        active_question = (
            template.AFTER_PRACTICE_CLARIFICATION
            if self._state.get("after_practice_unclear")
            else template.AFTER_PRACTICE_QUESTION
        )
        if self._intercept_stop_request(
            child_text,
            utterances,
            diagnostics,
            resume_phase="AFTER_PRACTICE",
            resume_utterances=(active_question,),
        ):
            return

        decision = classify_activity_response(
            self.llm,
            question=active_question,
            child_text=child_text,
        )
        diagnostics.append(f"[ACTIVITY] {decision}")
        if decision == "KIES_JIJ":
            decision = random.choice(("REKENEN", "KLETSEN"))
        if decision in {"REKENEN", "KEREN", "DELEN", "KLETSEN", "KLETSEN_ZELFDE"}:
            self._state.pop("after_practice_unclear", None)
            self._queue_activity_choice(decision, child_text, utterances)
            return
        if decision == "STOP":
            self._finish_after_practice(utterances)
            return

        self._state["after_practice_unclear"] = True
        utterances.append(template.AFTER_PRACTICE_CLARIFICATION)

    def _finish_after_practice(self, utterances):
        if self._state.get("child_name"):
            utterances.append(self._make_named_farewell())
        else:
            utterances.append(
                choose_nonrepeating_response(
                    self._state,
                    "last_after_practice_farewell",
                    template.AFTER_PRACTICE_FAREWELLS,
                )
            )
        return_to_standby(self._state)
        self._entry_pending = False

    def _intercept_stop_request(
        self,
        child_text,
        utterances,
        diagnostics,
        *,
        resume_phase,
        resume_utterances,
        support_utterances=None,
    ):
        """Handle explicit stopping, uncertainty, and negative feelings."""
        stop_request = classify_stop_request(child_text)
        if stop_request == "EXPLICIT":
            diagnostics.append("[CLASSIFIER] STOP")
            self._finish_stop(utterances)
            return True
        if stop_request == "UNCERTAIN":
            diagnostics.append("[CLASSIFIER] STOP_ONZEKER")
            self._request_stop_confirmation(
                utterances,
                resume_phase=resume_phase,
                resume_utterances=resume_utterances,
            )
            return True
        if is_negative_feeling(child_text):
            diagnostics.append("[CLASSIFIER] NEGATIEF_GEVOEL")
            supportive_resume = support_utterances or (
                template.FEELING_CONTINUE_PREFIX,
                *tuple(resume_utterances),
            )
            self._request_stop_confirmation(
                utterances,
                resume_phase=resume_phase,
                resume_utterances=resume_utterances,
                question=template.NEGATIVE_FEELING_STOP_QUESTION,
                continue_utterances=supportive_resume,
            )
            return True
        return False

    def _request_stop_confirmation(
        self,
        utterances,
        *,
        resume_phase,
        resume_utterances,
        question=None,
        continue_utterances=None,
    ):
        """Pause the lesson without changing its exercise or attempt state."""
        self._state["stop_confirmation"] = {
            "resume_phase": resume_phase,
            "resume_utterances": tuple(resume_utterances),
            "continue_utterances": (
                tuple(continue_utterances)
                if continue_utterances is not None
                else None
            ),
            "question": question or template.STOP_CONFIRMATION_QUESTION,
            "unclear_count": 0,
        }
        self._state["phase"] = "STOP_CONFIRMATION"
        self._entry_pending = False
        utterances.append(question or template.STOP_CONFIRMATION_QUESTION)

    def _handle_stop_confirmation(self, child_text, utterances, diagnostics):
        confirmation = self._state["stop_confirmation"]
        decision = classify_stop_confirmation_response(
            self.llm,
            child_text,
            question=confirmation["question"],
        )
        diagnostics.append(f"[CLASSIFIER] {decision}")

        if decision == "YES":
            self._finish_stop(utterances)
            return

        if decision == "NO":
            resume_phase = confirmation["resume_phase"]
            resume_utterances = confirmation["resume_utterances"]
            continue_utterances = confirmation.get("continue_utterances")
            self._state.pop("stop_confirmation", None)
            self._state["phase"] = resume_phase
            self._entry_pending = False
            if continue_utterances is not None:
                utterances.extend(continue_utterances)
            else:
                utterances.append(
                    choose_nonrepeating_response(
                        self._state,
                        "last_stop_continue",
                        template.STOP_CONTINUE_RESPONSES,
                    )
                )
                utterances.extend(resume_utterances)
            return

        confirmation["unclear_count"] += 1
        if confirmation["unclear_count"] >= MAX_STOP_CONFIRMATION_UNCLEAR:
            self._finish_stop(utterances, fallback=True)
            return
        utterances.append(template.STOP_CONFIRMATION_CLARIFICATION)

    def _handle_choice(self, child_text, utterances, diagnostics):
        active_question = self._state.get("active_choice_question", CHOICE_QUESTION)
        if self._intercept_stop_request(
            child_text,
            utterances,
            diagnostics,
            resume_phase="CHOICE",
            resume_utterances=(active_question,),
        ):
            return

        choice = classify_choice_response(
            self.llm,
            child_text,
            question=active_question,
        )
        diagnostics.append(f"[CLASSIFIER] {choice}")

        if choice == "STOP":
            self._finish_stop(utterances)
            return

        if choice == "KIES_JIJ":
            choice = random.choice(AVAILABLE_TOPICS)

        if choice not in AVAILABLE_TOPICS:
            utterances.append(template.CHOICE_CLARIFICATION)
            self._state["active_choice_question"] = template.CHOICE_CLARIFICATION
            return

        self._start_topic_session(choice, utterances)

    def _start_topic_session(
        self,
        topic,
        utterances,
        *,
        switched=False,
        announce=True,
    ):
        """Start a fresh adaptive two-question session for one known topic."""
        state = self._state
        return_to_choice(state)
        state["activity"] = "REKENEN"
        state.pop("pending_activity", None)
        state.pop("pending_math_topic", None)
        state.pop("active_choice_question", None)
        state["topic"] = topic
        state["name_used_in_practice_session"] = False
        if topic == "KEREN":
            state["multiplication_question_number"] = 1
        else:
            state["division_question_number"] = 1
        state["exercise"] = exercise_for_topic(
            state,
            topic,
            difficulty="easy",
        )
        response = (
            template.SUBJECT_SWITCH_RESPONSE
            if switched
            else template.TOPIC_START_RESPONSE
        )
        if announce:
            utterances.append(response.format(topic=topic.lower()))
        self._set_phase("QUESTION_1")

    def _handle_subject_change(self, response_type, utterances):
        """Apply a direct topic switch or ask only when no target was named."""
        requested_topic = DIRECT_SUBJECT_SWITCHES.get(response_type)
        if requested_topic is not None:
            self._start_topic_session(
                requested_topic,
                utterances,
                switched=True,
            )
            return

        utterances.append(template.GENERIC_SUBJECT_CHANGE_RESPONSE)
        if self._state.get("outer_conversation"):
            clear_math_activity(self._state)
            self._state["activity_choice_context"] = "SWITCH"
            self._set_phase("ACTIVITY_CHOICE")
        else:
            return_to_choice(self._state)
        self._entry_pending = True

    def _enter_question(self, utterances):
        topic = self._state["topic"]
        exercise = self._state.get("exercise")
        if exercise is None:
            exercise = exercise_for_topic(
                self._state,
                topic,
                difficulty="easy" if topic in AVAILABLE_TOPICS else None,
            )
            self._state["exercise"] = exercise

        progress = self._state.get("question_progress")
        if progress is None or progress.get("exercise_expression") != exercise["expression"]:
            progress = {
                "exercise_expression": exercise["expression"],
                "wrong_or_unclear": 0,
                "wrong_answers": 0,
                "off_topic_count": 0,
                "unrecognized_answers": 0,
                "first_error_large": False,
                "turns": 0,
            }
            self._state["question_progress"] = progress
        utterances.append(exercise["question"])

    def _handle_chat_request_during_math(self, utterances, active_question):
        """Remember one request; a repeated request switches immediately."""
        state = self._state
        if state.get("pending_activity") == "KLETSEN":
            utterances.append(template.MATH_CHAT_SWITCH_SKIP)
            self._start_chat_activity(utterances, announce=False)
            return
        state["pending_activity"] = "KLETSEN"
        utterances.append(
            template.MATH_CHAT_SWITCH_PENDING.format(question=active_question)
        )

    def _skip_current_math_exercise(self, utterances):
        state = self._state
        if state.get("pending_activity") == "KLETSEN":
            utterances.append(template.MATH_CHAT_SWITCH_SKIP)
            self._start_chat_activity(utterances, announce=False)
            return
        clear_math_activity(state)
        state["activity"] = None
        state["phase"] = "ACTIVITY_CHOICE"
        state["active_activity_question"] = template.MATH_SKIP_TO_ACTIVITY
        self._entry_pending = False
        utterances.append(template.MATH_SKIP_TO_ACTIVITY)

    def _handle_question(self, child_text, utterances, diagnostics):
        state = self._state
        topic = state["topic"]
        exercise = state["exercise"]
        question = exercise["question"]
        correct_answer = exercise["answer"]
        progress = state["question_progress"]

        if topic == "KEREN":
            supportive_resume = (
                f"{template.FEELING_CONTINUE_PREFIX} {exercise['hint']} "
                "Hoeveel zijn dat samen?",
            )
        else:
            supportive_resume = (
                f"{template.FEELING_CONTINUE_PREFIX} "
                f"{exercise['sharing_question']}",
            )

        if self._intercept_stop_request(
            child_text,
            utterances,
            diagnostics,
            resume_phase="QUESTION_1",
            resume_utterances=(question,),
            support_utterances=supportive_resume,
        ):
            return

        response_type = classify_child_response(
            self.llm,
            question,
            child_text,
            current_topic=topic,
        )
        diagnostics.append(f"[CLASSIFIER] {response_type}")

        if response_type == "STOP":
            self._finish_stop(utterances)
            return
        if response_type == "WISSEL_KLETSEN":
            self._handle_chat_request_during_math(utterances, question)
            return
        if response_type == "SLA_OVER":
            self._skip_current_math_exercise(utterances)
            return
        if response_type in SUBJECT_CHANGE_INTENTS:
            self._handle_subject_change(response_type, utterances)
            return
        if is_unclear_number_attempt(child_text, response_type):
            diagnostics.append("[ASR] Unclear numerical answer")
            self._handle_unrecognized_number_answer(utterances)
            return
        if response_type not in {
            "HERHAAL",
            "HULP",
            "ANTWOORD",
        }:
            self._handle_off_topic_question(child_text, utterances)
            return
        progress["turns"] += 1
        if response_type == "HERHAAL":
            utterances.append(question)
        elif response_type == "HULP":
            utterances.append(f"Natuurlijk! {exercise['hint']}")
        elif response_type == "ANTWOORD":
            value = extract_answer_number(child_text)
            if value == correct_answer:
                qualifies_for_harder = (
                    progress["wrong_answers"] == 0
                    or (
                        progress["wrong_answers"] == 1
                        and not progress["first_error_large"]
                    )
                )
                state.pop("question_progress", None)
                if topic == "KEREN":
                    go_to_keren_explanation(
                        state,
                        solved=True,
                        qualifies_for_harder=qualifies_for_harder,
                    )
                else:
                    go_to_division_explanation(
                        state,
                        solved=True,
                        qualifies_for_harder=qualifies_for_harder,
                    )
                self._entry_pending = True
                return

            if value is None:
                progress["wrong_or_unclear"] += 1
                utterances.append(
                    "Ik hoorde niet precies één getal. Kun je alleen je antwoord zeggen?"
                )
            else:
                self._handle_wrong_number(
                    child_text,
                    value,
                    utterances,
                )
                if self._state["phase"] != "QUESTION_1":
                    return
        if progress["turns"] >= MAX_TURNS_PER_QUESTION:
            state.pop("question_progress", None)
            if topic == "KEREN":
                go_to_keren_explanation(state, solved=False)
            else:
                go_to_division_explanation(state, solved=False)
            self._entry_pending = True

    def _handle_unrecognized_number_answer(self, utterances):
        """Repair a likely transcript failure without judging the arithmetic."""
        state = self._state
        progress = state["question_progress"]
        attempts = progress.get("unrecognized_answers", 0) + 1
        progress["unrecognized_answers"] = attempts

        if attempts == 1:
            utterances.append(template.NUMBER_ANSWER_CLARIFICATION)
            return
        if attempts < MAX_UNRECOGNIZED_NUMBER_ATTEMPTS:
            utterances.append(template.NUMBER_ANSWER_RETRY)
            return

        utterances.append(template.NUMBER_ANSWER_SUPPORT)
        state.pop("question_progress", None)
        if state["topic"] == "KEREN":
            go_to_keren_explanation(state, solved=False)
        else:
            go_to_division_explanation(state, solved=False)
        self._entry_pending = True

    def _handle_off_topic_question(self, child_text, utterances):
        """Redirect playfully, add support, then enter guided teaching."""
        state = self._state
        exercise = state["exercise"]
        progress = state["question_progress"]
        progress["off_topic_count"] = progress.get("off_topic_count", 0) + 1
        off_topic_count = progress["off_topic_count"]

        if off_topic_count == 1:
            acknowledgement = playful_off_topic_acknowledgement(
                state,
                child_text,
            )
            utterances.append(
                template.OFF_TOPIC_REDIRECT.format(
                    acknowledgement=acknowledgement,
                    question=exercise["question"],
                )
            )
            return

        if off_topic_count < MAX_OFF_TOPIC_RESPONSES:
            if state["topic"] == "KEREN":
                utterances.append(
                    f"{template.OFF_TOPIC_HINT_PREFIX} {exercise['hint']} "
                    "Hoeveel zijn dat samen?"
                )
            else:
                utterances.append(
                    f"{template.OFF_TOPIC_HINT_PREFIX} "
                    f"{exercise['sharing_question']}"
                )
            return

        self._request_stop_confirmation(
            utterances,
            resume_phase="QUESTION_1",
            resume_utterances=(exercise["question"],),
            question=template.OFF_TOPIC_STOP_QUESTION,
        )

    def _handle_wrong_number(self, child_text, value, utterances):
        state = self._state
        topic = state["topic"]
        exercise = state["exercise"]
        progress = state["question_progress"]
        progress["wrong_answers"] += 1
        progress["wrong_or_unclear"] += 1
        wrong_answers = progress["wrong_answers"]

        if wrong_answers == 1:
            progress["first_error_large"] = (
                abs(value - exercise["answer"]) > SMALL_ERROR_MAX
            )
            if progress["first_error_large"]:
                if topic == "KEREN":
                    feedback = generate_supportive_feedback(
                        self.llm,
                        template.WRONG_ANSWER,
                        question=exercise["question"],
                        child_text=child_text,
                        hint=exercise["hint"],
                    )
                else:
                    feedback = template.DIVISION_CONCRETE_RETRY.format(
                        sharing_question=exercise["sharing_question"],
                    )
                utterances.append(feedback)
            else:
                utterances.append(template.SMALL_ERROR_RETRY)
            return

        if wrong_answers == 2 and not progress["first_error_large"]:
            if topic == "KEREN":
                feedback = generate_supportive_feedback(
                    self.llm,
                    template.WRONG_ANSWER,
                    question=exercise["question"],
                    child_text=child_text,
                    hint=exercise["hint"],
                )
            else:
                feedback = template.DIVISION_CONCRETE_RETRY.format(
                    sharing_question=exercise["sharing_question"],
                )
            utterances.append(feedback)
            return

        state.pop("question_progress", None)
        if topic == "KEREN":
            go_to_keren_explanation(state, solved=False)
        else:
            go_to_division_explanation(state, solved=False)
        self._entry_pending = True

    def _multiplication_dialogue(self):
        exercise = self._state["exercise"]
        progressive_addition = exercise["progressive_addition"]
        counting_guidance = template.COUNTING_GUIDANCE.format(
            known_total=exercise["counting_known_total"],
            start=exercise["counting_start"],
            amount=exercise["counting_amount"],
            lead_in=exercise["counting_lead_in"],
        )
        if progressive_addition:
            success_feedback = template.PROGRESSIVE_COUNTING_SUCCESS.format(
                previous_total=exercise["progressive_previous_total"].capitalize(),
                group_size=exercise["counting_amount"],
                answer=exercise["answer_spoken"],
                multiplication=exercise["multiplication_spoken"],
            )
        else:
            success_feedback = template.COUNTING_SUCCESS.format(
                addition=exercise["addition_spoken"],
                answer=exercise["answer_spoken"],
                multiplication=exercise["multiplication_spoken"],
                groups=exercise["groups_spoken"],
            )
        counting_correction = template.COUNTING_CORRECTION.format(
            previous=exercise["counting_previous"],
            answer=exercise["answer_spoken"],
            group_breakdown=exercise["group_breakdown"],
            multiplication=exercise["multiplication_spoken"],
        )
        return counting_guidance, success_feedback, counting_correction

    def _enter_multiplication_explanation(self, utterances):
        state = self._state
        exercise = state.get("exercise")
        if exercise is None:
            exercise = exercise_for_topic(state, "KEREN")
            state["exercise"] = exercise

        if state.get("explanation_solved"):
            praise = self._choose_correct_praise("KEREN")
            if exercise["progressive_addition"]:
                explanation = template.LONG_DIRECT_SUCCESS_EXPLANATION.format(
                    groups=exercise["groups_spoken"].capitalize(),
                    answer=exercise["answer_spoken"],
                )
            else:
                explanation = template.DIRECT_SUCCESS_EXPLANATION.format(
                    groups=exercise["groups_spoken"].capitalize(),
                    addition=exercise["addition"],
                    answer=exercise["answer"],
                )
            utterances.extend((praise, explanation))
            self._complete_current_exercise(
                utterances,
                qualifies_for_harder=state.get("harder_second_question", False),
            )
            return

        progress = state.get("multiplication_explanation_progress")
        if progress is None or progress.get("exercise_expression") != exercise["expression"]:
            progress = {
                "exercise_expression": exercise["expression"],
                "turns": 0,
                "off_topic_count": 0,
                "counting_guidance_given": False,
            }
            state["multiplication_explanation_progress"] = progress

        if exercise["progressive_addition"]:
            utterances.extend(exercise["progressive_addition"])
        else:
            utterances.append(
                f"Laten we samen naar de som kijken. Wat is {exercise['addition']}?"
            )

    def _handle_multiplication_explanation(
        self,
        child_text,
        utterances,
        diagnostics,
    ):
        state = self._state
        exercise = state["exercise"]
        progress = state["multiplication_explanation_progress"]
        counting_guidance, success_feedback, counting_correction = (
            self._multiplication_dialogue()
        )
        explanation_question = (
            exercise["progressive_final_question"]
            if exercise["progressive_addition"]
            else f"Wat is {exercise['addition']}?"
        )
        active_question = (
            "Welk getal komt daarna?"
            if progress["counting_guidance_given"]
            else explanation_question
        )
        resume_utterance = (
            counting_guidance
            if progress["counting_guidance_given"]
            else explanation_question
        )
        if progress["counting_guidance_given"]:
            supportive_resume = (
                f"{template.FEELING_CONTINUE_PREFIX} {counting_guidance}",
            )
        elif exercise["progressive_addition"]:
            supportive_resume = (
                f"{template.FEELING_CONTINUE_PREFIX} "
                f"{exercise['progressive_final_hint']} {explanation_question}",
            )
        else:
            supportive_resume = (
                f"{template.FEELING_CONTINUE_PREFIX} "
                f"{exercise['explanation_hint']} {explanation_question}",
            )
        if self._intercept_stop_request(
            child_text,
            utterances,
            diagnostics,
            resume_phase="KEREN_EXPLANATION",
            resume_utterances=(resume_utterance,),
            support_utterances=supportive_resume,
        ):
            return

        response_type = classify_child_response(
            self.llm,
            active_question,
            child_text,
            current_topic=state["topic"],
        )
        diagnostics.append(f"[CLASSIFIER] {response_type}")

        if response_type == "STOP":
            self._finish_stop(utterances)
            return
        if response_type == "WISSEL_KLETSEN":
            self._handle_chat_request_during_math(utterances, resume_utterance)
            return
        if response_type == "SLA_OVER":
            self._skip_current_math_exercise(utterances)
            return
        if response_type in SUBJECT_CHANGE_INTENTS:
            self._handle_subject_change(response_type, utterances)
            return
        if response_type not in {
            "HERHAAL",
            "HULP",
            "ANTWOORD",
        }:
            self._handle_off_topic_multiplication(
                child_text,
                utterances,
                resume_utterance=resume_utterance,
                supportive_resume=supportive_resume[0],
            )
            return
        progress["turns"] += 1
        if response_type == "HERHAAL":
            utterances.append(
                counting_guidance
                if progress["counting_guidance_given"]
                else explanation_question
            )
        elif response_type == "HULP":
            if progress["counting_guidance_given"]:
                utterances.append(counting_guidance)
            elif exercise["progressive_addition"]:
                utterances.append(exercise["progressive_final_hint"])
            else:
                utterances.append(exercise["explanation_hint"])
        elif response_type == "ANTWOORD":
            value = extract_answer_number(child_text)
            if value == exercise["answer"]:
                utterances.append(success_feedback)
                state.pop("multiplication_explanation_progress", None)
                self._complete_current_exercise(utterances)
                return
            if value is not None:
                if not progress["counting_guidance_given"]:
                    utterances.append(counting_guidance)
                    progress["counting_guidance_given"] = True
                else:
                    utterances.append(counting_correction)
                    state.pop("multiplication_explanation_progress", None)
                    self._complete_current_exercise(utterances)
                    return
            elif progress["counting_guidance_given"]:
                utterances.append(counting_guidance)
            elif exercise["progressive_addition"]:
                utterances.append(exercise["progressive_final_hint"])
            else:
                utterances.append(exercise["explanation_hint"])
        elif progress["counting_guidance_given"]:
            utterances.append(counting_guidance)
        elif exercise["progressive_addition"]:
            utterances.append(exercise["progressive_final_hint"])
        else:
            utterances.append(exercise["explanation_hint"])

        if progress["turns"] >= MAX_EXPLANATION_TURNS:
            if exercise["progressive_addition"]:
                feedback = counting_correction
            else:
                feedback = generate_final_explanation(
                    self.llm,
                    addition=exercise["addition"],
                    expression=exercise["expression"],
                    answer=exercise["answer"],
                )
            utterances.append(feedback)
            state.pop("multiplication_explanation_progress", None)
            self._complete_current_exercise(utterances)

    def _handle_off_topic_multiplication(
        self,
        child_text,
        utterances,
        *,
        resume_utterance,
        supportive_resume,
    ):
        """Redirect tangents, then offer a genuine pause from the lesson."""
        state = self._state
        progress = state["multiplication_explanation_progress"]
        progress["off_topic_count"] = progress.get("off_topic_count", 0) + 1
        off_topic_count = progress["off_topic_count"]

        if off_topic_count == 1:
            acknowledgement = playful_off_topic_acknowledgement(
                state,
                child_text,
            )
            utterances.append(
                template.OFF_TOPIC_REDIRECT.format(
                    acknowledgement=acknowledgement,
                    question=resume_utterance,
                )
            )
            return

        if off_topic_count < MAX_OFF_TOPIC_RESPONSES:
            utterances.append(supportive_resume)
            return

        self._request_stop_confirmation(
            utterances,
            resume_phase="KEREN_EXPLANATION",
            resume_utterances=(resume_utterance,),
            question=template.OFF_TOPIC_STOP_QUESTION,
        )

    def _enter_division_explanation(self, utterances):
        state = self._state
        exercise = state.get("exercise")
        if exercise is None:
            exercise = exercise_for_topic(state, "DELEN", difficulty="easy")
            state["exercise"] = exercise

        direct_explanation = template.DIVISION_DIRECT_SUCCESS.format(
            multiplication=exercise["multiplication_connection"],
            division=exercise["division_spoken"],
        )
        if state.get("explanation_solved"):
            utterances.extend(
                (self._choose_correct_praise("DELEN"), direct_explanation)
            )
            self._complete_current_exercise(
                utterances,
                qualifies_for_harder=state.get("harder_second_question", False),
            )
            return

        progress = state.get("division_explanation_progress")
        if progress is None:
            progress = {
                "exercise_expression": exercise["expression"],
                "step_index": 0,
                "distributed": 0,
                "remaining": exercise["dividend"],
                "each_child_has": 0,
                "active_step": 0,
                "correct_answer": None,
                "attempts": 0,
                "off_topic_count": 0,
            }
            state["division_explanation_progress"] = progress
            utterances.append(exercise["sharing_intro"])
        self._prepare_division_step(utterances)

    def _prepare_division_step(self, utterances):
        state = self._state
        exercise = state["exercise"]
        progress = state["division_explanation_progress"]
        step = exercise["sharing_rounds"][progress["step_index"]]
        if (
            progress["distributed"] != step["distributed_before"]
            or progress["remaining"] != step["remaining_before"]
            or progress["each_child_has"] != step["each_before"]
        ):
            raise AssertionError(
                "Stored division progress does not match the active step"
            )
        progress.update(
            {
                "active_step": step["step"],
                "correct_answer": step["correct_answer"],
                "attempts": 0,
                "off_topic_count": 0,
                "distributed": step["distributed_after"],
                "remaining": step["remaining_after"],
                "each_child_has": step["each_after"],
            }
        )
        utterances.append(step["question"])

    def _handle_division_explanation(
        self,
        child_text,
        utterances,
        diagnostics,
    ):
        state = self._state
        exercise = state["exercise"]
        progress = state["division_explanation_progress"]
        step = exercise["sharing_rounds"][progress["step_index"]]
        supportive_resume = (
            f"{template.FEELING_CONTINUE_PREFIX} {step['hint']}",
        )
        if self._intercept_stop_request(
            child_text,
            utterances,
            diagnostics,
            resume_phase="DIVISION_EXPLANATION",
            resume_utterances=(step["question"],),
            support_utterances=supportive_resume,
        ):
            return

        response_type = classify_child_response(
            self.llm,
            step["question"],
            child_text,
            current_topic=state["topic"],
        )
        diagnostics.append(f"[CLASSIFIER] {response_type}")

        if response_type == "STOP":
            self._finish_stop(utterances)
            return
        if response_type == "WISSEL_KLETSEN":
            self._handle_chat_request_during_math(utterances, step["question"])
            return
        if response_type == "SLA_OVER":
            self._skip_current_math_exercise(utterances)
            return
        if response_type in SUBJECT_CHANGE_INTENTS:
            self._handle_subject_change(response_type, utterances)
            return
        if response_type == "HERHAAL":
            utterances.append(step["question"])
            return

        if response_type not in ("ANTWOORD", "HULP"):
            self._handle_off_topic_division(child_text, utterances, step)
            return

        value = (
            extract_answer_number(child_text)
            if response_type == "ANTWOORD"
            else None
        )
        if value == step["correct_answer"]:
            utterances.append(step["success"])
            self._advance_division_step(utterances, step)
            return

        progress["attempts"] += 1
        if progress["attempts"] == 1:
            utterances.append(step["hint"])
            return

        utterances.append(step["reveal"])
        self._advance_division_step(utterances, step)

    def _handle_off_topic_division(self, child_text, utterances, step):
        """Redirect tangents, then offer a genuine pause from equal sharing."""
        state = self._state
        progress = state["division_explanation_progress"]
        progress["off_topic_count"] = progress.get("off_topic_count", 0) + 1
        off_topic_count = progress["off_topic_count"]

        if off_topic_count == 1:
            acknowledgement = playful_off_topic_acknowledgement(
                state,
                child_text,
            )
            utterances.append(
                template.OFF_TOPIC_REDIRECT.format(
                    acknowledgement=acknowledgement,
                    question=step["question"],
                )
            )
            return
        if off_topic_count < MAX_OFF_TOPIC_RESPONSES:
            utterances.append(
                f"{template.OFF_TOPIC_HINT_PREFIX} {step['hint']}"
            )
            return

        self._request_stop_confirmation(
            utterances,
            resume_phase="DIVISION_EXPLANATION",
            resume_utterances=(step["question"],),
            question=template.OFF_TOPIC_STOP_QUESTION,
        )

    def _advance_division_step(self, utterances, step):
        state = self._state
        exercise = state["exercise"]
        progress = state["division_explanation_progress"]
        progress.update(
            {
                "distributed": step["distributed_after"],
                "remaining": step["remaining_after"],
                "each_child_has": step["each_after"],
                "step_index": progress["step_index"] + 1,
            }
        )

        if progress["step_index"] < len(exercise["sharing_rounds"]):
            self._prepare_division_step(utterances)
            return

        if (
            progress["distributed"] != exercise["dividend"]
            or progress["remaining"] != 0
            or progress["each_child_has"] != exercise["answer"]
        ):
            raise AssertionError("Completed division explanation has invalid totals")
        state.pop("division_explanation_progress", None)
        self._complete_current_exercise(utterances)

    def _complete_current_exercise(
        self,
        utterances,
        *,
        qualifies_for_harder=False,
    ):
        state = self._state
        if (
            state.get("outer_conversation")
            and state.get("pending_activity") == "KLETSEN"
        ):
            state.pop("pending_activity", None)
            utterances.append(template.MATH_CHAT_SWITCH_COMPLETE)
            self._start_chat_activity(utterances, announce=False)
            return

        if (
            state.get("topic") == "KEREN"
            and state.get("multiplication_question_number", 1) == 1
        ):
            difficulty = "hard" if qualifies_for_harder else "easy"
            state["multiplication_question_number"] = 2
            state["exercise"] = exercise_for_topic(
                state,
                "KEREN",
                difficulty=difficulty,
            )
            state.pop("explanation_solved", None)
            state.pop("harder_second_question", None)
            state.pop("multiplication_explanation_progress", None)
            state.pop("question_progress", None)
            utterances.append(template.SECOND_MULTIPLICATION_INTRO)
            self._set_phase("QUESTION_1")
            return

        if (
            state.get("topic") == "DELEN"
            and state.get("division_question_number", 1) == 1
        ):
            difficulty = "hard" if qualifies_for_harder else "easy"
            state["division_question_number"] = 2
            state["exercise"] = exercise_for_topic(
                state,
                "DELEN",
                difficulty=difficulty,
            )
            state.pop("explanation_solved", None)
            state.pop("harder_second_question", None)
            state.pop("division_explanation_progress", None)
            state.pop("question_progress", None)
            utterances.append(template.SECOND_DIVISION_INTRO)
            self._set_phase("QUESTION_1")
            return

        if state.get("outer_conversation"):
            return_to_after_practice(state)
        else:
            # Preserve the old phase-helper contract for research scripts that
            # supply their own legacy state rather than starting TutorEngine.
            return_to_choice(state)
        self._entry_pending = True
