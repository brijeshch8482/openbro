"""Gmail skill - read inbox, send email.

Uses Google OAuth via stored token. Requires:
- skills.gmail.client_id, client_secret (Google OAuth app)
- skills.gmail.refresh_token (obtained via one-time auth flow)

For now this skill works in 'app password' mode using IMAP/SMTP for simplicity.
Required config:
- skills.gmail.email
- skills.gmail.app_password  (https://myaccount.google.com/apppasswords)
"""

import email
import imaplib
import smtplib
from email.message import EmailMessage

from openbro.skills.base import BaseSkill
from openbro.tools.base import BaseTool, RiskLevel


class GmailTool(BaseTool):
    name = "gmail"
    description = (
        "Read recent inbox messages or send email via Gmail. "
        "Requires email + app password in config (skills.gmail)."
    )
    risk = RiskLevel.MODERATE

    def __init__(self, email_addr: str | None = None, app_password: str | None = None):
        self.email = email_addr
        self.app_password = app_password

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["inbox", "send"]},
                    "limit": {
                        "type": "integer",
                        "description": "How many recent messages to fetch (default 5)",
                    },
                    "to": {"type": "string", "description": "Recipient (for send)"},
                    "subject": {"type": "string", "description": "Subject (for send)"},
                    "body": {"type": "string", "description": "Email body (for send)"},
                },
                "required": ["action"],
            },
        }

    def run(self, **kwargs) -> str:
        if not self.email or not self.app_password:
            return (
                "Gmail not configured. Set skills.gmail.email and skills.gmail.app_password "
                "(create one at https://myaccount.google.com/apppasswords)."
            )
        action = kwargs.get("action")
        if action == "inbox":
            return self._inbox(kwargs.get("limit", 5))
        if action == "send":
            return self._send(
                kwargs.get("to", ""),
                kwargs.get("subject", ""),
                kwargs.get("body", ""),
            )
        return f"Unknown action: {action}"

    def _inbox(self, limit: int) -> str:
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
                imap.login(self.email, self.app_password)
                imap.select("INBOX")
                status, data = imap.search(None, "ALL")
                if status != "OK":
                    return "Could not search inbox."
                ids = data[0].split()
                recent = ids[-limit:][::-1]
                lines = []
                for msg_id in recent:
                    status, msg_data = imap.fetch(msg_id, "(RFC822.HEADER)")
                    if status != "OK":
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    subj = msg.get("Subject", "(no subject)")
                    sender = msg.get("From", "(unknown)")
                    lines.append(f"- {sender} | {subj}")
                return "\n".join(lines) if lines else "Inbox empty."
        except Exception as e:
            return f"Gmail inbox failed: {e}"

    def _send(self, to: str, subject: str, body: str) -> str:
        if not to or not subject:
            return "to and subject required."
        try:
            msg = EmailMessage()
            msg["From"] = self.email
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(body or "")
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(self.email, self.app_password)
                smtp.send_message(msg)
            return f"Sent email to {to}."
        except Exception as e:
            return f"Gmail send failed: {e}"


class GmailSkill(BaseSkill):
    name = "gmail"
    description = "Read recent emails and send mail via Gmail (app password)."
    version = "0.1.0"
    author = "openbro"
    config_keys = ["skills.gmail.email", "skills.gmail.app_password"]

    def tools(self) -> list[BaseTool]:
        return [
            GmailTool(
                email_addr=self._get_nested(self.config, "skills.gmail.email"),
                app_password=self._get_nested(self.config, "skills.gmail.app_password"),
            )
        ]
