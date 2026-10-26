import os
from pathlib import Path
from collections import deque
from shared.llm import LLM
from furhat.prompts import (
    PERSONA, 
    RULES, 
    OPEN_QUESTION_INSTRUCTIONS, 
    INTENT_ROUTER_PROMPT,
    ROBOT_DESCRIPTIONS
)

# Initialize the LLM module
llm = LLM()

def load_text_file(filename):
    script_dir = Path(__file__).resolve().parent
    file_path = script_dir / filename
    if not file_path.exists():
        return f"Kan bestand {filename} niet vinden."
    with open(file_path, "r", encoding="utf-8-sig") as f:
        return f.read().strip()

def load_summaries(filename="papers_nl.txt"):
    script_dir = Path(__file__).resolve().parent
    file_path = script_dir / filename
    if not file_path.exists():
        file_path = script_dir / "papers.txt"
        if not file_path.exists():
            return {}

    with open(file_path, "r", encoding="utf-8-sig") as f:
        lines = f.readlines()
    
    summaries = {}
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        if " – " in line_clean or " - " in line_clean:
            paper_name = line_clean.split(" – ")[0].strip() if " – " in line_clean else line_clean.split(" - ")[0].strip()
            summaries[paper_name] = line_clean
    return summaries

def ask_llm(prompt_text, conversation_history, is_intro=False):
    """Sends prompt wrapped with persona, rules, and deque-based conversation memory."""
    history_block = ""
    if conversation_history:
        history_block = "\n".join([f"Gebruiker: {u}\nFurhat: {r}" for u, r in conversation_history])
        history_block = f"\nGesprekshistorie:\n{history_block}\n"

    intro_constraint = (
        "BELANGRIJK: Begin je antwoord NOOIT met 'Ik ben Furhat' of een nieuwe introductie, "
        "omdat je jezelf al hebt voorgesteld."
    ) if not is_intro else ""

    full_prompt = f"{PERSONA}\n{ROBOT_DESCRIPTIONS}{history_block}\n\nTaak: {prompt_text}\n\n{intro_constraint}\n\n{RULES}"
    return llm.prompt(full_prompt).strip()

def route_intent(user_input, conversation_history):
    """Uses LLM to route free-form user speech using conversation history context."""
    history_block = ""
    if conversation_history:
        history_block = "\n".join([f"Gebruiker: {u}\nFurhat: {r}" for u, r in conversation_history])
        history_block = f"\nLaatste context:\n{history_block}\n"

    prompt = f"{INTENT_ROUTER_PROMPT}{history_block}\n\nInvoer van gebruiker: '{user_input}'"
    response = llm.prompt(prompt).strip().lower()
    
    for category in ["robots", "research", "open", "human"]:
        if category in response:
            return category
    return "open"

def run_conversation():
    summaries = load_summaries()
    if not summaries:
        print("[Systeem]: Fout: Geen samenvattingen gevonden.")
        return

    while True:
        # Initialize a deque with a maximum capacity of 10 slots for memory
        conversation_history = deque(maxlen=10)

        # 1. Greet the user and explain function (is_intro=True)
        intro_prompt = (
            "Introduceer jezelf als Furhat. Leg kort uit dat je de informatie-robot bent die informatie verstrekt "
            "over sociale robots en onderzoek, en vraag waar de gebruiker meer over wil weten."
        )
        intro_msg = ask_llm(intro_prompt, conversation_history, is_intro=True)
        print(f"\n[Furhat]: {intro_msg}")

        # 2. Main conversational loop
        in_session = True
        while in_session:
            user_input = input("\n[Jouw invoer (typ 'back' om opnieuw te beginnen)]: ").strip()
            
            if not user_input:
                continue

            if "back" in user_input.lower() or "terug" in user_input.lower():
                print("\n[Systeem]: Terug naar het begin...")
                break 

            if "stop" in user_input.lower() or "dag" in user_input.lower():
                farewell = ask_llm("Neem vriendelijk afscheid van de gebruiker.", conversation_history, is_intro=False)
                print(f"\n[Furhat]: {farewell}")
                return

            # Classify user intent dynamically using history
            intent = route_intent(user_input, conversation_history)

            if intent == "robots":
                robot_content = load_text_file("info_robot.txt")
                robot_prompt = (
                    f"De gebruiker vraagt naar de robots ('{user_input}'). "
                    f"Geef direct en beknopt antwoord op basis van dit document. "
                    f"Als het over Furhat gaat, spreek in de eerste persoon. "
                    f"Informatie: {robot_content}"
                )
                answer = ask_llm(robot_prompt, conversation_history, is_intro=False)
                print(f"\n[Furhat]: {answer}")

            elif intent == "research":
                all_summaries_text = " ".join(list(summaries.values()))
                research_prompt = (
                    f"De gebruiker vraagt naar onderzoek ('{user_input}'). "
                    f"Geef direct een beknopte toelichting of samenvatting op basis van deze data: {all_summaries_text}"
                )
                answer = ask_llm(research_prompt, conversation_history, is_intro=False)
                print(f"\n[Furhat]: {answer}")

            elif intent == "human":
                referral_prompt = (
                    f"De vraag van de gebruiker ('{user_input}') valt buiten je scope. "
                    f"Zeg direct dat de gebruiker contact kan opnemen met de menselijke projectmedewerkers."
                )
                answer = ask_llm(referral_prompt, conversation_history, is_intro=False)
                print(f"\n[Furhat]: {answer}")

            else:  # 'open' questions
                open_prompt = (
                    f"{OPEN_QUESTION_INSTRUCTIONS}\n\n"
                    f"Geef direct antwoord op deze vraag: {user_input}"
                )
                answer = ask_llm(open_prompt, conversation_history, is_intro=False)
                print(f"\n[Furhat]: {answer}")

            # Append the current turn to the deque (automatically manages size up to 10 slots)
            conversation_history.append((user_input, answer))

if __name__ == "__main__":
    run_conversation()