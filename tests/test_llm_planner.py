from __future__ import annotations

import unittest

from laptop_agent.planner.core import PlanDecision, Planner
from laptop_agent.planner.openai_compatible import _FEWSHOT, _SYSTEM_PROMPT, OpenAICompatiblePlannerProvider


def provider(content: str) -> OpenAICompatiblePlannerProvider:
    return OpenAICompatiblePlannerProvider("key", "model", transport=lambda payload: content)


class LlmPlannerParsingTests(unittest.TestCase):
    def test_plain_json_command(self) -> None:
        decision = provider('{"action":"command","command":"scan files .","confidence":0.9}').plan("look at files", "help", {})
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "scan files .")

    def test_chat_response(self) -> None:
        decision = provider('{"action":"chat","response":"Hello! How can I help?","confidence":0.8}').plan("hi", "help", {})
        self.assertTrue(decision.is_chat)
        self.assertEqual(decision.response, "Hello! How can I help?")

    def test_strips_think_block_and_fences(self) -> None:
        content = '<think>The user greeted me.</think>\n```json\n{"action":"chat","response":"Hi there"}\n```'
        decision = provider(content).plan("hi", "help", {})
        self.assertTrue(decision.is_chat)
        self.assertEqual(decision.response, "Hi there")

    def test_non_json_prose_becomes_chat(self) -> None:
        decision = provider("Sure, I can help you with that!").plan("hi", "help", {})
        self.assertTrue(decision.is_chat)
        self.assertEqual(decision.response, "Sure, I can help you with that!")

    def test_json_embedded_in_text(self) -> None:
        decision = provider('Here you go: {"action":"command","command":"tasks"} hope that helps').plan("show tasks", "help", {})
        self.assertTrue(decision.is_command)
        self.assertEqual(decision.command, "tasks")

    def test_transport_error_returns_chat(self) -> None:
        def boom(payload: dict) -> str:
            raise TimeoutError("slow")

        decision = OpenAICompatiblePlannerProvider("k", "m", transport=boom).plan("hi", "help", {})
        self.assertTrue(decision.is_chat)
        self.assertIn("could not reach", decision.response.lower())

    def test_narrate_returns_plain_text(self) -> None:
        prov = OpenAICompatiblePlannerProvider("k", "m", transport=lambda p: "You have 3 files here.")
        out = prov.narrate("what files are here", "Scanned 3 files.", {"files": [1, 2, 3]})
        self.assertEqual(out, "You have 3 files here.")

    def test_narrate_failure_returns_none(self) -> None:
        def boom(payload: dict) -> str:
            raise TimeoutError("slow")

        prov = OpenAICompatiblePlannerProvider("k", "m", transport=boom)
        self.assertIsNone(prov.narrate("x", "msg", {}))

    def test_nvidia_payload_disables_thinking(self) -> None:
        captured: dict = {}

        def capture(payload: dict) -> str:
            captured.update(payload)
            return '{"action":"chat","response":"ok"}'

        OpenAICompatiblePlannerProvider(
            "k", "m", base_url="https://integrate.api.nvidia.com/v1", transport=capture
        ).plan("hi", "help", {})
        self.assertEqual(captured.get("chat_template_kwargs"), {"enable_thinking": False})

    def test_reasoning_tier_enables_thinking_for_answers(self) -> None:
        captured: dict = {}

        def capture(payload: dict) -> str:
            captured.update(payload)
            return "the answer"

        prov = OpenAICompatiblePlannerProvider(
            "k", "m", base_url="https://integrate.api.nvidia.com/v1",
            transport=capture, reasoning=True, reasoning_budget=16384,
        )
        out = prov.answer("explain transformers", {})
        self.assertEqual(out, "the answer")
        self.assertEqual(captured.get("chat_template_kwargs"), {"enable_thinking": True})
        self.assertEqual(captured.get("reasoning_budget"), 16384)
        self.assertEqual(captured.get("top_p"), 0.95)
        self.assertEqual(captured.get("temperature"), 1.0)
        self.assertGreaterEqual(captured.get("max_tokens"), 16384)

    def test_reasoning_tier_still_routes_without_thinking(self) -> None:
        captured: dict = {}

        def capture(payload: dict) -> str:
            captured.update(payload)
            return '{"action":"chat","response":"ok"}'

        OpenAICompatiblePlannerProvider(
            "k", "m", base_url="https://integrate.api.nvidia.com/v1",
            transport=capture, reasoning=True,
        ).plan("hi", "help", {})
        self.assertEqual(captured.get("chat_template_kwargs"), {"enable_thinking": False})
        self.assertNotIn("reasoning_budget", captured)

    def test_reasoning_ignored_for_non_nvidia(self) -> None:
        captured: dict = {}

        def capture(payload: dict) -> str:
            captured.update(payload)
            return "ok"

        OpenAICompatiblePlannerProvider("k", "m", transport=capture, reasoning=True).answer("hi", {})
        self.assertNotIn("chat_template_kwargs", captured)
        self.assertNotIn("reasoning_budget", captured)

    def test_plan_includes_recent_history(self) -> None:
        captured: dict = {}

        def capture(payload: dict) -> str:
            captured.update(payload)
            return '{"action":"chat","response":"ok"}'

        OpenAICompatiblePlannerProvider("k", "m", transport=capture).plan(
            "what did I ask?",
            "help",
            {},
            [{"role": "user", "text": "summarize the README"}, {"role": "assistant", "text": "Done."}],
        )

        system = captured["messages"][0]["content"]
        self.assertIn("Recent conversation", system)
        self.assertIn("User: summarize the README", system)
        self.assertIn("J.A.R.V.I.S: Done.", system)

    def test_follow_up_gets_the_previous_answer_and_a_referent_note(self) -> None:
        # A long earlier answer must reach the router in full (the old block clipped each
        # turn to 300 chars), and "this" must be resolved to that reply so the model
        # answers from it instead of hunting for a file.
        captured: dict = {}

        def capture(payload: dict) -> str:
            captured.update(payload)
            return '{"action":"chat","response":null}'

        schema = "## Action plan\n" + "\n".join(f"{i}. Create `table_{i}` with id UUID PK." for i in range(1, 30))
        OpenAICompatiblePlannerProvider("k", "m", transport=capture).plan(
            "build an ERD for this", "help", {},
            [{"role": "user", "text": "design a marketplace schema"}, {"role": "assistant", "text": schema}],
        )
        system = captured["messages"][0]["content"]
        self.assertIn("table_29", system)
        self.assertIn("most likely means J.A.R.V.I.S's reply in turn 2", system)
        self.assertIn("follow-up", _SYSTEM_PROMPT)
        self.assertTrue(any("ERD" in user for user, _json in _FEWSHOT))

    def test_context_query_ranks_the_session_instead_of_a_synthesized_prompt(self) -> None:
        # A grounded-news prompt contains "this"; the context must be judged on the
        # user's own question so no bogus "refers back" note is injected.
        captured: dict = {}

        def capture(payload: dict) -> str:
            captured.update(payload)
            return "ok"

        history = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "Hello, Jeevan."}]
        prompt = "Answer the user's question using the web search results below. Prefer this live information. QUESTION: did the war end?"
        OpenAICompatiblePlannerProvider("k", "m", transport=capture).answer(prompt, {}, history=history, context_query="did the war end?")
        self.assertNotIn("most likely means", captured["messages"][0]["content"])
        OpenAICompatiblePlannerProvider("k", "m", transport=capture).answer("shorter", {}, history=history)
        self.assertIn("most likely means", captured["messages"][0]["content"])

    def test_solve_routing_is_taught_to_the_model(self) -> None:
        # The brain must be told it can route decisions/problems to `solve`, both in
        # the system prompt and via a worked few-shot example — so it auto-routes
        # without the user typing the command.
        self.assertIn("solve", _SYSTEM_PROMPT)
        self.assertTrue(any('"command":"solve' in example_json for _user, example_json in _FEWSHOT))

    def test_planner_accepts_legacy_provider_without_history(self) -> None:
        class LegacyProvider:
            def plan(self, text: str, available_commands: str, memory_profile: dict[str, object]) -> PlanDecision:
                return PlanDecision(action="chat", confidence=1, explanation="legacy", response=text)

        decision = Planner(LegacyProvider()).plan("hello", "help", {}, [{"role": "user", "text": "old"}])
        self.assertTrue(decision.is_chat)
        self.assertEqual(decision.response, "hello")


if __name__ == "__main__":
    unittest.main()
