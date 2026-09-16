from __future__ import annotations

import unittest

from laptop_agent.safety import ApprovalDenied, ApprovalGate
from laptop_agent.tools.windows import (
    DesktopWindow,
    LAYOUTS,
    WindowTool,
    normalize_layout,
    parse_placements,
)


class FakeBackend:
    """A 1920x1032 work area with WhatsApp and Chrome open — the real desktop this was
    built against, minus the ctypes."""

    def __init__(self, windows=None, place_ok=True):
        self.placed: list[tuple] = []
        self.place_ok = place_ok
        self._windows = windows if windows is not None else [
            DesktopWindow(101, "WhatsApp", "WhatsApp.Root.exe"),
            DesktopWindow(202, "Inbox (3) - Gmail - Google Chrome", "chrome.exe"),
            DesktopWindow(303, ".env - codex new project - Visual Studio Code", "Code.exe"),
        ]

    def list_windows(self):
        return list(self._windows)

    def work_area(self):
        return (0, 0, 1920, 1032)

    def place(self, handle, left, top, width, height):
        self.placed.append((handle, left, top, width, height))
        return self.place_ok


def tool(backend=None, allow=True):
    return WindowTool(ApprovalGate(ask=lambda request: allow), backend=backend or FakeBackend())


class ParsingTests(unittest.TestCase):
    def test_the_way_it_was_actually_said_out_loud(self) -> None:
        """Asked by voice this came out as "left side WhatsApp right side Chrome" — the
        position before the name, and no conjunction between the two halves. Splitting on
        "and" first cannot parse that, which is why the positions are found first and the
        gaps between them read as names."""
        self.assertEqual(
            parse_placements("left side WhatsApp right side Chrome"),
            [("WhatsApp", "left"), ("Chrome", "right")],
        )

    def test_politeness_is_not_part_of_the_app_name(self) -> None:
        """Reported: "Hey Jarvis, could you put my WhatsApp on left and chrome on right?"
        asked for a window called 'could you WhatsApp' and said it was not open."""
        self.assertEqual(
            parse_placements("Hey Jarvis, could you put my WhatsApp on left and chrome on right?"),
            [("WhatsApp", "left"), ("chrome", "right")],
        )
        self.assertEqual(
            parse_placements("can you split whatsapp left and chrome right please"),
            [("whatsapp", "left"), ("chrome", "right")],
        )

    def test_a_mis_heard_sentence_still_finds_its_window(self) -> None:
        """Dictated, the follow-up arrived as "And Chrome on right using split windows
        function". The stray words must not cost the whole request."""
        self.assertEqual(
            parse_placements("And Chrome on right using split windows function"),
            [("Chrome", "right")],
        )

    def test_the_way_it_is_usually_typed(self) -> None:
        self.assertEqual(
            parse_placements("put whatsapp on the left and chrome on the right"),
            [("whatsapp", "left"), ("chrome", "right")],
        )

    def test_name_first_without_a_verb(self) -> None:
        self.assertEqual(
            parse_placements("whatsapp on the left, chrome on the right"),
            [("whatsapp", "left"), ("chrome", "right")],
        )

    def test_corners_and_multi_word_names(self) -> None:
        self.assertEqual(
            parse_placements("move vs code to the top left and spotify bottom right"),
            [("vs code", "top left"), ("spotify", "bottom right")],
        )

    def test_a_single_window(self) -> None:
        self.assertEqual(parse_placements("snap chrome to the left"), [("chrome", "left")])
        self.assertEqual(parse_placements("maximize chrome"), [("chrome", "full")])
        self.assertEqual(parse_placements("centre the whatsapp window"), [("whatsapp", "centre")])

    def test_a_sentence_with_no_position_is_not_a_placement(self) -> None:
        self.assertEqual(parse_placements("what is the weather today"), [])
        self.assertEqual(parse_placements(""), [])

    def test_layout_aliases(self) -> None:
        self.assertEqual(normalize_layout("maximise"), "full")
        self.assertEqual(normalize_layout("Full Screen"), "full")
        self.assertEqual(normalize_layout("center"), "centre")
        self.assertEqual(normalize_layout("right half"), "right")
        self.assertIsNone(normalize_layout("diagonally"))

    def test_every_layout_is_a_sane_fraction(self) -> None:
        for name, (x, y, w, h) in LAYOUTS.items():
            self.assertTrue(0 <= x < 1 and 0 <= y < 1, name)
            self.assertTrue(0 < w <= 1 and 0 < h <= 1, name)
            self.assertLessEqual(x + w, 1.0001, name)
            self.assertLessEqual(y + h, 1.0001, name)


class MatchingTests(unittest.TestCase):
    def test_a_window_matches_on_its_title_or_its_process(self) -> None:
        chrome = DesktopWindow(1, "Inbox (3) - Gmail - Google Chrome", "chrome.exe")
        whatsapp = DesktopWindow(2, "WhatsApp", "WhatsApp.Root.exe")
        self.assertTrue(chrome.matches("chrome"))
        self.assertTrue(chrome.matches("gmail"), "the title is what the user sees")
        self.assertTrue(whatsapp.matches("whatsapp"), "matched via WhatsApp.Root.exe")
        self.assertFalse(chrome.matches("firefox"))
        self.assertFalse(chrome.matches(""))


