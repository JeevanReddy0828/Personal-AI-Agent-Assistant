"""The setup view: each capability, whether it is ready, and what to do if not.

It is shown in the page, so it may name an environment variable or an install command and
nothing else from the configuration: no key, password, path or model id.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from laptop_agent.health import chromium_installed, setup_report

SECRETS = ("nvapi-SECRET-chat", "nvapi-SECRET-image", "SECRET-search", "SECRET-imap-password",
           "SECRET-smtp-password", "SECRET-openrouter", "C:\\Users\\me\\Private Vault", "vendor/secret-model-7b",
           "imap.secret-host.example", "me@secret.example")


class OpenAICompatibleProvider:
    """Named like the real provider: the report tells a model from built-in rules by the type."""


class _Status:
    def __init__(self, tiers=None, broken=(), reasons=None):
        self._snapshot = {"tiers": tiers or {}, "broken": list(broken), "reasons": reasons or {}}

    def snapshot(self):
        return self._snapshot


def orchestrator(*, model=True, smart=True, ultra=True, vision=True, vault=True, status=None):
    return SimpleNamespace(
        planner=SimpleNamespace(provider=OpenAICompatibleProvider() if model else object()),
        smart_planner=object() if smart else None, ultra_planner=object() if ultra else None,
        vision_planner=object() if vision else None, model_status=status or _Status({"fast": "ok"}),
        context=SimpleNamespace(obsidian=SimpleNamespace(available=lambda: vault)))


def config(**overrides):
    values = dict(llm_api_key=SECRETS[0], llm_image_api_key=SECRETS[1], search_provider="brave",
                  search_api_key=SECRETS[2], imap_host=SECRETS[8], imap_username=SECRETS[9],
                  imap_password=SECRETS[3], smtp_host=SECRETS[8], smtp_username=SECRETS[9],
                  smtp_password=SECRETS[4], openrouter_api_key=SECRETS[5], obsidian_vault=SECRETS[6],
                  llm_model=SECRETS[7])
    values.update(overrides)
    return SimpleNamespace(**values)


def everything(module):
    return object()


def nothing(module):
    return None


class SetupReportTests(unittest.TestCase):
    def report(self, orch=None, cfg=None, **kwargs):
        options = dict(llm_reachable=True, stt_engine="vosk", ocr_engine="tesseract", sign_in=True, lan_mode=True,
                       find_spec=everything, which=lambda name: "/usr/bin/" + name, browser_engine=lambda: True)
        options.update(kwargs)
        rows = setup_report(orch or orchestrator(), cfg or config(), **options)
        return {row["key"]: row for row in rows}

    def test_everything_set_up_is_ready_and_says_nothing_to_do(self):
        rows = self.report()
        self.assertEqual(len(rows), 18)
        self.assertEqual({key: row["state"] for key, row in rows.items() if row["state"] != "ready"}, {})
        self.assertEqual({key: row["next"] for key, row in rows.items() if row["next"]}, {})

    def test_a_fresh_install_says_what_to_do_for_each_missing_piece(self):
        rows = self.report(orchestrator(model=False, smart=False, ultra=False, vision=False, vault=False),
                           SimpleNamespace(), llm_reachable=None, stt_engine=None, ocr_engine=None,
                           sign_in=False, lan_mode=False, find_spec=nothing, which=lambda name: None)
        states = {key: row["state"] for key, row in rows.items()}
        self.assertEqual(states["chat"], "off")
        self.assertEqual({states[key] for key in ("stt", "ocr", "docs", "browser", "metrics")}, {"missing"})
        for key, row in rows.items():
            with self.subTest(key):
                if row["state"] != "ready":
                    self.assertTrue(row["next"], f"{key} is {row['state']} and says nothing about what to do")
        self.assertIn("OPENAI_API_KEY", rows["chat"]["next"])
        # The shipped .env.example says heuristic; a key alone never turns the model on.
        self.assertIn("LAPTOP_AGENT_LLM_PROVIDER", rows["chat"]["next"])
        self.assertIn("laptop-agent[browser]", rows["browser"]["next"])

    def test_a_busy_tier_and_a_broken_one_are_told_apart(self):
        status = _Status({"fast": "degraded", "smart": "degraded"}, broken=["smart"],
                         reasons={"smart": "the key was rejected (HTTP 401): check OPENAI_API_KEY"})
        rows = self.report(orchestrator(status=status))
        self.assertEqual((rows["chat"]["state"], rows["deeper"]["state"]), ("busy", "broken"))
        self.assertIn("HTTP 401", rows["deeper"]["next"], "a broken tier must say what to change")
        unreachable = self.report(llm_reachable=False)["chat"]
        self.assertEqual(unreachable["state"], "busy")

    def test_no_secret_path_or_model_id_reaches_the_report(self):
        # Compared without case: the search row once title-cased what it echoed, which an
        # exact-case check does not see ("Nvapi-Secret-..." for a key typed into the wrong field).
        for rows in (self.report(), self.report(find_spec=nothing, stt_engine=None, ocr_engine=None),
                     self.report(cfg=config(search_api_key=None)), self.report(cfg=config(search_provider=SECRETS[0]))):
            text = json.dumps(list(rows.values())).lower()
            for secret in SECRETS:
                with self.subTest(secret):
                    self.assertNotIn(secret.lower(), text)

    def test_a_package_probe_that_raises_counts_as_missing(self):
        def broken(module):
            raise ValueError(f"{module}.__spec__ is None")

        rows = self.report(find_spec=broken)
        self.assertEqual(rows["browser"]["state"], "missing")

    def test_tesseract_needs_its_program_not_just_the_package(self):
        self.assertEqual(self.report(which=lambda name: None)["ocr"]["state"], "missing")
        self.assertEqual(self.report(which=lambda name: None, ocr_engine="nemotron-parse")["ocr"]["state"], "ready")

    def test_a_real_failure_reason_never_shows_the_model_id(self):
        # Codex's review of #144: a stored reason is classify_failure's own wording, which names
        # the model id, and it went straight into `next`. The advice is rebuilt from the status.
        from laptop_agent.planner.openai_compatible import classify_failure

        reasons = {}
        for tier, code in (("fast", 410), ("smart", 404), ("ultra", 401)):
            _, reasons[tier] = classify_failure(urllib.error.HTTPError("https://example.test", code, "x", {}, None),
                                                SECRETS[7])
            self.assertIn(SECRETS[7], reasons[tier], "the premise: the stored reason names the model")
        status = _Status({tier: "degraded" for tier in reasons}, broken=list(reasons), reasons=reasons)
        rows = self.report(orchestrator(status=status))
        self.assertEqual((rows["chat"]["state"], rows["deeper"]["state"]), ("broken", "broken"))
        self.assertNotIn(SECRETS[7], json.dumps(list(rows.values())))
        self.assertIn("HTTP 410", rows["chat"]["next"])
        self.assertIn("OPENAI_MODEL", rows["chat"]["next"])
        for needed in ("OPENAI_SMART_MODEL", "OPENAI_API_KEY", "HTTP 404", "HTTP 401"):
            self.assertIn(needed, rows["deeper"]["next"])

    def test_the_browser_row_needs_playwright_s_own_chromium(self):
        # Codex's review of #144: the package alone said ready with no browser installed. And an
        # upgrade leaves the old revision behind, which Playwright will not launch.
        with tempfile.TemporaryDirectory() as root:
            package, browsers = Path(root) / "playwright", Path(root) / "ms-playwright"
            (package / "driver" / "package").mkdir(parents=True)
            (package / "driver" / "package" / "browsers.json").write_text(json.dumps({"browsers": [
                {"name": "chromium", "revision": "1223"}, {"name": "chromium-headless-shell", "revision": "1223"},
                {"name": "firefox", "revision": "1522"}]}), encoding="utf-8")
            (browsers / "chromium-1217").mkdir(parents=True)
            (browsers / "firefox-1522").mkdir()

            def row():
                return self.report(browser_engine=lambda: chromium_installed(package, browsers))["browser"]

            self.assertEqual((row()["state"], row()["next"]), ("missing", "python -m playwright install chromium"))
            # Codex's re-review: an interrupted install leaves the revision's folders and nothing
            # in them. Neither the folders, nor the marker alone, is an installed browser.
            chrome = browsers / "chromium-1223"
            chrome.mkdir()
            (chrome / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
            self.assertEqual(row()["state"], "missing", "the marker without a browser counted as installed")
            shell = browsers / "chromium_headless_shell-1223"
            (shell / "chrome-headless-shell-win64").mkdir(parents=True)
            self.assertEqual(row()["state"], "missing", "empty revision folders counted as installed")
            (shell / "chrome-headless-shell-win64" / "chrome-headless-shell.exe").write_bytes(b"MZ")
            self.assertEqual(row()["state"], "missing", "a download Playwright never finished counted as installed")
            (shell / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
            self.assertEqual(row()["state"], "ready")
            self.assertFalse(chromium_installed(Path(root) / "no-such-package", browsers))

    def test_the_browsers_directory_is_the_one_playwright_uses(self):
        with tempfile.TemporaryDirectory() as empty, patch.dict("os.environ", {"PLAYWRIGHT_BROWSERS_PATH": empty}):
            self.assertFalse(chromium_installed(), "an empty browsers directory has no Chromium")

    def test_busy_says_wait_and_broken_says_what_to_change_even_when_unreachable(self):
        busy = self.report(orchestrator(status=_Status({"fast": "degraded", "smart": "degraded"})))
        self.assertEqual((busy["chat"]["state"], busy["chat"]["next"]), ("busy", None))
        self.assertEqual((busy["deeper"]["state"], busy["deeper"]["next"]), ("busy", None))
        broken = _Status({"fast": "degraded"}, broken=["fast"], reasons={"fast": "the API key was rejected - HTTP 401"})
        row = self.report(orchestrator(status=broken), llm_reachable=False)["chat"]
        self.assertEqual(row["state"], "broken", "an unanswered ping must not hide a known misconfiguration")
        self.assertIn("OPENAI_API_KEY", row["next"])

    def test_web_search_names_only_providers_it_uses(self):
        rows = {name: self.report(cfg=config(**values))["search"] for name, values in (
            ("unknown", {"search_provider": "bing"}), ("no provider", {"search_provider": ""}),
            ("no key", {"search_api_key": None}), ("working", {}))}
        self.assertEqual(rows["working"]["detail"], "Brave, with DuckDuckGo behind it.")
        self.assertIn("SEARCH_API_KEY", rows["no key"]["next"])
        for name in ("unknown", "no provider"):
            with self.subTest(name):
                self.assertIn("DuckDuckGo", rows[name]["detail"])
                self.assertIn("brave, serpapi, serper", rows[name]["next"])
        self.assertNotIn("bing", json.dumps(rows["unknown"]).lower())

    def test_usage_meters_count_windows_own_counters(self):
        # metrics.py falls back to PowerShell when psutil is absent, and the drawer shows usage.
        self.assertEqual(self.report(find_spec=nothing, which=lambda name: "C:/ps/powershell.exe")["metrics"]["state"],
                         "ready")
        self.assertEqual(self.report(find_spec=nothing, which=lambda name: None)["metrics"]["state"], "missing")

    def test_a_chosen_search_provider_without_its_key_says_so(self):
        row = self.report(cfg=config(search_api_key=None))["search"]
        self.assertEqual(row["state"], "ready")
        self.assertIn("SEARCH_API_KEY", row["next"])


class OverTheWebTests(unittest.TestCase):
    """Developer-only: it describes this installation. The route allow-list refuses it to a
    personal account, with the header that stops the page reloading."""

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer

        import laptop_agent.webui as webui

        cls.webui = webui
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        from laptop_agent.accounts import AccountStore
        from laptop_agent.sessions import SessionStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        accounts = AccountStore(Path(tmp.name) / "accounts.json", cost=(2 ** 10, 8, 1))
        sessions = SessionStore(Path(tmp.name) / "sessions.json")
        for name, value in (("ACCOUNTS", accounts), ("SESSIONS", sessions)):
            patcher = patch.object(self.webui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cookies = {}
        for username, role in (("jeevan", "dev"), ("family", "personal")):
            account = accounts.create(username, role, "correct horse battery")
            self.cookies[role] = f"jarvis_session={sessions.create(account.id, 'password')}"

    def get(self, role):
        request = urllib.request.Request(self.base + "/api/setup", headers={"Cookie": self.cookies[role]})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read()), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}"), dict(error.headers)

    def test_a_developer_gets_every_row(self):
        status, body, _ = self.get("dev")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["items"]), 18)
        self.assertEqual({row["key"]: row["state"] for row in body["items"]}["sign_in"], "ready")

    def test_a_personal_account_is_refused(self):
        status, _, headers = self.get("personal")
        self.assertEqual((status, headers.get("X-Jarvis-Denied")), (403, "role"))


if __name__ == "__main__":
    unittest.main()
