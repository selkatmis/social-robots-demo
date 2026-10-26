"""Keyboard interface for the shared Dutch QT mathematics tutor."""

from shared.llm import LLM

from qt import responses as brain

# Keep the established helper imports available to text-only regression tests
# and small research scripts.  They are aliases to the single implementation
# in responses.py, not copies of teaching logic.
random = brain.random
MULTIPLICATION_EXERCISES = brain.MULTIPLICATION_EXERCISES
DIVISION_EXERCISES = brain.DIVISION_EXERCISES
choose_correct_multiplication_response = brain.choose_correct_multiplication_response
choose_correct_division_response = brain.choose_correct_division_response
classify_choice_response = brain.classify_choice_response
dutch_number = brain.dutch_number
exercise_for_topic = brain.exercise_for_topic
extract_answer_number = brain.extract_answer_number
generate_final_explanation = brain.generate_final_explanation
generate_supportive_feedback = brain.generate_supportive_feedback
go_to_division_explanation = brain.go_to_division_explanation
go_to_keren_explanation = brain.go_to_keren_explanation
is_simple_numeric_answer = brain.is_simple_numeric_answer
keep_first_question = brain.keep_first_question
make_division_exercise = brain.make_division_exercise
make_division_round_shares = brain.make_division_round_shares
make_first_sharing_question = brain.make_first_sharing_question
make_multiplication_exercise = brain.make_multiplication_exercise
make_progressive_addition = brain.make_progressive_addition
return_to_choice = brain.return_to_choice


def print_turn(turn):
    """Render engine data using the tutor's established console format."""
    for diagnostic in turn.diagnostics:
        print(diagnostic)
    for utterance in turn.utterances:
        print(f"QT: {utterance}")


def classify_child_response(llm, question, child_text):
    """Compatibility wrapper retaining the former diagnostic side effect."""
    response_type = brain.classify_child_response(llm, question, child_text)
    print(f"[CLASSIFIER] {response_type}")
    return response_type


def _run_legacy_phase(llm, state, phase):
    """Drive one old blocking phase through the shared stateful engine.

    This keeps existing text regression helpers working while all lesson
    decisions remain inside TutorEngine.
    """
    engine = brain.TutorEngine(llm, state=state)
    turn = engine.start_current_phase(follow_transitions=False)
    print_turn(turn)

    while state["phase"] == phase and not engine.is_done:
        child_text = input("Kind: ").strip()
        turn = engine.handle_input(child_text, follow_transitions=False)
        print_turn(turn)


def choice_phase(llm, state):
    _run_legacy_phase(llm, state, "CHOICE")


def question1_phase(llm, state):
    _run_legacy_phase(llm, state, "QUESTION_1")


def keren_explanation_phase(llm, state):
    _run_legacy_phase(llm, state, "KEREN_EXPLANATION")


def division_explanation_phase(llm, state):
    _run_legacy_phase(llm, state, "DIVISION_EXPLANATION")


def complete_current_exercise(state, *, qualifies_for_harder=False):
    """Compatibility entry point delegated to the shared engine."""
    engine = brain.TutorEngine(None, state=state)
    utterances = []
    engine._complete_current_exercise(  # noqa: SLF001 - compatibility shim
        utterances,
        qualifies_for_harder=qualifies_for_harder,
    )
    print_turn(brain.TutorTurn(tuple(utterances)))


def main():
    engine = brain.TutorEngine(LLM())
    print_turn(engine.start())

    while not engine.is_done:
        child_text = input("Kind: ").strip()
        print_turn(engine.handle_input(child_text))

    print("[Demo finished]")


def __getattr__(name):
    """Forward less-common legacy constants/helpers to the shared module."""
    try:
        return getattr(brain, name)
    except AttributeError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Demo stopped]")
