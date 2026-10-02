from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request

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
        """A transport error that is not a timeout: the endpoint really is unreachable, so
        saying so is right. A *timeout* is deliberately different now — see
        RoutingDeadlineTests, where it falls through and lets the chat tier answer rather
        than reporting a working model as unreachable."""
        def boom(payload: dict) -> str:
            raise urllib.error.URLError("connection refused")

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
        self.assertEqual(captured.get("top_p"), 0.95)
        self.assertEqual(captured.get("temperature"), 1.0)
        self.assertGreaterEqual(captured.get("max_tokens"), 16384)

    def test_reasoning_budget_is_never_sent_to_the_endpoint(self) -> None:
        # NVIDIA moved to the V2 model runner and rejects it: every ultra turn came back
        # HTTP 400 "thinking_token_budget is not yet supported by the V2 model runner",
        # which health read as congestion. The budget now only sizes max_tokens locally.
        captured: dict = {}

        def capture(payload: dict) -> str:
            captured.update(payload)
            return "the answer"

        provider = OpenAICompatiblePlannerProvider(
            "k", "m", base_url="https://integrate.api.nvidia.com/v1",
            transport=capture, reasoning=True, reasoning_budget=16384,
        )
        provider.answer("explain transformers", {})
        self.assertNotIn("reasoning_budget", captured)
        self.assertEqual(captured.get("chat_template_kwargs"), {"enable_thinking": True})
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



class ChatGuardTests(unittest.TestCase):
    """A tool result reaches the next turn as transcript, and the model learned to copy the
    tool's shape: after one generated picture it answered with "Here is a diagram..." plus a
    link to the previous turn's file, so the page showed a broken image and a Save control
    with nothing behind it."""

    def chat_system_prompt(self) -> str:
        captured: list[dict] = []

        def transport(payload):
            captured.append(payload)
            return "ok"

        provider = OpenAICompatiblePlannerProvider(
            api_key="k", model="m", base_url="https://example.invalid/v1", transport=transport
        )
        provider.answer("hello", {})
        return captured[0]["messages"][0]["content"]

    def test_chat_is_told_not_to_claim_it_already_made_a_file(self) -> None:
        system = " ".join(self.chat_system_prompt().split())
        self.assertIn("never say you have already made or attached one", system.lower())
        self.assertIn("never write a markdown image link", system.lower())
        self.assertIn("JSON of a tool result", " ".join(system.split()))

    def test_streaming_chat_carries_the_same_guard(self) -> None:
        # stream_answer builds its own request, so assert it uses the same constant rather
        # than letting the two prompts drift apart.
        import inspect

        source = inspect.getsource(OpenAICompatiblePlannerProvider.stream_answer)
        self.assertIn("_NO_TOOL_CLAIMS", source)

    def test_chat_is_not_told_the_assistant_cannot_make_files(self) -> None:
        # The first wording over-corrected: the model began telling users "I can't generate
        # images", which is false — the assistant does generate them, through a tool.
        system = " ".join(self.chat_system_prompt().split())
        self.assertIn("never tell the user that a picture or document is impossible", system.lower())
        self.assertNotIn("you cannot create images", system.lower())

    def test_a_diagram_is_drawn_in_the_reply_not_requested_again(self) -> None:
        # Measured after the capability statement landed: "draw a flowchart of how a pull
        # request gets merged" was answered "I'll provide the Mermaid syntax for you to
        # request the actual drawing. To draw this flowchart, please ask me to: draw a
        # flowchart of how a pull request gets merged" — the model handed the request back.
        # "Say what to ask for" is right for a picture or a document and wrong for a
        # diagram, which this reply is supposed to contain.
        system = " ".join(self.chat_system_prompt().split()).lower()
        self.assertIn("you draw it yourself, in this reply", system)
        self.assertIn("never repeat their own request back at them", system)
        self.assertIn("they already asked, so draw it now", system)

    def test_chat_is_told_what_the_assistant_can_actually_do(self) -> None:
        # Reported: "can you download something for me" was answered "I can't directly
        # download files from the internet or access external resources", and "is it safe to
        # run risky commands" with "I do not have direct access to your system's shell or
        # file system". Both false — there is a download tool and a shell tool, each behind
        # the approval gate. Told only what it must not claim, the model guessed low.
        system = " ".join(self.chat_system_prompt().split()).lower()
        for ability in (
            "download files",
            "run shell commands",
            "send and search email",
            "search the web",
            "ocr images",
        ):
            self.assertIn(ability, system, ability)
        self.assertIn("never tell the user you cannot reach the internet", system)
        self.assertIn("asks the user to approve it first", system)

    def test_chat_knows_how_the_app_is_actually_started(self) -> None:
        # Reported: "how do I start the app in a browser tab" was answered with
        # "try http://localhost:3000" — a port nobody uses. #63 stopped it *acting* on
        # invented targets; it could still say them.
        system = " ".join(self.chat_system_prompt().split())
        self.assertIn("python -m laptop_agent.webui", system)
        self.assertIn("8770", system)
        self.assertNotIn("3000", system)

    def test_streaming_chat_carries_the_capability_statement_too(self) -> None:
        import inspect

        source = inspect.getsource(OpenAICompatiblePlannerProvider.stream_answer)
        self.assertIn("_CAPABILITIES", source)

    def test_chat_is_told_it_has_no_follow_up_turn(self) -> None:
        # Without this it answered "Let me create that for you now" and then never did.
        system = " ".join(self.chat_system_prompt().split())
        self.assertIn("no follow-up turn", system.lower())

    def test_chat_is_told_quoted_tool_data_is_not_a_template(self) -> None:
        system = " ".join(self.chat_system_prompt().split())
        self.assertIn("rather than a format to copy", system.lower())

    def test_the_routing_prompt_is_left_alone(self) -> None:
        # Routing must keep emitting JSON; the guard is for conversational replies only.
        captured: list[dict] = []

        def transport(payload):
            captured.append(payload)
            return '{"action":"chat","command":null,"response":"hi"}'

        provider = OpenAICompatiblePlannerProvider(
            api_key="k", model="m", base_url="https://example.invalid/v1", transport=transport
        )
        provider.plan("hello", "commands", {})
        self.assertNotIn("cannot create images", captured[0]["messages"][0]["content"])


