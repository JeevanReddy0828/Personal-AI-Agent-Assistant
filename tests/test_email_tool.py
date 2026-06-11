from __future__ import annotations

import unittest
from pathlib import Path

from laptop_agent.config import AppConfig
from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.email import EmailTool


class EmailToolTests(unittest.TestCase):
    def build_config(self) -> AppConfig:
        return AppConfig(
            data_dir=Path("."),
            memory_path=Path("memory.json"),
            audit_log_path=Path("audit.jsonl"),
            token_vault_path=Path("email_tokens.json"),
            downloads_dir=Path("downloads"),
            smtp_host=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_from=None,
            imap_host=None,
            imap_port=993,
            imap_username=None,
            imap_password=None,
            imap_mailbox="INBOX",
            google_client_id="google-client",
            google_client_secret="google-secret",
            google_redirect_uri="http://localhost:8765/oauth/callback",
            microsoft_client_id="microsoft-client",
            microsoft_client_secret="microsoft-secret",
            microsoft_tenant="common",
            microsoft_redirect_uri="http://localhost:8765/oauth/callback",
            llm_provider="heuristic",
            llm_base_url="https://api.openai.com/v1",
            llm_model=None,
            llm_api_key=None,
        )

    def test_oauth_status_redacts_client_ids(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        result = tool.oauth_status()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["providers"]["gmail"]["client_id"], "goog...ient")
        self.assertTrue(result.data["providers"]["outlook"]["configured"])

    def test_gmail_authorization_url(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        result = tool.oauth_authorization_url("gmail")
        self.assertTrue(result.ok)
        self.assertIn("accounts.google.com", result.data["url"])
        self.assertIn("gmail.readonly", result.data["url"])

    def test_outlook_authorization_url(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        result = tool.oauth_authorization_url("outlook")
        self.assertTrue(result.ok)
        self.assertIn("login.microsoftonline.com/common", result.data["url"])
        self.assertIn("Mail.Read", result.data["url"])

    def test_imap_criteria(self) -> None:
        self.assertEqual(EmailTool._imap_criteria("ALL"), ["ALL"])
        self.assertEqual(EmailTool._imap_criteria("unseen"), ["UNSEEN"])
        self.assertEqual(EmailTool._imap_criteria("invoice"), ["TEXT", '"invoice"'])

    def test_token_status(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        result = tool.token_status()
        self.assertTrue(result.ok)
        self.assertIn("available", result.data["vault"])

    def test_exchange_oauth_code_requires_code(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        result = tool.exchange_oauth_code("gmail", "")
        self.assertFalse(result.ok)
        self.assertIn("required", result.message)


if __name__ == "__main__":
    unittest.main()
