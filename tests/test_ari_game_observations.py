"""Visitor wording regressions; no robot, microphone or live LLM calls."""

import unittest
from unittest.mock import Mock

from ari import prompts
from ari.difference_game import DifferenceGame
from ari.responses import ConversationEngine


class GameObservationTests(unittest.TestCase):
    def start_game(self):
        llm = Mock()
        llm.prompt.side_effect = AssertionError("Unexpected LLM call")
        engine = ConversationEngine(llm)
        engine.start()
        engine.state["phase"] = "ACTIVITY_CHOICE"
        engine._entry_pending = False
        turn = engine.handle_input("Het spelletje")
        self.assertEqual(engine.state["phase"], "ZOEK_DE_VERSCHILLEN")
        return engine, llm, turn

    def test_missing_banana_and_transcript_asr_are_accepted_without_llm(self):
        for text in (
            "Er mist een banaan.",
            "Ik zie ook dat er een banaan mist.",
            "Er ontbreekt een banaan.",
            "Er is een banaan meest.",
            "Er is een banaan minder.",
            "Links heb je 2 bananen en rechts heb je er 3.",
            "Links zijn er twee bananen en rechts drie.",
            "Links liggen twee bananen en rechts drie.",
        ):
            with self.subTest(text=text):
                engine, llm, _ = self.start_game()
                turn = engine.handle_input(text)
                self.assertEqual(engine.difference_game.found_ids, {"bananen"})
                self.assertEqual(engine.state["game_mistakes_since_hint"], 0)
                self.assertNotIn("[GAME] UNVERIFIED", turn.diagnostics)
                self.assertNotIn("[GAME] HINT", turn.diagnostics)
                llm.prompt.assert_not_called()

    def test_repeat_wording_counts_same_difference_once(self):
        engine, _, _ = self.start_game()
        engine.handle_input("Er mist een banaan")
        turn = engine.handle_input("Er is een banaan meest")
        self.assertEqual(engine.difference_game.score, 1)
        self.assertIn("[GAME] DUPLICATE", turn.diagnostics)

    def test_negation_wrong_counts_other_objects_and_reversal_do_not_fast_match(self):
        game = DifferenceGame()
        for text in (
            "Banaan", "Bananen", "Er mist geen banaan", "Er mist een appel",
            "Er missen twee bananen", "Rechts mist een banaan",
            "Links zijn er drie bananen en rechts twee",
            "Links zijn er vier bananen en rechts drie",
            "Er mist een banaan, maar dat is niet waar",
            "Er mist een banaan en het gordijn is rood",
        ):
            with self.subTest(text=text):
                self.assertIsNone(game.interpret_known_observation(text))

    def test_reversed_answer_still_uses_validator(self):
        engine, llm, _ = self.start_game()
        llm.prompt.side_effect = None
        llm.prompt.return_value = {"intent": "ANSWER", "matches": [{
            "difference_id": "bananen", "feature": "aantal bananen",
            "picture_a": "drie bananen", "picture_b": "twee bananen",
            "confidence": .95,
        }]}
        engine.handle_input("Links zijn er drie bananen en rechts twee")
        self.assertEqual(engine.difference_game.score, 0)
        llm.prompt.assert_called_once()

    def test_instruction_is_example_and_does_not_award_a_point(self):
        engine, _, turn = self.start_game()
        self.assertEqual(engine.difference_game.score, 0)
        self.assertTrue(any("bananen" in text for text in turn.utterances))
        for introduction in prompts.GAME_INTRODUCTIONS:
            self.assertIn("bijvoorbeeld", introduction)
            self.assertIn("links", introduction)
            self.assertIn("rechts drie", introduction)
            example = introduction.split(":", 1)[1].strip()
            self.assertIsNotNone(engine.difference_game.interpret_known_observation(example))

    def test_prompt_keeps_semantic_recognition_for_other_phrasings(self):
        prompt = DifferenceGame().interpretation_prompt("Bij de tros is er eentje minder")
        self.assertIn("Er mist een banaan", prompt)
        self.assertIn("banaan meest", prompt)
        self.assertIn("niet verplicht", prompt)
        self.assertIn("omgekeerde", prompt)

    def test_known_observation_preserves_stop_and_round_progress(self):
        engine, llm, _ = self.start_game()
        engine.handle_input("Er mist een banaan")
        self.assertEqual(engine.difference_game.target_count, 3)
        turn = engine.handle_input("Ik wil stoppen")
        self.assertEqual(engine.state["phase"], "STANDBY")
        self.assertEqual(engine.difference_game.score, 0)
        self.assertIn("[PHASE] GOODBYE", turn.diagnostics)
        llm.prompt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