class AmbiguousNameTests(unittest.TestCase):
    """Reported: "chrome" arranged **Live Caption** — a Chrome-hosted widget whose process
    is also chrome.exe — because its title is shorter than "J.A.R.V.I.S - Google Chrome"
    and the tie-break was shortest-title."""

    LIVE_DESKTOP = [
        DesktopWindow(1, "WhatsApp", "WhatsApp.Root.exe"),
        DesktopWindow(2, "J.A.R.V.I.S - Google Chrome", "chrome.exe"),
        DesktopWindow(3, "Live Caption", "chrome.exe"),
        DesktopWindow(4, "WhatsApp", "msedgewebview2.exe"),
    ]

    def test_chrome_is_the_chrome_window_not_a_chrome_hosted_widget(self) -> None:
        backend = FakeBackend(windows=self.LIVE_DESKTOP)
        result = tool(backend).arrange([("chrome", "right")])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(backend.placed[0][0], 2, "picked the wrong chrome.exe window")
        self.assertIn("Google Chrome", result.message)

    def test_whatsapp_is_the_app_not_its_webview(self) -> None:
        backend = FakeBackend(windows=self.LIVE_DESKTOP)
        tool(backend).arrange([("whatsapp", "left")])
        self.assertEqual(backend.placed[0][0], 1, "picked the msedgewebview2 window")

    def test_a_title_only_match_still_works(self) -> None:
        """"gmail" names no process at all, only a page title."""
        backend = FakeBackend(windows=[DesktopWindow(9, "Inbox - Gmail - Google Chrome", "chrome.exe")])
        result = tool(backend).arrange([("gmail", "left")])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(backend.placed[0][0], 9)


class ArrangeTests(unittest.TestCase):
    def test_a_split_places_both_halves(self) -> None:
        backend = FakeBackend()
        result = tool(backend).arrange([("whatsapp", "left"), ("chrome", "right")])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(backend.placed, [(101, 0, 0, 960, 1032), (202, 960, 0, 960, 1032)])

    def test_full_uses_the_work_area_not_the_screen(self) -> None:
        """The work area excludes the taskbar, so a maximised window is not hidden by it."""
        backend = FakeBackend()
        tool(backend).arrange([("chrome", "full")])
        self.assertEqual(backend.placed, [(202, 0, 0, 1920, 1032)])

    def test_the_shortest_title_wins_a_tie(self) -> None:
        """"chrome" should prefer a real Chrome window over one that merely mentions it."""
        backend = FakeBackend(windows=[
            DesktopWindow(1, "How to uninstall Google Chrome - Stack Overflow — Firefox", "firefox.exe"),
            DesktopWindow(2, "Google Chrome", "chrome.exe"),
        ])
        tool(backend).arrange([("chrome", "left")])
        self.assertEqual(backend.placed[0][0], 2)

    def test_a_stray_word_falls_back_to_the_words_that_matter(self) -> None:
        """Speech brings along words no filter will catch. Refusing the whole request over
        one of them is worse than acting on its most specific word."""
        backend = FakeBackend()
        result = tool(backend).arrange([("some whatsapp thing", "left")])
        self.assertTrue(result.ok, result.message)
        self.assertEqual(backend.placed[0][0], 101)

    def test_a_window_that_is_not_open_says_what_is(self) -> None:
        result = tool().arrange([("spotify", "left")])
        self.assertFalse(result.ok)
        self.assertIn("spotify", result.message)
        self.assertIn("chrome.exe", result.message, "name what is actually open")

    def test_an_unknown_position_lists_the_real_ones(self) -> None:
        result = tool().arrange([("chrome", "diagonally")])
        self.assertFalse(result.ok)
        self.assertIn("diagonally", result.message)
        self.assertIn("bottom right", result.message)

    def test_nothing_to_do_asks_for_a_window(self) -> None:
        result = tool().arrange([])
        self.assertFalse(result.ok)
        self.assertIn("window", result.message.lower())

    def test_a_denied_approval_moves_nothing(self) -> None:
        backend = FakeBackend()
        with self.assertRaises(ApprovalDenied):
            tool(backend, allow=False).arrange([("chrome", "left")])
        self.assertEqual(backend.placed, [])

    def test_a_backend_that_cannot_place_reports_it(self) -> None:
        result = tool(FakeBackend(place_ok=False)).arrange([("chrome", "left")])
        self.assertFalse(result.ok)
        self.assertIn("could not move", result.message.lower())

    def test_listing_shows_titles_and_processes(self) -> None:
        result = tool().list_windows()
        self.assertTrue(result.ok)
        self.assertIn("WhatsApp", result.message)
        self.assertEqual(len(result.data["windows"]), 3)

    def test_an_empty_desktop_says_so(self) -> None:
        result = tool(FakeBackend(windows=[])).list_windows()
        self.assertFalse(result.ok)


class UnsupportedPlatformTests(unittest.TestCase):
    def test_without_a_backend_it_says_windows_only(self) -> None:
        import laptop_agent.tools.windows as module

        original = module.sys.platform
        module.sys.platform = "linux"
        self.addCleanup(setattr, module.sys, "platform", original)
        bare = WindowTool(ApprovalGate(ask=lambda request: True))
        self.assertFalse(bare.available())
        result = bare.arrange([("chrome", "left")])
        self.assertFalse(result.ok)
        self.assertIn("Windows", result.message)


if __name__ == "__main__":
    unittest.main()
