from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from laptop_agent.tools import transcribe

_UNSET = object()


PACKAGING = Path(__file__).resolve().parent.parent / "packaging"


class BundledTimeZoneDataTests(unittest.TestCase):
    """Windows ships no time zone database, so `zoneinfo` cannot resolve a named zone and
    `time in Tokyo` degrades to "pip install tzdata" — advice a user of a packaged
    JARVIS.exe cannot act on. Both builds therefore carry tzdata (~700KB).

    Asserted against the scripts because it only exists in a frozen build, the same blind
    spot that shipped a small build with no speech-to-text. There are two scripts, and a
    flag added to one and forgotten in the other has happened here before."""

    def scripts(self) -> list[Path]:
        found = sorted(PACKAGING.glob("build_app*.ps1"))
        self.assertTrue(found, "no PyInstaller build scripts found")
        return found

    def test_every_build_bundles_the_time_zone_database(self) -> None:
        for script in self.scripts():
            text = script.read_text(encoding="utf-8")
            self.assertIn(
                "--collect-all tzdata", text,
                f"{script.name} ships no time zone database, so a packaged app "
                "cannot convert a named zone",
            )

    def test_the_package_itself_still_declares_no_dependencies(self) -> None:
        """Bundling is the installer's business. tzdata must not become a runtime dep."""
        pyproject = (PACKAGING.parent / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("dependencies = []", pyproject,
                      "the zero-required-dependency rule was broken")


class FrozenVoskModelTests(unittest.TestCase):
    """`build_app_small.ps1` bundles a Vosk model with `--add-data "models;models"` and
    ships Vosk *instead of* Whisper/PyTorch, to keep JARVIS.exe small.

    `--onefile` extracts bundled data into `sys._MEIPASS`, not next to the executable, so
    the packaged app could not see the model it shipped with — `_vosk_available()` returned
    False, `LAPTOP_AGENT_STT=auto` fell through to a Whisper that was deliberately not in
    the bundle, and the small build had no working speech-to-text at all. It only shows up
    in the .exe, which is why the unit suite never caught it.
    """

    def setUp(self) -> None:
        for name in ("frozen", "_MEIPASS"):
            original = getattr(sys, name, _UNSET)
            self.addCleanup(self._restore, name, original)
        executable = sys.executable
        self.addCleanup(setattr, sys, "executable", executable)

        for key in ("VOSK_MODEL", "LAPTOP_AGENT_VOSK_MODEL"):
            original_env = os.environ.pop(key, None)
            if original_env is not None:
                self.addCleanup(os.environ.__setitem__, key, original_env)

        # Path.cwd() is one of the search bases, so run from somewhere with no models/.
        here = Path.cwd()
        self.addCleanup(os.chdir, here)
        os.chdir(tempfile.mkdtemp())

    @staticmethod
    def _restore(name: str, original: object) -> None:
        if original is _UNSET:
            if hasattr(sys, name):
                delattr(sys, name)
        else:
            setattr(sys, name, original)

    def freeze(self, bundled: str | None, beside: str | None) -> str | None:
        """Stand in for a packaged app: `bundled` lands in _MEIPASS, `beside` next to the exe."""
        exe_dir = Path(tempfile.mkdtemp())
        meipass = Path(tempfile.mkdtemp())
        if bundled:
            (meipass / "models" / bundled).mkdir(parents=True)
        if beside:
            (exe_dir / "models" / beside).mkdir(parents=True)
        sys.frozen = True
        sys._MEIPASS = str(meipass)
        sys.executable = str(exe_dir / "JARVIS.exe")
        found = transcribe._resolve_vosk_model_path()
        return Path(found).name if found else None

    def test_the_bundled_model_is_found(self) -> None:
        self.assertEqual(self.freeze("vosk-model-small-en-us-0.15", None), "vosk-model-small-en-us-0.15")

    def test_a_model_beside_the_exe_is_found(self) -> None:
        self.assertEqual(self.freeze(None, "vosk-model-en-us-0.22"), "vosk-model-en-us-0.22")

    def test_a_users_own_model_wins_over_the_bundled_one(self) -> None:
        self.assertEqual(
            self.freeze("vosk-model-small-en-us-0.15", "vosk-model-en-us-0.22"),
            "vosk-model-en-us-0.22",
        )

    def test_no_model_anywhere_resolves_to_nothing(self) -> None:
        self.assertIsNone(self.freeze(None, None))


if __name__ == "__main__":
    unittest.main()
