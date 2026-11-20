"""The supervisor's rules, checked wholesale: every agent command by every kind of agent, random step
sequences against the rules that must always hold, disguised spellings, and every branch of the
orchestrator that reaches a tool no run may use. `test_supervisor.py` holds the case-by-case tests."""
from __future__ import annotations

import ast
import random
import unittest
from pathlib import Path

from laptop_agent import supervisor
from laptop_agent.agents.orchestrator import AgentOrchestrator
from laptop_agent.supervisor import Supervisor, Verdict

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "src" / "laptop_agent" / "agents" / "orchestrator.py"

# Every command form the agent is told about, by the supervisor group it must be in. A new command, or
# one moved to another group, fails here until this table says so: a write filed under SAFE would run
# for a helper, and a change filed under READS would skip the taint card.
GROUPS = {
    "scan files <path>": "safe", "scan files <path> by size": "safe", "read file <path>": "reads",
    "ask file <path> about <question>": "reads", "summarize file <path>": "reads", "extract text <path>": "reads",
    "extract tables <path>": "reads", "analyze spreadsheet <path>": "reads", "process file <path>": "reads",
    "file info <path>": "safe", "search files <query> <path>": "reads", "file move <file> to <folder>": "carded",
    "file copy <file> to <folder>": "carded", "file rename <file> to <name>": "carded",
    "file delete <file>": "direct", "undo file": "carded", "convert file <source> to <destination>": "carded",
    "organize folder <path>": "carded", "index file <path>": "guarded", "recall <query>": "reads",
    "ask knowledge <question>": "reads", "knowledge list": "safe", "knowledge stats": "safe",
    "notes search <query>": "reads", "ask vault <question>": "reads", "notes audit": "safe",
    "read note <name>": "reads", "save note <title> : <body>": "guarded", "remember <key> = <value>": "guarded",
    "learn from chat": "carded", "forget <key>": "guarded", "knowledge prune": "guarded",
    "window <name> <left|right|top|bottom|full|corner>": "guarded", "windows": "safe",
    "web search <query>": "reads", "news [topic]": "reads", "watch news <topic>": "guarded",
    "watch health": "safe", "watches": "safe", "unwatch <topic|health>": "guarded", "image <description>": "safe",
    "document <request> as pdf|word|markdown": "safe", "workspace <request>": "carded",
    "weather <location>": "safe", "translate <text> to <language>": "safe",
    "distance <origin> to <destination>": "safe", "trip <stop1> to <stop2> to <stop3>": "safe",
    "around <category>": "safe", "map <place|origin to destination>": "safe", "hotels near <place>": "safe",
    "summarize youtube <url>": "reads", "research <topic>": "reads", "research report <topic>": "reads",
    "mcp": "carded", "mcp <server> <tool> <json arguments>": "carded", "solve <problem or decision>": "reads",
    "open url <https url>": "guarded", "message whatsapp|telegram [to <who>] :: <text>": "carded",
    "download <url>": "carded", "inspect page <url>": "guarded", "read screen <question>": "reads",
    "describe image <path>": "reads", "describe video <path> :: <question>": "reads",
    "look at webcam <question>": "reads", "open app <path-or-app>": "carded", "screenshot <output.png>": "guarded",
    "play music <file-folder-or-url>": "guarded", "email digest": "reads", "email api search gmail <query>": "reads",
    "email api draft gmail to <addr> subject <subject> body <body>": "carded",
    "email api send gmail to <addr> subject <subject> body <body>": "carded", "briefing": "reads",
    "briefing set <reminders, weather [in city], news [about topic], mail, <name> list, laptop>": "guarded",
    "total|average|count <column> by <column> in <file.csv>": "safe", "jobs": "safe",
    "job add <company> [stage]": "guarded", "tasks": "safe", "reminders due": "safe",
    "reminder add <YYYY-MM-DD> <text>": "safe", "timer <duration>": "safe", "alarm <time>": "safe",
    "timers": "safe", "alarms": "safe", "lists": "safe", "list <name> show": "safe",
    "list <name> add <items>": "safe", "calendar": "safe", "convert <amount> <unit> to <unit>": "safe",
    "run command <command>": "direct", "code run <script.py>": "direct", "code fix <script.py>": "direct",
    "memory": "safe", "audit": "safe",
}
# The forms a helper may run: reads, minus the camera and the screen, and a few computations.
HELPER_RUNS = {
    "read file <path>", "ask file <path> about <question>", "summarize file <path>", "extract text <path>",
    "extract tables <path>", "analyze spreadsheet <path>", "process file <path>", "file info <path>",
    "search files <query> <path>", "recall <query>", "ask knowledge <question>", "notes search <query>",
    "ask vault <question>", "read note <name>", "web search <query>", "news [topic]", "weather <location>",
    "translate <text> to <language>", "distance <origin> to <destination>", "trip <stop1> to <stop2> to <stop3>",
    "summarize youtube <url>", "research <topic>", "research report <topic>", "solve <problem or decision>",
    "describe image <path>", "describe video <path> :: <question>", "email digest",
    "email api search gmail <query>", "briefing", "total|average|count <column> by <column> in <file.csv>",
    "calendar", "convert <amount> <unit> to <unit>",
}
# What each kind of agent gets for each group, outside the helper's own list.
EXPECTED = {
    #           planner  tainted planner  helper   backup model
    "reads":   ("allow", "allow", "helper", "allow"),
    "safe":    ("allow", "allow", "helper", "allow"),
    "carded":  ("allow", "allow", "helper", "trust"),
    "guarded": ("allow", "ask",   "helper", "trust"),
    "unknown": ("allow", "ask",   "helper", "trust"),
    "direct":  ("direct", "direct", "direct", "direct"),
}


