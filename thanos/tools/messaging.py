"""Messaging: iMessage (sends) and WhatsApp (opens the chat with the text filled in).

Both look the contact up in the Contacts app and ALWAYS ask the user (services.confirm)
showing the exact person, number and text before doing anything.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from urllib.parse import quote

from thanos import services
from thanos.tools._osa import OSAError, q, run_applescript
from thanos.tools.base import ToolResult
from thanos.tools.registry import tool

COUNTRY_CALLING_CODES = {"IN": "91", "US": "1", "CA": "1", "GB": "44", "AE": "971", "SG": "65",
                         "AU": "61", "DE": "49", "FR": "33"}


@dataclass
class Contact:
    name: str
    phone: str
    email: str


def find_contacts(name: str) -> list[Contact]:
    script = f'''tell application "Contacts"
set out to ""
repeat with p in (every person whose name contains {q(name)})
set ph to ""
if (count of phones of p) > 0 then set ph to value of first phone of p
set em to ""
if (count of emails of p) > 0 then set em to value of first email of p
set out to out & (name of p) & "|" & ph & "|" & em & linefeed
end repeat
return out
end tell'''
    out = run_applescript(script, timeout=30)
    contacts = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) == 3 and parts[0].strip():
            contacts.append(Contact(*(p.strip() for p in parts)))
    return contacts


def _pick_contact(name: str) -> Contact | ToolResult:
    try:
        matches = find_contacts(name)
    except OSAError as e:
        return ToolResult.fail(str(e))
    if not matches:
        return ToolResult.fail(f"No contact named {name} in Contacts.")
    exact = [c for c in matches if c.name.lower() == name.strip().lower()]
    if len(exact) == 1:
        return exact[0]
    if len(matches) > 1:
        names = ", ".join(c.name for c in matches[:5])
        return ToolResult.fail(f"Several contacts match {name}: {names}. Ask which one.")
    return matches[0]


@tool(
    category="messaging",
    direct=True,
    examples=["text mom that I'll be late", "send an iMessage to Ravi saying on my way",
              "message Priya happy birthday"],
)
def send_imessage(contact: str, text: str) -> ToolResult:
    """Send an iMessage/SMS to a contact (asks the user to confirm first).

    Args:
        contact: Contact name as saved in Contacts, e.g. "Mom" or "Ravi Kumar".
        text: The exact message, e.g. "I'll be late".
    """
    c = _pick_contact(contact)
    if isinstance(c, ToolResult):
        return c
    handle = c.phone or c.email
    if not handle:
        return ToolResult.fail(f"{c.name} has no phone number or email in Contacts.")
    if not services.confirm(f'Send iMessage to {c.name} ({handle}): "{text}"?'):
        return ToolResult.fail("Not sent: the user didn't confirm.")
    script = f'''tell application "Messages"
set svc to 1st account whose service type = iMessage
send {q(text)} to participant {q(handle)} of svc
end tell'''
    try:
        run_applescript(script, timeout=30)
    except OSAError as e:
        return ToolResult.fail(f"Couldn't send: {e}")
    return ToolResult.success(f"Sent to {c.name}.")


def whatsapp_number(phone: str, country: str) -> str | None:
    digits = re.sub(r"\D", "", phone)
    if phone.strip().startswith("+") or phone.strip().startswith("00"):
        return digits.lstrip("0") or None
    if len(digits) == 10 and country in COUNTRY_CALLING_CODES:
        return COUNTRY_CALLING_CODES[country] + digits
    if len(digits) == 11 and digits.startswith("0") and country in COUNTRY_CALLING_CODES:
        return COUNTRY_CALLING_CODES[country] + digits[1:]
    return digits if len(digits) > 10 else None


@tool(
    category="messaging",
    direct=True,
    examples=["whatsapp Ravi that the meeting moved", "send a whatsapp message to mom",
              "message Priya on whatsapp"],
)
def send_whatsapp(contact: str, text: str) -> ToolResult:
    """Open a WhatsApp chat with a contact with the message typed in; the user presses Send.
    Asks the user to confirm first.

    Args:
        contact: Contact name as saved in Contacts, e.g. "Ravi".
        text: The exact message, e.g. "Meeting moved to 4".
    """
    c = _pick_contact(contact)
    if isinstance(c, ToolResult):
        return c
    if not c.phone:
        return ToolResult.fail(f"{c.name} has no phone number in Contacts.")
    from thanos.tools.info import home_location

    try:
        country = (home_location().get("country") or "").upper()
    except Exception:
        country = ""
    number = whatsapp_number(c.phone, country)
    if not number:
        return ToolResult.fail(f"{c.name}'s number {c.phone} has no country code; add it in Contacts.")
    if not services.confirm(f'Open WhatsApp to {c.name} (+{number}) with: "{text}"?'):
        return ToolResult.fail("Cancelled: the user didn't confirm.")
    subprocess.run(["open", f"whatsapp://send?phone={number}&text={quote(text)}"], check=False)
    return ToolResult.success(f"WhatsApp is open with your message to {c.name}. Press Send.")
