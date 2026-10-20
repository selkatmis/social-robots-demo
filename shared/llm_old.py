"""
Mistral client shared by New Friend and Introduction.

complete() retries a few times and then returns the fallback line, so a
conversation doesn't die halfway when Mistral is rate limited or down.
"""

import os
import time

from dotenv import load_dotenv
from mistralai.client import Mistral


MODEL = "ministral-8b-2512"
RETRY_WAITS = [1, 2, 4]

load_dotenv(override=True)

api_key = os.getenv("MISTRAL_API_KEY")

if not api_key:
    raise RuntimeError(
        "MISTRAL_API_KEY was not found. Make sure it exists in your .env file."
    )

client = Mistral(api_key=api_key)


def complete(prompt, max_tokens=100, temperature=0.3, fallback=None):
    """
    Returns fallback if Mistral still fails after the retries, or raises when
    there is no fallback.
    """
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            response = client.chat.complete(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content.strip()

        except Exception as error:
            summary = str(error).splitlines()[0][:120] if str(error) else type(error).__name__

            # No point retrying a rejected key.
            if "401" in summary or "Unauthorized" in summary:
                raise RuntimeError(f"Mistral rejected the API key: {summary}") from None

            if attempt < len(RETRY_WAITS):
                wait = RETRY_WAITS[attempt]
                print(f"[Mistral error: {summary} - retrying in {wait}s]")
                time.sleep(wait)
                continue

            if fallback is None:
                raise

            print(f"[Mistral unavailable: {summary} - using a fallback line]")
            return fallback
