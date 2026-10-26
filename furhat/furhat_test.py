import asyncio
import os
import re
import sys
import time
from collections import deque
from pathlib import Path

from dotenv import load_dotenv
from furhat_realtime_api import Events
from robond.core.listener_backend import UNSET, ListenerBackend
from robond.core.robot_interface import Expression, Gesture
from robond.robot.furhat import FurhatRobotInterface

from shared.laptop import LaptopRobot
from shared.llm import LLM

LANGUAGE = "nl-NL"
VOICE = "Harper:MAI-Voice-2.1-Flash"
HINTS = ["Furhat", "FOAF", "ARI", "QT", "Q-T", "cutie", "Alice", "Nieuwspoort", "VU"]
END_SPEECH_TIMEOUT = 1.0  
NEW_VISITOR_AFTER = 3     

# Vertical gaze offset in meters (negative = look lower). Adjust if needed.
GAZE_Y_OFFSET = -0.18

GREETING = (
    "[HAPPY] Hallo! Ik ben Furhat, de centrale informatie-robot van dit onderzoeksproject. "
    "Ik vertel u graag over onze sociale robots Ari, cutie en mijzelf, of over ons wetenschappelijk onderzoek. "
    "Waar wilt u meer over weten?"
)
FALLBACK_RETRY = "[CONFUSED] Sorry, ik heb u niet helemaal goed verstaan. Kunt u uw vraag nog eens op een andere manier stellen?"
FALLBACK_HUMAN = "[NEUTRAL] Dat valt helaas buiten wat ik weet. U kunt deze vraag het beste stellen aan de menselijke projectmedewerkers hier bij de stand."

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


class LowerGazeFurhat(FurhatRobotInterface):
    """Furhat implementation with custom lowered gaze tracking and built-in microphone listening."""

    def __init__(self, host, auth_key=None, lang=LANGUAGE, voice=VOICE, y_offset=GAZE_Y_OFFSET):
        self.y_offset = y_offset
        try:
            super().__init__(host=host, auth_key=auth_key, listener=_NoListener(), tts_lang=lang)
        except BaseException:
            self._close_loop()
            raise
        self._run(self._client.request_voice_config(name=voice, language=lang))
        self._run(self._client.request_listen_config(languages=[lang], phrases=HINTS))
        self._run(self._setup_lower_gaze_tracking())

    async def _setup_lower_gaze_tracking(self):
        """Starts user detection events and shifts the gaze slightly downward from the user's camera coordinates."""
        async def on_users_data(event):
            users = event.get("users", [])
            if not users:
                return
            try:
                closest = min(
                    users,
                    key=lambda u: u.get("location", {}).get("z", 999.0)
                    if isinstance(u.get("location"), dict)
                    else 999.0,
                )
                loc = closest.get("location")
                if isinstance(loc, dict) and "x" in loc and "y" in loc and "z" in loc:
                    await self._client.request_attend_location(
                        float(loc["x"]),
                        float(loc["y"]) + self.y_offset,
                        float(loc["z"]),
                    )
            except Exception:
                pass

        if hasattr(self._client, "add_handler") and hasattr(Events, "response_users_data"):
            self._client.add_handler(Events.response_users_data, on_users_data)
        if hasattr(self._client, "request_users_start"):
            try:
                await self._client.request_users_start()
            except Exception:
                pass
        try:
            await self._client.request_attend_location(0.0, self.y_offset, 1.5)
        except Exception:
            pass

    def _close_loop(self):
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
        except BaseException:
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

        async def on_users_data(event):
            users = event.get("users", [])
            if not users:
                return
            try:
                closest = min(
                    users,
                    key=lambda u: u.get("location", {}).get("z", 999.0)
                    if isinstance(u.get("location"), dict)
                    else 999.0,
                )
                loc = closest.get("location")
                if isinstance(loc, dict) and "x" in loc and "y" in loc and "z" in loc:
                    await self._client.request_attend_location(
                        float(loc["x"]),
                        float(loc["y"]) + self.y_offset,
                        float(loc["z"]),
                    )
            except Exception:
                pass

        with self._client.scoped_handlers() as add_handler:
            add_handler(Events.response_hear_end, on_hear_end)
            add_handler(Events.response_listen_end, on_listen_end)
            if hasattr(Events, "response_users_data"):
                add_handler(Events.response_users_data, on_users_data)
            await self._client.request_listen_start(
                no_speech_timeout=timeout, end_speech_timeout=END_SPEECH_TIMEOUT
            )
            try:
                return await asyncio.wait_for(result, timeout + 5)
            except asyncio.TimeoutError:
                await self._client.request_listen_stop()
                return "[TIMEOUT]"


