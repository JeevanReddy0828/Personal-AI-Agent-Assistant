from __future__ import annotations

import re
import unittest

from laptop_agent.webui_page import PAGE

# The browser regression tests catch all of this — but they are opt-in
# (JARVIS_BROWSER_TESTS=1) and need Playwright and a Chromium download, so in practice a
# broken page ships and is found by a person. These checks are structural, need nothing
# installed, and run every time.
#
# The bug class they exist for: editing 2,376 lines of JS and CSS inside a Python string
# through shell heredocs, which repeatedly turned "\n" into a real newline and split a
# string literal in half. Python still imports the module fine; the page is simply dead.


def script_blocks() -> list[str]:
    return re.findall(r"<script\b[^>]*>(.*?)</script>", PAGE, re.S | re.I)


def style_blocks() -> list[str]:
    return re.findall(r"<style\b[^>]*>(.*?)</style>", PAGE, re.S | re.I)


def strip_js(source: str) -> str:
    """Blank out strings, template literals, regex literals and comments.

    Balance counting is meaningless without this: a brace inside a string, or a `/` that
    opens a regex, would be counted as code.
    """
    out: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        nxt = source[index + 1] if index + 1 < length else ""
        if char == "/" and nxt == "/":
            index = source.find("\n", index)
            if index == -1:
                break
            continue
        if char == "/" and nxt == "*":
            end = source.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        if char in "'\"`":
            quote = char
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                # A template literal may span lines; a plain string may not, and an
                # unterminated one is exactly the corruption being hunted.
                if quote != "`" and source[index] == "\n":
                    out.append("\x00")  # marker: string broken across a newline
                    break
                index += 1
            out.append('""')
            continue
        if char == "/":
            # A regex literal only follows an operator or an opening bracket.
            previous = "".join(out).rstrip()
            if previous and previous[-1] in "(,=:[!&|?{};+-*%~^<>" or not previous:
                index += 1
                while index < length:
                    if source[index] == "\\":
                        index += 2
                        continue
                    if source[index] == "\n":
                        break
                    if source[index] == "/":
                        index += 1
                        break
                    index += 1
                out.append("R")
                continue
        out.append(char)
        index += 1
    return "".join(out)


class ScriptIntegrityTests(unittest.TestCase):
    def test_there_is_exactly_one_script_block(self) -> None:
        blocks = script_blocks()
        self.assertEqual(len(blocks), 1, "the page should have one inline script")
        self.assertGreater(len(blocks[0]), 20_000, "the script looks truncated")

    def test_no_string_literal_is_broken_across_a_newline(self) -> None:
        # The exact corruption heredoc editing kept producing: a "\n" inside a JS string
        # became a real newline, splitting the literal and killing the whole script.
        cleaned = strip_js(script_blocks()[0])
        self.assertNotIn("\x00", cleaned, "a quoted string is broken across a newline")

    def test_brackets_balance(self) -> None:
        cleaned = strip_js(script_blocks()[0])
        for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
            self.assertEqual(
                cleaned.count(opener), cleaned.count(closer),
                f"unbalanced {opener}{closer} in the page script",
            )

    def test_the_script_carries_its_nonce(self) -> None:
        # The CSP is script-src 'nonce-…' with no 'self', so a script without the
        # placeholder is silently blocked and the page is inert.
        self.assertIn('<script nonce="{{NONCE}}">', PAGE)

    def test_every_placeholder_the_server_fills_is_present(self) -> None:
        for placeholder in ("{{NONCE}}", "{{API_TOKEN}}", "{{PLANNER}}", "{{SMART}}",
                            "{{ULTRA}}", "{{VISION}}"):
            self.assertIn(placeholder, PAGE, placeholder)

    def test_no_placeholder_is_left_unknown(self) -> None:
        from laptop_agent import webui

        source = webui.PAGE if hasattr(webui, "PAGE") else PAGE
        del source
        known = {"{{NONCE}}", "{{API_TOKEN}}", "{{PLANNER}}", "{{SMART}}", "{{ULTRA}}", "{{VISION}}"}
        found = set(re.findall(r"\{\{[A-Z_]+\}\}", PAGE))
        self.assertEqual(found - known, set(), "the page expects a value the server never fills")


class ContentSecurityTests(unittest.TestCase):
    """The CSP is script-src 'nonce-…' with no 'self' and font-src 'self', so anything
    fetched from another origin fails silently — no error, just a feature that does
    nothing. Catching it here beats discovering it in the browser."""

    def test_no_external_script_or_stylesheet(self) -> None:
        offenders = re.findall(r'<(?:script|link)\b[^>]*(?:src|href)="(https?:)?//[^"]+"', PAGE)
        self.assertEqual(offenders, [], "an external asset would be blocked by the CSP")

    def test_no_web_font_import(self) -> None:
        self.assertNotIn("fonts.googleapis.com", PAGE)
        self.assertNotIn("@import url(http", PAGE.replace(" ", ""))


class StyleIntegrityTests(unittest.TestCase):
    def test_there_is_one_style_block_and_it_balances(self) -> None:
        blocks = style_blocks()
        self.assertEqual(len(blocks), 1)
        css = re.sub(r"/\*.*?\*/", "", blocks[0], flags=re.S)
        self.assertEqual(css.count("{"), css.count("}"), "unbalanced braces in the page CSS")

    def test_the_design_tokens_are_defined(self) -> None:
        css = style_blocks()[0]
        for token in ("--bg", "--surface", "--text", "--accent", "--sans", "--mono", "--danger"):
            self.assertIn(token + ":", css, token)


class RequiredHooksTests(unittest.TestCase):
    """Ids and classes the browser regression tests and the server both depend on. When
    one is renamed, this fails in the default run rather than only under Playwright."""

    REQUIRED_IDS = (
        "ta", "newChat", "chat", "sysDrawer", "nav", "mobileChats",
        "rsContact", "rsCerts", "rsProfileSave", "pipeMsg", "schedMsg",
    )

    def test_required_ids_exist(self) -> None:
        missing = [name for name in self.REQUIRED_IDS if f'id="{name}"' not in PAGE]
        self.assertEqual(missing, [], f"missing element ids: {missing}")

    def test_every_getelementbyid_target_exists_in_the_markup(self) -> None:
        # A typo here is a runtime TypeError on a null, which kills the rest of the
        # handler and usually the feature with it.
        referenced = set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", PAGE))
        declared = set(re.findall(r'id="([A-Za-z0-9_-]+)"', PAGE))
        created = set(re.findall(r"\.id\s*=\s*'([A-Za-z0-9_-]+)'", PAGE))
        missing = sorted(referenced - declared - created)
        self.assertEqual(missing, [], f"getElementById targets with no element: {missing}")

    def test_the_routed_views_are_all_present(self) -> None:
        for view in ("chat", "overview", "jobs", "pipeline"):
            self.assertIn(f'data-view="{view}"', PAGE, view)


class NoCorruptionTests(unittest.TestCase):
    """Stray control characters are the signature of a mangled edit."""

    def test_the_page_has_no_control_characters(self) -> None:
        bad = {ord(c) for c in PAGE if ord(c) < 32 and c not in "\n\r\t"}
        self.assertEqual(bad, set(), f"control characters in the page: {sorted(bad)}")

    def test_the_page_is_valid_utf8_without_replacement_chars(self) -> None:
        self.assertNotIn("�", PAGE, "a replacement character means an encoding was lost")


if __name__ == "__main__":
    unittest.main()
