from __future__ import annotations

import unittest
from unittest import mock

from laptop_agent import window_fx


class WindowFxTests(unittest.TestCase):
    def test_noop_off_windows(self) -> None:
        with mock.patch.object(window_fx.sys, "platform", "linux"):
            applied = window_fx.apply_window_effects(opacity=0.5, on_top=True)
        self.assertEqual(applied, {"opacity": None, "on_top": None})

    def test_noop_when_window_not_found(self) -> None:
        # A finder returning 0 (no matching window) must short-circuit to a no-op,
        # regardless of platform — nothing is applied and nothing raises.
        applied = window_fx.apply_window_effects(opacity=0.5, on_top=True, finder=lambda title: 0)
        self.assertEqual(applied, {"opacity": None, "on_top": None})

    def test_default_title(self) -> None:
        self.assertEqual(window_fx.WINDOW_TITLE, "J.A.R.V.I.S")


if __name__ == "__main__":
    unittest.main()
