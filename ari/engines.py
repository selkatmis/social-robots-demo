"""Pick ARI's conversation: the VUmc guide or the open conversation demo."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ari import guardian_prompts, prompts
from ari.guardian import GuardianEngine
from ari.responses import ConversationEngine
from shared.llm import LLM


MODES = ("guardian", "companion")


def build_engine(mode: str, llm_factory: Callable[..., Any] = LLM) -> Any:
    if mode == "guardian":
        return GuardianEngine(llm_factory(system_prompt=guardian_prompts.build_system_prompt()))
    return ConversationEngine(llm_factory(system_prompt=prompts.SYSTEM_PROMPT))
