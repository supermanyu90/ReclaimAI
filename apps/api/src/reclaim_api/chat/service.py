"""Chat orchestration: one owner message in, one assistant reply out.

Flow: redact → model extracts (intent + verbatim phrases) → schema validation → the
server parses phrases deterministically and checks each one appears in the message →
a server tool drafts a proposal → the reply asks for the next missing field or shows
the review card. Replies are server templates; model text is never shown verbatim.
Failures (model unavailable, invalid output) change nothing and say so.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api import proposals
from reclaim_api.auth import Actor
from reclaim_api.chat import parse, tools
from reclaim_api.chat.schema import SYSTEM_PROMPT, Extraction, extraction_json_schema
from reclaim_api.lib.log import message_tag
from reclaim_api.providers.base import ProviderUnavailable
from reclaim_api.providers.llm import InvalidModelOutput

log = logging.getLogger("reclaim_api.chat")

MAX_MESSAGE_CHARS = 1000

_AMOUNT_COLUMN = {
    "balance": "balance_minor",
    "sip_installment": "sip_installment_minor",
    "insurance_cover": "insurance_cover_minor",
    "estimated_value": "estimated_value_minor",
    "liability": "liability_minor",
}
_DEFAULT_AMOUNT = {
    "savings": "balance_minor", "fixed_deposit": "balance_minor", "mutual_fund": "balance_minor",
    "pension": "balance_minor", "life_insurance": "insurance_cover_minor", "property": "estimated_value_minor",
}  # fmt: skip
_QUESTIONS = {
    "asset_type": "What kind of asset is it: savings account, fixed deposit, mutual fund, insurance policy, property or pension?",
    "institution_name": "Which bank or institution is this {type} with?",
    "balance_minor": "How much is it for? (for example ₹5 lakh)",
    "currency": "Which currency is that amount in?",
    "term_months": "What is the term, or the maturity date?",
    "opened_on": "On what date did you open it? (for example 10 October 2026)",
}


class Forbidden(PermissionError):
    pass


@dataclass
class Parsed:
    fields: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[tuple[str, str]] = field(default_factory=list)  # (field, phrase)
    dropped: int = 0  # phrases the model returned that are not in the message


def _parse_extraction(ex: Extraction, message: str, today: date, asset_type: str | None, known: dict[str, Any] | None = None) -> Parsed:
    """`known`: fields already on the draft. An unparseable phrase never overrides one."""
    out = Parsed()
    known = known or {}

    def verbatim(phrase: str | None, candidates: list[str] | None = None) -> str | None:
        """The message's wording for the model's phrase. If the model's wording is not in
        the message but the server finds exactly one candidate of that kind there, use it:
        the value still comes from the owner's own text, never from the model."""
        if phrase is None:
            return None
        found = parse.locate(phrase, message)
        if found is None and candidates is not None and len(candidates) == 1:
            found = candidates[0]
        if found is None:
            out.dropped += 1
        return found

    if phrase := verbatim(ex.institution_text):
        inst = parse.resolve_institution(phrase)
        if inst is None:
            out.ambiguous.append(("institution_name", phrase))
        else:
            out.fields["institution_name"] = inst.name
            ref = {"field": "institution_name", "source": "user_message", "quote": phrase}
            if inst.mapped_from:
                ref["note"] = f"'{inst.mapped_from}' mapped to the synthetic demo institution {inst.name}"
            out.evidence.append(ref)
            if inst.jurisdiction:
                out.fields["jurisdiction"] = inst.jurisdiction

    if phrase := verbatim(ex.amount_text, parse.find_amount_phrases(message)):
        amount = parse.parse_amount(phrase)
        column = _AMOUNT_COLUMN.get(ex.amount_kind or "") or _DEFAULT_AMOUNT.get(asset_type or "")
        if amount is None or column is None:
            out.ambiguous.append(("balance_minor", phrase))
        else:
            out.fields[column] = amount.minor
            out.evidence.append({"field": column, "source": "user_message", "quote": phrase})
            if amount.currency:
                out.fields["currency"] = amount.currency
                out.evidence.append({"field": "currency", "source": "user_message", "quote": phrase})

    if phrase := verbatim(ex.term_text, parse.find_duration_phrases(message)):
        months = parse.parse_term_months(phrase)
        if months is None:
            out.ambiguous.append(("term_months", phrase))
        else:
            out.fields["term_months"] = months
            out.evidence.append({"field": "term_months", "source": "user_message", "quote": phrase})

    for name, phrase_in in (("opened_on", ex.opened_date_text), ("maturity_on", ex.maturity_date_text)):
        if phrase := verbatim(phrase_in):
            result = parse.parse_date(phrase, today)
            if result.value is None or result.value > today and name == "opened_on":
                out.ambiguous.append((name, phrase))
            else:
                out.fields[name] = result.value.isoformat()
                out.evidence.append({"field": name, "source": "user_message", "quote": phrase})

    if ex.nomination:
        out.fields["nomination_status"] = ex.nomination
        out.evidence.append({"field": "nomination_status", "source": "user_message"})
    out.ambiguous = [(f, ph) for f, ph in out.ambiguous if known.get(f) is None]
    return out


