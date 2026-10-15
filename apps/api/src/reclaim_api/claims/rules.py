"""Checklist rules, pinned to reviewed policy versions.

Rules choose the checklist; the language model only explains it. Each rule set is written
against one policy document version; a newer version without reviewed rules produces a
referral, not a guess. Every requirement carries the policy section it comes from.
"""

from dataclasses import dataclass
from typing import Literal

Tri = Literal["yes", "no", "unknown"]


@dataclass(frozen=True)
class Item:
    id: str
    requirement: str


@dataclass(frozen=True)
class Path:
    key: str
    section: str
    title: str
    items: tuple[Item, ...]
    note: str | None = None


DEATH_CERT = Item("death_certificate", "Copy of the death certificate")
ACCOUNT_PROOF = Item("account_proof", "Passbook, statement or deposit receipt")


@dataclass(frozen=True)
class RuleSet:
    doc_id: str
    version: str
    threshold_minor: int
    paths: dict[str, Path]


RULES: dict[tuple[str, str], RuleSet] = {
    ("sbb-deceased-claims", "2"): RuleSet(
        doc_id="sbb-deceased-claims",
        version="2",
        threshold_minor=150_000_000,  # Rs. 15,00,000 in paise (fictional policy §4)
        paths={
            "nominee": Path("nominee", "§2", "Registered nominee claims",
                            (Item("claim_form", "Claim form"), DEATH_CERT, Item("nominee_kyc", "Nominee's identity and address proof (KYC)"), ACCOUNT_PROOF),
                            note="Payment to the nominee does not decide who inherits; the nominee holds it for the legal heirs."),
            "survivor": Path("survivor", "§3", "Surviving joint holder", (DEATH_CERT, Item("survivor_kyc", "Surviving holder's KYC"))),
            "heirs_small": Path("heirs_small", "§4", "Legal heirs, undisputed, up to Rs. 15,00,000",
                                (Item("claim_form_all_heirs", "Claim form signed by all legal heirs"), DEATH_CERT,
                                 Item("indemnity", "Letter of indemnity"), Item("heirship_affidavit", "Affidavit of legal heirship"),
                                 Item("heirs_kyc", "KYC of each legal heir")),
                                note="A succession certificate is not required on this path."),
            "heirs_large": Path("heirs_large", "§4", "Legal heirs, above Rs. 15,00,000",
                                (Item("succession_certificate", "Succession certificate, letter of administration or probate"), DEATH_CERT)),
            "disputed": Path("disputed", "§5", "Disputed claim",
                             (Item("court_order", "Court order, succession certificate, letter of administration or probate"),),
                             note="The bank does not settle a disputed claim until a court document is produced."),
        },
    ),
}


@dataclass(frozen=True)
class Choice:
    path: Path
    condition: str | None  # None: applies on the facts given; else: applies only if ...


def choose(rules: RuleSet, *, nomination: str, survivorship: Tri, dispute: Tri, balance_minor: int | None) -> tuple[list[Choice], list[str]]:
    """Returns (paths, unknowns). Unknown facts yield conditional paths, never a guess."""
    p = rules.paths
    unknowns: list[str] = []
    if dispute == "yes":
        return [Choice(p["disputed"], None)], unknowns
    if survivorship == "yes":
        return [Choice(p["survivor"], None)], unknowns

    def heirs(condition: str | None) -> list[Choice]:
        if balance_minor is None:
            unknowns.append("total balance: decides between the two legal-heir paths")
            return [Choice(p["heirs_small"], _join(condition, "the total is up to Rs. 15,00,000")),
                    Choice(p["heirs_large"], _join(condition, "the total is above Rs. 15,00,000"))]  # fmt: skip
        return [Choice(p["heirs_small" if balance_minor <= rules.threshold_minor else "heirs_large"], condition)]

    out: list[Choice] = []
    if survivorship == "unknown":
        unknowns.append("holding mode: whether the account had a survivorship mandate")
        out.append(Choice(p["survivor"], "the account had an \"either or survivor\" mandate"))
    if nomination == "recorded":
        out.append(Choice(p["nominee"], None if survivorship == "no" else "there is no surviving joint holder"))
    elif nomination in ("not_recorded", "not_applicable"):
        out.extend(heirs(None if survivorship == "no" else "there is no surviving joint holder"))
    else:
        unknowns.append("nomination: not known whether a nominee is registered")
        out.append(Choice(p["nominee"], "a nominee is registered"))
        out.extend(heirs("no nominee is registered"))
    if dispute == "unknown":
        unknowns.append("dispute: whether any legal heir disputes the claim")
        out.append(Choice(p["disputed"], "any legal heir disputes the claim"))
    return out, unknowns


def _join(a: str | None, b: str) -> str:
    return f"{a} and {b}" if a else b
