"""Run the unit suite without reading .env, contacting services, or using personal data."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
sys.dont_write_bytecode = True


def main() -> int:
    import laptop_agent.config as config

    config._load_dotenv = lambda *a, **k: None
    for key in list(os.environ):
        if key.startswith(("OPENAI_", "OPENROUTER_", "SMTP_", "IMAP_", "GOOGLE_", "MICROSOFT_", "JOBRIGHT_", "SEARCH_", "BRAVE_", "SERPER_", "SERPAPI_", "OBSIDIAN_", "LAPTOP_AGENT_")):
            os.environ.pop(key, None)
    os.environ["LAPTOP_AGENT_LLM_PROVIDER"] = "heuristic"
    connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise OSError("External connections are disabled by the test runner")
        return connect(sock, address)

    socket.socket.connect = local_only
    with tempfile.TemporaryDirectory(prefix="jarvis_tests_") as scratch:
        try:
            os.chdir(scratch)
            os.environ["LAPTOP_AGENT_DATA_DIR"] = str(Path(scratch) / "data")
            import laptop_agent.webui as webui

            webui.UPLOAD_DIR = Path(scratch) / "uploads"
            pattern = sys.argv[1] if len(sys.argv) > 1 else "test_*.py"
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            return 0 if result.wasSuccessful() else 1
        finally:
            os.chdir(ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