def concrete(form: str) -> str:
    """A command written from a reference form: placeholders filled, options dropped, first alternative."""
    words = []
    for word in form.replace("[", " [").split():
        if word.startswith("["):
            continue
        words.append("x" if word.startswith("<") else word.split("|")[0])
    text = " ".join(words)
    while "[" in text:
        start = text.index("[")
        text = text[:start] + text[text.index("]", start) + 1:]
    return " ".join(text.replace(">", "").split())


def verdict(supervisor_: Supervisor, command: str, tier: str) -> str:
    decision = supervisor_.check(command, tier)
    if decision.verdict is Verdict.ALLOW:
        return "allow"
    if decision.verdict is Verdict.ASK:
        return "ask"
    return decision.rule


def roles(command: str) -> tuple[str, str, str, str]:
    planner = Supervisor(tier="ultra")
    tainted = Supervisor(tier="ultra")
    tainted.check("web search cats", "ultra")
    helper = Supervisor(supervisor.RunTree(), agent="a1", parent="a0", depth=1, tier="smart")
    backup = Supervisor(tier="ultra")
    return (verdict(planner, command, "ultra"), verdict(tainted, command, "ultra"),
            verdict(helper, command, "smart"), verdict(backup, command, "fallback"))


class CommandTableTests(unittest.TestCase):
    def test_every_agent_command_is_in_the_group_the_table_says(self) -> None:
        forms = set(AgentOrchestrator._AGENT_COMMANDS)
        self.assertEqual(forms - set(GROUPS), set(), "a new agent command: add it to GROUPS")
        self.assertEqual(set(GROUPS) - forms, set(), "a command left the list: drop it from GROUPS")
        for form, group in GROUPS.items():
            self.assertEqual(supervisor.group_of(concrete(form))[0], group, form)

    def test_every_agent_command_gets_the_right_verdict_from_every_kind_of_agent(self) -> None:
        for form, group in GROUPS.items():
            expected = list(EXPECTED[group])
            if form in HELPER_RUNS:
                expected[2] = "allow"
            self.assertEqual(roles(concrete(form)), tuple(expected), form)

    def test_a_helper_is_offered_exactly_what_it_may_run(self) -> None:
        offered = {form for form in GROUPS if supervisor.allowed_in_a_run(form) and supervisor.offered_to_helper(form)}
        self.assertEqual(offered, HELPER_RUNS)


class DirectBranchTests(unittest.TestCase):
    """Every way the orchestrator reaches a tool no run may use is refused as a step."""

    REACH = {"_run_terminal_command", "_run_agent", "_run_autopilot", "_code_fix"}

    @staticmethod
    def _reaches_direct(body: list[ast.stmt]) -> bool:
        for node in ast.walk(ast.Module(body=body, type_ignores=[])):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            name, owner = node.func.attr, node.func.value
            if name in DirectBranchTests.REACH:
                return True
            if name == "delete" and isinstance(owner, ast.Call) and getattr(owner.func, "attr", "") == "_file_ops":
                return True
            if name == "_schedule_add" and node.args and getattr(node.args[0], "value", None) == "agent":
                return True
        return False

    def test_every_prefix_that_reaches_a_direct_tool_is_direct(self) -> None:
        """Live review: `terminal` and `shell` ran the shell, and the supervisor did not know them."""
        prefixes: list[str] = []
        for node in ast.walk(ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.If) or not self._reaches_direct(node.body):
                continue
            test = node.test
            if (isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute) and test.func.attr == "startswith"
                    and getattr(test.func.value, "id", "") == "lowered"):
                for arg in test.args:
                    values = arg.elts if isinstance(arg, ast.Tuple) else [arg]
                    prefixes += [value.value for value in values if isinstance(value, ast.Constant)]
        self.assertGreaterEqual(len(prefixes), 8, prefixes)   # the walk itself still finds the branches
        for prefix in prefixes:
            self.assertEqual(supervisor.group_of(f"{prefix.strip()} x")[0], "direct", prefix)