def _jurisdiction_default(fields: dict[str, Any], evidence: list[dict[str, Any]]) -> None:
    """Jurisdiction is required. Without an institution match it is assumed from currency
    and flagged as an assumption on the review card for the owner to check."""
    if "jurisdiction" in fields:
        return
    guess = {"INR": "IN", "SGD": "SG"}.get(fields.get("currency") or "INR")
    if guess:
        fields["jurisdiction"] = guess
        evidence.append({"field": "jurisdiction", "source": "assumption", "note": "assumed from currency; please check"})


def _open_proposal(session: Session, actor: Actor) -> m.Proposal | None:
    return session.scalar(
        select(m.Proposal)
        .where(
            m.Proposal.tenant_id == actor.tenant_id, m.Proposal.owner_user_id == actor.user_id,
            m.Proposal.status.in_(("draft", "awaiting_confirmation")),
        )
        .order_by(m.Proposal.updated_at.desc(), m.Proposal.created_at.desc())
        .limit(1)
    )


def _history(session: Session, actor: Actor, limit: int) -> list[dict[str, str]]:
    turns = session.scalars(
        select(m.ChatTurn)
        .where(m.ChatTurn.tenant_id == actor.tenant_id, m.ChatTurn.user_id == actor.user_id)
        .order_by(m.ChatTurn.created_at.desc())
        .limit(limit)
    ).all()
    return [{"role": "user" if t.speaker == "owner" else "assistant", "content": t.text} for t in reversed(turns)]


def _asset_summary(a: dict[str, Any]) -> dict[str, Any]:
    return {k: a[k] for k in ("label", "asset_type", "institution_name", "masked_reference", "status")}


def _resolve_asset(assets: list[dict[str, Any]], ex: Extraction, message: str) -> list[dict[str, Any]]:
    """Match an existing asset by type and institution/label words, among authorized assets only."""
    active = [a for a in assets if a["status"] == "active"]
    if ex.asset_type:
        active = [a for a in active if a["asset_type"] == ex.asset_type]
    text = " ".join(p for p in (ex.asset_ref_text, ex.institution_text, message) if p).casefold()
    words = set(re.findall(r"[a-z0-9]{3,}", text))
    scored = []
    for a in active:
        hay = " ".join(str(a[k] or "") for k in ("label", "institution_name", "masked_reference", "currency")).casefold()
        score = sum(1 for w in words if w in hay)
        if "sgd" in words and a.get("currency") == "SGD":
            score += 2
        scored.append((score, a))
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [a for s, a in scored if s == best] if best > 0 or len(scored) == 1 else []


def _reply(state: str, text: str, proposal: m.Proposal | None = None, target: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"state": state, "reply": text, "proposal": proposals.proposal_view(proposal, target) if proposal else None}


def _target(asset: dict[str, Any]) -> dict[str, Any]:
    return {k: asset[k] for k in ("label", "institution_name", "masked_reference", "version")}


