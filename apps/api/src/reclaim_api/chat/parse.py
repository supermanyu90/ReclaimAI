"""Deterministic parsing of verbatim phrases into typed values.

The model only points at phrases in the owner's message; this module turns those
phrases into amounts, terms and dates, and decides when something is ambiguous.
A value the model invents (a phrase not present in the message) is discarded.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

# --- verbatim check ------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def appears_in(phrase: str | None, message: str) -> bool:
    return bool(phrase) and _norm(phrase) in _norm(message)


_CURRENCY_PREFIX = re.compile(r"^\s*(₹|rs\.?|inr|s\$|sgd)\s*", re.IGNORECASE)


def locate(phrase: str | None, message: str) -> str | None:
    """The message's own wording for a phrase the model returned, or None if absent.

    Models sometimes decorate a phrase (adding "₹" to "2 lakh"); the decorated form is
    not in the message, so the undecorated form is looked up instead. What is returned
    is always text copied from the message itself."""
    if not phrase:
        return None
    for candidate in (phrase, _CURRENCY_PREFIX.sub("", phrase)):
        if candidate and _norm(candidate) in _norm(message):
            start = _norm(message).index(_norm(candidate))
            # Map back to original text (normalisation only collapses whitespace/case).
            return re.sub(r"\s+", " ", message).strip()[start : start + len(_norm(candidate))]
    return None


# --- redaction -----------------------------------------------------------------

_LONG_DIGITS = re.compile(r"\d[\d\s-]{7,}\d")
UPDATE_WORDS = re.compile(r"\b(actually|is now|now is|should be|correction|corrected|updated?|changed)\b", re.IGNORECASE)
CLOSING_WORDS = re.compile(r"\b(closed|matured|redeemed|surrendered|sold)\b", re.IGNORECASE)
CREDENTIAL_WORDS = re.compile(r"\b(otp|pin|password|passcode|cvv|upi pin|net ?banking login)\b", re.IGNORECASE)


def redact_long_numbers(text: str) -> tuple[str, bool]:
    """Replace runs of 9+ digits (account, policy, Aadhaar numbers) with a masked tail."""
    redacted = False

    def _mask(match: re.Match[str]) -> str:
        nonlocal redacted
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 9:
            return match.group(0)
        redacted = True
        return "••••" + digits[-4:]

    return _LONG_DIGITS.sub(_mask, text), redacted


# --- amounts -------------------------------------------------------------------

_MULTIPLIERS = {
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "l": 100_000,
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000,
    "k": 1_000, "thousand": 1_000, "million": 1_000_000, "mn": 1_000_000,
}
_INR = r"₹|rs\.?|inr|rupees?"
_SGD = r"s\$|sgd|singapore dollars?"
_AMOUNT = re.compile(
    rf"(?P<pre>{_INR}|{_SGD})?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<mult>lakhs?|lacs?|crores?|cr|k|thousand|million|mn|l)?\b\s*(?P<post>{_INR}|{_SGD})?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Amount:
    minor: int
    currency: str | None  # None: no currency marker in the phrase


def parse_amount(phrase: str) -> Amount | None:
    """'₹5 lakh' → 50000000 INR minor units. Exactly one amount, else None."""
    matches = [m for m in _AMOUNT.finditer(phrase) if m.group("pre") or m.group("mult") or m.group("post") or "," in m.group("num")]
    if len(matches) != 1:
        return None
    m = matches[0]
    try:
        value = Decimal(m.group("num").replace(",", ""))
    except InvalidOperation:
        return None
    mult = (m.group("mult") or "").lower()
    value *= _MULTIPLIERS.get(mult, 1)
    marker = (m.group("pre") or m.group("post") or "").lower()
    if re.fullmatch(_SGD, marker, re.IGNORECASE):
        currency = "SGD"
    elif marker or mult in ("lakh", "lakhs", "lac", "lacs", "crore", "crores", "cr", "l"):
        currency = "INR"  # lakh/crore are Indian numbering
    else:
        currency = None
    minor = int((value * 100).to_integral_value())
    return Amount(minor, currency) if minor > 0 else None


def find_amount_phrases(message: str) -> list[str]:
    return [m.group(0).strip() for m in _AMOUNT.finditer(message) if parse_amount(m.group(0))]


# --- durations -----------------------------------------------------------------

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a": 1, "an": 1}
_DURATION = re.compile(r"\b(?P<n>\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|a|an)[\s-]*(?P<unit>years?|yrs?|months?|mos?)\b", re.IGNORECASE)


def parse_term_months(phrase: str) -> int | None:
    matches = list(_DURATION.finditer(phrase))
    if len(matches) != 1:
        return None
    n_text = matches[0].group("n").lower()
    n = int(n_text) if n_text.isdigit() else _WORDS[n_text]
    months = n * 12 if matches[0].group("unit").lower().startswith("y") else n
    return months if 0 < months <= 1200 else None


def find_duration_phrases(message: str) -> list[str]:
    return [m.group(0) for m in _DURATION.finditer(message)]


# --- dates ---------------------------------------------------------------------

_MONTHS = {
    name: i
    for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
         ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")],
        start=1,
    )
    for name in names
}
MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_MONTH_RE = MONTH_RE
_DAY_MONTH = re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<m>{_MONTH_RE})\.?,?(?:\s+(?P<y>\d{{4}}))?\b", re.IGNORECASE)
_MONTH_DAY = re.compile(rf"\b(?P<m>{_MONTH_RE})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?(?:\s+(?P<y>\d{{4}}))?\b", re.IGNORECASE)
_NUMERIC = re.compile(r"\b(?P<a>\d{1,2})[/.-](?P<b>\d{1,2})[/.-](?P<y>\d{4})\b")
_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")


@dataclass(frozen=True)
class DateResult:
    value: date | None
    ambiguous: bool = False


def parse_date(phrase: str, today: date) -> DateResult:
    """Explicit dates only. Relative phrases other than today/yesterday, and numeric
    dates that read differently as dd/mm and mm/dd, are ambiguous."""
    text = phrase.strip().lower()
    if re.search(r"\btoday\b", text):
        return DateResult(today)
    if re.search(r"\byesterday\b", text):
        return DateResult(date.fromordinal(today.toordinal() - 1))
    try:
        if m := _ISO.search(text):
            return DateResult(date(int(m["y"]), int(m["m"]), int(m["d"])))
        if m := (_DAY_MONTH.search(text) or _MONTH_DAY.search(text)):
            month, day = _MONTHS[m["m"].lower()], int(m["d"])
            if m["y"]:
                return DateResult(date(int(m["y"]), month, day))
            candidate = date(today.year, month, day)
            # No year given: the most recent such date that is not in the future.
            return DateResult(candidate if candidate <= today else date(today.year - 1, month, day))
        if m := _NUMERIC.search(text):
            a, b, y = int(m["a"]), int(m["b"]), int(m["y"])
            if a <= 12 and b <= 12 and a != b:
                return DateResult(None, ambiguous=True)
            return DateResult(date(y, b, a))  # Indian convention dd/mm/yyyy
    except ValueError:
        return DateResult(None, ambiguous=True)
    return DateResult(None, ambiguous=True)


_ANY_DATE = re.compile(
    rf"\b(today|yesterday|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{4}}"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTH_RE})\.?,?(?:\s+\d{{4}})?"
    rf"|(?:{MONTH_RE})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?(?:\s+\d{{4}})?)\b",
    re.IGNORECASE,
)


def find_date_phrases(message: str) -> list[str]:
    return [m.group(0) for m in _ANY_DATE.finditer(message)]


# --- institutions --------------------------------------------------------------


@dataclass(frozen=True)
class Institution:
    name: str
    jurisdiction: str | None
    mapped_from: str | None = None  # set when a real-bank alias mapped to a synthetic name


@lru_cache
def _catalog() -> tuple[dict[str, str], dict[str, str]]:
    from reclaim_api.seed import default_fixture_path

    path: Path = default_fixture_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {i["name"]: i["jurisdiction"] for i in data["institutions"]}
    aliases = {k: v for k, v in data.get("institution_aliases", {}).items() if not k.startswith("_")}
    return names, aliases


_GENERIC = {"bank", "my bank", "the bank", "a bank", "fund", "insurance", "insurer", "post office"}


def resolve_institution(phrase: str) -> Institution | None:
    """Known synthetic name, a demo alias for one, or the owner's own wording."""
    names, aliases = _catalog()
    p = _norm(phrase)
    if not p or p in _GENERIC:
        return None
    for alias, target in aliases.items():
        if re.search(rf"\b{re.escape(alias.casefold())}\b", p):
            return Institution(target, names.get(target), mapped_from=alias)
    for name, jurisdiction in names.items():
        if name.casefold() in p or p in name.casefold():
            return Institution(name, jurisdiction)
    return Institution(phrase.strip(), None)


def find_institution_mentions(message: str) -> list[str]:
    """Catalog names and aliases mentioned in a message (used by the rules fixture)."""
    names, aliases = _catalog()
    found = [a for a in aliases if re.search(rf"\b{re.escape(a)}\b", message, re.IGNORECASE)]
    found += [n for n in names if n.casefold() in message.casefold()]
    return found


# --- asset type words ----------------------------------------------------------

TYPE_PATTERNS = (
    (r"\bfds?\b|fixed deposits?", "fixed_deposit"),
    (r"\bsips?\b|mutual funds?|\bmfs?\b|folios?", "mutual_fund"),
    (r"polic(y|ies)|insurance|sum assured", "life_insurance"),
    (r"\bflats?\b|house|property|plot|apartment", "property"),
    (r"pension|\bnps\b|\bepf\b", "pension"),
    (r"savings", "savings"),
)


def detect_asset_types(message: str) -> set[str]:
    """Asset types the owner's words name explicitly. Used to keep one topic from
    leaking into another's draft, whatever the model inferred."""
    low = message.lower()
    return {t for pat, t in TYPE_PATTERNS if re.search(pat, low)}
