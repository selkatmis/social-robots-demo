from shared.llm import LLM
llm = LLM()

persona = "Je bent een sociale robot genaamd Furhat, je legt mensen uit over het onderzoek zelf.."
rules = "Regels: Antwoord met 1 zin. Geen emojis. Geen vragen. Verzin niet nieuwe dingen."

answer = llm.prompt("Geef een voorbeeld met pizza met een korte antwoord.")
print(answer)

persona_answer = llm.prompt(f"{persona}, wat is je naam?\n\n{rules}")
print(persona_answer)