def handle_message(session: Session, actor: Actor, text: str, *, llm: Any, today: date, history_turns: int = 6, channel: str = "web") -> dict[str, Any]:
    if actor.role != "owner":
        raise Forbidden
    message, redacted = parse.redact_long_numbers(text.strip()[:MAX_MESSAGE_CHARS])
    history = _history(session, actor, history_turns)
    owner_turn = m.ChatTurn(tenant_id=actor.tenant_id, user_id=actor.user_id, speaker="owner", channel=channel, text=message)
    session.add(owner_turn)
    session.flush()

    result = _respond(session, actor, message, history, llm=llm, today=today, channel=channel)
    if redacted:
        result["reply"] = "I hid a long number from your message; the last 4 digits are enough. " + result["reply"]
    p = result.get("proposal")
    session.add(
        m.ChatTurn(
            tenant_id=actor.tenant_id, user_id=actor.user_id, speaker="assistant", channel=channel, text=result["reply"],
            state=result["state"], proposal_id=uuid.UUID(p["id"]) if p else None,
        )
    )  # fmt: skip
    session.flush()
    log.info("chat turn handled state=%s %s", result["state"], message_tag(message))
    return result


def _respond(session: Session, actor: Actor, message: str, history: list[dict[str, str]], *, llm: Any, today: date, channel: str) -> dict[str, Any]:
    if parse.CREDENTIAL_WORDS.search(message):
        return _reply("blocked", "Please never share OTPs, PINs or passwords. ReclaimAI never needs them. Nothing was recorded.")

    assets = tools.list_authorized_assets(session, actor)
    draft = _open_proposal(session, actor)
    payload = {
        "latest_message": message,
        "recorded_assets": [_asset_summary(a) for a in assets],
        "open_draft": {k: v for k, v in draft.proposed_fields.items()} | {"change_kind": draft.change_kind} if draft else None,
    }
    messages = [*history, {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}]

    try:
        raw = llm.generate_structured(system=SYSTEM_PROMPT, messages=messages, schema=extraction_json_schema())
    except ProviderUnavailable as err:
        return _reply("unavailable", f"The assistant model is unavailable right now ({err.args[0]}). Nothing was changed.", draft)
    except InvalidModelOutput:
        return _reply("invalid_output", "I couldn't process that reliably, so nothing was changed. Could you rephrase it?", draft)
    try:
        ex = Extraction.model_validate(raw)
    except ValidationError as err:
        log.warning("model output failed validation errors=%d", err.error_count())
        return _reply("invalid_output", "I couldn't process that reliably, so nothing was changed. Could you rephrase it?", draft)

    model_version = getattr(llm, "model_version", None)

    # The owner's explicit words outrank the model's labels: a small model can carry a
    # previous topic over from history. One named type, or a closing verb, decides.
    named_types = parse.detect_asset_types(message)
    if len(named_types) == 1:
        ex = ex.model_copy(update={"asset_type": next(iter(named_types))})
    elif named_types and ex.asset_type not in named_types:
        ex = ex.model_copy(update={"asset_type": None})
    if parse.CLOSING_WORDS.search(message) and ex.intent in ("update_asset", "other"):
        ex = ex.model_copy(update={"intent": "close_asset"})

    if ex.intent == "list_assets":
        if not assets:
            return _reply("ok", "You have no recorded assets yet.", draft)
        if channel == "telegram":
            # Names and institutions can identify accounts; Telegram gets a count only.
            active = sum(1 for a in assets if a["status"] == "active")
            return _reply("ok", f"You have {active} active recorded assets. The list is in the web app.", draft)
        lines = [f"• {a['label']} — {a['institution_name'] or 'no institution'}" + (" (closed)" if a["status"] == "closed" else "") for a in assets]
        return _reply("ok", f"You have {len(assets)} recorded assets:\n" + "\n".join(lines), draft)

    if ex.intent == "policy_question":
        try:
            ans = tools.retrieve_policy(session, actor, message, llm=llm, today=today)
        except ProviderUnavailable:
            return _reply("unavailable", "Policy search is unavailable right now. Nothing was changed.", draft)
        if ans["status"] != "grounded":
            return _reply("ok", ans["answer"], draft)
        cites = "; ".join(f"{c['title']} v{c['version']} {c['section']}" for c in ans["citations"])
        return _reply("ok", f"{ans['answer']}\n\nSources: {cites}. Guidance only; the institution decides claims.", draft)

    draft_type = draft.proposed_fields.get("asset_type") if draft and draft.change_kind == "create" else None
    recorded_types = {a["asset_type"] for a in assets if a["status"] == "active"}
    if (
        parse.UPDATE_WORDS.search(message)
        and ex.intent in ("add_asset", "other")
        and ex.asset_type in recorded_types
        and ex.asset_type != draft_type
    ):
        ex = ex.model_copy(update={"intent": "update_asset"})

    if ex.intent == "close_asset" or (ex.intent == "update_asset" and (draft_type is None or ex.asset_type not in (None, draft_type))):
        return _propose_existing(session, actor, ex, message, assets, today, model_version, channel)

    # Follow-ups ("I opened it on 10 October") continue an open new-asset draft whatever
    # intent label the model chose, unless they name another type or restate it with
    # different values (a second FD is a new draft, not an overwrite).
    if draft_type and ex.intent in ("add_asset", "update_asset", "other") and ex.asset_type in (None, draft_type):
        parsed = _parse_extraction(ex, message, today, draft_type, draft.proposed_fields)
        _fill_asked_slot(parsed, draft, message, today)
        clashes = [f for f, v in parsed.fields.items() if f not in ("jurisdiction", "currency") and draft.proposed_fields.get(f) not in (None, v)]
        if not (ex.intent == "add_asset" and clashes):
            if not parsed.fields and not parsed.ambiguous:
                return _reply("clarification", _next_question(draft.missing_fields, draft.proposed_fields) or "Could you say that another way?", draft)
            return _save_create(session, actor, assets, draft_type, parsed, draft, model_version, channel)

    if ex.intent == "add_asset":
        if ex.asset_type is None:
            return _reply("clarification", _QUESTIONS["asset_type"])
        parsed = _parse_extraction(ex, message, today, ex.asset_type)
        return _save_create(session, actor, assets, ex.asset_type, parsed, None, model_version, channel)

    if draft:
        return _reply("ok", "I can help you record or update assets. Your draft below is still open.", draft)
    return _reply("ok", "I can help you record a new asset or update one. For example: \"I opened a fixed deposit for ₹5 lakh for 3 years.\"")


