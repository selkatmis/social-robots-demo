"""Text-only ARI demo for creating a small music playlist with a visitor.

Run this module from the project root with::

    python -m ari.music_conversation

The dialogue itself is deterministic.  The LLM is only used to classify the
visitor's intent, while artist and song names are copied from the visitor's
answer.  This keeps the demonstration predictable and prevents the model from
inventing playlist entries.
"""

from dataclasses import dataclass
from typing import Callable, Optional

import ari.prompts as prompts
from shared.llm import LLM

CONTINUE_WORDS = {
    "graag",
    "is goed",
    "ja",
    "ja graag",
    "ja hoor",
    "jazeker",
    "leuk",
    "natuurlijk",
    "ok",
    "oke",
    "oké",
    "prima",
    "zeker",
}
STOP_PHRASES = {
    "geen zin",
    "hoeft niet",
    "ik wil niet",
    "ik wil stoppen",
    "laat maar",
    "nee",
    "nee bedankt",
    "nee dank u",
    "stop",
}


@dataclass
class Playlist:
    """The music preferences collected during one conversation."""

    artist: str = ""
    favorite_song: str = ""
    companion_song: str = ""


def normalize_for_matching(text: str) -> str:
    """Return a small normalized form for deterministic common-case matching."""
    return " ".join(text.casefold().strip().strip(".!?,;:\"'").split())


def clean_music_answer(text: str) -> str:
    """Clean speech-recognition punctuation without rewriting the answer."""
    return " ".join(text.strip().strip("\"'").rstrip(".!?,;:").split())


def classify_intent(llm: LLM, question: str, visitor_text: str) -> str:
    """Classify a reply, handling obvious consent/refusal without an API call."""
    normalized = normalize_for_matching(visitor_text)
    if normalized in CONTINUE_WORDS:
        return "DOORGAAN"
    if normalized in STOP_PHRASES:
        return "STOP"
    if not normalized:
        return "ONBEKEND"

    result = llm.prompt(
        f"{prompts.INSTRUCTIONS}\n"
        f"{prompts.INTENT_CLASSIFIER}\n"
        f"Vraag van ARI: {question!r}\n"
        f"Bezoeker zegt: {visitor_text!r}",
        max_tokens=10,
        temperature=0.0,
        fallback="ONBEKEND",
    )
    label = result.strip().upper()
    if label not in {"DOORGAAN", "ANTWOORD", "STOP", "ONBEKEND"}:
        return "ONBEKEND"
    return label


def ask_for_music_answer(
    llm: LLM,
    question: str,
    *,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> Optional[str]:
    """Ask until ARI receives a music answer, or return None when asked to stop."""
    output_fn(f"ARI: {question}")

    while True:
        visitor_text = input_fn("Visitor: ").strip()
        intent = classify_intent(llm, question, visitor_text)

        if intent == "STOP":
            output_fn(f"ARI: {prompts.GOODBYE}")
            return None

        answer = clean_music_answer(visitor_text)
        if intent == "ANTWOORD" and answer:
            return answer

        output_fn(f"ARI: {prompts.UNCLEAR_ANSWER}")


def run_conversation(
    llm: LLM,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> Optional[Playlist]:
    """Run the complete music conversation and return its collected playlist."""
    output_fn(f"ARI: {prompts.GREETING}")

    while True:
        visitor_text = input_fn("Visitor: ").strip()
        intent = classify_intent(llm, prompts.GREETING, visitor_text)

        if intent == "DOORGAAN":
            break
        if intent == "STOP":
            output_fn(f"ARI: {prompts.DECLINE}")
            return None

        output_fn(f"ARI: {prompts.UNCLEAR_CONSENT}")

    artist = ask_for_music_answer(
        llm,
        prompts.MUSIC_QUESTION,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    if artist is None:
        return None

    favorite_song = ask_for_music_answer(
        llm,
        f"{artist}! Heeft u een favoriet nummer?",
        input_fn=input_fn,
        output_fn=output_fn,
    )
    if favorite_song is None:
        return None

    companion_song = ask_for_music_answer(
        llm,
        prompts.PLAYLIST_QUESTION,
        input_fn=input_fn,
        output_fn=output_fn,
    )
    if companion_song is None:
        return None

    playlist = Playlist(
        artist=artist,
        favorite_song=favorite_song,
        companion_song=companion_song,
    )
    final_response = prompts.FINAL_RESPONSE.format(
        favorite_song=playlist.favorite_song,
        companion_song=playlist.companion_song,
    )
    output_fn(f"ARI: {final_response}")
    return playlist


def main() -> None:
    run_conversation(LLM())


if __name__ == "__main__":
    main()
