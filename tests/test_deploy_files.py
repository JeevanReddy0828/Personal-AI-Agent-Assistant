"""The files that deploy a hosted copy agree with each other and keep the key out of the image.

Nothing here builds an image: a mismatch between the Dockerfile, fly.toml and the app (the
port, the volume path, the hostname the server accepts) otherwise only shows when a deploy
starts and refuses every request.
"""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETISH = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASSCODE", re.IGNORECASE)


def _dockerfile_env() -> dict[str, str]:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    block = re.search(r"^ENV\s+(.*?)(?=^\S)", text, re.MULTILINE | re.DOTALL).group(1)
    return dict(re.findall(r"([A-Z_]+)=(\S+?)\s*\\?\s*$", block.replace("\\\n", "\n"), re.MULTILINE))


class DeployFilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fly = tomllib.loads((ROOT / "fly.toml").read_text(encoding="utf-8"))
        cls.image_env = _dockerfile_env()

    def test_the_image_starts_hosted_and_reachable_by_the_proxy(self) -> None:
        env = self.image_env
        self.assertEqual(env["LAPTOP_AGENT_HOSTED"], "1")
        self.assertEqual(env["LAPTOP_AGENT_HOST"], "0.0.0.0")
        self.assertEqual(int(env["LAPTOP_AGENT_PORT"]), self.fly["http_service"]["internal_port"])

    def test_the_data_is_on_the_volume(self) -> None:
        self.assertEqual(self.image_env["LAPTOP_AGENT_DATA_DIR"], self.fly["mounts"]["destination"])

    def test_the_server_answers_to_the_apps_own_name(self) -> None:
        allowed = self.fly["env"]["LAPTOP_AGENT_ALLOWED_HOSTS"]
        self.assertEqual(allowed, f"{self.fly['app']}.fly.dev")
        import laptop_agent.webui as webui
        from unittest.mock import patch

        with patch.dict("os.environ", {"LAPTOP_AGENT_ALLOWED_HOSTS": allowed}):
            self.assertEqual(webui._allowed_hosts(), frozenset({allowed}), "not a plain hostname the server accepts")

    def test_https_only_and_always_on(self) -> None:
        service = self.fly["http_service"]
        self.assertTrue(service["force_https"])
        self.assertFalse(service["auto_stop_machines"], "the reminder ticker needs the machine running")
        self.assertGreaterEqual(service["min_machines_running"], 1)

    def test_no_secret_is_written_into_the_config(self) -> None:
        names = list(self.fly["env"]) + list(self.image_env)
        self.assertEqual([name for name in names if SECRETISH.search(name)], [])
        self.assertNotIn("nvapi-", (ROOT / "fly.toml").read_text(encoding="utf-8"))

    def test_the_local_env_file_never_enters_the_image(self) -> None:
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        for needed in (".env", ".agent_data", ".git"):
            self.assertIn(needed, ignored)
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        copied = re.findall(r"^COPY\s+(.+?)\s+\S+$", dockerfile, re.MULTILINE)
        self.assertNotIn(".env", " ".join(copied).split())

    def test_the_server_does_not_run_as_root(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"setpriv --reuid=jarvis")
        self.assertNotRegex(dockerfile, r"(?m)^USER\s+root")


def _service(name: str) -> str:
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    found = re.search(rf"^  {name}:\n(.*?)(?=^  \w|^\w|\Z)", text, re.MULTILINE | re.DOTALL)
    assert found, f"no service {name} in compose.yaml"
    return found.group(1)


class ComposeFilesTests(unittest.TestCase):
    """The VM setup: Caddy in front, the app never published, nothing secret in the file."""

    def test_only_caddy_is_reachable_from_outside(self) -> None:
        self.assertNotRegex(_service("jarvis"), r"(?m)^    ports:", "the app must not be published")
        published = re.findall(r'- "(\d+):\d+"', _service("caddy"))
        self.assertEqual(sorted(published), ["443", "80"])
        self.assertIn(f'- "{_dockerfile_env()["LAPTOP_AGENT_PORT"]}"', _service("jarvis"))

    def test_the_name_is_one_setting_for_the_app_and_for_caddy(self) -> None:
        self.assertRegex(_service("jarvis"), r"LAPTOP_AGENT_ALLOWED_HOSTS: \$\{JARVIS_HOST:\?")
        self.assertIn("JARVIS_HOST: ${JARVIS_HOST}", _service("caddy"))
        self.assertIn("{$JARVIS_HOST}", (ROOT / "Caddyfile").read_text(encoding="utf-8"))

    def test_the_key_and_name_must_be_supplied_not_defaulted(self) -> None:
        env = _service("jarvis")
        self.assertRegex(env, r"OPENAI_API_KEY: \$\{OPENAI_API_KEY:\?")     # required, and never a literal
        self.assertNotIn("nvapi-", (ROOT / "compose.yaml").read_text(encoding="utf-8"))

    def test_the_proxy_header_the_app_trusts_is_the_one_caddy_sets(self) -> None:
        caddyfile = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        named = re.search(r"LAPTOP_AGENT_CLIENT_IP_HEADER: (\S+)", _service("jarvis")).group(1)
        self.assertRegex(caddyfile, rf"header_up {re.escape(named)} \{{remote_host\}}")

    def test_caddy_proxies_to_the_apps_port_and_does_not_hold_back_streamed_replies(self) -> None:
        caddyfile = (ROOT / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn(f"reverse_proxy jarvis:{_dockerfile_env()['LAPTOP_AGENT_PORT']}", caddyfile)
        self.assertIn("flush_interval -1", caddyfile)

    def test_the_app_and_its_proxy_keep_their_state_across_restarts(self) -> None:
        self.assertIn("jarvis_data:/data", _service("jarvis"))
        self.assertIn("caddy_data:/data", _service("caddy"))   # certificates: losing them re-requests, and rate limits bite
        for service in ("jarvis", "caddy"):
            self.assertIn("restart: unless-stopped", _service(service))

    def test_fly_names_its_own_proxy_header(self) -> None:
        self.assertEqual(tomllib.loads((ROOT / "fly.toml").read_text(encoding="utf-8"))["env"]
                         ["LAPTOP_AGENT_CLIENT_IP_HEADER"], "Fly-Client-IP")


if __name__ == "__main__":
    unittest.main()