def _fill_asked_slot(parsed: Parsed, draft: m.Proposal, message: str, today: date) -> None:
    """If the owner's reply holds exactly one value of the kind the assistant just asked
    for, take it from their text, independent of how the model labelled it."""
    if not draft.missing_fields:
        return
    slot = draft.missing_fields[0]
    if slot in parsed.fields:
        return
    note = "answer to the assistant's question"
    if slot == "opened_on" and len(dates := parse.find_date_phrases(message)) == 1:
        result = parse.parse_date(dates[0], today)
        if result.value and result.value <= today:
            parsed.fields["opened_on"] = result.value.isoformat()
            parsed.evidence.append({"field": "opened_on", "source": "user_message", "quote": dates[0], "note": note})
            parsed.ambiguous = [a for a in parsed.ambiguous if a[0] != "opened_on"]
        elif result.ambiguous:
            parsed.ambiguous.append(("opened_on", dates[0]))
    elif slot == "balance_minor" and len(amounts := parse.find_amount_phrases(message)) == 1:
        amount = parse.parse_amount(amounts[0])
        parsed.fields["balance_minor"] = amount.minor
        parsed.evidence.append({"field": "balance_minor", "source": "user_message", "quote": amounts[0], "note": note})
        if amount.currency:
            parsed.fields["currency"] = amount.currency
    elif slot == "term_months" and len(terms := parse.find_duration_phrases(message)) == 1:
        parsed.fields["term_months"] = parse.parse_term_months(terms[0])
        parsed.evidence.append({"field": "term_months", "source": "user_message", "quote": terms[0], "note": note})
    elif slot == "institution_name" and len(names := parse.find_institution_mentions(message)) == 1:
        inst = parse.resolve_institution(names[0])
        parsed.fields["institution_name"] = inst.name
        if inst.jurisdiction:
            parsed.fields["jurisdiction"] = inst.jurisdiction
        parsed.evidence.append({"field": "institution_name", "source": "user_message", "quote": names[0], "note": note})