class ChatPromptActionClaimsTests(unittest.TestCase):
    """Asked to put two windows side by side, the chat tier replied "May I resize and
    reposition...", then "Approved. [Window arrangement initiated]" — and nothing had
    happened. The prompt forbade claiming a *file* was made; it said nothing about
    claiming an action on the machine."""

    def test_the_prompt_forbids_asking_for_permission_and_claiming_action(self) -> None:
        from laptop_agent.planner.openai_compatible import _NO_TOOL_CLAIMS

        lowered = _NO_TOOL_CLAIMS.lower()
        for phrase in ("never ask", "permission", "arranging windows",
                       "done, started or initiated", "approval card"):
            self.assertIn(phrase, lowered, f"the chat prompt no longer covers: {phrase}")

    def test_the_rule_is_stated_without_quoting_the_failure(self) -> None:
        # Quoting the forbidden replies primed the model to give them: on the fast tier, five
        # tempting prompts x 5 trials, 17/25 replies asked leave to act ("May I open Chrome for
        # you?", "May I resize and reposition your windows...") with the quotes, 1/25 without.
        from laptop_agent.planner.openai_compatible import _CAPABILITIES, _NO_TOOL_CLAIMS

        for text in (_NO_TOOL_CLAIMS, _CAPABILITIES):
            self.assertNotRegex(text, r"(?i)\b(may i|shall i|should i)\b")

    def test_the_chat_knows_recording_is_a_tool_and_what_to_ask_for(self) -> None:
        # It answered "record voice upto 20 seconds" with "May I record your voice for up to
        # 20 seconds?", and asked again after every "yes": nothing told it recording is a
        # tool, so it took it for its own to do once allowed.
        from laptop_agent.planner.openai_compatible import _CAPABILITIES, _NO_TOOL_CLAIMS

        self.assertIn("record voice notes", _CAPABILITIES)
        self.assertIn("recording from the microphone", _NO_TOOL_CLAIMS)
        self.assertIn("'record my voice for 20 seconds'", _NO_TOOL_CLAIMS)



