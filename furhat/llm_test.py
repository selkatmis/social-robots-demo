from shared.llm import LLM
llm = LLM()

persona = "Je bent een sociale robot genaamd Furhat, je legt mensen uit over het onderzoek zelf.."
rules = "Regels: Antwoord met 1 zin. Geen emojis. Geen vragen. Verzin niet nieuwe dingen."

answer = llm.prompt("Geef een voorbeeld met pizza met een korte antwoord.")
print(answer)

persona_answer = llm.prompt(f"{persona}, wat is je naam?\n\n{rules}")
print(persona_answer)

import os
import re
from pathlib import Path
from shared.llm import LLM

# Initialize LLM module
llm = LLM()

# Persona and Rules
PERSONA = "Je bent een sociale robot genaamd Furhat, je legt mensen uit over het onderzoek zelf."
RULES = "Regels: Antwoord met 1 zin. Geen emojis. Geen vragen. Verzin niet nieuwe dingen."

# Expanded Main Menu Options
MAIN_OPTIONS = {
    "1": "Onderzoekssamenvattingen",
    "2": "Informatie over de gebruikte robots",
    "3": "Toekomstige doelen en doelstellingen"
}

ROBOT_OPTIONS = {
    "1": "Furhat",
    "2": "Qtrobot",
    "3": "Ari"
}

TOPIC_CATEGORIES = {
    "1": {
        "topic": "Psychologie, Heuristieken en Binding",
        "papers": [
            "Banks, Edwards, & Westerman (2021)",
            "Konijn, Preciado Vanegas, & van Minkelen (2025)",
            "Broadbent (2017)"
        ]
    },
    "2": {
        "topic": "Embodiment en Taxonomie",
        "papers": [
            "Hellström, Kaiser, & Bensch (2024)"
        ]
    },
    "3": {
        "topic": "Personalisatie en Cultuur",
        "papers": [
            "Gasteiger, Hellou, & Ahn (2023)"
        ]
    },
    "4": {
        "topic": "Onderwijs en Sociale Leren",
        "papers": [
            "Belpaeme, Kennedy, Ramachandran, Scassellati, & Tanaka (2018)",
            "Barry, Neumann, Clough, & Waters (2026)",
            "Konijn, Smakman, & van den Berghe (2020)"
        ]
    },
    "5": {
        "topic": "Gezondheidszorg en Welzijn",
        "papers": [
            "Konijn & Hoorn (2020)",
            "Laban, Kappas, Morrison, & Cross (2024)"
        ]
    }
}

def load_text_file(filename):
    """Safely loads text content from a specified file."""
    script_dir = Path(__file__).resolve().parent
    file_path = script_dir / filename
    
    if not file_path.exists():
        return f"Kan bestand {filename} niet vinden."

    with open(file_path, "r", encoding="utf-8-sig") as f:
        return f.read().strip()

