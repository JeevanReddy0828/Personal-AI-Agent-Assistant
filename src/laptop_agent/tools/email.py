from __future__ import annotations

import imaplib
import smtplib
import urllib.parse
import webbrowser
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from laptop_agent.config import AppConfig
from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult


@dataclass(frozen=True)
class EmailDraft:
    to: str
    subject: str
    body: str


class EmailTool:
    def __init__(self, approval_gate: ApprovalGate, config: AppConfig) -> None:
        self.approval_gate = approval_gate
        self.config = config

    def open_draft(self, draft: EmailDraft) -> ToolResult:
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Open email draft to {draft.to}",
                risk=RiskLevel.HIGH,
                reason="Email drafts may expose private information to an email client.",
                preview=self._preview(draft),
            )
        )
        url = "mailto:" + urllib.parse.quote(draft.to)
        query = urllib.parse.urlencode({"subject": draft.subject, "body": draft.body})
        webbrowser.open(f"{url}?{query}")
        return ToolResult.success("Opened email draft in default mail client.", to=draft.to)

    def send_smtp(self, draft: EmailDraft) -> ToolResult:
        missing = [
            name
            for name, value in {
                "SMTP_HOST": self.config.smtp_host,
                "SMTP_USERNAME": self.config.smtp_username,
                "SMTP_PASSWORD": self.config.smtp_password,
                "SMTP_FROM": self.config.smtp_from,
            }.items()
            if not value
        ]
        if missing:
            return ToolResult.failure("SMTP is not configured.", missing=missing)

        self.approval_gate.require(
            ApprovalRequest(
                action=f"Send email to {draft.to}",
                risk=RiskLevel.CRITICAL,
                reason="This sends an external message from your account.",
                preview=self._preview(draft),
            )
        )

        message = EmailMessage()
        message["From"] = self.config.smtp_from
        message["To"] = draft.to
        message["Subject"] = draft.subject
        message.set_content(draft.body)

        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
            server.starttls()
            server.login(self.config.smtp_username, self.config.smtp_password)
            server.send_message(message)
        return ToolResult.success("Email sent.", to=draft.to)

    def search_inbox(self, query: str = "ALL", limit: int = 10) -> ToolResult:
        missing = [
            name
            for name, value in {
                "IMAP_HOST": self.config.imap_host,
                "IMAP_USERNAME": self.config.imap_username,
                "IMAP_PASSWORD": self.config.imap_password,
            }.items()
            if not value
        ]
        if missing:
            return ToolResult.failure("IMAP inbox search is not configured.", missing=missing)

        safe_limit = max(1, min(limit, 25))
        criteria = self._imap_criteria(query)
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Search inbox: {criteria}",
                risk=RiskLevel.HIGH,
                reason="Inbox search reads private email metadata and message snippets.",
                preview=f"Mailbox: {self.config.imap_mailbox}\nLimit: {safe_limit}",
            )
        )

        with imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port) as client:
            client.login(self.config.imap_username, self.config.imap_password)
            client.select(self.config.imap_mailbox, readonly=True)
            status, payload = client.search(None, *criteria)
            if status != "OK":
                return ToolResult.failure("Inbox search failed.", status=status)
            ids = payload[0].split()[-safe_limit:]
            messages = []
            for message_id in reversed(ids):
                fetch_status, fetch_payload = client.fetch(message_id, "(BODY.PEEK[])")
                if fetch_status != "OK":
                    continue
                for item in fetch_payload:
                    if not isinstance(item, tuple):
                        continue
                    parsed = BytesParser(policy=policy.default).parsebytes(item[1])
                    messages.append(self._message_summary(parsed, message_id.decode("ascii", errors="replace")))
                    break
        return ToolResult.success(f"Found {len(messages)} email message(s).", query=query, messages=messages)

    def oauth_status(self) -> ToolResult:
        providers = {
            "gmail": {
                "configured": bool(self.config.google_client_id),
                "client_id": self._redact(self.config.google_client_id),
                "redirect_uri": self.config.google_redirect_uri,
            },
            "outlook": {
                "configured": bool(self.config.microsoft_client_id),
                "client_id": self._redact(self.config.microsoft_client_id),
                "tenant": self.config.microsoft_tenant,
                "redirect_uri": self.config.microsoft_redirect_uri,
            },
        }
        return ToolResult.success("Email OAuth provider status.", providers=providers)

    def oauth_authorization_url(self, provider: str) -> ToolResult:
        normalized = provider.lower().strip()
        if normalized in {"gmail", "google"}:
            if not self.config.google_client_id:
                return ToolResult.failure("Gmail OAuth is not configured.", missing=["GOOGLE_CLIENT_ID"])
            params = {
                "client_id": self.config.google_client_id,
                "redirect_uri": self.config.google_redirect_uri,
                "response_type": "code",
                "scope": "openid email https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send",
                "access_type": "offline",
                "prompt": "consent",
                "state": "laptop-agent",
            }
            url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
            return ToolResult.success("Created Gmail OAuth authorization URL.", provider="gmail", url=url)

        if normalized in {"outlook", "microsoft", "office365", "office"}:
            if not self.config.microsoft_client_id:
                return ToolResult.failure("Outlook OAuth is not configured.", missing=["MICROSOFT_CLIENT_ID"])
            tenant = self.config.microsoft_tenant or "common"
            params = {
                "client_id": self.config.microsoft_client_id,
                "redirect_uri": self.config.microsoft_redirect_uri,
                "response_type": "code",
                "scope": "openid email offline_access Mail.Read Mail.Send",
                "response_mode": "query",
                "state": "laptop-agent",
            }
            url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)
            return ToolResult.success("Created Outlook OAuth authorization URL.", provider="outlook", url=url)

        return ToolResult.failure("Unknown email OAuth provider.", supported=["gmail", "outlook"])

    @staticmethod
    def _preview(draft: EmailDraft) -> str:
        return f"To: {draft.to}\nSubject: {draft.subject}\n\n{draft.body}"

    @staticmethod
    def _imap_criteria(query: str) -> list[str]:
        cleaned = query.strip()
        if not cleaned or cleaned.upper() == "ALL":
            return ["ALL"]
        if cleaned.upper() in {"UNSEEN", "SEEN"}:
            return [cleaned.upper()]
        escaped = cleaned.replace("\\", "\\\\").replace('"', '\\"')
        return ["TEXT", f'"{escaped}"']

    @staticmethod
    def _message_summary(message: EmailMessage, message_id: str) -> dict[str, str]:
        body = EmailTool._plain_text_body(message)
        return {
            "id": message_id,
            "from": str(message.get("from", "")),
            "to": str(message.get("to", "")),
            "date": str(message.get("date", "")),
            "subject": str(message.get("subject", "")),
            "snippet": " ".join(body.split())[:500],
        }

    @staticmethod
    def _plain_text_body(message: EmailMessage) -> str:
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    try:
                        return part.get_content()
                    except (LookupError, UnicodeDecodeError):
                        return ""
            return ""
        if message.get_content_type() == "text/plain":
            try:
                return message.get_content()
            except (LookupError, UnicodeDecodeError):
                return ""
        return ""

    @staticmethod
    def _redact(value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 8:
            return "****"
        return value[:4] + "..." + value[-4:]
