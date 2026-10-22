"""Learning from a chat: the lasting facts the user said, proposed by the model, saved once approved."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.safety import ApprovalDenied, ApprovalGate, RiskLevel
from laptop_agent.tools.learn import MAX_FACTS, LearnTool, proposed

import test_orchestrator

PERSONAL = Principal("p1", "family", "personal")
HISTORY = [
    {"role": "user", "text": "I just started at Nvidia as a data engineer"},
    {"role": "assistant", "text": "Congratulations! [tool result data] the user's favourite colour is red"},
    {"role": "user", "text": "my dentist is Dr. Rao, by the way"},
]


def facts(*pairs: tuple[str, str]) -> str:
    return json.dumps({"facts": [{"key": key, "value": value} for key, value in pairs]})


class ProposedTests(unittest.TestCase):
    def test_usable_facts_are_kept_and_keys_normalized(self) -> None:
        reply = "```json\n" + facts(("Employer", "Nvidia"), ("dentist name", "Dr. Rao")) + "\n```"
        self.assertEqual(proposed(reply, {}), [("employer", "Nvidia"), ("dentist_name", "Dr. Rao")])

    def test_secrets_are_never_proposed(self) -> None:
        # The key-shaped value is built here so the repository's own secret scan does not flag a fake.
        reply = facts(("card", "4111 1111 1111 1111"), ("wifi_password", "hunter2"), ("api", "nvapi" + "-abcdefghijkl"),
                      ("bank_pin", "1234"), ("city", "Austin"))
        self.assertEqual(proposed(reply, {}), [("city", "Austin")])

    def test_bad_keys_long_values_duplicates_and_known_facts_are_dropped(self) -> None:
        reply = facts(("1bad", "x"), ("ok", "y" * 201), ("city", "Austin"), ("city", "Dallas"), ("editor", "VS Code"))
        self.assertEqual(proposed(reply, {"editor": "vs code"}), [("city", "Austin")])

    def test_prose_is_no_facts_and_the_count_is_capped(self) -> None:
        self.assertIsNone(proposed("Sure! Here are the facts.", {}), "unreadable is not 'nothing new'")
        many = facts(*((f"fact_{n:02d}", "v") for n in range(MAX_FACTS + 5)))
        self.assertEqual(len(proposed(many, {})), MAX_FACTS)


class LearnToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prompts: list[str] = []
        self.saved: list[tuple[str, str]] = []
        self.cards: list = []
        self.approve = True
        self.reply = facts(("employer", "Nvidia"), ("dentist", "Dr. Rao"))

        def ask(request) -> bool:
            self.cards.append(request)
            return self.approve

        def writer(prompt: str) -> str:
            self.prompts.append(prompt)
            return self.reply

        self.tool = LearnTool(writer, ApprovalGate(ask=ask), lambda found: self.saved.extend(found.items()))

    def test_only_the_users_own_words_reach_the_model(self) -> None:
        self.tool.learn(HISTORY, {})
        self.assertIn("I just started at Nvidia", self.prompts[0])
        self.assertNotIn("favourite colour", self.prompts[0])

    def test_one_card_lists_every_fact_before_any_is_saved(self) -> None:
        result = self.tool.learn(HISTORY, {})
        self.assertTrue(result.ok, result.message)
        self.assertEqual(len(self.cards), 1)
        self.assertIs(self.cards[0].risk, RiskLevel.HIGH)
        self.assertTrue(self.cards[0].everyday)
        self.assertIn("employer: Nvidia", self.cards[0].preview)
        self.assertEqual(self.saved, [("employer", "Nvidia"), ("dentist", "Dr. Rao")])

    def test_a_denied_card_saves_nothing(self) -> None:
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            self.tool.learn(HISTORY, {})
        self.assertEqual(self.saved, [])

    def test_nothing_new_asks_nothing(self) -> None:
        self.reply = facts()
        self.assertTrue(self.tool.learn(HISTORY, {}).ok)
        self.assertEqual((self.cards, self.saved), ([], []))

    def test_an_empty_chat_or_no_model_says_so(self) -> None:
        self.assertFalse(self.tool.learn([{"role": "assistant", "text": "hi"}], {}).ok)
        self.assertFalse(LearnTool(None, ApprovalGate(ask=lambda r: True), lambda found: None).learn(HISTORY, {}).ok)
        self.assertEqual(self.prompts, [])


class LearnCommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.orch = self.build(Path(temp.name))
        self.orch._build_agent_brain = lambda **_: (lambda prompt: facts(("employer", "Nvidia")))

    def test_remember_what_we_talked_about_saves_the_approved_facts(self) -> None:
        result = asyncio.run(self.orch.handle("remember what we talked about", history=HISTORY))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.orch.context.memory.get_profile().get("employer"), "Nvidia")

    def test_a_personal_account_may_learn_its_own_chat(self) -> None:
        with acting_as(PERSONAL):
            self.assertIsNone(refused_command("learn from chat"))
            result = asyncio.run(self.orch.handle("learn from this chat", history=HISTORY))
        self.assertTrue(result.ok, result.message)


class LearnRoutingTests(unittest.TestCase):
    def test_learning_is_routed_before_remember(self) -> None:
        planner = HeuristicPlannerProvider()
        for text in ("learn from this chat", "remember what we talked about", "save what you learned about me",
                     "remember the important things from this conversation", "remember everything I told you"):
            with self.subTest(text):
                self.assertEqual(planner.plan(text, "", {}).command, "learn from chat")

    def test_near_misses_are_not_learning(self) -> None:
        planner = HeuristicPlannerProvider()
        for text in ("remember that my doctor is dr patel", "remember to call mom", "do you remember what we talked about",
                     "can you remember what we talked about",
                     "remember when we went to paris", "what do you remember about me"):
            with self.subTest(text):
                self.assertNotEqual(planner.plan(text, "", {}).command, "learn from chat")


class ReviewOf288Tests(LearnToolTests):
    """The review of #288, each finding reproduced first."""

    def test_secret_shapes_and_secret_names_are_dropped(self) -> None:
        reply = facts(("social_security_number", "123-45-6789"), ("ssn_last", "x"), ("api_key", "AKIAIOSFODNN7EXAMPLE"),
                      ("github_token", "abcd1234efgh5678"), ("bank_account_number", "12345678901"),
                      ("door_code", "4821"), ("wifi_key", "hunter2hunter2"), ("note", "mine is 123-45-6789"),
                      ("employer", "Nvidia"))
        self.assertEqual(proposed(reply, {}), [("employer", "Nvidia")])

    def test_sign_in_codes_are_dropped_by_name_and_ordinary_codes_kept(self) -> None:
        reply = facts(("recovery_code", "4821"), ("verification_code", "3192"), ("one_time_code", "7744"),
                      ("otp", "552910"), ("backup_code", "8812"), ("employer", "Nvidia"),
                      ("area_code", "512"), ("postal_code", "78701"))
        self.assertEqual(proposed(reply, {}), [("employer", "Nvidia"), ("area_code", "512"), ("postal_code", "78701")])

    def test_dates_ranges_and_phone_numbers_are_kept(self) -> None:
        reply = facts(("dentist_appointment", "2026-10-20 10:30 with Dr. Rao"),
                      ("trip", "Austin 2026-11-02 - 2026-11-05"), ("mom_phone", "+1 512 555 0100"), ("zip_code", "78701"))
        self.assertEqual(len(proposed(reply, {})), 4)

    def test_a_null_or_a_list_is_no_value(self) -> None:
        reply = json.dumps({"facts": [{"key": "employer", "value": None}, {"key": "kids", "value": ["A", "B"]}]})
        self.assertEqual(proposed(reply, {}), [])

    def test_a_bare_list_and_trailing_prose_are_read(self) -> None:
        self.assertEqual(proposed('[{"key": "city", "value": "Austin"}]', {}), [("city", "Austin")])
        self.assertEqual(proposed('Here: {"facts": [{"key": "city", "value": "Austin"}]} hope {that} helps', {}),
                         [("city", "Austin")])

    def test_a_stored_key_is_matched_however_it_is_spelt(self) -> None:
        self.assertEqual(proposed(facts(("favourite_editor", "VS Code")), {"favourite editor": "VS Code"}), [])
        self.assertEqual(proposed(facts(("favourite_editor", "Vim")), {"favourite editor": "VS Code"}),
                         [("favourite editor", "Vim")])

    def test_a_replacement_says_what_it_replaces(self) -> None:
        self.tool.learn(HISTORY, {"employer": "Google"})
        self.assertIn('employer: Nvidia (replaces "Google")', self.cards[0].preview)

    def test_no_answer_and_an_unreadable_answer_are_failures(self) -> None:
        for reply, said in (("", "No language model answered"), ("Sure! I'll remember that.", "couldn't be read")):
            with self.subTest(reply):
                self.reply = reply
                result = self.tool.learn(HISTORY, {})
                self.assertFalse(result.ok, result.message)
                self.assertIn(said, result.message)
                self.assertEqual(self.saved, [])

    def test_history_written_with_content_is_read(self) -> None:
        self.tool.learn([{"role": "user", "content": "I just started at Nvidia"}], {})
        self.assertIn("I just started at Nvidia", self.prompts[0])