def parse_reply(reply):
    """Split '[HAPPY] Leuk!' into (Expression.HAPPY, 'Leuk!') and drop what Furhat can't say."""
    match = re.match(r"\s*\[(\w+)\]", reply)
    expression = EXPRESSIONS.get(match.group(1).upper()) if match else None
    text = re.sub(r"\[\w+\]", "", reply)
    text = re.sub(r"[^\w\s.,!?;:'\"()€%-]", "", text)
    text = " ".join(text.split())
    end = max(text.rfind(c) for c in ".!?")
    if end > 0:
        text = text[:end + 1]
    return expression, text


def respond(robot, expression, text, note=""):
    label = f"[{expression.name.lower()}] " if expression else ""
    print(f"Furhat: {label}{text}{note}")
    if expression:
        robot.set_expression(expression)
    robot.say(text)


def load_text_file(filename):
    script_dir = Path(__file__).resolve().parent
    file_path = script_dir / filename
    if not file_path.exists():
        return ""
    with open(file_path, "r", encoding="utf-8-sig") as f:
        return f.read().strip()


def build_system_prompt():
    """Builds a comprehensive system prompt combining persona, robot descriptions, and research summaries."""
    papers_text = load_text_file("papers_nl.txt")
    robot_descriptions = load_text_file("info_robot.txt")
    return (
        "Je bent Furhat, de vriendelijke en centrale informatie-robot van dit onderzoeksproject over sociale robots.\n"
        "Jouw doel is om bezoekers helder en concreet te informeren over onze drie sociale robots (Ari, Q-T en jezelf) "
        "en over ons wetenschappelijk onderzoek naar mens-robotinteractie.\n\n"
        "BELANGRIJKE GEDRAGSREGELS:\n"
        "1. Spreek altijd in het Nederlands, in de eerste persoon ('ik'), en spreek ALLEEN de menselijke bezoeker aan (met 'u' of 'je').\n"
        "2. SPREEK NOOIT TEGEN DE ANDERE ROBOTS: Praat over Ari en Q-T uitsluitend in de derde persoon tegen de bezoeker "
        "(zeg dus NOOIT dingen tegen Ari of Q-T zelf, maar zeg tegen de bezoeker bijv.: 'Heeft u de demo van Ari al geprobeerd?').\n"
        "3. Antwoord ALTIJD in maximaal 2 korte zinnen (hooguit 30 woorden) in gewone spreektaal. Geen emojis, geen opsommingen, geen markdown.\n"
        "4. Begin je antwoord NOOIT met een nieuwe introductie zoals 'Ik ben Furhat', want je hebt jezelf al voorgesteld bij de start.\n"
        "5. GEEN INTERNET OF ONLINE ZOEKEN: Je kunt en mag geen dingen online opzoeken of op internet rondkijken. Als de bezoeker vraagt om iets online op te zoeken, "
        "of naar recente gebeurtenissen/nieuws vraagt, verontschuldig je dan dat je dat niet kunt doen, en vraag de bezoeker om een andere vraag te stellen over de robots of het onderzoek.\n"
        "6. STRIKTE FOCUS OP ÉÉN ROBOT: Als de bezoeker specifiek naar Ari vraagt, vertel dan ALLEEN over Ari en noem Q-T of jezelf NIET. "
        "Als de bezoeker specifiek naar Q-T vraagt, vertel dan ALLEEN over Q-T en noem Ari of jezelf NIET. "
        "Als de bezoeker specifiek naar jou (Furhat) vraagt, vertel dan ALLEEN over jezelf.\n"
        "7. DEMO VRAAG AAN DE BEZOEKER: Als de bezoeker specifiek naar Ari of Q-T vraagt, vraag dan aan de bezoeker of hij/zij de demo van die robot al heeft geprobeerd. "
        "Als de bezoeker daarop 'nee' antwoordt, stel dan aan de bezoeker voor om even naar die robot toe te lopen en de demo zelf uit te proberen! "
        "(Vraag dit NOOIT over jezelf/Furhat, want de bezoeker praat al met jou).\n"
        "8. ONDUIDELIJK OF BUITEN SCOPE: Als een vraag totaal onbegrijpelijk is of helemaal niets met robots, onderwijs, zorg of dit project te maken heeft, "
        "zet dan exact het label [ONDUIDELIJK] aan het begin van je antwoord.\n"
        "9. GEZICHTSUITDRUKKING: Begin elk normaal antwoord met precies één label voor je gezichtsuitdrukking: "
        "[HAPPY] bij enthousiasme of een uitnodiging voor een demo, [SURPRISED] bij iets onverwachts, "
        "[SAD] bij een verdrietig onderwerp, [CONFUSED] als je iets niet begrijpt, en anders [NEUTRAL].\n\n"
        f"FEITEN OVER DE ROBOTS:\n{robot_descriptions}\n\n"
        f"SAMENVATTINGEN VAN HET WETENSCHAPPELIJK ONDERZOEK:\n{papers_text}"
    )


