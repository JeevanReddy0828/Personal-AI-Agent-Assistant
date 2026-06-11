from __future__ import annotations

import smtplib
import urllib.parse
import webbrowser
from dataclasses import dataclass
from email.message import EmailMessage

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

    @staticmethod
    def _preview(draft: EmailDraft) -> str:
        return f"To: {draft.to}\nSubject: {draft.subject}\n\n{draft.body}"