def load_robot_info(robot_name, filename="info_robot.txt"):
    """Extracts only the section for the specific robot using whole-word regex matching."""
    script_dir = Path(__file__).resolve().parent
    file_path = script_dir / filename
    
    if not file_path.exists():
        return f"Kan bestand {filename} niet vinden."

    with open(file_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    # Use whole-word boundary matching (\b) so "Ari" doesn't match inside other words
    pattern = rf"(?:^|\n)\s*({re.escape(robot_name)})\b.*?(?=(?:\n\s*(?:Furhat|Qtrobot|Ari)\b)|\Z)"
    match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
    
    if match:
        return match.group(0).strip()
    
    return f"Geen specifieke informatie gevonden over {robot_name}."

def load_summaries(filename="papers_nl.txt"):
    """Loads paper summaries robustly."""
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

def speak_and_print(text):
    """Outputs text for the user."""
    print(f"\n[Furhat Speaks]: {text}")

def match_topic_with_llm(user_input):
    """Uses LLM to match typed text to category 1-5."""
    categories_str = "\n".join([f"[{k}] {v['topic']}" for k, v in TOPIC_CATEGORIES.items()])
    prompt = (
        f"Kies op basis van wat de gebruiker zei ('{user_input}') "
        f"welk categorie-nummer (1, 2, 3, 4 of 5) hier het beste bij past.\n"
        f"Categorieën:\n{categories_str}\n"
        f"Antwoord ALLEEN met het cijfer van de categorie, of '0' als het onbekend is."
    )
    response = llm.prompt(prompt).strip()
    for key in TOPIC_CATEGORIES.keys():
        if key in response:
            return key
    return None

def run_interaction():
    summaries = load_summaries()
    if not summaries:
        print("Error: No paper summaries found.")
        return

    while True:
        # 1. First Greet
        intro_prompt = f"{PERSONA}, begroet de gebruiker vriendelijk.\n\n{RULES}"
        speak_and_print(llm.prompt(intro_prompt))

        # 2. Ask to start
        start_prompt = f"{PERSONA}, vraag de gebruiker om op start te drukken om te beginnen.\n\n{RULES}"
        speak_and_print(start_prompt)

        start_input = input("\n[Jouw invoer (typ 'start' of 'ja' om door te gaan)]: ").strip()
        if not start_input or "stop" in start_input.lower() or "dag" in start_input.lower():
            exit_prompt = f"{PERSONA}, neem vriendelijk afscheid van de gebruiker.\n\n{RULES}"
            speak_and_print(llm.prompt(exit_prompt))
            break

        # 3. Main Menu Loop (Presented one by one)
        back_to_main = False
        while not back_to_main:
            speak_and_print("Laten we de opties langslopen.")
            
            chosen_main = None
            for opt_key, opt_desc in MAIN_OPTIONS.items():
                speak_and_print(f"Wil je meer weten over optie {opt_key}: {opt_desc}?")
                response = input(f"\n[Jouw invoer (typ 'ja', 'nee', 'terug' of 'start')]: ").strip().lower()

                if "start" in response or "begin" in response:
                    speak_and_print("We gaan terug naar het begin.")
                    back_to_main = True
                    break
                if "terug" in response:
                    break
                if "stop" in response or "dag" in response:
                    exit_prompt = f"{PERSONA}, neem vriendelijk afscheid van de gebruiker.\n\n{RULES}"
                    speak_and_print(llm.prompt(exit_prompt))
                    return

                if "ja" in response or opt_key in response:
                    chosen_main = opt_key
                    break
            
            if back_to_main:
                break 

            if not chosen_main:
                speak_and_print("Geen optie geselecteerd. Laten we opnieuw beginnen.")
                continue

            # --- OPTION 1: Research Summaries ---
            if chosen_main == "1":
                in_categories = True
                while in_categories:
                    cat_overview = "Je kunt kiezen uit de volgende onderzoekscategorieën: "
                    for k, v in TOPIC_CATEGORIES.items():
                        cat_overview += f"Categorie {k}: {v['topic']}. "
                    speak_and_print(cat_overview)

                    user_input = input("\n[Kies een categorie nummer (1-5), of typ 'terug' / 'start']: ").strip()

                    if not user_input:
                        continue
                    if "start" in user_input.lower() or "begin" in user_input.lower():
                        speak_and_print("We gaan terug naar het begin.")
                        back_to_main = True
                        break
                    if "terug" in user_input.lower():
                        break

                    topic_key = match_topic_with_llm(user_input)
                    if not topic_key or topic_key not in TOPIC_CATEGORIES:
                        speak_and_print("Dat begreep ik niet helemaal.")
                        continue

                    selected_category = TOPIC_CATEGORIES[topic_key]
                    confirm_prompt = f"{PERSONA}, reageer enthousiast op de keuze voor {selected_category['topic']}.\n\n{RULES}"
                    speak_and_print(llm.prompt(confirm_prompt))

                    # Paper Loop
                    in_papers = True
                    while in_papers:
                        paper_list = selected_category["papers"]
                        paper_prompt_text = "Welk specifiek onderzoek wil je bespreken? Opties: "
                        for idx, paper in enumerate(paper_list, 1):
                            paper_prompt_text += f"Optie {idx}: {paper}. "
                        
                        speak_and_print(paper_prompt_text)
                        paper_input = input("\n[Kies een paper nummer, of typ 'terug' / 'start']: ").strip()

                        if not paper_input:
                            continue
                        if "start" in paper_input.lower() or "begin" in paper_input.lower():
                            speak_and_print("We gaan terug naar het begin.")
                            back_to_main = True
                            in_papers = False
                            break
                        if "terug" in paper_input.lower():
                            in_papers = False
                            break

                        selected_paper_name = None
                        for idx, paper in enumerate(paper_list, 1):
                            if str(idx) in paper_input or any(w.lower() in paper_input.lower() for w in paper.split()[:2]):
                                selected_paper_name = paper
                                break

                        if selected_paper_name:
                            matched_summary = None
                            for key, text in summaries.items():
                                if selected_paper_name.split("(")[0].strip() in key:
                                    matched_summary = text
                                    break
                            
                            if matched_summary:
                                summary_prompt = f"{PERSONA}, leg dit onderzoek uit op basis van deze tekst: {matched_summary}\n\n{RULES}"
                                speak_and_print(llm.prompt(summary_prompt))
                            else:
                                speak_and_print("Kan de samenvatting niet vinden.")
                        else:
                            speak_and_print("Onderzoek niet herkend.")
                    
                    if back_to_main:
                        break

            # --- OPTION 2: Robots Used (Select specific robot) ---
            elif chosen_main == "2":
                in_robots = True
                while in_robots:
                    robot_menu_text = "Over welke robot wil je meer horen? "
                    for r_key, r_name in ROBOT_OPTIONS.items():
                        robot_menu_text += f"[{r_key}] {r_name}. "
                    
                    speak_and_print(robot_menu_text)
                    robot_choice = input("\n[Kies een robot nummer (1-3), of typ 'terug' / 'start']: ").strip().lower()

                    if "start" in robot_choice or "begin" in robot_choice:
                        speak_and_print("We gaan terug naar het begin.")
                        back_to_main = True
                        in_robots = False
                        break
                    if "terug" in robot_choice:
                        in_robots = False
                        break

                    selected_robot = ROBOT_OPTIONS.get(robot_choice)
                    if selected_robot:
                        robot_content = load_robot_info(selected_robot, "info_robot.txt")
                        speak_and_print(robot_content)
                        
                        feedback = input("\n[Druk op Enter voor een andere robot, of typ 'terug' / 'start']: ").strip().lower()
                        if "start" in feedback or "begin" in feedback:
                            speak_and_print("We gaan terug naar het begin.")
                            back_to_main = True
                            in_robots = False
                            break
                        elif "terug" in feedback:
                            in_robots = False
                            break
                    else:
                        speak_and_print("Dat is geen geldig robotnummer.")

            # --- OPTION 3: Future Aims and Goals ---
            elif chosen_main == "3":
                project_content = load_text_file("info_project.txt")
                speak_and_print(project_content)
                
                user_feedback = input("\n[Druk op Enter om door te gaan, of typ 'terug' / 'start']: ").strip().lower()
                if "start" in user_feedback or "begin" in user_feedback:
                    speak_and_print("We gaan terug naar het begin.")
                    back_to_main = True
                    break
                elif "terug" in user_feedback:
                    continue

if __name__ == "__main__":
    run_interaction()