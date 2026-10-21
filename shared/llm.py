import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from mistralai.client import Mistral
from openai import OpenAI

logger = logging.getLogger(__name__)

MODEL = "ministral-8b-2512"
STT_MODEL = "voxtral-mini-latest"

class LLM:
    def __init__(self) -> None:
        load_dotenv(Path(__file__).parent / ".env", override=True)
        api_key = os.getenv("MISTRAL_API_KEY")  

        if not api_key:
            raise RuntimeError("MISTRAL_API_KEY is missing from .env")

        self.client = Mistral(api_key=api_key)
        self.model = MODEL

    def prompt(self, text, max_tokens=100, temperature=0.3):
        return self.chat([{"role": "user", "content": text}], max_tokens, temperature)

    def chat(self, messages, max_tokens=100, temperature=0.3):
        """Send a whole conversation (list of {"role", "content"} dicts) and return the reply."""
        response = self.client.chat.complete(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,)
        return response.choices[0].message.content.strip()

    def transcribe(self, wav_bytes, language="nl", hints=None):
        """Speech to text with Mistral's Voxtral. `hints` are words it should expect, e.g. names."""
        response = self.client.audio.transcriptions.complete(
            model=STT_MODEL,
            file={"file_name": "audio.wav", "content": wav_bytes},
            language=language,
            context_bias=hints,)
        return response.text.strip()