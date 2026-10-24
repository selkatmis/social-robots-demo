from shared.llm import LLM
import qt.prompts as template

llm = LLM()

answer = llm.prompt(f"{template.INSTRUCTIONS}, {template.CHOICE_CLASSIFIER_1},\
Kind zegt:'Doe maar die laatste'")

print(answer)

answer = llm.prompt(f"")