def _save_create(session, actor, assets, asset_type, parsed: Parsed, draft: m.Proposal | None, model_version, channel) -> dict[str, Any]:
    base_fields = dict(draft.proposed_fields) if draft else {}
    base_evidence = list(draft.evidence_refs) if draft else []
    fields = {**base_fields, **parsed.fields, "asset_type": asset_type}
    evidence = [r for r in base_evidence if r.get("field") not in parsed.fields] + parsed.evidence
    if "term_months" in parsed.fields or "label" not in fields:
        fields["label"] = proposals.TYPE_LABELS[asset_type] + (f" ({proposals.term_text(fields['term_months'])})" if fields.get("term_months") else "")
    _jurisdiction_default(fields, evidence)
    p = tools.propose_asset_change(
        session, actor,
        tools.ProposeArgs(
            change_kind="create", fields=fields, evidence_refs=evidence, conflicts=_duplicates(assets, fields),
            channel=channel, model_version=model_version, proposal_id=draft.id if draft else None,
        ),
    )  # fmt: skip
    return _after_proposal(p, parsed)


def _duplicates(assets: list[dict[str, Any]], fields: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for a in assets:
        if (
            a["status"] == "active"
            and a["asset_type"] == fields.get("asset_type")
            and a["institution_name"] == fields.get("institution_name")
            and fields.get("balance_minor") is not None
            and a["amounts"].get("balance_minor") == fields.get("balance_minor")
        ):
            out.append({"kind": "possible_duplicate", "asset_id": a["id"], "label": a["label"], "note": "Same type, institution and amount. Not merged."})
    return out


def _next_question(missing: list[str], fields: dict[str, Any]) -> str | None:
    if not missing:
        return None
    kind = proposals.TYPE_LABELS.get(fields.get("asset_type", ""), "asset").lower()
    return _QUESTIONS.get(missing[0], "Could you tell me the {f}?").format(type=kind, f=missing[0].replace("_", " "))


def _after_proposal(p: m.Proposal, parsed: Parsed, target: dict[str, Any] | None = None) -> dict[str, Any]:
    if parsed.ambiguous:
        name, phrase = parsed.ambiguous[0]
        hint = {"opened_on": "a date like 10 October 2026", "maturity_on": "a date like 10 October 2029", "institution_name": "the bank's name"}.get(name, "a clearer value")
        return _reply("clarification", f"I couldn't tell exactly what \"{phrase}\" means. Could you give {hint}?", p, target)
    if p.missing_fields:
        return _reply("clarification", _next_question(p.missing_fields, p.proposed_fields), p, target)
    return _reply("review", "Please review the details below. Nothing is recorded until you confirm.", p, target)


def _propose_existing(session, actor, ex, message, assets, today, model_version, channel) -> dict[str, Any]:
    matches = _resolve_asset(assets, ex, message)
    if not matches:
        return _reply("clarification", "I couldn't find which of your recorded assets you mean. Could you name the institution or type?")
    if len(matches) > 1:
        names = "; ".join(f"{a['label']} ({a['institution_name']})" for a in matches[:5])
        return _reply("clarification", f"Which one do you mean: {names}?")
    asset = matches[0]
    if ex.intent == "close_asset":
        p = tools.propose_asset_change(
            session, actor,
            tools.ProposeArgs(change_kind="close", asset_id=uuid.UUID(asset["id"]), fields={}, channel=channel, model_version=model_version),
        )  # fmt: skip
        return _reply("review", f"Mark \"{asset['label']}\" as closed? Nothing changes until you confirm.", p, _target(asset))

    parsed = _parse_extraction(ex, message, today, asset["asset_type"])
    changes = {k: v for k, v in parsed.fields.items() if k not in ("jurisdiction",)}
    if not changes:
        return _reply("clarification", f"What should change on \"{asset['label']}\"?")
    current = {**asset["amounts"], "currency": asset["currency"], "institution_name": asset["institution_name"],
               "opened_on": asset["opened_on"], "term_months": asset["term_months"], "maturity_on": asset["maturity_on"]}  # fmt: skip
    conflicts = [
        {"kind": "differs_from_recorded", "field": f, "recorded": current.get(f), "proposed": v}
        for f, v in changes.items()
        if current.get(f) is not None and current.get(f) != v
    ]
    p = tools.propose_asset_change(
        session, actor,
        tools.ProposeArgs(
            change_kind="update", asset_id=uuid.UUID(asset["id"]), fields=changes, evidence_refs=parsed.evidence,
            conflicts=conflicts, channel=channel, model_version=model_version,
        ),
    )  # fmt: skip
    return _after_proposal(p, parsed, _target(asset))
