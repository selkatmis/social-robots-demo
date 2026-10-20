import os
import time

from dotenv import load_dotenv
from mistralai.client import Mistral

MODEL = "ministral-8b-2512"

class LLM:
    def __init__(self) -> None:
        load_dotenv(override=True)
        api_key = os.getenv("MISTRAL_API_KEY")  

        if not api_key:
            raise RuntimeError("MISTRAL_API_KEY is missing from .env")

        self.client = Mistral(api_key=api_key)
        self.model = MODEL

    def prompt(self, text, max_tokens=100, temperature=0.3):
        response = self.client.chat.complete(
            model=self.model,
            messages=[{"role": "user", "content": text}],
            max_tokens=max_tokens,
            temperature=temperature,)
        return response.choices[0].message.content.strip()