class RoutingDeadlineTests(unittest.TestCase):
    """Routing is spent BEFORE the answer starts, so the user sits looking at nothing for
    the whole of it. Measured over 300 real turns: the LLM router ran on 8% of them at a
    median of 951ms, a p90 of 2492ms and a worst case of 7954ms - and every one of those
    was bounded only by the 45s ceiling shared with the answer itself."""

    def test_routing_uses_its_own_short_deadline(self) -> None:
        seen = {}
        real = urllib.request.urlopen

        def spy(request, timeout=None, **kwargs):
            seen["timeout"] = timeout
            raise TimeoutError("timed out")

        urllib.request.urlopen = spy
        try:
            provider = OpenAICompatiblePlannerProvider("k", "m")
            provider.plan("what is a b-tree", "help", {})
        finally:
            urllib.request.urlopen = real
        self.assertEqual(seen.get("timeout"), provider.route_timeout)
        self.assertLess(
            provider.route_timeout, provider.timeout,
            "routing waits as long as a full answer")

    def test_a_slow_router_still_lets_the_answer_happen(self) -> None:
        """A timeout means slow, not unreachable. Returning the canned "I could not reach
        my language model" here would replace a working answer with an error, because only
        the classify call ran out of time."""
        for raising in (lambda payload: (_ for _ in ()).throw(TimeoutError("timed out")),
                        lambda payload: (_ for _ in ()).throw(
                            urllib.error.URLError(TimeoutError("timed out")))):
            with self.subTest(raising):
                decision = OpenAICompatiblePlannerProvider(
                    "k", "m", transport=raising).plan("what is a b-tree", "help", {})
                self.assertEqual(decision.action, "chat")
                self.assertFalse(
                    decision.response,
                    "a slow router answered for the chat tier instead of letting it answer")

    def test_an_unreachable_model_still_says_so(self) -> None:
        def refused(payload):
            raise urllib.error.URLError("connection refused")

        decision = OpenAICompatiblePlannerProvider(
            "k", "m", transport=refused).plan("what is a b-tree", "help", {})
        self.assertEqual(decision.action, "chat")
        self.assertIn("could not reach", (decision.response or "").lower())

    def test_a_routing_failure_is_recorded(self) -> None:
        """An except that only returns a fallback is how two outages stayed invisible."""
        from laptop_agent.failures import FAILURES

        before = len(FAILURES.recent(50))
        OpenAICompatiblePlannerProvider(
            "k", "m", transport=lambda payload: (_ for _ in ()).throw(TimeoutError("x"))
        ).plan("what is a b-tree", "help", {})
        after = FAILURES.recent(50)
        self.assertGreater(len(after), before, "the routing failure was swallowed silently")
        self.assertTrue(any("planner/route" in str(entry) for entry in after))

    def test_an_injected_transport_is_still_called_with_the_payload_alone(self) -> None:
        """Tests inject `lambda payload: ...`; the deadline must not change that contract."""
        calls = []
        provider = OpenAICompatiblePlannerProvider(
            "k", "m", transport=lambda payload: calls.append(payload) or '{"action":"chat"}')
        provider.plan("hello", "help", {})
        self.assertEqual(len(calls), 1)


class _FakeStream:
    """An SSE response for stream_answer, recording the request it answered."""

    def __init__(self, request, finish: str, pieces=("Step one", ", step two"), reasoning_only: bool = False) -> None:
        self.payload = json.loads(request.data)
        field = "reasoning_content" if reasoning_only else "content"
        chunks = [{"choices": [{"delta": {field: piece}, "finish_reason": None}]} for piece in pieces]
        chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
        self.lines = [f"data: {json.dumps(chunk)}\n".encode() for chunk in chunks] + [b"data: [DONE]\n"]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.lines)


