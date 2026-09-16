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
