"""What a `personal` account may not do, enforced where it cannot be routed around.

The gate refuses it every HIGH and CRITICAL action before anyone is asked: an approval card
it could simply click through would be no control at all. The orchestrator refuses the
developer-only commands on the command about to run, however it was reached, and dispatches
for it only what is marked everyday. The broker shows an account only its own approvals,
and the web server lets it use a short list of routes. Nobody signed in (the CLI, the
ticker) is the owner, as before.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import tempfile
import textwrap
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from laptop_agent import access
from laptop_agent.access import EVERYDAY_EXACT, EVERYDAY_PREFIX, acting_as, everyday_form, is_personal, refused_command
from laptop_agent.accounts import AccountStore, Principal
from laptop_agent.agents.orchestrator import AgentOrchestrator
from laptop_agent.approvals import ApprovalBroker
from laptop_agent.planner.core import Planner
from laptop_agent.safety import ApprovalDenied, ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.sessions import SessionStore
from laptop_agent.tools.base import ToolResult
from test_everyday_requests import CONTRACT, MUST_STAY_CHAT, Everyday, reached

PERSONAL = Principal("p1", "family", "personal")
OTHER = Principal("p2", "guest", "personal")
DEV = Principal("d1", "jeevan", "dev")


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.asked: list[str] = []
        self.gate = ApprovalGate(lambda request: self.asked.append(request.action) or True)

    def request(self, risk: RiskLevel, everyday: bool = False) -> ApprovalRequest:
        return ApprovalRequest(action=f"a {risk.value} thing", risk=risk, reason="test", everyday=everyday)

    def test_a_personal_account_is_refused_before_anyone_is_asked(self) -> None:
        with acting_as(PERSONAL):
            for risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                with self.subTest(risk):
                    with self.assertRaises(ApprovalDenied) as raised:
                        self.gate.require(self.request(risk))
                    self.assertIn("developer account", str(raised.exception))
        self.assertEqual(self.asked, [], "a refused account must not be offered an approval card")

    def test_what_a_personal_account_may_still_do(self) -> None:
        with acting_as(PERSONAL):
            self.gate.require(self.request(RiskLevel.LOW))
            self.gate.require(self.request(RiskLevel.MEDIUM))
            self.gate.require(self.request(RiskLevel.HIGH, everyday=True))
        self.assertEqual(self.asked, ["a medium thing", "a high thing"])

    def test_a_developer_and_the_machine_itself_are_asked_as_before(self) -> None:
        for principal in (DEV, None):
            with self.subTest(principal=principal):
                self.asked.clear()
                with acting_as(principal):
                    self.gate.require(self.request(RiskLevel.CRITICAL))
                self.assertEqual(self.asked, ["a critical thing"])

    def test_the_principal_does_not_outlive_its_request(self) -> None:
        with acting_as(PERSONAL):
            self.assertTrue(is_personal())
        self.assertFalse(is_personal())


class CommandTests(unittest.TestCase):
    def test_developer_commands_are_refused_to_a_personal_account(self) -> None:
        with acting_as(PERSONAL):
            for command, form in (("latency", "latency"), ("  Latency ", "latency"), ("traces", "traces"),
                                  ("agent run tidy my files", "agent"), ("autopilot workflow x", "autopilot"),
                                  ("schedule list", "schedule list"), ("email unread", "email"),
                                  ("summarize my inbox", "summarize my inbox"), ("knowledge prune", "knowledge prune"),
                                  ("jobright pull", "jobright pull"), ("audit notes", "audit notes"),
                                  ("read file C:\\Users\\me\\.env", "read file"), ("process file notes.txt", "process file"),
                                  ("what's on my screen", "what's on my screen"), ("take a photo", "take a photo"),
                                  ("run command dir", "run command"), ("play music lofi", "play music"),
                                  ("ask knowledge what is this", "ask knowledge"), ("read note Plans", "read note")):
                with self.subTest(command):
                    self.assertEqual(refused_command(command), form)

    def test_everyday_words_that_start_like_a_developer_command_are_not(self) -> None:
        with acting_as(PERSONAL):
            for command in ("speed of light in km/s", "errors in my essay", "agentic ai explained",
                            "what time is it", "remind me at 5pm to call mom", "tasks for today are fun",
                            "diagnose why my plant is wilting", "knowledge is power", "emails are annoying",
                            "notes about the meeting", "list shopping", "convert 5 miles to km"):
                with self.subTest(command):
                    self.assertIsNone(refused_command(command))

    def test_nobody_else_is_refused_anything(self) -> None:
        for principal in (DEV, None):
            with acting_as(principal):
                self.assertIsNone(refused_command("latency"))
                self.assertIsNone(refused_command("agent run tidy my files"))


def _strings(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return {elt.value for elt in node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)}
    return set()


def _on_lowered(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "lowered"


def _names_a_form(test: ast.AST) -> bool:
    """Whether a branch is chosen by a literal form of `lowered` (possibly among others)."""
    for node in ast.walk(test):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith" and _on_lowered(node.func.value)):
            return True
        if (isinstance(node, ast.Compare) and _on_lowered(node.left)
                and isinstance(node.ops[0], (ast.Eq, ast.In)) and _strings(node.comparators[0])):
            return True
    return False


def _only_forms(test: ast.AST) -> bool:
    """Whether a branch is chosen by literal forms alone. An `or` that also admits a pattern
    (`lowered == "record" or re.fullmatch(r"record \\d+", lowered)`) is a branch chosen by a
    pattern too, and was counted as neither."""
    return _names_a_form(test) and all(
        _names_a_form(value) for node in ast.walk(test)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) for value in node.values)


def dispatch_forms() -> tuple[set[str], set[str], dict[str, int]]:
    """Every literal the dispatchers match whole, every prefix they match, and per group how
    many top-level branches are chosen some other way (a pattern, a parser)."""
    exact: set[str] = set()
    prefixes: set[str] = set()
    other: dict[str, int] = {}
    for group in AgentOrchestrator._DISPATCH:
        function = ast.parse(textwrap.dedent(inspect.getsource(group))).body[0]
        for node in ast.walk(function):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "startswith" and _on_lowered(node.func.value)):
                prefixes |= _strings(node.args[0])
            elif (isinstance(node, ast.Compare) and _on_lowered(node.left)
                  and isinstance(node.ops[0], (ast.Eq, ast.In))):
                exact |= _strings(node.comparators[0])
            elif isinstance(node, ast.For) and isinstance(node.target, ast.Name) and _strings(node.iter):
                # `for verb in ("solve ", "advise ", ...): if lowered.startswith(verb)`
                if any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                       and call.func.attr == "startswith" and _on_lowered(call.func.value)
                       and isinstance(call.args[0], ast.Name) and call.args[0].id == node.target.id
                       for call in ast.walk(node)):
                    prefixes |= _strings(node.iter)
        count = sum(1 for statement in function.body
                    if isinstance(statement, ast.If) and not _only_forms(statement.test))
        if count:
            other[group.__name__] = count
    return exact, prefixes, other


# Top-level branches chosen by a pattern or a parser, which the scan above cannot read.
# Everyday: deleting a reminder, a bare or asked-after list, a remembered fact, a coin or
# dice, how long a timer has left, a list edit, a date question. Refused: `media volume N`
# by the `media` prefix, and `record N` (REC-01) by default-deny, which refuses any
# dispatched command that is not marked everyday.
OTHER_BRANCHES = {"_dispatch_automation": 1, "_dispatch_desktop": 1, "_dispatch_file_intelligence": 1,
                  "_dispatch_personal": 7}


class DispatchMirrorTests(unittest.TestCase):
    """Both lists in `access` are copies of what the dispatchers match, and a hand-kept copy
    of a list fails by omission. So both are checked against the dispatchers, both ways."""

    def setUp(self) -> None:
        self.exact, self.prefixes, self.other = dispatch_forms()

    def test_the_scan_reads_the_dispatchers(self) -> None:
        self.assertGreater(len(self.exact), 150)
        self.assertGreater(len(self.prefixes), 100)
        self.assertIn("solve ", self.prefixes, "the loop over verbs was not read")

    def test_every_refused_form_is_one_the_dispatchers_match(self) -> None:
        # A form no dispatcher matches would only refuse ordinary sentences.
        self.assertEqual(access._DEV_EXACT - self.exact, set())
        self.assertEqual({form + " " for form in access._DEV_PREFIX} - self.prefixes, set())

    def test_every_everyday_form_is_one_the_dispatchers_match(self) -> None:
        # One that matched nothing would widen what is dispatched for a personal account.
        self.assertEqual(EVERYDAY_EXACT - self.exact, set())
        self.assertEqual(set(EVERYDAY_PREFIX) - self.prefixes, set())

    def test_every_form_the_dispatchers_match_is_decided(self) -> None:
        # Refused by any developer form, however broad, is decided: that fails closed. Everyday
        # must be the form itself, never a broader everyday prefix: `list secrets` added under
        # `list ` would otherwise pass here and be dispatched for a personal account.
        undecided = []
        with acting_as(PERSONAL):
            for form in sorted(self.exact | self.prefixes):
                sample = form if form in self.exact else form + ("x" if form.endswith(" ") else " x")
                everyday = form in (EVERYDAY_EXACT if form in self.exact else EVERYDAY_PREFIX)
                if refused_command(sample) is not None:
                    continue
                if not everyday:
                    undecided.append(form)
                else:
                    self.assertTrue(everyday_form(sample), form)
        self.assertEqual(undecided, [], "forms nobody decided on: refuse them in access._DEV_* or "
                                        "mark them in access.EVERYDAY_*")

    def test_no_branch_escapes_the_scan(self) -> None:
        self.assertEqual(self.other, OTHER_BRANCHES,
                         "a dispatcher branch is chosen by a pattern or a parser: decide whether a "
                         "personal account may run it, refuse its forms in access if not, then "
                         "update OTHER_BRANCHES")


class SignedOutTests(unittest.TestCase):
    """Revoking a session ended the session but not the work it had started: an agent run
    kept dispatching commands under the principal it began with until it finished."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.everyday = Everyday(Path(tmp.name), llm="none")

    def facts(self) -> dict:
        return self.everyday.orchestrator.context.memory.get_profile()

    def test_a_command_after_the_session_ended_is_not_dispatched(self) -> None:
        with acting_as(DEV, lambda: False):
            with self.assertRaises(access.SignedOut):
                self.everyday.say("remember colour = blue")
        self.assertNotIn("colour", self.facts())

    def test_nothing_changes_without_a_principal_or_while_the_session_stands(self) -> None:
        with acting_as(None, lambda: False):     # the CLI and the ticker: no session to end
            self.assertTrue(self.everyday.say("remember colour = blue")[0].ok)
        with acting_as(DEV, lambda: True):
            self.assertTrue(self.everyday.say("remember shape = round")[0].ok)
        self.assertEqual((self.facts()["colour"], self.facts()["shape"]), ("blue", "round"))

    def test_an_agent_run_stops_at_the_step_after_its_session_ends(self) -> None:
        ended: list[bool] = []

        class Brain:
            calls = 0

            def answer(self, text, memory_profile, model=None, history=None, **kwargs):
                Brain.calls += 1
                if Brain.calls == 2:
                    ended.append(True)   # the owner disables the account while it thinks
                return {1: "THOUGHT: one\nACTION: remember first_step = done",
                        2: "THOUGHT: two\nACTION: remember second_step = done"}.get(
                    Brain.calls, "THOUGHT: done\nFINAL: finished")

        self.everyday.orchestrator.smart_planner = Planner(Brain())
        with acting_as(DEV, lambda: not ended):
            with self.assertRaises(access.SignedOut):
                self.everyday.say("agent run note two things")
        self.assertEqual(self.facts().get("first_step"), "done")
        self.assertNotIn("second_step", self.facts())

    def test_a_sentence_is_not_routed_after_the_session_ended(self) -> None:
        # This reads as prose, so no command is claimed before the router would be asked:
        # the session is checked where Stop is, ahead of everything a turn does.
        orchestrator = self.everyday.orchestrator
        with patch.object(orchestrator, "_route", wraps=orchestrator._route) as route:
            with acting_as(DEV, lambda: False):
                with self.assertRaises(access.SignedOut):
                    self.everyday.say("schedule a meeting with bob")
        route.assert_not_called()

    def test_a_batch_stops_instead_of_reporting_failed_subtasks(self) -> None:
        # gather(return_exceptions=True) turns a stopped subtask into a bare CancelledError,
        # so without asking again the batch answered "0 succeeded" to a signed-out client.
        ended: list[bool] = []
        memory = self.everyday.orchestrator.context.memory
        save = memory.set_profile_value

        def save_then_end(*args, **kwargs):
            ended.append(True)   # the owner disables the account while the batch runs
            return save(*args, **kwargs)

        with patch.object(memory, "set_profile_value", side_effect=save_then_end):
            with acting_as(DEV, lambda: not ended):
                with self.assertRaises(access.SignedOut):
                    self.everyday.say("multi remember first = one ;; remember second = two")
        self.assertTrue(ended, "no subtask ran, so this proved nothing")

    def test_a_stopped_step_does_not_stay_working(self) -> None:
        # The workflow and autopilot loops caught only Exception, and a cancellation is not
        # one, so the step that never ran stayed `working` in the control room for good
        # (Codex's review of #156).
        orchestrator = self.everyday.orchestrator
        memory = orchestrator.context.memory
        save = memory.set_profile_value
        steps = ["remember first = one", "remember second = two"]
        runs = {
            "workflow": lambda: self.everyday.say("workflow " + " ;; ".join(steps)),
            "autopilot": lambda: asyncio.run(orchestrator._run_autopilot("note two things", steps)),
        }
        for name, run in runs.items():
            with self.subTest(name):
                ended: list[bool] = []

                def save_then_end(*args, **kwargs):
                    result = save(*args, **kwargs)
                    ended.append(True)
                    return result

                with patch.object(memory, "set_profile_value", side_effect=save_then_end),                         patch.object(orchestrator.autopilot_planner, "is_safe_command", return_value=True):
                    with acting_as(DEV, lambda: not ended):
                        with self.assertRaises(access.SignedOut):
                            run()
                self.assertNotIn("second", self.facts())
                self.assertEqual(orchestrator.control_room.snapshot()["summary"]["working"], 0)
                memory.forget_profile_value("first")

    def test_a_routed_command_does_not_stay_working(self) -> None:
        # The session ends while the sentence is being routed (the routing call can take
        # seconds): the routed command's own turn stops, and the specialist it lit up was left
        # working, since that path had no cleanup at all (review of #156).
        asked: list[bool] = []

        def still_signed_in() -> bool:
            asked.append(True)
            return len(asked) == 1

        orchestrator = self.everyday.orchestrator
        with acting_as(DEV, still_signed_in):
            with self.assertRaises(access.SignedOut):
                asyncio.run(orchestrator.handle("what's the weather"))
        self.assertGreater(len(asked), 1, "the routed command never ran, so this proved nothing")
        self.assertEqual(orchestrator.control_room.snapshot()["summary"]["working"], 0)


class ThroughTheAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.everyday = Everyday(Path(tmp.name), llm="none")

    def say(self, principal: Principal | None, text: str, history=None):
        self.everyday.approvals.clear()
        with acting_as(principal):
            return self.everyday.say(text, history=history)

    def test_a_developer_command_is_refused_with_a_reason(self) -> None:
        result, _ran = self.say(PERSONAL, "latency")
        self.assertFalse(result.ok)
        self.assertIn("needs a developer account", result.message)
        self.assertTrue(self.say(DEV, "latency")[0].ok)

    def test_a_risky_action_is_refused_without_an_approval_card(self) -> None:
        self.assertEqual(self.say(PERSONAL, "run command dir")[0].data.get("refused"), "run command")
        self.assertEqual(self.everyday.approvals, [], "a personal account was offered the approval card")
        self.say(DEV, "run command dir")
        self.assertEqual([risk for risk, _ in self.everyday.approvals], ["critical"],
                         "a developer is still asked, as before")

    def test_a_risky_tool_reached_some_other_way_still_stops_at_the_gate(self) -> None:
        # The command check comes first, so this is the backstop: a path to a tool that no
        # listed form covers, today or after a change, still meets the gate as this account.
        context = self.everyday.orchestrator.context
        with acting_as(PERSONAL):
            with self.assertRaises(ApprovalDenied):
                context.terminal.run("dir")
            with self.assertRaises(ApprovalDenied):
                context.desktop.open_app_or_file("notepad")
        self.assertEqual(self.everyday.approvals, [])

    def test_a_reply_cannot_rebuild_a_developer_command_from_the_history_sent(self) -> None:
        # The history comes from the client. Answering "our" question with a time made the
        # command "email unread 5pm" out of a user turn nobody checked, and reading the
        # inbox is MEDIUM, which the gate lets through.
        history = [{"role": "user", "text": "email unread"},
                   {"role": "assistant", "text": "I could not find a time in that."}]
        result, _ran = self.say(PERSONAL, "5pm", history=history)
        self.assertEqual(result.data.get("refused"), "email", result.message)
        self.assertEqual(self.everyday.approvals, [])

    def test_prose_that_starts_like_a_developer_command_is_not_refused(self) -> None:
        for text in ("schedule a meeting with bob tomorrow", "email is how most people reach me"):
            with self.subTest(text):
                result, _ran = self.say(PERSONAL, text)
                self.assertNotIn("developer account", result.message)

    def test_a_command_nobody_classified_is_not_run_for_a_personal_account(self) -> None:
        # Codex's point on #140: the AST test catches this in CI, but a command added without
        # a decision must also be refused where it runs.
        ran: list[str] = []

        async def zap(orchestrator, command, lowered, history_turns):
            if lowered == "zap everything":
                ran.append(command)
                return ToolResult.success("zapped")
            return None

        table = (zap,) + AgentOrchestrator._DISPATCH
        with patch.object(AgentOrchestrator, "_DISPATCH", table):
            self.say(PERSONAL, "zap everything")
            with acting_as(PERSONAL):
                routed = asyncio.run(self.everyday.orchestrator.handle("zap everything", _allow_planner=False))
            self.assertEqual(ran, [], "an unclassified command ran for a personal account")
            self.assertEqual((routed.ok, routed.data.get("refused")), (False, "zap"))
            self.assertEqual(self.say(DEV, "zap everything")[0].message, "zapped")

    def test_each_everyday_branch_chosen_by_a_pattern_runs_directly(self) -> None:
        # One phrase per pattern in `_everyday`: the routing contract names none of these
        # shapes, so dropping one there went unnoticed until this test.
        for setup in ("add milk to my shopping list", "remind me in 30 minutes to stretch",
                      "set a timer for 10 minutes"):
            self.say(None, setup)
        for text in ("shopping list", "what's on my list", "how much longer", "what's my name",
                     "add eggs to the list", "how many days until christmas", "roll a die",
                     "reminder delete 1"):
            with self.subTest(text):
                result, ran = self.say(PERSONAL, text)
                self.assertEqual(ran, text, f"{text!r} was not dispatched for a personal account")
                self.assertNotIn("refused", result.data)

    def test_everyday_requests_still_work(self) -> None:
        for text in ("remind me in 10 minutes to stretch", "what's 17 times 23", "set a timer for 5 minutes",
                     "what time is it", "show my reminders", "weather in Paris", "flip a coin"):
            with self.subTest(text):
                result, _ran = self.say(PERSONAL, text)
                self.assertIsNotNone(result, text)
                self.assertNotIn("developer account", result.message)

    def test_clearing_several_reminders_is_still_asked_not_refused(self) -> None:
        for text in ("remind me in 10 minutes to stretch", "remind me in 20 minutes to drink water"):
            self.say(PERSONAL, text)
        self.say(PERSONAL, "cancel all reminders")
        self.assertEqual([risk for risk, _ in self.everyday.approvals], ["high"],
                         "bulk cancelling is everyday: asked, as for anyone")


