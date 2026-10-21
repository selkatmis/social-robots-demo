
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from mistralai.client import Mistral
from openai import OpenAI

logger = logging.getLogger(__name__)

MODEL = "ministral-8b-2512"

class LLM:
    def __init__(
        self,
        system_prompt="Antwoord altijd in het Nederlands.",
        env_override=True,
    ) -> None:
        # Load .env from the same directory as this file.
        env_path = Path(__file__).resolve().parent / ".env"
        load_dotenv(dotenv_path=env_path, override=env_override)

        self.system_prompt = system_prompt
        self.last_provider = None

        # Primary provider: SURF
        surf_url = os.getenv("SURF_BASE_URL")
        surf_key = os.getenv("SURF_API_KEY")
        self.surf_model = os.getenv("SURF_MODEL")
        self.surf = None

        if surf_url and surf_key and self.surf_model:
            self.surf = OpenAI(
                base_url=surf_url,
                api_key=surf_key,
                timeout=5.0,
                max_retries=0,
            )

        # Backup provider: Mistral
        mistral_key = os.getenv("MISTRAL_API_KEY")

        self.client = (
            Mistral(api_key=mistral_key)
            if mistral_key else None
        )
        self.model = MODEL

        # Aliases for compatibility with friend's version
        self.mistral = self.client
        self.mistral_model = self.model

        if self.surf is None and self.mistral is None:
            raise RuntimeError(
                "No LLM configured. Set SURF credentials "
                "and/or MISTRAL_API_KEY in shared/.env"
            )

    def prompt(
        self,
        text,
        max_tokens=100,
        temperature=0.3,
        fallback=None,
    ):
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": text},
        ]

        self.last_provider = None

        # Attempt SURF first
        if self.surf is not None:
            try:
                response = self.surf.chat.completions.create(
                    model=self.surf_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

                answer = response.choices[0].message.content

                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("SURF returned no text")

                self.last_provider = "SURF"
                return answer.strip()

            except Exception as error:
                logger.warning(
                    "SURF unavailable (%s); trying Mistral",
                    type(error).__name__,
                )

        # Fall back to Mistral
        if self.mistral is not None:
            try:
                response = self.mistral.chat.complete(
                    model=self.mistral_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

                answer = response.choices[0].message.content

                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("Mistral returned no text")

                self.last_provider = "Mistral"
                return answer.strip()

            except Exception as error:
                logger.error(
                    "Mistral unavailable (%s)",
                    type(error).__name__,
                )

        if fallback is not None:
            return fallback

        raise RuntimeError(
            "Both LLM providers are unavailable"
        )
