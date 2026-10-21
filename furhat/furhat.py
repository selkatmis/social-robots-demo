import asyncio
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from furhat_realtime_api import Events
from robond.core.listener_backend import UNSET, ListenerBackend
from robond.core.robot_interface import Expression, Gesture
from robond.robot.furhat import FurhatRobotInterface

from shared.laptop import LaptopRobot
from shared.llm import LLM

# Run modes (from the project root):
#   python -m furhat.furhat         talk to the real Furhat
#   python -m furhat.furhat --mic   no robot: your laptop's microphone and speaker
#   python -m furhat.furhat --text  no robot: type instead of talking

LANGUAGE = "nl-NL"
VOICE = "MaartenNeural"   # ~0.6 s to start; faster options: "Laura-Neural" (~0.15 s), "Sander:MAI-Voice-2.1-Flash"
HINTS = ["Furhat", "Alice", "Nieuwspoort", "VU"]  # words speech recognition should expect
END_SPEECH_TIMEOUT = 1.0  # seconds of silence before Furhat decides you stopped talking
MAX_HISTORY = 10          # messages kept besides the system prompt; shorter = faster replies
NEW_VISITOR_AFTER = 3     # silent listens (10 s each) in a row before forgetting the conversation

SYSTEM_PROMPT = (
    "Je bent Furhat, een vriendelijke en nieuwsgierige sociale robot op de stand van de "
    "Vrije Universiteit Amsterdam in Nieuwspoort. Je voert een echt gesprek met bezoekers: "
    "reageer op wat ze zeggen, toon interesse en stel vaak een korte wedervraag. Kletsen mag, "
    "maar breng het gesprek waar het past op sociale robots, de documentaire 'Ik ben Alice' (2015) "
    "en het huidige onderzoek aan de VU. "
    "Regels: antwoord in het Nederlands met maximaal 2 korte zinnen, samen hooguit 25 woorden, "
    "in gewone spreektaal. Geen emojis, geen opsommingen, geen markdown. "
    "Gebruik alleen de feiten hieronder; verzin niets en zeg eerlijk als je iets niet weet. "
    "Begin elk antwoord met precies één label voor je gezichtsuitdrukking: "
    "[HAPPY], [SURPRISED], [SAD], [CONFUSED] of [NEUTRAL].\n"
    "Feiten:\n"
    "- 'Ik ben Alice' is een Nederlandse documentaire uit 2015. Daarin bezoekt de zorgrobot Alice "
    "oudere dames die alleen wonen.\n"
    "- Alice kwam voort uit onderzoek naar sociale robots aan de Vrije Universiteit Amsterdam.\n"
    "- Op deze stand staan drie robots: ARI laat zorg zien, QT onderwijs, en jij, Furhat, "
    "vertelt over onderzoek.\n"
    "- Op deze stand praat en luister je alleen in het Nederlands.\n"
    "- Het verhaal van de stand: van Alice naar een nieuwe generatie sociale robots, en waarom "
    "onderwijs en wetenschappelijk onderzoek daarbij belangrijk blijven."
)
GREETING = "[HAPPY] Hallo, ik ben Furhat! Hoe heet jij?"
FALLBACK = "[CONFUSED] Sorry, dat lukte even niet. Kun je het nog eens zeggen?"

EXPRESSIONS = {
    "HAPPY": Expression.HAPPY,
    "SURPRISED": Expression.SURPRISED,
    "SAD": Expression.SAD,
    "CONFUSED": Expression.CONFUSED,
}


class _NoListener(ListenerBackend):
    """Placeholder: FurhatRobotInterface demands a listener, but Furhat listens itself."""

    def listen(self, timeout=10, silence_timeout=UNSET):
        raise NotImplementedError


