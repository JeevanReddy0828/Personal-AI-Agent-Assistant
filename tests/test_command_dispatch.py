from __future__ import annotations

import ast
import inspect
import re
import unittest

from laptop_agent.agents.orchestrator import AgentOrchestrator

# Every literal prefix `_handle` tests, in the order it tests them.
_STARTSWITH = re.compile(r'startswith\(\s*\(([^)]*)\)\s*\)|startswith\(\s*"([^"]+)"')
_LITERAL = re.compile(r'"([^"]+)"')


def dispatch_source() -> str:
    """Every dispatch group, concatenated in the order _DISPATCH runs them.

    The chain used to live in one 500-line `_handle`; it is now grouped methods, and the
    order that matters is the order of the table, not the order of the file.
    """
    return "\n".join(
        inspect.getsource(group) for group in AgentOrchestrator._DISPATCH
    )


def dispatch_prefixes() -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for offset, line in enumerate(dispatch_source().splitlines(), start=1):
        for match in _STARTSWITH.finditer(line):
            if match.group(2):
                found.append((offset, match.group(2)))
            else:
                for literal in _LITERAL.findall(match.group(1)):
                    found.append((offset, literal))
    return found


class PrefixOrderTests(unittest.TestCase):
    """`_handle` is a long sequence of `startswith` checks, so a general prefix placed
    above a more specific one silently swallows it — and silently is the problem: the
    command still "works", it just does the wrong thing.

    Found this way: `remember ` sat above `remember note `, so `remember note buy milk`
    stored the literal string "note buy milk" as a profile note instead of appending to
    the Obsidian vault. No error, no hint, and the command is documented in `help`.
    """

    def test_no_prefix_shadows_a_later_one(self) -> None:
        seen: list[tuple[int, str]] = []
        shadowed: list[str] = []
        for line_no, prefix in dispatch_prefixes():
            for earlier_line, earlier in seen:
                if prefix != earlier and prefix.startswith(earlier):
                    shadowed.append(
                        f"{earlier!r} (line +{earlier_line}) swallows {prefix!r} (line +{line_no}); "
                        f"move the longer one above it"
                    )
            seen.append((line_no, prefix))
        self.assertEqual(shadowed, [], "\n" + "\n".join(shadowed))

    def test_the_dispatch_is_actually_being_scanned(self) -> None:
        # A regex that silently matches nothing would make the test above vacuous.
        prefixes = dispatch_prefixes()
        self.assertGreater(len(prefixes), 60, "the prefix scan found almost nothing")
        self.assertIn("remember note ", [p for _, p in prefixes])

    def test_remember_note_reaches_the_vault_not_the_profile(self) -> None:
        source = dispatch_source()
        note_at = source.index('startswith("remember note ")')
        general_at = source.index('startswith("remember ")')
        self.assertLess(note_at, general_at, "remember note must be checked first")


class DispatchShapeTests(unittest.TestCase):
    """The chain was 500 lines inside one `_handle`, which is how a general prefix ended
    up above a specific one. It is now grouped methods behind a table; these keep any one
    group from quietly becoming the old problem again."""

    def group_length(self, group) -> int:
        tree = ast.parse(inspect.getsource(group).lstrip())
        function = tree.body[0]
        return (function.end_lineno or function.lineno) - function.lineno

    def test_handle_itself_stays_small(self) -> None:
        tree = ast.parse(inspect.getsource(AgentOrchestrator._handle).lstrip())
        function = tree.body[0]
        length = (function.end_lineno or function.lineno) - function.lineno
        self.assertLess(length, 220, f"_handle is {length} lines; it should only route")

    def test_no_single_group_has_grown_unchecked(self) -> None:
        oversized = {
            group.__name__: self.group_length(group)
            for group in AgentOrchestrator._DISPATCH
            if self.group_length(group) > 120
        }
        self.assertEqual(
            oversized, {},
            f"these dispatch groups need splitting: {oversized}",
        )

    def test_every_group_is_reachable_from_the_table(self) -> None:
        # A group defined but left out of _DISPATCH is dead code that looks alive.
        defined = {
            name for name in dir(AgentOrchestrator)
            if name.startswith("_dispatch_")
        }
        wired = {group.__name__ for group in AgentOrchestrator._DISPATCH}
        self.assertEqual(defined - wired, set(), "a dispatch group is not in the table")


if __name__ == "__main__":
    unittest.main()
