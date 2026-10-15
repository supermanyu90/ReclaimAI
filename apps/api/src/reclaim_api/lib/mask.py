"""Display-safe masking.

Use these whenever an identifier is shown to anyone, including the owner, and in
every Telegram response and passport export. Raw values never leave the API.
"""

import re

_DOTS = "••••"


def mask_reference(raw: str) -> str:
    """Account, policy, folio, UAN or any institution reference: last 4 only."""
    raw = raw.strip()
    if len(raw) <= 4:
        return _DOTS
    return _DOTS + raw[-4:]


def mask_aadhaar(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 12:
        return "XXXX XXXX ????"
    return f"XXXX XXXX {digits[-4:]}"


def mask_pan(raw: str) -> str:
    upper = raw.strip().upper()
    if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", upper):
        return "##########"
    return upper[:5] + "####" + upper[-1]


def mask_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 5:
        return "•••••"
    prefix = "+91 " if raw.strip().startswith("+91") else ""
    return f"{prefix}••••• {digits[-5:]}"
