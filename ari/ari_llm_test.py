"""Text-only adapter for the shared ARI conversation engine.

Run from the repository root with::

    python -m ari.ari_llm_test                    # the VUmc guide
    python -m ari.ari_llm_test --mode companion   # conversation and activities
"""

import argparse

from ari.engines import MODES, build_engine
from ari.responses import ConversationTurn


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
    actions = getattr(turn, "actions", ())
    for index, utterance in enumerate(turn.utterances):
        action = actions[index] if index < len(actions) else {}
        extras = ", ".join(
            f"{key}={value}" for key, value in action.items() if value
        )
        print(f"ARI: {utterance}" + (f"   ({extras})" if extras else ""))
    for diagnostic in post_speech:
        print(diagnostic)


def main() -> None:
    parser = argparse.ArgumentParser(description="Talk to ARI's conversation by typing.")
    parser.add_argument("--mode", choices=MODES, default="guardian")
    args = parser.parse_args()

    engine = build_engine(args.mode, activities_enabled=True)
    print_turn(engine.start())
    if args.mode == "guardian":
        print("(ARI staat in standby. Begin met bijvoorbeeld 'Hallo' of 'Waar is de apotheek?')")

    while not engine.is_done:
        visitor_text = input("Bezoeker: ").strip()
        print_turn(engine.handle_input(visitor_text))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Demo stopped]")
