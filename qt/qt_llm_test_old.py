import random
import re

from shared.llm import LLM
import qt.prompts as template


# Keep these easy to tune during text-only conversation tests.
MAX_WRONG_OR_UNCLEAR = 3  # Existing limit for division exercises.
MAX_TURNS_PER_QUESTION = 8
SMALL_ERROR_MAX = 2  # A first multiplication answer within 2 of the correct value is "close".
MAX_EXPLANATION_TURNS = 5
AVAILABLE_TOPICS = ("KEREN", "DELEN")

AFFIRMATIVE_CHOICE_RESPONSES = {
    "ja",
    "yes",
    "ja hoor",
    "is goed",
    "prima",
    "oke",
    "oké",
}
MULTIPLICATION_CHOICE_WORDS = {
    "keren",
    "keersom",
    "keersommen",
    "tafel",
    "tafels",
    "vermenigvuldigen",
}
DIVISION_CHOICE_WORDS = {
    "delen",
    "deelsom",
    "deelsommen",
    "verdelen",
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
    ("mandarijn", "mandarijnen"),
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


def exercise_for_topic(state, topic, *, difficulty=None):
    """Draw a random unused exercise, avoiding immediate repetition."""
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
        non_repeating = [index for index in candidates if index != previous]
        if non_repeating:
            candidates = non_repeating

        selected = random.choice(candidates)
        remaining.remove(selected)
        state["remaining_multiplication_indices"] = remaining
        state["last_multiplication_index"] = selected
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


def classify_choice_response(llm, child_text):
    """Classify a topic choice, with deterministic affirmative handling."""
    words = re.findall(r"[^\W\d_]+", child_text.casefold(), re.UNICODE)
    word_set = set(words)
    normalized = " ".join(words)

    wants_multiplication = bool(word_set & MULTIPLICATION_CHOICE_WORDS)
    wants_division = bool(word_set & DIVISION_CHOICE_WORDS)
    has_affirmative = bool(
        word_set & {"ja", "yes", "prima", "oke", "oké"}
    ) or " is goed " in f" {normalized} "

    if has_affirmative and wants_multiplication and not wants_division:
        return "KEREN"
    if has_affirmative and wants_division and not wants_multiplication:
        return "DELEN"

    if normalized in AFFIRMATIVE_CHOICE_RESPONSES:
        return "KIES_JIJ"

    result = llm.prompt(
        f"{template.INSTRUCTIONS}\n"
        f"{template.CHOICE_CLASSIFIER_1}\n"
        f"Kind zegt: {child_text!r}",
        max_tokens=15,
    )
    return result.strip().upper()


def choice_phase(llm, state):
    print("QT: Wil je oefenen met keren of delen, of zal ik een sommetje kiezen voor jou?")

    while True:
        child_text = input("Kind: ").strip()
        choice = classify_choice_response(llm, child_text)
        print(f"[CLASSIFIER] {choice}")

        if choice == "STOP":
            print("QT: Helemaal goed! Tot ziens!")
            state["phase"] = "DONE"
            return

        if choice == "KIES_JIJ":
            choice = random.choice(AVAILABLE_TOPICS)

        if choice not in AVAILABLE_TOPICS:
            print(f"QT: {template.CHOICE_CLARIFICATION}")
            continue

        state["topic"] = choice
        if choice == "KEREN":
            state["multiplication_question_number"] = 1
            state["exercise"] = exercise_for_topic(
                state,
                choice,
                difficulty="easy",
            )
        else:
            state["division_question_number"] = 1
            state["exercise"] = exercise_for_topic(
                state,
                choice,
                difficulty="easy",
            )
        print(f"QT: Leuk! Dan gaan we {choice.lower()} oefenen!")
        state["phase"] = "QUESTION_1"
        return


def return_to_choice(state):
    state["topic"] = None
    state["exercise"] = None
    state.pop("explanation_solved", None)
    state.pop("harder_second_question", None)
    state.pop("multiplication_question_number", None)
    state.pop("division_question_number", None)
    state.pop("division_explanation_progress", None)
    state["phase"] = "CHOICE"


def complete_current_exercise(state, *, qualifies_for_harder=False):
    """Start adaptive question two, or finish the two-question session."""
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
        state["phase"] = "QUESTION_1"
        print(f"QT: {template.SECOND_MULTIPLICATION_INTRO}")
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
        state["phase"] = "QUESTION_1"
        print(f"QT: {template.SECOND_DIVISION_INTRO}")
        return

    return_to_choice(state)


def classify_child_response(llm, question, child_text):
    """Use Python for bare numbers and the LLM only for other intentions."""
    if is_simple_numeric_answer(child_text):
        response_type = "ANTWOORD"
    else:
        result = llm.prompt(
            f"{template.INSTRUCTIONS}\n"
            f"{template.QUESTION_1_CLASSIFIER}\n"
            f"Rekensom: {question}\n"
            f"Kind zegt: {child_text!r}",
            max_tokens=15,
        )
        response_type = result.strip().upper()
        # Do not override HULP / STOP / HERHAAL / ANDER_ONDERWERP.
        if response_type == "ONBEKEND" and extract_answer_number(child_text) is not None:
            response_type = "ANTWOORD"

    print(f"[CLASSIFIER] {response_type}")
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


def question1_phase(llm, state):
    topic = state["topic"]
    exercise = state.get("exercise")
    if exercise is None:
        difficulty = "easy" if topic in AVAILABLE_TOPICS else None
        exercise = exercise_for_topic(state, topic, difficulty=difficulty)
        state["exercise"] = exercise
    question = exercise["question"]
    correct_answer = exercise["answer"]
    hint = exercise["hint"]
    wrong_or_unclear = 0
    wrong_answers = 0
    first_error_large = False
    turns = 0

    print(f"QT: {question}")

    while True:
        child_text = input("Kind: ").strip()
        turns += 1
        response_type = classify_child_response(llm, question, child_text)

        if response_type == "STOP":
            print("QT: Geen probleem! Bedankt voor het oefenen!")
            state["phase"] = "DONE"
            return

        if response_type == "ANDER_ONDERWERP":
            print("QT: Natuurlijk! Dan kiezen we iets anders.")
            return_to_choice(state)
            return

        if response_type == "HERHAAL":
            print(f"QT: {question}")

        elif response_type == "HULP":
            print(f"QT: Natuurlijk! {hint}")

        elif response_type == "ANTWOORD":
            value = extract_answer_number(child_text)
            if value == correct_answer:
                if topic == "KEREN":
                    qualifies_for_harder = (
                        wrong_answers == 0
                        or (wrong_answers == 1 and not first_error_large)
                    )
                    go_to_keren_explanation(
                        state,
                        solved=True,
                        qualifies_for_harder=qualifies_for_harder,
                    )
                elif topic == "DELEN":
                    qualifies_for_harder = (
                        wrong_answers == 0
                        or (wrong_answers == 1 and not first_error_large)
                    )
                    go_to_division_explanation(
                        state,
                        solved=True,
                        qualifies_for_harder=qualifies_for_harder,
                    )
                return

            if value is None:
                wrong_or_unclear += 1
                print("QT: Ik hoorde niet precies één getal. Kun je alleen je antwoord zeggen?")
            else:
                wrong_answers += 1
                wrong_or_unclear += 1
                if topic == "KEREN":
                    if wrong_answers == 1:
                        first_error_large = abs(value - correct_answer) > SMALL_ERROR_MAX
                        if first_error_large:
                            feedback = generate_supportive_feedback(
                                llm,
                                template.WRONG_ANSWER,
                                question=question,
                                child_text=child_text,
                                hint=hint,
                            )
                            print(f"QT: {feedback}")
                        else:
                            print(f"QT: {template.SMALL_ERROR_RETRY}")
                    elif wrong_answers == 2 and not first_error_large:
                        feedback = generate_supportive_feedback(
                            llm,
                            template.WRONG_ANSWER,
                            question=question,
                            child_text=child_text,
                            hint=hint,
                        )
                        print(f"QT: {feedback}")
                    else:
                        # First answer far away -> explanation after the second miss.
                        # First answer close -> explanation after the third miss.
                        go_to_keren_explanation(
                            state,
                            solved=False,
                            qualifies_for_harder=False,
                        )
                        return
                elif topic == "DELEN":
                    if wrong_answers == 1:
                        first_error_large = abs(value - correct_answer) > SMALL_ERROR_MAX
                        if first_error_large:
                            feedback = template.DIVISION_CONCRETE_RETRY.format(
                                sharing_question=exercise["sharing_question"],
                            )
                            print(f"QT: {feedback}")
                        else:
                            print(f"QT: {template.SMALL_ERROR_RETRY}")
                    elif wrong_answers == 2 and not first_error_large:
                        feedback = template.DIVISION_CONCRETE_RETRY.format(
                            sharing_question=exercise["sharing_question"],
                        )
                        print(f"QT: {feedback}")
                    else:
                        go_to_division_explanation(
                            state,
                            solved=False,
                            qualifies_for_harder=False,
                        )
                        return
                elif wrong_or_unclear >= 2:
                    feedback = generate_supportive_feedback(
                        llm,
                        template.WRONG_ANSWER,
                        question=question,
                        child_text=child_text,
                        hint=hint,
                    )
                    print(f"QT: {feedback}")
                else:
                    feedback = generate_supportive_feedback(
                        llm,
                        template.WRONG_ANSWER,
                        question=question,
                        child_text=child_text,
                    )
                    print(f"QT: {feedback}")

        else:
            wrong_or_unclear += 1
            print("QT: Ik begrijp je nog niet. Je mag ook hulp vragen of stoppen.")

        if topic == "KEREN":
            # Help, repetition, or unclear input must not count as wrong math.
            # Still leave this phase after too many turns to prevent an endless loop.
            if turns >= MAX_TURNS_PER_QUESTION:
                go_to_keren_explanation(state, solved=False)
                return
        elif topic == "DELEN":
            if turns >= MAX_TURNS_PER_QUESTION:
                go_to_division_explanation(state, solved=False)
                return
        elif (wrong_or_unclear >= MAX_WRONG_OR_UNCLEAR
              or turns >= MAX_TURNS_PER_QUESTION):
            print(
                f"QT: Dat was best lastig! Het antwoord is {correct_answer}. "
                "Geeft niets. We kunnen ook iets anders proberen."
            )
            return_to_choice(state)
            return


def keren_explanation_phase(llm, state):
    """Always explain multiplication, with guided practice after repeated errors."""
    exercise = state.get("exercise")
    if exercise is None:
        exercise = exercise_for_topic(state, "KEREN")
        state["exercise"] = exercise
    addition = exercise["addition"]
    expression = exercise["expression"]
    answer = exercise["answer"]
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

    if state.get("explanation_solved"):
        praise = choose_correct_multiplication_response(state)
        if progressive_addition:
            explanation = template.LONG_DIRECT_SUCCESS_EXPLANATION.format(
                groups=exercise["groups_spoken"].capitalize(),
                answer=exercise["answer_spoken"],
            )
        else:
            explanation = template.DIRECT_SUCCESS_EXPLANATION.format(
                groups=exercise["groups_spoken"].capitalize(),
                addition=addition,
                answer=answer,
            )
        print(f"QT: {praise}")
        print(f"QT: {explanation}")
        complete_current_exercise(
            state,
            qualifies_for_harder=state.get("harder_second_question", False),
        )
        return

    if progressive_addition:
        explanation_question = exercise["progressive_final_question"]
        for line in progressive_addition:
            print(f"QT: {line}")
    else:
        explanation_question = f"Wat is {addition}?"
        print(f"QT: Laten we samen naar de som kijken. {explanation_question}")
    counting_guidance_given = False

    for turn in range(MAX_EXPLANATION_TURNS):
        child_text = input("Kind: ").strip()
        active_question = (
            "Welk getal komt daarna?"
            if counting_guidance_given
            else explanation_question
        )
        response_type = classify_child_response(llm, active_question, child_text)

        if response_type == "STOP":
            print("QT: Geen probleem! Bedankt voor het oefenen!")
            state["phase"] = "DONE"
            return
        if response_type == "ANDER_ONDERWERP":
            print("QT: Natuurlijk! Dan kiezen we iets anders.")
            return_to_choice(state)
            return
        if response_type == "HERHAAL":
            print(f"QT: {counting_guidance if counting_guidance_given else explanation_question}")
            continue
        if response_type == "HULP":
            if counting_guidance_given:
                print(f"QT: {counting_guidance}")
            elif progressive_addition:
                print(f"QT: {exercise['progressive_final_hint']}")
            else:
                print(f"QT: {exercise['explanation_hint']}")
            continue
        if response_type == "ANTWOORD":
            value = extract_answer_number(child_text)
            if value == answer:
                print(f"QT: {success_feedback}")
                complete_current_exercise(state)
                return
            if value is not None:
                if not counting_guidance_given:
                    print(f"QT: {counting_guidance}")
                    counting_guidance_given = True
                    continue
                print(f"QT: {counting_correction}")
                complete_current_exercise(state)
                return

        if counting_guidance_given:
            print(f"QT: {counting_guidance}")
        elif progressive_addition:
            print(f"QT: {exercise['progressive_final_hint']}")
        else:
            print(f"QT: {exercise['explanation_hint']}")

    if progressive_addition:
        feedback = counting_correction
    else:
        feedback = generate_final_explanation(
            llm,
            addition=addition,
            expression=expression,
            answer=answer,
        )
    print(f"QT: {feedback}")
    complete_current_exercise(state)


def division_explanation_phase(llm, state):
    """Explain division through deterministic, interactive equal sharing."""
    exercise = state.get("exercise")
    if exercise is None:
        exercise = exercise_for_topic(state, "DELEN", difficulty="easy")
        state["exercise"] = exercise

    direct_explanation = template.DIVISION_DIRECT_SUCCESS.format(
        multiplication=exercise["multiplication_connection"],
        division=exercise["division_spoken"],
    )

    if state.get("explanation_solved"):
        praise = choose_correct_division_response(state)
        print(f"QT: {praise}")
        print(f"QT: {direct_explanation}")
        complete_current_exercise(
            state,
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
        }
        state["division_explanation_progress"] = progress
        print(f"QT: {exercise['sharing_intro']}")

    rounds = exercise["sharing_rounds"]
    while progress["step_index"] < len(rounds):
        step = rounds[progress["step_index"]]
        if (
            progress["distributed"] != step["distributed_before"]
            or progress["remaining"] != step["remaining_before"]
            or progress["each_child_has"] != step["each_before"]
        ):
            raise AssertionError("Stored division progress does not match the active step")

        progress["active_step"] = step["step"]
        progress["correct_answer"] = step["correct_answer"]
        progress["attempts"] = 0
        # QT has just carried out this distribution in the spoken scenario,
        # so these fields represent the active, post-distribution situation.
        progress["distributed"] = step["distributed_after"]
        progress["remaining"] = step["remaining_after"]
        progress["each_child_has"] = step["each_after"]
        print(f"QT: {step['question']}")

        while True:
            child_text = input("Kind: ").strip()
            response_type = classify_child_response(
                llm,
                step["question"],
                child_text,
            )

            if response_type == "STOP":
                print("QT: Geen probleem! Bedankt voor het oefenen!")
                state.pop("division_explanation_progress", None)
                state["phase"] = "DONE"
                return
            if response_type == "ANDER_ONDERWERP":
                print("QT: Natuurlijk! Dan kiezen we iets anders.")
                return_to_choice(state)
                return
            if response_type == "HERHAAL":
                print(f"QT: {step['question']}")
                continue

            value = (
                extract_answer_number(child_text)
                if response_type == "ANTWOORD"
                else None
            )
            if value == step["correct_answer"]:
                print(f"QT: {step['success']}")
                step_completed = True
            else:
                if response_type not in ("ANTWOORD", "HULP"):
                    print(
                        "QT: Ik begrijp je nog niet helemaal. "
                        "We kijken samen naar deze stap."
                    )
                progress["attempts"] += 1
                if progress["attempts"] == 1:
                    print(f"QT: {step['hint']}")
                    continue
                print(f"QT: {step['reveal']}")
                step_completed = True

            if step_completed:
                progress["distributed"] = step["distributed_after"]
                progress["remaining"] = step["remaining_after"]
                progress["each_child_has"] = step["each_after"]
                progress["step_index"] += 1
                break

    if (
        progress["distributed"] != exercise["dividend"]
        or progress["remaining"] != 0
        or progress["each_child_has"] != exercise["answer"]
    ):
        raise AssertionError("Completed division explanation has invalid totals")

    state.pop("division_explanation_progress", None)
    complete_current_exercise(state)


def main():
    llm = LLM()
    state = {
        "phase": "CHOICE",
        "topic": None,
        "exercise": None,
        "remaining_multiplication_indices": [],
        "last_multiplication_index": None,
        "last_correct_response": None,
        "remaining_division_indices": [],
        "last_division_index": None,
        "last_correct_division_response": None,
    }

    while state["phase"] != "DONE":
        if state["phase"] == "CHOICE":
            choice_phase(llm, state)
        elif state["phase"] == "QUESTION_1":
            question1_phase(llm, state)
        elif state["phase"] == "KEREN_EXPLANATION":
            keren_explanation_phase(llm, state)
        elif state["phase"] == "DIVISION_EXPLANATION":
            division_explanation_phase(llm, state)
        else:
            raise RuntimeError(f"Unknown conversation phase: {state['phase']}")

    print("[Demo finished]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Demo stopped]")
