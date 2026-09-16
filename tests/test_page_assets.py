from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import laptop_agent.webui_page as webui_page

ASSETS = Path(webui_page.__file__).resolve().parent / "webui_assets"


class AssemblyTests(unittest.TestCase):
    """The page was one 183KB raw string in a 2529-line module. It is three asset files
    now, stitched back together at import — still served as one inlined document, because
    the CSP is `script-src 'nonce-…'` with no `'self'` and a `<script src>` would be
    blocked outright."""

    def test_the_three_assets_exist_and_are_not_empty(self) -> None:
        for name in ("app.html", "app.css", "app.js"):
            path = ASSETS / name
            self.assertTrue(path.is_file(), f"{name} is missing")
            self.assertGreater(path.stat().st_size, 1000, f"{name} looks truncated")

    def test_the_assembled_page_is_one_document(self) -> None:
        page = webui_page.build_page()
        self.assertEqual(page.count("<style>"), 1)
        self.assertEqual(page.count("</style>"), 1)
        self.assertEqual(page.count('<script nonce="{{NONCE}}">'), 1)
        self.assertTrue(page.rstrip().endswith("</html>"), page[-60:])

    def test_no_assembly_marker_survives(self) -> None:
        page = webui_page.build_page()
        self.assertFalse("{{STYLE}}" in page, "the stylesheet was not inlined")
        self.assertFalse("{{SCRIPT}}" in page, "the script was not inlined")

    def test_the_runtime_placeholders_are_still_there(self) -> None:
        """webui._rendered_page() substitutes these on the assembled page, so they have to
        survive assembly wherever they live."""
        page = webui_page.build_page()
        tokens = ("{{NONCE}}", "{{API_TOKEN}}", "{{PLANNER}}", "{{SMART}}",
                  "{{ULTRA}}", "{{VISION}}")
        lost = [token for token in tokens if token not in page]
        self.assertEqual(lost, [], f"lost in the split: {lost}")

    def test_the_hooks_the_browser_tests_drive_are_present(self) -> None:
        """CLAUDE.md names these as load-bearing for the Chromium suite. Checked as a set
        so a failure lists what went missing instead of printing all 183KB of the page."""
        page = webui_page.build_page()
        markers = ("data-view", "#nav", 'id="ta"', 'id="newChat"', 'id="mobileChats"',
                   'id="sysDrawer"', 'id="rsContact"', 'id="rsCerts"', 'id="rsProfileSave"',
                   'id="pipeMsg"')
        missing = [marker for marker in markers if marker not in page]
        self.assertEqual(missing, [], f"lost from the page: {missing}")

    def test_a_link_tag_was_not_introduced(self) -> None:
        """A served stylesheet would need `style-src 'self'` and a script would be blocked
        entirely. If someone "finishes" the split by linking the assets, this fails."""
        page = webui_page.build_page()
        self.assertFalse("<script src=" in page, "an external script would be CSP-blocked")
        self.assertFalse('rel="stylesheet"' in page, "a linked stylesheet needs style-src self")


class AssetResolutionTests(unittest.TestCase):
    """PyInstaller --onefile extracts --add-data into sys._MEIPASS, not next to the
    executable. Missing that is what left the packaged app unable to find the Vosk model
    it shipped with, so the page's assets must not repeat it."""

    def setUp(self) -> None:
        self._file = webui_page.__file__
        self.addCleanup(setattr, webui_page, "__file__", self._file)
        had = hasattr(sys, "_MEIPASS")
        previous = getattr(sys, "_MEIPASS", None)

        def restore():
            if had:
                sys._MEIPASS = previous
            elif hasattr(sys, "_MEIPASS"):
                del sys._MEIPASS

        self.addCleanup(restore)

    def frozen(self, layout: str | None) -> Path:
        """Pretend to be a bundle whose assets sit at `layout` under _MEIPASS."""
        root = Path(tempfile.mkdtemp())
        if layout:
            shutil.copytree(ASSETS, root / layout)
        webui_page.__file__ = str(root / "elsewhere" / "webui_page.py")
        sys._MEIPASS = str(root)
        return root

    def test_a_checkout_finds_the_assets_beside_the_module(self) -> None:
        self.assertEqual(webui_page._asset_dir(), ASSETS)

    def test_a_bundle_finds_them_under_the_package(self) -> None:
        self.frozen("laptop_agent/webui_assets")
        self.assertTrue(webui_page._asset_dir().is_dir())
        self.assertEqual(len(webui_page.build_page()), len(webui_page.PAGE))

    def test_a_bundle_finds_them_at_its_root(self) -> None:
        self.frozen("webui_assets")
        self.assertTrue(webui_page._asset_dir().is_dir())
        self.assertEqual(len(webui_page.build_page()), len(webui_page.PAGE))

    def test_missing_assets_say_what_is_missing_and_where(self) -> None:
        self.frozen(None)
        with self.assertRaises(RuntimeError) as caught:
            webui_page.build_page()
        message = str(caught.exception)
        self.assertIn("app.html", message)
        self.assertIn("--add-data", message, "the fix belongs in the error")


if __name__ == "__main__":
    unittest.main()