def normalize_speech(text):
    """Normalizes common speech-to-text misrecognitions for the robot names."""
    cleaned = text.strip()
    cleaned = re.sub(r"\b(cutie\s*robot|cutie|q\s*t\s*robot|qtrobot|qt)\b", "Q-T", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(arie|ari\s*robot|ali)\b", "Ari", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(voorhout|farhad|ferhat|fur\s*hat)\b", "Furhat", cleaned, flags=re.IGNORECASE)
    return cleaned


def detect_specific_robot_instruction(user_input):
    """Returns an inline instruction to force single-robot adherence and third-person demo prompts."""
    lower = user_input.lower()
    asks_ari = bool(re.search(r"\bari\b", lower))
    asks_qt = bool(re.search(r"\bq-t\b", lower))
    asks_furhat = bool(re.search(r"\bfurhat\b", lower))

    if asks_ari and not asks_qt:
        return (
            " (Instructie: De bezoeker vraagt specifiek naar Ari. Vertel ALLEEN over Ari in de derde persoon, "
            "noem Q-T of jezelf NIET, spreek de robot Ari NIET direct aan, en vraag aan de bezoeker: 'Heeft u de demo bij Ari al geprobeerd?')"
        )
    if asks_qt and not asks_ari:
        return (
            " (Instructie: De bezoeker vraagt specifiek naar Q-T. Vertel ALLEEN over Q-T voor studenten met leerstoornissen in de derde persoon, "
            "noem Ari of jezelf NIET, spreek de robot Q-T NIET direct aan, en vraag aan de bezoeker: 'Heeft u de demo bij Q-T al geprobeerd?')"
        )
    if asks_furhat and not asks_ari and not asks_qt:
        return (
            " (Instructie: De bezoeker vraagt specifiek naar jou, Furhat. Vertel in de eerste persoon ALLEEN over jezelf, "
            "noem Ari of Q-T NIET, en vraag NIET naar een demo.)"
        )
    if re.search(r"\b(nee|nog niet|niet gedaan|no)\b", lower):
        return (
            " (Instructie: Als dit een antwoord is op de vraag over de demo, zeg dan direct tegen de bezoeker dat hij/zij "
            "even naar die robot kan lopen om de demo uit te proberen. Spreek altijd tegen de bezoeker, nooit tegen de andere robot.)"
        )
    return ""