class PersonalContractTests(unittest.TestCase):
    """The whole routing contract, said by a personal account: every phrase whose command is
    everyday still runs it, which is what holds the pattern-chosen branches in
    `AgentOrchestrator._everyday` to the dispatchers, and every other one is refused."""

    def test_the_routing_contract_for_a_personal_account(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        everyday = Everyday(Path(tmp.name))
        broken = []
        for text, expected in CONTRACT:
            with acting_as(PERSONAL):
                developer = refused_command(expected) is not None or refused_command(expected + " x") is not None
                result, ran = everyday.say(text)
            refused = result is not None and bool(result.data.get("refused"))
            # As in the owner's contract test: a direct command answers before any router.
            direct = ran == text.strip() and result is not None and "answered]" not in result.message
            if developer and not refused:
                broken.append(f"{text!r} ran {ran!r} for a personal account")
            elif not developer and (refused or not (reached(ran, expected) or direct)):
                broken.append(f"{text!r} reached {ran!r}, not {expected!r}")
        for text in MUST_STAY_CHAT:
            with acting_as(PERSONAL):
                result, ran = everyday.say(text)
            if ran is not None or "answered]" not in result.message:
                broken.append(f"{text!r} did not stay chat for a personal account: {ran!r}")
        self.assertEqual(broken, [], "\n" + "\n".join(broken))


class ApprovalScopeTests(unittest.TestCase):
    """An approval card carries the command, the recipient or the path, and whoever sees it
    can answer it. So it goes to the account that asked, and only that account answers."""

    def setUp(self) -> None:
        self.broker = ApprovalBroker(timeout=5)
        self.heard: dict[str, list[dict]] = {}

    def listen(self, principal: Principal | None) -> None:
        heard = self.heard.setdefault(principal.username if principal else "machine", [])
        with acting_as(principal):
            self.broker.add_listener(heard.append)

    def ask(self, principal: Principal | None) -> tuple[threading.Thread, list[bool]]:
        outcome: list[bool] = []

        def run() -> None:
            with acting_as(principal):
                outcome.append(self.broker.request("Run command: dir", "critical", "test", preview="dir"))

        thread = threading.Thread(target=run)
        thread.start()
        return thread, outcome

    def pending_for(self, principal: Principal | None) -> list[dict]:
        with acting_as(principal):
            return self.broker.pending()

    def answer(self, principal: Principal | None, request_id: str) -> bool:
        with acting_as(principal):
            return self.broker.resolve(request_id, True)

    def wait_for_card(self, principal: Principal | None) -> str:
        for _ in range(200):
            pending = self.pending_for(principal)
            if pending:
                return str(pending[0]["id"])
            threading.Event().wait(0.01)
        self.fail("the request never became pending")

    def test_an_account_sees_and_answers_only_its_own(self) -> None:
        for principal in (DEV, PERSONAL, OTHER):
            self.listen(principal)
        thread, outcome = self.ask(DEV)
        request_id = self.wait_for_card(DEV)
        self.assertEqual([len(self.heard[name]) for name in ("jeevan", "family", "guest")], [1, 0, 0])
        self.assertEqual(self.pending_for(PERSONAL), [])
        self.assertFalse(self.answer(PERSONAL, request_id), "another account answered the owner's card")
        self.assertTrue(self.answer(DEV, request_id))
        thread.join(5)
        self.assertEqual(outcome, [True])

    def test_what_the_machine_asks_for_goes_to_a_developer(self) -> None:
        for principal in (DEV, PERSONAL):
            self.listen(principal)
        thread, outcome = self.ask(None)
        request_id = self.wait_for_card(DEV)
        self.assertEqual((len(self.heard["jeevan"]), len(self.heard["family"])), (1, 0))
        self.assertFalse(self.answer(PERSONAL, request_id))
        self.assertTrue(self.answer(DEV, request_id))
        thread.join(5)
        self.assertEqual(outcome, [True])

    def test_nobody_to_ask_still_denies_at_once(self) -> None:
        self.listen(PERSONAL)
        thread, outcome = self.ask(DEV)
        thread.join(2)
        self.assertEqual(outcome, [False], "only another account was listening, so nobody could answer")

    def test_a_listener_is_removed_by_the_callback_it_was_added_with(self) -> None:
        heard: list[dict] = []
        with acting_as(DEV):
            self.broker.add_listener(heard.append)
            self.broker.remove_listener(heard.append)   # a new bound method, equal but not identical
        thread, outcome = self.ask(DEV)
        thread.join(2)
        self.assertEqual((heard, outcome), ([], [False]))


class OverTheWebTests(unittest.TestCase):
    """The web server must set who a request acts for, and hold a personal account to its
    routes. Without the first every request would act as the machine's owner."""

    @classmethod
    def setUpClass(cls) -> None:
        from http.server import ThreadingHTTPServer

        import laptop_agent.webui as webui

        cls.webui = webui
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        accounts = AccountStore(Path(tmp.name) / "accounts.json", cost=(2 ** 10, 8, 1))
        sessions = SessionStore(Path(tmp.name) / "sessions.json")
        for name, value in (("ACCOUNTS", accounts), ("SESSIONS", sessions), ("_APPROVALS", ApprovalBroker(timeout=5))):
            patcher = patch.object(self.webui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cookies = {}
        for username, role in (("jeevan", "dev"), ("family", "personal")):
            account = accounts.create(username, role, "correct horse battery")
            self.cookies[role] = f"jarvis_session={sessions.create(account.id, 'password')}"

    def call(self, role: str, path: str, body: dict | None = None) -> tuple[int, dict, dict]:
        headers = {"Cookie": self.cookies[role]}
        data = None
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-Jarvis-Token": self.webui._API_TOKEN})
            data = json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, headers=headers,
                                         method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw, status, got = response.read(), response.status, dict(response.headers)
        except urllib.error.HTTPError as error:
            raw, status, got = error.read(), error.code, dict(error.headers)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}
        return status, payload, got

    def test_a_personal_session_is_refused_what_a_developer_is_not(self) -> None:
        self.assertIn("needs a developer account", self.call("personal", "/api/command", {"command": "latency"})[1]["message"])
        self.assertNotIn("developer account", self.call("dev", "/api/command", {"command": "latency"})[1]["message"])

    def test_a_personal_session_cannot_run_a_risky_command(self) -> None:
        status, body, _ = self.call("personal", "/api/command", {"command": "run command dir"})
        self.assertFalse(body["ok"])
        self.assertIn("developer account", body["message"])

    @staticmethod
    def routes_in(method) -> set[str]:
        routes: set[str] = set()
        for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(method)))):
            if isinstance(node, ast.Compare) and isinstance(node.ops[0], (ast.Eq, ast.In)):
                routes |= {value for value in _strings(node.comparators[0]) if value.startswith("/")}
        return routes

    def test_the_routes_a_personal_account_may_use(self) -> None:
        # Every route the handlers answer, read from their source: the ones not on the list
        # are refused outright, with the header that stops the page reloading.
        gets, posts = (self.routes_in(self.webui.Handler._do_get), self.routes_in(self.webui.Handler._dispatch_post))
        routes = gets | posts
        self.assertGreater(len(routes), 30)
        self.assertEqual(self.webui._PERSONAL_ROUTES - routes, set(), "a listed route does not exist")
        for route in sorted(routes - self.webui._PERSONAL_ROUTES):
            with self.subTest(route):
                status, _body, headers = self.call("personal", route, {} if route in posts else None)
                self.assertEqual((status, headers.get("X-Jarvis-Denied")), (403, "role"))

    def test_one_account_cannot_see_or_answer_anothers_approval(self) -> None:
        broker = self.webui._APPROVALS
        dev = self.webui.ACCOUNTS.find("jeevan")
        owner = Principal(dev.id, dev.username, dev.role)
        heard: list[dict] = []
        with acting_as(owner):
            broker.add_listener(heard.append)
        outcome: list[bool] = []

        def run() -> None:
            with acting_as(owner):
                outcome.append(broker.request("Run command: dir", "critical", "test"))

        thread = threading.Thread(target=run)
        thread.start()
        for _ in range(200):
            if heard:
                break
            threading.Event().wait(0.01)
        request_id = heard[0]["id"]
        self.assertEqual(self.call("personal", "/api/approvals")[1]["pending"], [])
        self.assertEqual(self.call("personal", "/api/approve", {"id": request_id, "approved": True})[0], 409)
        self.assertEqual(len(self.call("dev", "/api/approvals")[1]["pending"]), 1)
        self.assertEqual(self.call("dev", "/api/approve", {"id": request_id, "approved": False})[0], 200)
        thread.join(5)
        self.assertEqual(outcome, [False])


if __name__ == "__main__":
    unittest.main()