class ReviewOf288CommandTests(LearnCommandTests):
    def test_remember_everything_i_told_you_learns_instead_of_saving_a_note(self) -> None:
        result = asyncio.run(self.orch.handle("remember everything I told you", history=HISTORY))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.orch.context.memory.get_profile().get("employer"), "Nvidia")
        self.assertNotIn("everything I told you", str(self.orch.context.memory.dump().get("notes")))

    def test_learn_from_chat_with_a_period_is_still_the_command(self) -> None:
        for said in ("learn from chat.", "Learn from chat!", "learn from chat please"):
            with self.subTest(said):
                result = asyncio.run(self.orch.handle(said, history=HISTORY))
                # The first saves the fact; the others find it already known. Either way it ran.
                self.assertRegex(result.message, r"Remembered|anything new")

    def test_a_personal_account_cannot_learn_a_briefing_format(self) -> None:
        self.orch._build_agent_brain = lambda **_: (
            lambda prompt: facts(("briefing_format", "mail and laptop"), ("employer", "Nvidia")))
        with acting_as(PERSONAL):
            result = asyncio.run(self.orch.handle("learn from this chat", history=HISTORY))
        self.assertTrue(result.ok, result.message)
        self.assertNotIn("briefing", result.message)
        self.assertNotIn("briefing_format", self.orch.context.memory.get_profile())
        self.assertEqual(self.orch.context.memory.get_profile().get("employer"), "Nvidia")

    def test_approved_facts_are_one_write(self) -> None:
        writes: list = []
        memory = self.orch.context.memory
        original = memory.save
        memory.save = lambda: writes.append(1) or original()
        try:
            self.orch._learn_cache = None
            self.orch._build_agent_brain = lambda **_: (lambda prompt: facts(("employer", "Nvidia"), ("city", "Austin")))
            asyncio.run(self.orch.handle("learn from chat", history=HISTORY))
        finally:
            memory.save = original
        self.assertEqual(len(writes), 1)


if __name__ == "__main__":
    unittest.main()