def main():
    load_dotenv(Path(__file__).resolve().parent.parent / "shared" / ".env", override=True)

    system_prompt = build_system_prompt()
    llm = LLM(system_prompt=system_prompt)

    if "--mic" in sys.argv or "--text" in sys.argv:
        robot = LaptopRobot(
            llm,
            typing="--text" in sys.argv,
            hints=HINTS,
            end_silence=END_SPEECH_TIMEOUT,
        )
    else:
        host = os.getenv("FURHAT_HOST")
        if not host:
            raise RuntimeError("FURHAT_HOST is missing from shared/.env (the robot's IP address)")
        try:
            robot = LowerGazeFurhat(host=host, auth_key=os.getenv("FURHAT_AUTH_KEY") or None)
        except RuntimeError as e:
            raise RuntimeError(
                f"{e} Set FURHAT_AUTH_KEY in shared/.env; the key is in Furhat Studio "
                f"(http://{host}) under Real-time API."
            ) from e
        except OSError as e:
            raise RuntimeError(
                f"Can't reach Furhat at {host}. Is the Real-time API started in "
                f"Furhat Studio, and is this laptop on the same network?"
            ) from e

    # 10-turn rolling memory using deque
    conversation_history = deque(maxlen=10)
    unclear_attempts = 0
    silent = 0

    try:
        respond(robot, *parse_reply(GREETING))
        conversation_history.append(("Bezoeker", "[Komt bij de stand]"))
        conversation_history.append(("Furhat", GREETING))

        while True:
            heard = robot.listen(timeout=10)

            # 30-second silence reset (3 x 10s timeouts)
            if heard == "[TIMEOUT]":
                silent += 1
                if silent >= NEW_VISITOR_AFTER:
                    print("[30 seconden geen spraak: gesprek wordt gereset naar het begin]")
                    silent = 0
                    unclear_attempts = 0
                    conversation_history.clear()
                    respond(robot, *parse_reply(GREETING))
                    conversation_history.append(("Bezoeker", "[Komt bij de stand]"))
                    conversation_history.append(("Furhat", GREETING))
                continue

            silent = 0

            user_input = normalize_speech(str(heard))
            if not user_input:
                continue

            # Global 'back' / 'terug' reset command
            if re.search(r"\b(back|terug|opnieuw)\b", user_input.lower()):
                print("[Commando 'terug' herkend: reset naar het begin]")
                unclear_attempts = 0
                conversation_history.clear()
                respond(robot, *parse_reply(GREETING))
                conversation_history.append(("Bezoeker", "[Komt bij de stand]"))
                conversation_history.append(("Furhat", GREETING))
                continue

            # Global 'stop' / 'dag' farewell command
            if re.search(r"\b(stop|tot ziens|doei)\b", user_input.lower()):
                farewell = "[HAPPY] Heel erg bedankt voor uw interesse en een fijne dag verder! Tot ziens!"
                respond(robot, *parse_reply(farewell))
                break

            robot.play_gesture(Gesture.NOD)  # Visual acknowledgement while LLM generates reply

            t = time.time()
            inline_hint = detect_specific_robot_instruction(user_input)
            conversation_history.append(("Bezoeker", f"{user_input}{inline_hint}"))

            try:
                history_str = "\n".join(f"{role}: {msg}" for role, msg in conversation_history)
                answer = llm.prompt(
                    f"Ga verder met dit gesprek (reageer uitsluitend tegen de bezoeker en stel jezelf niet opnieuw voor):\n{history_str}\nFurhat:",
                    max_tokens=90,
                )
            except Exception as e:
                print(f"[llm error] {e}")
                conversation_history.pop()
                respond(robot, *parse_reply(FALLBACK_RETRY))
                continue

            # Check if LLM flagged the input as unclear/out-of-scope -> Two-tier fallback
            if "[ONDUIDELIJK]" in answer.upper():
                conversation_history.pop()  # Remove unclear turn from prompt history
                if unclear_attempts == 0:
                    unclear_attempts += 1
                    answer = FALLBACK_RETRY
                else:
                    unclear_attempts = 0
                    answer = FALLBACK_HUMAN
            else:
                unclear_attempts = 0

            expression, text = parse_reply(answer)
            if not text:
                expression, text = parse_reply(FALLBACK_RETRY)

            # Replace the temporary hinted user entry with the clean user input in the deque
            if conversation_history and conversation_history[-1][0] == "Bezoeker":
                conversation_history[-1] = ("Bezoeker", user_input)

            label = expression.name if expression else "NEUTRAL"
            conversation_history.append(("Furhat", f"[{label}] {text}"))
            respond(robot, expression, text, f"   ({(time.time() - t) * 1000:.0f} ms)")

    except (KeyboardInterrupt, EOFError):
        print("\nStopping...")
    finally:
        robot.stop()
        robot.shutdown()


if __name__ == "__main__":
    main()