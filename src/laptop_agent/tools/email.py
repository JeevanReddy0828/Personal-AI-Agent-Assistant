from __future__ import annotations

import imaplib
import json
import mimetypes
import smtplib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from base64 import b64encode, urlsafe_b64encode
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from laptop_agent.config import AppConfig
from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.token_vault import TokenVault, TokenVaultError
from laptop_agent.tools.base import ToolResult

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class EmailDraft:
    to: str
    subject: str
    body: str
    attachments: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoadedAttachment:
    path: str
    name: str
    content_type: str
    size_bytes: int
    data: bytes


class EmailTool:
    def __init__(self, approval_gate: ApprovalGate, config: AppConfig) -> None:
        self.approval_gate = approval_gate
        self.config = config
        self.token_vault = TokenVault(config.token_vault_path)

    def open_draft(self, draft: EmailDraft) -> ToolResult:
        attachments = self._load_attachments(draft)
        if not attachments.ok:
            return attachments
        if attachments.data["attachments"]:
            return ToolResult.failure(
                "Default mail-client drafts do not support adding attachments automatically.",
                attachments=self._attachment_summaries(attachments.data["attachments"]),
            )
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Open email draft to {draft.to}",
                risk=RiskLevel.HIGH,
                reason="Email drafts may expose private information to an email client.",
                preview=self._preview(draft, attachments.data["attachments"]),
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
        attachments = self._load_attachments(draft)
        if not attachments.ok:
            return attachments

        self.approval_gate.require(
            ApprovalRequest(
                action=f"Send email to {draft.to}",
                risk=RiskLevel.CRITICAL,
                reason="This sends an external message from your account.",
                preview=self._preview(draft, attachments.data["attachments"]),
            )
        )

        message = self._message_with_attachments(draft, attachments.data["attachments"])
        message["From"] = self.config.smtp_from

        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
            server.starttls()
            server.login(self.config.smtp_username, self.config.smtp_password)
            server.send_message(message)
        return ToolResult.success(
            "Email sent.",
            to=draft.to,
            attachments=self._attachment_summaries(attachments.data["attachments"]),
        )

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
            return ToolResult.failure(
                "I'm not connected to your email yet. To read Gmail, add an app password to your .env: "
                "IMAP_HOST=imap.gmail.com, IMAP_USERNAME=<your-gmail>, IMAP_PASSWORD=<16-char app password>. "
                "Generate the app password at https://myaccount.google.com/apppasswords (needs 2-step verification). "
                "Then ask me to check your email again.",
                missing=missing,
                setup="gmail-app-password",
            )

        safe_limit = max(1, min(limit, 25))
        criteria = self._imap_criteria(query)
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Search inbox: {criteria}",
                risk=RiskLevel.MEDIUM,
                reason="Inbox search reads your own email metadata and snippets (read-only, nothing is sent).",
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

    def search_oauth_mail(self, provider: str, query: str = "ALL", limit: int = 10) -> ToolResult:
        normalized = self._normalize_provider(provider)
        if normalized not in {"gmail", "outlook"}:
            return ToolResult.failure("Unknown email OAuth provider.", supported=["gmail", "outlook"])
        safe_limit = max(1, min(limit, 25))
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Read {normalized} mail through OAuth API",
                risk=RiskLevel.HIGH,
                reason="This reads mailbox metadata and snippets using a stored OAuth access token.",
                preview=f"Query: {query}\nLimit: {safe_limit}",
            )
        )
        try:
            token_payload = self.token_vault.load(normalized)
        except TokenVaultError as exc:
            return ToolResult.failure(f"Could not load OAuth token securely: {exc}")
        if not token_payload:
            return ToolResult.failure(f"No stored OAuth token for {normalized}.", hint=f"Run: email oauth exchange {normalized} <authorization-code>")
        access_token = token_payload.get("access_token")
        if not access_token:
            return ToolResult.failure(f"Stored OAuth token for {normalized} has no access token.")

        if normalized == "gmail":
            return self._search_gmail_api(str(access_token), query, safe_limit)
        return self._search_outlook_api(str(access_token), query, safe_limit)

    def create_oauth_draft(self, provider: str, draft: EmailDraft) -> ToolResult:
        normalized = self._normalize_provider(provider)
        if normalized not in {"gmail", "outlook"}:
            return ToolResult.failure("Unknown email OAuth provider.", supported=["gmail", "outlook"])
        attachments = self._load_attachments(draft)
        if not attachments.ok:
            return attachments
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Create {normalized} draft to {draft.to}",
                risk=RiskLevel.HIGH,
                reason="This creates a draft in your mailbox using a stored OAuth access token.",
                preview=self._preview(draft, attachments.data["attachments"]),
            )
        )
        token = self._load_oauth_access_token(normalized)
        if not token.ok:
            return token
        access_token = str(token.data["access_token"])
        if normalized == "gmail":
            response = self._api_post_json(
                self._gmail_draft_url(),
                access_token,
                self._gmail_draft_payload(draft, attachments.data["attachments"]),
            )
        else:
            response = self._api_post_json(
                self._outlook_draft_url(),
                access_token,
                self._outlook_message_payload(draft, attachments.data["attachments"]),
            )
        if not response.ok:
            return response
        return ToolResult.success(
            f"Created {normalized} email draft.",
            provider=normalized,
            response=response.data.get("json", {}),
            attachments=self._attachment_summaries(attachments.data["attachments"]),
        )

    def send_oauth_mail(self, provider: str, draft: EmailDraft) -> ToolResult:
        normalized = self._normalize_provider(provider)
        if normalized not in {"gmail", "outlook"}:
            return ToolResult.failure("Unknown email OAuth provider.", supported=["gmail", "outlook"])
        attachments = self._load_attachments(draft)
        if not attachments.ok:
            return attachments
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Send {normalized} email to {draft.to}",
                risk=RiskLevel.CRITICAL,
                reason="This sends an external email using a stored OAuth access token.",
                preview=self._preview(draft, attachments.data["attachments"]),
            )
        )
        token = self._load_oauth_access_token(normalized)
        if not token.ok:
            return token
        access_token = str(token.data["access_token"])
        if normalized == "gmail":
            response = self._api_post_json(
                self._gmail_send_url(),
                access_token,
                self._gmail_message_payload(draft, attachments.data["attachments"]),
            )
        else:
            response = self._api_post_json(
                self._outlook_send_url(),
                access_token,
                self._outlook_send_payload(draft, attachments.data["attachments"]),
            )
        if not response.ok:
            return response
        return ToolResult.success(
            f"Sent {normalized} email.",
            provider=normalized,
            response=response.data.get("json", {}),
            attachments=self._attachment_summaries(attachments.data["attachments"]),
        )

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

    def token_status(self) -> ToolResult:
        return ToolResult.success("Email token vault status.", vault=self.token_vault.status())

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

    def exchange_oauth_code(self, provider: str, code: str) -> ToolResult:
        normalized = self._normalize_provider(provider)
        if normalized == "gmail":
            missing = [
                name
                for name, value in {
                    "GOOGLE_CLIENT_ID": self.config.google_client_id,
                    "GOOGLE_CLIENT_SECRET": self.config.google_client_secret,
                }.items()
                if not value
            ]
            token_url = "https://oauth2.googleapis.com/token"
            payload = {
                "client_id": self.config.google_client_id,
                "client_secret": self.config.google_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.config.google_redirect_uri,
            }
        elif normalized == "outlook":
            missing = [
                name
                for name, value in {
                    "MICROSOFT_CLIENT_ID": self.config.microsoft_client_id,
                    "MICROSOFT_CLIENT_SECRET": self.config.microsoft_client_secret,
                }.items()
                if not value
            ]
            tenant = self.config.microsoft_tenant or "common"
            token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
            payload = {
                "client_id": self.config.microsoft_client_id,
                "client_secret": self.config.microsoft_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.config.microsoft_redirect_uri,
            }
        else:
            return ToolResult.failure("Unknown email OAuth provider.", supported=["gmail", "outlook"])

        if missing:
            return ToolResult.failure("OAuth token exchange is not configured.", missing=missing)
        if not code.strip():
            return ToolResult.failure("OAuth authorization code is required.")

        self.approval_gate.require(
            ApprovalRequest(
                action=f"Exchange and store OAuth token for {normalized}",
                risk=RiskLevel.CRITICAL,
                reason="This exchanges an authorization code for mailbox tokens and stores them in the local encrypted token vault.",
                preview=f"Provider: {normalized}\nCode: {self._redact(code)}",
            )
        )

        request = urllib.request.Request(
            token_url,
            data=urllib.parse.urlencode(payload).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                token_payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            return ToolResult.failure(f"OAuth token exchange failed: {exc}")
        if not isinstance(token_payload, dict) or "access_token" not in token_payload:
            return ToolResult.failure("OAuth token response did not contain an access token.")

        try:
            info = self.token_vault.store(normalized, token_payload)
        except TokenVaultError as exc:
            return ToolResult.failure(f"Could not store token securely: {exc}")
        return ToolResult.success(
            f"Stored {normalized} OAuth token securely.",
            token={
                "provider": info.provider,
                "token_type": info.token_type,
                "scope": info.scope,
                "expires_in": info.expires_in,
                "has_refresh_token": info.has_refresh_token,
            },
        )

    def refresh_oauth_token(self, provider: str) -> ToolResult:
        normalized = self._normalize_provider(provider)
        if normalized not in {"gmail", "outlook"}:
            return ToolResult.failure("Unknown email OAuth provider.", supported=["gmail", "outlook"])
        missing = self._missing_refresh_config(normalized)
        if missing:
            return ToolResult.failure("OAuth token refresh is not configured.", missing=missing)
        try:
            existing = self.token_vault.load(normalized)
        except TokenVaultError as exc:
            return ToolResult.failure(f"Could not load OAuth token securely: {exc}")
        if not existing:
            return ToolResult.failure(f"No stored OAuth token for {normalized}.", hint=f"Run: email oauth exchange {normalized} <authorization-code>")
        refresh_token = existing.get("refresh_token")
        if not refresh_token:
            return ToolResult.failure(f"Stored OAuth token for {normalized} does not include a refresh token.")

        self.approval_gate.require(
            ApprovalRequest(
                action=f"Refresh OAuth token for {normalized}",
                risk=RiskLevel.CRITICAL,
                reason="This uses a stored refresh token to request a new mailbox access token and updates the encrypted token vault.",
                preview=f"Provider: {normalized}\nStored token: present",
            )
        )

        token_url, payload = self._refresh_request(normalized, str(refresh_token))
        request = urllib.request.Request(
            token_url,
            data=urllib.parse.urlencode(payload).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                refreshed = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            return ToolResult.failure(f"OAuth token refresh failed: {exc}")
        if not isinstance(refreshed, dict) or "access_token" not in refreshed:
            return ToolResult.failure("OAuth refresh response did not contain an access token.")

        merged = self._merge_refreshed_token(existing, refreshed)
        try:
            info = self.token_vault.store(normalized, merged)
        except TokenVaultError as exc:
            return ToolResult.failure(f"Could not store refreshed token securely: {exc}")
        return ToolResult.success(
            f"Refreshed {normalized} OAuth token securely.",
            token={
                "provider": info.provider,
                "token_type": info.token_type,
                "scope": info.scope,
                "expires_in": info.expires_in,
                "has_refresh_token": info.has_refresh_token,
            },
        )

    def forget_oauth_token(self, provider: str) -> ToolResult:
        normalized = self._normalize_provider(provider)
        if normalized not in {"gmail", "outlook"}:
            return ToolResult.failure("Unknown email OAuth provider.", supported=["gmail", "outlook"])
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Forget OAuth token for {normalized}",
                risk=RiskLevel.HIGH,
                reason="This removes locally stored mailbox credentials.",
            )
        )
        removed = self.token_vault.forget(normalized)
        return ToolResult.success("Removed stored OAuth token." if removed else "No stored OAuth token existed.", provider=normalized, removed=removed)

    @staticmethod
    def _preview(draft: EmailDraft, attachments: list[LoadedAttachment] | None = None) -> str:
        preview = f"To: {draft.to}\nSubject: {draft.subject}\n\n{draft.body}"
        summaries = EmailTool._attachment_summaries(attachments or [])
        if summaries:
            names = "\n".join(f"- {item['name']} ({item['size_bytes']} bytes)" for item in summaries)
            preview += f"\n\nAttachments:\n{names}"
        return preview

    @staticmethod
    def _load_attachments(draft: EmailDraft) -> ToolResult:
        loaded: list[LoadedAttachment] = []
        total = 0
        for raw_path in getattr(draft, "attachments", ()) or ():
            target = Path(str(raw_path)).expanduser().resolve()
            if not target.exists() or not target.is_file():
                return ToolResult.failure(f"Attachment does not exist: {target}")
            try:
                size = target.stat().st_size
            except OSError as exc:
                return ToolResult.failure(f"Could not read attachment metadata for {target}: {exc}")
            total += size
            if total > MAX_ATTACHMENT_BYTES:
                return ToolResult.failure(
                    "Email attachments are too large.",
                    max_bytes=MAX_ATTACHMENT_BYTES,
                    total_bytes=total,
                )
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            try:
                data = target.read_bytes()
            except OSError as exc:
                return ToolResult.failure(f"Could not read attachment {target}: {exc}")
            loaded.append(
                LoadedAttachment(
                    path=str(target),
                    name=target.name,
                    content_type=content_type,
                    size_bytes=size,
                    data=data,
                )
            )
        return ToolResult.success(
            "Loaded email attachment(s).",
            attachments=loaded,
            total_bytes=total,
        )

    @staticmethod
    def _attachment_summaries(attachments: list[LoadedAttachment]) -> list[dict[str, object]]:
        return [
            {
                "path": item.path,
                "name": item.name,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
            }
            for item in attachments
        ]

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

    def _search_gmail_api(self, access_token: str, query: str, limit: int) -> ToolResult:
        list_url = self._gmail_list_url(query, limit)
        list_payload = self._api_get_json(list_url, access_token)
        if not list_payload.ok:
            return list_payload
        messages = list_payload.data.get("json", {}).get("messages", [])
        summaries = []
        for item in messages[:limit]:
            message_id = item.get("id")
            if not message_id:
                continue
            detail = self._api_get_json(self._gmail_get_url(str(message_id)), access_token)
            if detail.ok:
                summaries.append(self._gmail_summary(detail.data.get("json", {})))
        return ToolResult.success(f"Found {len(summaries)} Gmail message(s).", provider="gmail", query=query, messages=summaries)

    def _search_outlook_api(self, access_token: str, query: str, limit: int) -> ToolResult:
        payload = self._api_get_json(self._outlook_messages_url(query, limit), access_token, {"Prefer": 'outlook.body-content-type="text"'})
        if not payload.ok:
            return payload
        messages = [self._outlook_summary(item) for item in payload.data.get("json", {}).get("value", [])]
        return ToolResult.success(f"Found {len(messages)} Outlook message(s).", provider="outlook", query=query, messages=messages)

    def _load_oauth_access_token(self, provider: str) -> ToolResult:
        try:
            token_payload = self.token_vault.load(provider)
        except TokenVaultError as exc:
            return ToolResult.failure(f"Could not load OAuth token securely: {exc}")
        if not token_payload:
            return ToolResult.failure(f"No stored OAuth token for {provider}.", hint=f"Run: email oauth exchange {provider} <authorization-code>")
        access_token = token_payload.get("access_token")
        if not access_token:
            return ToolResult.failure(f"Stored OAuth token for {provider} has no access token.")
        return ToolResult.success("Loaded OAuth access token.", access_token=access_token)

    @staticmethod
    def _api_get_json(url: str, access_token: str, extra_headers: dict[str, str] | None = None) -> ToolResult:
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return ToolResult.success("API request succeeded.", json=json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            return ToolResult.failure(f"API request failed with HTTP {exc.code}.", detail=detail)
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            return ToolResult.failure(f"API request failed: {exc}")

    @staticmethod
    def _api_post_json(url: str, access_token: str, payload: dict[str, object]) -> ToolResult:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                parsed = json.loads(raw) if raw.strip() else {}
                return ToolResult.success("API request succeeded.", json=parsed, status=getattr(response, "status", None))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            return ToolResult.failure(f"API request failed with HTTP {exc.code}.", detail=detail)
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            return ToolResult.failure(f"API request failed: {exc}")

    @staticmethod
    def _gmail_list_url(query: str, limit: int) -> str:
        params = {"maxResults": str(max(1, min(limit, 25)))}
        cleaned = query.strip()
        if cleaned and cleaned.upper() != "ALL":
            params["q"] = "is:unread" if cleaned.upper() == "UNSEEN" else cleaned
        return "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urllib.parse.urlencode(params)

    @staticmethod
    def _gmail_get_url(message_id: str) -> str:
        params = [
            ("format", "metadata"),
            ("metadataHeaders", "From"),
            ("metadataHeaders", "To"),
            ("metadataHeaders", "Subject"),
            ("metadataHeaders", "Date"),
        ]
        return f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{urllib.parse.quote(message_id)}?" + urllib.parse.urlencode(params)

    @staticmethod
    def _gmail_draft_url() -> str:
        return "https://gmail.googleapis.com/gmail/v1/users/me/drafts"

    @staticmethod
    def _gmail_send_url() -> str:
        return "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

    @staticmethod
    def _gmail_draft_payload(
        draft: EmailDraft,
        attachments: list[LoadedAttachment] | None = None,
    ) -> dict[str, object]:
        return {"message": EmailTool._gmail_message_payload(draft, attachments)}

    @staticmethod
    def _gmail_message_payload(
        draft: EmailDraft,
        attachments: list[LoadedAttachment] | None = None,
    ) -> dict[str, object]:
        raw = EmailTool._raw_rfc2822_message(draft, attachments)
        encoded = urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return {"raw": encoded}

    @staticmethod
    def _raw_rfc2822_message(
        draft: EmailDraft,
        attachments: list[LoadedAttachment] | None = None,
    ) -> bytes:
        return EmailTool._message_with_attachments(draft, attachments or []).as_bytes()

    @staticmethod
    def _message_with_attachments(
        draft: EmailDraft,
        attachments: list[LoadedAttachment],
    ) -> EmailMessage:
        message = EmailMessage()
        message["To"] = draft.to
        message["Subject"] = draft.subject
        message.set_content(draft.body)
        for item in attachments:
            maintype, _, subtype = item.content_type.partition("/")
            if not subtype:
                maintype, subtype = "application", "octet-stream"
            message.add_attachment(
                item.data,
                maintype=maintype,
                subtype=subtype,
                filename=item.name,
            )
        return message

    @staticmethod
    def _outlook_messages_url(query: str, limit: int) -> str:
        params = {
            "$top": str(max(1, min(limit, 25))),
            "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,isRead",
            "$orderby": "receivedDateTime desc",
        }
        cleaned = query.strip()
        if cleaned.upper() == "UNSEEN":
            params["$filter"] = "isRead eq false"
        elif cleaned and cleaned.upper() != "ALL":
            params["$search"] = f'"{cleaned.replace(chr(34), chr(92) + chr(34))}"'
        return "https://graph.microsoft.com/v1.0/me/messages?" + urllib.parse.urlencode(params)

    @staticmethod
    def _outlook_draft_url() -> str:
        return "https://graph.microsoft.com/v1.0/me/messages"

    @staticmethod
    def _outlook_send_url() -> str:
        return "https://graph.microsoft.com/v1.0/me/sendMail"

    @staticmethod
    def _outlook_message_payload(
        draft: EmailDraft,
        attachments: list[LoadedAttachment] | None = None,
    ) -> dict[str, object]:
        message: dict[str, object] = {
            "subject": draft.subject,
            "body": {"contentType": "Text", "content": draft.body},
            "toRecipients": [{"emailAddress": {"address": draft.to}}],
        }
        if attachments:
            message["attachments"] = [EmailTool._outlook_attachment_payload(item) for item in attachments]
        return message

    @staticmethod
    def _outlook_send_payload(
        draft: EmailDraft,
        attachments: list[LoadedAttachment] | None = None,
    ) -> dict[str, object]:
        return {"message": EmailTool._outlook_message_payload(draft, attachments), "saveToSentItems": True}

    @staticmethod
    def _outlook_attachment_payload(attachment: LoadedAttachment) -> dict[str, object]:
        return {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": attachment.name,
            "contentType": attachment.content_type,
            "contentBytes": b64encode(attachment.data).decode("ascii"),
        }

    @staticmethod
    def _gmail_summary(message: dict[str, object]) -> dict[str, str]:
        payload = message.get("payload", {})
        raw_headers = payload.get("headers", []) if isinstance(payload, dict) else []
        headers = {
            str(item.get("name", "")).lower(): str(item.get("value", ""))
            for item in raw_headers
            if isinstance(item, dict)
        }
        return {
            "id": str(message.get("id", "")),
            "thread_id": str(message.get("threadId", "")),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "subject": headers.get("subject", ""),
            "snippet": str(message.get("snippet", ""))[:500],
        }

    @staticmethod
    def _outlook_summary(message: dict[str, object]) -> dict[str, str]:
        sender = message.get("from") or {}
        email_address = sender.get("emailAddress", {}) if isinstance(sender, dict) else {}
        return {
            "id": str(message.get("id", "")),
            "from": str(email_address.get("address", "")) if isinstance(email_address, dict) else "",
            "date": str(message.get("receivedDateTime", "")),
            "subject": str(message.get("subject", "")),
            "snippet": str(message.get("bodyPreview", ""))[:500],
            "is_read": str(message.get("isRead", "")),
        }

    def _missing_refresh_config(self, provider: str) -> list[str]:
        if provider == "gmail":
            return [
                name
                for name, value in {
                    "GOOGLE_CLIENT_ID": self.config.google_client_id,
                    "GOOGLE_CLIENT_SECRET": self.config.google_client_secret,
                }.items()
                if not value
            ]
        return [
            name
            for name, value in {
                "MICROSOFT_CLIENT_ID": self.config.microsoft_client_id,
                "MICROSOFT_CLIENT_SECRET": self.config.microsoft_client_secret,
            }.items()
            if not value
        ]

    def _refresh_request(self, provider: str, refresh_token: str) -> tuple[str, dict[str, str | None]]:
        if provider == "gmail":
            return (
                "https://oauth2.googleapis.com/token",
                {
                    "client_id": self.config.google_client_id,
                    "client_secret": self.config.google_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
        tenant = self.config.microsoft_tenant or "common"
        return (
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            {
                "client_id": self.config.microsoft_client_id,
                "client_secret": self.config.microsoft_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )

    @staticmethod
    def _merge_refreshed_token(existing: dict[str, object], refreshed: dict[str, object]) -> dict[str, object]:
        merged = dict(existing)
        merged.update(refreshed)
        if "refresh_token" not in refreshed and "refresh_token" in existing:
            merged["refresh_token"] = existing["refresh_token"]
        return merged

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

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        normalized = provider.lower().strip()
        if normalized in {"gmail", "google"}:
            return "gmail"
        if normalized in {"outlook", "microsoft", "office", "office365"}:
            return "outlook"
        return normalized
