from __future__ import annotations

import unittest
from pathlib import Path

from laptop_agent.config import AppConfig
from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.email import EmailDraft, EmailTool


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
            llm_smart_model=None,
            llm_ultra_model=None,
            llm_vision_model=None,
            llm_api_key=None,
            obsidian_vault=None,
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

    def test_oauth_mail_search_without_token_fails_cleanly(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        result = tool.search_oauth_mail("gmail", "invoice")
        self.assertFalse(result.ok)
        self.assertIn("OAuth token", result.message)

    def test_exchange_oauth_code_requires_code(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        result = tool.exchange_oauth_code("gmail", "")
        self.assertFalse(result.ok)
        self.assertIn("required", result.message)

    def test_refresh_oauth_token_without_stored_token_fails_cleanly(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        result = tool.refresh_oauth_token("gmail")
        self.assertFalse(result.ok)
        self.assertIn("OAuth token", result.message)

    def test_refresh_request_urls(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        gmail_url, gmail_payload = tool._refresh_request("gmail", "refresh-token")
        outlook_url, outlook_payload = tool._refresh_request("outlook", "refresh-token")
        self.assertEqual(gmail_url, "https://oauth2.googleapis.com/token")
        self.assertEqual(gmail_payload["grant_type"], "refresh_token")
        self.assertIn("login.microsoftonline.com/common", outlook_url)
        self.assertEqual(outlook_payload["refresh_token"], "refresh-token")

    def test_merge_refreshed_token_preserves_refresh_token(self) -> None:
        merged = EmailTool._merge_refreshed_token(
            {"access_token": "old", "refresh_token": "refresh", "scope": "Mail.Read"},
            {"access_token": "new", "expires_in": 3600},
        )
        self.assertEqual(merged["access_token"], "new")
        self.assertEqual(merged["refresh_token"], "refresh")
        self.assertEqual(merged["expires_in"], 3600)

    def test_oauth_draft_without_token_fails_cleanly(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        result = tool.create_oauth_draft("gmail", EmailDraft("ada@example.com", "Hi", "Hello"))
        self.assertFalse(result.ok)
        self.assertIn("OAuth token", result.message)

    def test_oauth_send_without_token_fails_cleanly(self) -> None:
        tool = EmailTool(ApprovalGate(lambda request: True), self.build_config())
        result = tool.send_oauth_mail("outlook", EmailDraft("ada@example.com", "Hi", "Hello"))
        self.assertFalse(result.ok)
        self.assertIn("OAuth token", result.message)

    def test_gmail_message_payload_is_base64url_raw_message(self) -> None:
        payload = EmailTool._gmail_message_payload(EmailDraft("ada@example.com", "Hi", "Hello"))
        self.assertIn("raw", payload)
        self.assertNotIn("=", str(payload["raw"]))

    def test_gmail_draft_payload_wraps_message(self) -> None:
        payload = EmailTool._gmail_draft_payload(EmailDraft("ada@example.com", "Hi", "Hello"))
        self.assertIn("message", payload)
        self.assertIn("raw", payload["message"])

    def test_outlook_payloads(self) -> None:
        draft = EmailDraft("ada@example.com", "Hi", "Hello")
        message = EmailTool._outlook_message_payload(draft)
        send = EmailTool._outlook_send_payload(draft)
        self.assertEqual(message["subject"], "Hi")
        self.assertEqual(message["toRecipients"][0]["emailAddress"]["address"], "ada@example.com")
        self.assertTrue(send["saveToSentItems"])
        self.assertIn("message", send)

    def test_gmail_api_urls(self) -> None:
        list_url = EmailTool._gmail_list_url("invoice", 99)
        get_url = EmailTool._gmail_get_url("abc/123")
        self.assertIn("maxResults=25", list_url)
        self.assertIn("q=invoice", list_url)
        self.assertIn("abc/123", get_url)
        self.assertIn("metadataHeaders=Subject", get_url)

    def test_outlook_api_urls(self) -> None:
        unread_url = EmailTool._outlook_messages_url("UNSEEN", 5)
        search_url = EmailTool._outlook_messages_url("invoice", 5)
        self.assertIn("%24filter=isRead+eq+false", unread_url)
        self.assertIn("%24search=", search_url)

    def test_gmail_summary(self) -> None:
        summary = EmailTool._gmail_summary(
            {
                "id": "m1",
                "threadId": "t1",
                "snippet": "hello",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "a@example.com"},
                        {"name": "Subject", "value": "Hi"},
                    ]
                },
            }
        )
        self.assertEqual(summary["from"], "a@example.com")
        self.assertEqual(summary["subject"], "Hi")

    def test_outlook_summary(self) -> None:
        summary = EmailTool._outlook_summary(
            {
                "id": "m1",
                "from": {"emailAddress": {"address": "a@example.com"}},
                "subject": "Hi",
                "bodyPreview": "hello",
                "isRead": False,
            }
        )
        self.assertEqual(summary["from"], "a@example.com")
        self.assertEqual(summary["is_read"], "False")


if __name__ == "__main__":
    unittest.main()