class OutputLimitTests(unittest.TestCase):
    """Jeevan: replies are "way too limited". Measured: streamed chat was capped at 2,048
    tokens and a long answer stopped after 701 words, mid-table, with no word that it had
    been cut; every NVIDIA model here accepts 65,536."""

    def stream(self, provider, finish: str = "stop", **stream):
        seen = {}
        real = urllib.request.urlopen

        def fake(request, timeout=None, **kwargs):
            seen["response"] = _FakeStream(request, finish, **stream)
            return seen["response"]

        urllib.request.urlopen = fake
        try:
            text = "".join(provider.stream_answer("write a long guide", {}))
        finally:
            urllib.request.urlopen = real
        return text, seen["response"].payload

    def test_a_measured_host_gets_the_long_caps(self) -> None:
        provider = OpenAICompatiblePlannerProvider("k", "m", max_output_tokens=16384)
        _text, payload = self.stream(provider)
        self.assertEqual(payload["max_tokens"], 16384)
        sent = []
        provider._transport = lambda payload: sent.append(payload) or "fine"
        provider._owns_transport = False
        provider.answer("hello", {})
        provider.answer("a resume", {}, max_tokens=8000)
        self.assertEqual([payload["max_tokens"] for payload in sent], [4096, 8000])

    def test_an_unmeasured_host_keeps_the_old_caps(self) -> None:
        provider = OpenAICompatiblePlannerProvider("k", "m")
        _text, payload = self.stream(provider)
        self.assertEqual(payload["max_tokens"], 2048)
        sent = []
        provider._transport = lambda payload: sent.append(payload) or "fine"
        provider._owns_transport = False
        provider.answer("hello", {})
        self.assertEqual(sent[0]["max_tokens"], 900)

    def test_a_reply_cut_off_at_the_cap_says_so(self) -> None:
        provider = OpenAICompatiblePlannerProvider("k", "m", max_output_tokens=16384)
        cut, _ = self.stream(provider, finish="length")
        self.assertTrue(cut.startswith("Step one, step two"))
        self.assertIn("reached the length limit", cut)
        whole, _ = self.stream(provider, finish="stop")
        self.assertEqual(whole, "Step one, step two")

    def test_a_reply_that_was_all_hidden_reasoning_stays_empty(self) -> None:
        # Codex's review of #177: the note alone read as an answer, so the orchestrator took
        # the tier as healthy and never tried the next one.
        provider = OpenAICompatiblePlannerProvider("k", "m", max_output_tokens=16384)
        text, _ = self.stream(provider, finish="length", pieces=("Let me think about it",), reasoning_only=True)
        self.assertEqual(text, "")

    def test_a_reply_cut_inside_a_code_block_closes_it_before_the_note(self) -> None:
        provider = OpenAICompatiblePlannerProvider("k", "m", max_output_tokens=16384)
        text, _ = self.stream(provider, finish="length", pieces=("Here it is:\n``", "`python\nprint(1)",))
        before, note = text.split("\n\n_(", 1)
        self.assertEqual(before.count("```") % 2, 0, before)
        self.assertIn("reached the length limit", note)
        whole, _ = self.stream(provider, finish="length", pieces=("```python\nprint(1)\n```\nDone",))
        self.assertEqual(whole.split("\n\n_(", 1)[0], "```python\nprint(1)\n```\nDone")

    def test_a_long_reply_is_given_the_time_to_be_written(self) -> None:
        provider = OpenAICompatiblePlannerProvider("k", "m", timeout=45, max_output_tokens=16384)
        self.assertEqual(provider._deadline({"max_tokens": 900}), 45)
        self.assertGreater(provider._deadline({"max_tokens": 4096}), 100)
        self.assertEqual(provider._deadline({"max_tokens": 65536}), 300)
        self.assertEqual(OpenAICompatiblePlannerProvider("k", "m", timeout=420)._deadline({"max_tokens": 20480}), 420)

    def test_the_request_itself_waits_that_long(self) -> None:
        seen = {}
        real = urllib.request.urlopen

        def spy(request, timeout=None, **kwargs):
            seen.setdefault("timeouts", []).append(timeout)
            raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, None)

        urllib.request.urlopen = spy
        try:
            OpenAICompatiblePlannerProvider("k", "m", timeout=45, max_output_tokens=16384).answer("hello", {})
        finally:
            urllib.request.urlopen = real
        self.assertGreater(seen["timeouts"][0], 100)

    def test_an_image_description_gets_room_too(self) -> None:
        import tempfile
        from pathlib import Path

        sent = {}
        real = urllib.request.urlopen

        def spy(request, timeout=None, **kwargs):
            sent["payload"] = json.loads(request.data)
            raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, None)

        with tempfile.TemporaryDirectory() as raw:
            image = Path(raw) / "screen.png"
            image.write_bytes(b"\x89PNG")
            urllib.request.urlopen = spy
            try:
                for cap, expected in ((16384, 2048), (None, 700)):
                    OpenAICompatiblePlannerProvider("k", "m", max_output_tokens=cap).describe_image(str(image), "what is this")
                    self.assertEqual(sent["payload"]["max_tokens"], expected)
            finally:
                urllib.request.urlopen = real

    def test_the_cap_is_configured_and_bounded(self) -> None:
        from laptop_agent.config import _output_tokens

        self.assertEqual([_output_tokens(raw) for raw in ("8000", "", None, "lots", "10", "999999")],
                         [8000, None, None, None, 256, 65536])

    def test_only_the_measured_host_gets_the_default(self) -> None:
        from dataclasses import replace

        from laptop_agent.app import _build_openrouter_planner, _output_tokens
        from laptop_agent.config import load_config

        config = load_config()
        nvidia = replace(config, llm_base_url="https://integrate.api.nvidia.com/v1", llm_max_output_tokens=None)
        self.assertEqual(_output_tokens(nvidia), 16384)
        self.assertIsNone(_output_tokens(replace(nvidia, llm_base_url="https://api.openai.com/v1")))
        self.assertEqual(_output_tokens(replace(nvidia, llm_base_url="https://api.openai.com/v1",
                                                llm_max_output_tokens=12000)), 12000)
        router = _build_openrouter_planner(replace(nvidia, openrouter_api_key="k", openrouter_model="m"))
        self.assertIsNone(router.provider.max_output_tokens)
        from laptop_agent.app import _build_planner, _build_smart_planner, _build_ultra_planner, _build_vision_planner

        tiers = replace(nvidia, llm_provider="openai-compatible", llm_api_key="k", llm_model="fast",
                        llm_smart_model="smart", llm_ultra_model="ultra", llm_vision_model="vision")
        for build in (_build_planner, _build_smart_planner, _build_ultra_planner, _build_vision_planner):
            self.assertEqual(build(tiers).provider.max_output_tokens, 16384, build.__name__)


if __name__ == "__main__":
    unittest.main()