class DisguiseTests(unittest.TestCase):
    """A spelling the orchestrator still runs is classified the same; one it does not run may be unknown,
    which a tainted run is asked about, and a helper or the backup model is refused."""

    def test_case_and_spacing_do_not_change_a_verdict(self) -> None:
        for form, group in GROUPS.items():
            plain = concrete(form)
            for disguise in (plain.upper(), plain.title(), "  " + plain.replace(" ", "   ") + "  ",
                             plain.replace(" ", "\t")):
                self.assertEqual(roles(disguise), roles(plain), repr(disguise))

    def test_a_disguised_command_is_never_judged_more_quietly(self) -> None:
        for form in GROUPS:
            plain = concrete(form)
            head, _, rest = plain.partition(" ")
            for disguise in ("please " + plain, '"' + plain, plain.replace(" ", "​", 1),
                             "".join(chr(ord(c) + 0xFEE0) if "a" <= c <= "z" else c for c in head) + " " + rest,
                             head + "-" + rest.replace(" ", "-", 1)):
                group = supervisor.group_of(disguise)[0]
                self.assertIn(group, {supervisor.group_of(plain)[0], "unknown"}, repr(disguise))
                if group == "unknown":
                    self.assertEqual(roles(disguise)[1:], ("ask", "helper", "trust"), repr(disguise))

    def test_a_disguised_direct_command_does_not_reach_its_tool(self) -> None:
        """Unknown to the supervisor, so allowed for a clean planner: the orchestrator must not run it."""
        source = ORCHESTRATOR.read_text(encoding="utf-8")
        for plain in ("run command whoami", "file delete a.txt", "code run a.py", "agent run tidy", "shell whoami"):
            head, _, rest = plain.partition(" ")
            for disguise in ("please " + plain, '"' + plain, plain.replace(" ", "​", 1),
                             "".join(chr(ord(c) + 0xFEE0) for c in head) + " " + rest):
                lowered = disguise.strip().lower()
                prefixes = [p for p in ("run command ", "terminal ", "shell ", "file delete ", "code run ",
                                        "agent run ") if f'lowered.startswith("{p}")' in source]
                self.assertFalse(any(lowered.startswith(p) for p in prefixes), repr(disguise))


class RandomRunTests(unittest.TestCase):
    """Thousands of random steps across a tree, against the rules that must always hold."""

    COMMANDS = [concrete(form) for form in GROUPS] + ["delegate fast part", "delegate smart part",
                                                      "delegate ultra part", "frobnicate", "(no command)"]

    def test_the_rules_hold_for_any_order_of_steps(self) -> None:
        for seed in range(300):
            rng = random.Random(seed)
            now = [0.0]
            tree = supervisor.RunTree(clock=lambda: now[0])
            agents = [Supervisor(tree, tier="ultra")]
            stopped_at = None
            for _ in range(rng.randint(5, 40)):
                now[0] += rng.choice((0.1, 1.0, 5.0, 30.0))
                agent = rng.choice(agents)
                command = rng.choice(self.COMMANDS)
                tier = rng.choice(("ultra", "smart", "fast", "fallback")) if agent.depth == 0 else agent.tier
                tainted_before = bool(tree.tainted_by)
                group = supervisor.group_of(command)[0]
                decision = agent.check(command, tier)
                where = f"seed {seed}: {agent.agent} depth {agent.depth} {tier} {command!r}"
                if stopped_at is not None:
                    self.assertIs(decision.verdict, Verdict.STOP, where)          # a stop is final
                    continue
                if decision.verdict is Verdict.STOP:
                    stopped_at = decision
                    continue
                self.assertLessEqual(tree.steps, supervisor.MAX_TREE_STEPS, where)
                if decision.verdict is not Verdict.ALLOW:
                    continue
                self.assertNotEqual(group, "direct", where)
                if agent.depth > 0:
                    self.assertTrue(supervisor.helper_may(command), where)
                if not supervisor.trusted(tier):
                    self.assertIn(group, {"reads", "safe"}, where)
                if tainted_before:
                    self.assertNotIn(group, {"guarded", "unknown"}, where)
                if group == "delegate":
                    self.assertLess(agent.depth, supervisor.MAX_DEPTH, where)
                    self.assertLess(len(tree.agents), supervisor.MAX_AGENTS, where)
                    target = command.split()[1]
                    self.assertLess(supervisor.TIER_RANK[target], supervisor.TIER_RANK[agent.tier], where)
                    child = tree.add_agent(agent.agent, agent.depth + 1, target, "part")
                    agents.append(Supervisor(tree, agent=child, parent=agent.agent, depth=agent.depth + 1, tier=target))
                if rng.random() < 0.05:
                    agent.refused("you did not approve it")


if __name__ == "__main__":
    unittest.main()
