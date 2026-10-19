"""`message whatsapp|telegram [to <who>] :: <text>` - a message written and opened, sent by you.

"send a WhatsApp to mom saying I'm running late" had no tool: the model router once turned
"text john" into an email (docs/design/routing.md). There is no API here that may send on the
user's behalf, so the message is put into WhatsApp's or Telegram's own share link, opened after
an approval card that shows it, and the user picks the chat and presses send. A phone number
given in the words goes straight to that chat; a name is shown so the user can pick it.
"""
from __future__ import annotations

import re
import urllib.parse
import webbrowser

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

APPS = {"whatsapp": "WhatsApp", "telegram": "Telegram"}
MAX_TEXT = 2000


def share_link(app: str, text: str, phone: str = "") -> str:
    if app == "whatsapp":
        return f"https://wa.me/{phone}?text={urllib.parse.quote(text)}"
    return "https://t.me/share/url?url=%20&text=" + urllib.parse.quote(text)


class MessagingTool:
    def __init__(self, approval_gate: ApprovalGate) -> None:
        self._gate = approval_gate

    def prepare(self, app: str, recipient: str, text: str) -> ToolResult:
        app = app.strip().lower()
        if app not in APPS:
            return ToolResult.failure("I can open a message in WhatsApp or Telegram.")
        text = text.strip()
        if not text:
            return ToolResult.failure("What should the message say?")
        if len(text) > MAX_TEXT:
            return ToolResult.failure(f"That message is over {MAX_TEXT:,} characters; shorten it first.")
        recipient = recipient.strip()
        digits = re.sub(r"[^\d]", "", recipient) if re.fullmatch(r"\+?[\d\s().-]{7,20}", recipient) else ""
        phone = digits if app == "whatsapp" else ""
        name = APPS[app]
        self._gate.require(ApprovalRequest(
            action=f"Open {name} with a message ready" + (f" for {recipient}" if recipient else ""),
            risk=RiskLevel.HIGH,
            reason=f"The text goes to {name} in the link; nothing is sent until you press send there.",
            preview=text,
        ))
        link = share_link(app, text, phone)
        if not webbrowser.open(link):
            return ToolResult.failure(f"Couldn't open {name}. Here is the link to open yourself: {link}", link=link)
        if phone:
            return ToolResult.success(f"Opened {name} at {recipient}'s chat with your message ready; press send.", link=link)
        chat = f"{recipient}'s chat" if recipient else "the chat"
        return ToolResult.success(f"Opened {name} with your message ready. Choose {chat}, then press send.", link=link)
