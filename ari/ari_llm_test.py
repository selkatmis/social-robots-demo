"""Text-only adapter for the shared ARI conversation engine.

Run from the repository root with::

    python -m ari.ari_llm_test
"""

from ari import prompts
from ari.responses import ConversationEngine, ConversationTurn
from shared.llm import LLM


def print_turn(turn: ConversationTurn) -> None:
    post_speech = tuple(
        diagnostic
        for diagnostic in turn.diagnostics
        if diagnostic == "[PHASE] STANDBY" and turn.utterances
    )
    for diagnostic in turn.diagnostics:
        if diagnostic in post_speech:
            continue
        print(diagnostic)
    for utterance in turn.utterances:
        print(f"ARI: {utterance}")
    for diagnostic in post_speech:
        print(diagnostic)


def main() -> None:
    engine = ConversationEngine(
        LLM(system_prompt=prompts.SYSTEM_PROMPT),
        activities_enabled=True,
    )
    print_turn(engine.start())

    while not engine.is_done:
        visitor_text = input("Bezoeker: ").strip()
        print_turn(engine.handle_input(visitor_text))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Demo stopped]")