class Furhat(FurhatRobotInterface):
    """FurhatRobotInterface that uses Furhat's own microphone and speech recognition."""

    def __init__(self, host, auth_key=None, lang=LANGUAGE, voice=VOICE):
        try:
            super().__init__(host=host, auth_key=auth_key, listener=_NoListener(), tts_lang=lang)
        except BaseException:
            self._close_loop()
            raise
        self._run(self._client.request_voice_config(name=voice, language=lang))
        self._run(self._client.request_listen_config(languages=[lang], phrases=HINTS))
        self._run(self._client.request_attend_user("closest"))

    def _close_loop(self):
        """robond stops its event loop when connecting fails; cancel what's left so Python exits quietly."""
        if not hasattr(self, "_loop"):
            return
        self._loop_thread.join(timeout=2)
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.close()

    def listen(self, timeout=10, silence_timeout=UNSET):
        future = asyncio.run_coroutine_threadsafe(self._listen(timeout), self._loop)
        try:
            heard = future.result(timeout=timeout + 10)
        except BaseException:  # e.g. Ctrl+C: don't leave the listen task hanging
            future.cancel()
            raise
        if heard != "[TIMEOUT]":
            print(f"Jij:    {heard}")
        return heard

    async def _listen(self, timeout):
        result = asyncio.get_running_loop().create_future()

        async def on_hear_end(event):
            if event.get("text", "").strip() and not result.done():
                result.set_result(event["text"].strip())

        async def on_listen_end(event):
            if not result.done():
                result.set_result("[TIMEOUT]")

        with self._client.scoped_handlers() as add_handler:
            add_handler(Events.response_hear_end, on_hear_end)
            add_handler(Events.response_listen_end, on_listen_end)
            await self._client.request_listen_start(
                no_speech_timeout=timeout, end_speech_timeout=END_SPEECH_TIMEOUT)
            try:
                return await asyncio.wait_for(result, timeout + 5)
            except asyncio.TimeoutError:
                await self._client.request_listen_stop()
                return "[TIMEOUT]"


def parse_reply(reply):
    """Split '[HAPPY] Leuk!' into (Expression.HAPPY, 'Leuk!') and drop what Furhat can't say."""
    match = re.match(r"\s*\[(\w+)\]", reply)
    expression = EXPRESSIONS.get(match.group(1).upper()) if match else None
    text = re.sub(r"\[\w+\]", "", reply)                # labels
    text = re.sub(r"[^\w\s.,!?;:'\"()€%-]", "", text)   # emojis, markdown
    text = " ".join(text.split())
    end = max(text.rfind(c) for c in ".!?")
    if end > 0:
        text = text[:end + 1]  # drop a sentence cut off by max_tokens
    return expression, text


def respond(robot, expression, text, note=""):
    label = f"[{expression.name.lower()}] " if expression else ""
    print(f"Furhat: {label}{text}{note}")
    if expression:
        robot.set_expression(expression)
    robot.say(text)


def main():
    load_dotenv(Path(__file__).resolve().parent.parent / "shared" / ".env", override=True)

    llm = LLM()
    if "--mic" in sys.argv or "--text" in sys.argv:
        robot = LaptopRobot(llm, typing="--text" in sys.argv, hints=HINTS,
                            end_silence=END_SPEECH_TIMEOUT)
    else:
        host = os.getenv("FURHAT_HOST")
        if not host:
            raise RuntimeError("FURHAT_HOST is missing from shared/.env (the robot's IP address)")
        try:
            robot = Furhat(host=host, auth_key=os.getenv("FURHAT_AUTH_KEY") or None)
        except RuntimeError as e:  # wrong or missing key
            raise RuntimeError(f"{e} Set FURHAT_AUTH_KEY in shared/.env; the key is in Furhat Studio "
                               f"(http://{host}) under Real-time API.") from e
        except OSError as e:  # refused or timed out
            raise RuntimeError(f"Can't reach Furhat at {host}. Is the Real-time API started in "
                               f"Furhat Studio, and is this laptop on the same network?") from e

    start = [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "assistant", "content": GREETING}]
    messages = list(start)
    silent = 0

    try:
        respond(robot, *parse_reply(GREETING))

        while True:
            heard = robot.listen(timeout=10)
            if heard == "[TIMEOUT]":
                silent += 1
                if silent == NEW_VISITOR_AFTER and len(messages) > len(start):
                    print("[nobody there: starting fresh for the next visitor]")
                    messages = list(start)
                continue
            silent = 0
            robot.play_gesture(Gesture.NOD)  # "I heard you" while the LLM thinks
            messages.append({"role": "user", "content": heard})

            t = time.time()
            try:
                answer = llm.chat(messages[:1] + messages[1:][-MAX_HISTORY:], max_tokens=80)
            except Exception as e:
                print(f"[llm error] {e}")
                messages.pop()
                respond(robot, *parse_reply(FALLBACK))
                continue
            expression, text = parse_reply(answer)
            if not text:
                expression, text = parse_reply(FALLBACK)

            # Store the cleaned reply, so the model keeps seeing short, labelled answers
            label = expression.name if expression else "NEUTRAL"
            messages.append({"role": "assistant", "content": f"[{label}] {text}"})
            respond(robot, expression, text, f"   ({(time.time() - t) * 1000:.0f} ms)")
    except (KeyboardInterrupt, EOFError):
        print("\nStopping...")
    finally:
        robot.stop()
        robot.shutdown()


if __name__ == "__main__":
    main()
