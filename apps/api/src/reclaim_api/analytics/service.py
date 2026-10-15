"""Owner-scoped analytics with explainable review flags.

Scope comes from the authenticated actor only: the tenant key is derived server-side and
account tokens are mapped back to labels only for the owner's own assets. Every flag carries
its data window, sample size, rule, threshold and a plain explanation. A flag asks a person
to look; it is not evidence of fraud, and analytics never decide entitlement.
"""

import statistics
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from reclaim_api import proposals, records
from reclaim_api.auth import Actor
from reclaim_api.events.envelope import pseudonym
from reclaim_api.providers.base import ProviderUnavailable

MIN_SERIES_MONTHS = 3  # a recurring series needs this many months before a gap is flagged
MIN_HISTORY = 8  # earlier debits needed before an amount can be called unusual
SD_MULTIPLIER = 3.0
STALE_DAYS = 365
CATEGORY_LABELS = {
    "investment_contribution": "SIP / investment contribution",
    "insurance_premium": "Insurance premium",
    "loan_payment": "Loan payment",
    "income": "Regular credit",
    "transfer": "Transfer",
    "other": "Other spending",
}
NOTICE = (
    "Synthetic transactions; no bank feed is connected. A flag asks a person to review; it is not "
    "evidence of fraud or wrongdoing, and analytics never decide entitlement."
)


class Forbidden(PermissionError):
    pass


def _months(start: date, end: date) -> list[date]:
    out, cur = [], start.replace(day=1)
    while cur <= end:
        out.append(cur)
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def _as_date(value: Any) -> date:
    return value.date() if hasattr(value, "date") and callable(value.date) else value


def summary(session: Session, actor: Actor, *, warehouse: Any, analytics_key: bytes, start: date, end: date, as_of: date) -> dict[str, Any]:
    if actor.role != "owner":
        raise Forbidden
    tenant = pseudonym(actor.tenant_id, analytics_key)
    labels = {pseudonym(a["id"], analytics_key): a["label"] for a in records.list_assets(session, actor)}
    window = {"start": start.isoformat(), "end": end.isoformat(), "as_of": as_of.isoformat()}
    params = {"tenant": tenant, "start": start, "end": end}
    try:
        flows = warehouse.query("monthly_flows", params)
        dup = warehouse.query("duplicate_rate", params)[0]
        series = warehouse.query("recurring_series", params)
        debits = warehouse.query("unusual_amounts", params)
        stale = warehouse.query("stale_records", {"tenant": tenant, "as_of": as_of})[0]
        handovers = warehouse.query("handover_events", params)
        errors = warehouse.query("pipeline_errors", {"start": start, "end": end})
    except ProviderUnavailable as err:
        return {"status": "unavailable", "window": window, "detail": err.args[0], "notice": NOTICE}

    sample = sum(int(f["transactions"]) for f in flows)
    by_currency: dict[str, list[dict[str, Any]]] = {}
    for f in flows:
        cur = f["currency"]
        by_currency.setdefault(cur, []).append({
            "month": _as_date(f["month"]).isoformat()[:7], "inflow_minor": int(f["inflow_minor"]), "outflow_minor": int(f["outflow_minor"]),
            "inflow": proposals.format_money(int(f["inflow_minor"]), cur), "outflow": proposals.format_money(int(f["outflow_minor"]), cur),
            "transactions": int(f["transactions"]),
        })  # fmt: skip

    deliveries, distinct = int(dup["deliveries"] or 0), int(dup["distinct_events"] or 0)
    duplicates = {
        "deliveries": deliveries, "distinct_events": distinct,
        "duplicate_rate": round(1 - distinct / deliveries, 4) if deliveries else None,
        "note": "Totals use distinct events only; redeliveries never inflate them.",
    }  # fmt: skip

    # Only months that are over, and that the feed has data for, can show a gap.
    last_complete = end if (end + timedelta(days=1)).day == 1 else end.replace(day=1) - timedelta(days=1)
    data_last = max((_as_date(f["month"]) for f in flows), default=None)
    gap_until = min(last_complete, data_last) if data_last else None
    flags = (_recurring_gaps(series, labels, start, gap_until) if gap_until else []) + _unusual(debits, labels, window)
    return {
        "status": "ok" if sample else "insufficient_data",
        "window": window,
        "sample_size": sample,
        "flows_by_currency": by_currency,
        "duplicates": duplicates,
        "flags": flags,
        "stale_records": {**{k: int(stale[k] or 0) for k in ("assets", "stale", "without_source_date")},
                          "rule": f"latest known source date older than {STALE_DAYS} days before {as_of.isoformat()}"},
        "handover_turnaround": _turnaround(handovers),
        "pipeline_errors": [{"topic": e["topic"], "reason": e["reason"], "errors": int(e["errors"])} for e in errors],
        "notice": NOTICE,
    }


def _recurring_gaps(rows: list[dict[str, Any]], labels: dict[str, str], start: date, until: date) -> list[dict[str, Any]]:
    window_months = _months(start, until)
    end = until
    grouped: dict[tuple[str, str, str], set[date]] = {}
    for r in rows:
        grouped.setdefault((r["account_token"], r["category"], r["currency"]), set()).add(_as_date(r["month"]))
    flags = []
    for (token, category, currency), present in sorted(grouped.items()):
        if len(present) < MIN_SERIES_MONTHS:
            continue
        expected = [mo for mo in window_months if mo >= min(present)]
        missing = [mo for mo in expected if mo not in present]
        for mo in missing:
            flags.append({
                "kind": "recurring_gap", "account": labels.get(token, "Unrecorded account"), "category": CATEGORY_LABELS.get(category, category),
                "currency": currency, "month": mo.isoformat()[:7],
                "rule": f"A series seen in at least {MIN_SERIES_MONTHS} months of the window is expected every month after it starts",
                "threshold": f"{MIN_SERIES_MONTHS} months", "data_window": {"start": start.isoformat(), "end": end.isoformat()},
                "sample_size": len(present),
                "explanation": f"{CATEGORY_LABELS.get(category, category)} appeared in {len(present)} of {len(expected)} months since it started; none in {mo.isoformat()[:7]}.",
            })  # fmt: skip
    return flags


def _unusual(rows: list[dict[str, Any]], labels: dict[str, str], window: dict[str, str]) -> list[dict[str, Any]]:
    flags = []
    for r in rows:
        n, mean, sd = int(r["prior_n"] or 0), r["prior_mean"], r["prior_sd"]
        if n < MIN_HISTORY or mean is None or sd is None:
            continue
        threshold = float(mean) + SD_MULTIPLIER * float(sd)
        amount = int(r["amount_minor"])
        if amount <= threshold:
            continue
        cur = r["currency"]
        flags.append({
            "kind": "unusual_amount", "account": labels.get(r["account_token"], "Unrecorded account"),
            "category": CATEGORY_LABELS.get(r["category"], r["category"]), "currency": cur,
            "date": _as_date(r["booked_at"]).isoformat(), "amount": proposals.format_money(amount, cur),
            "rule": f"Debit above the mean plus {SD_MULTIPLIER:g} standard deviations of this account's earlier debits",
            "threshold": proposals.format_money(int(threshold), cur), "data_window": window, "sample_size": n,
            "explanation": f"{proposals.format_money(amount, cur)} on {_as_date(r['booked_at']).isoformat()} compared with an average earlier debit of "
                           f"{proposals.format_money(int(mean), cur)} across {n} debits.",
        })  # fmt: skip
    return flags


def _turnaround(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[str, dict[str, Any]] = {}
    for r in rows:
        pairs.setdefault(r["handover_key"], {})[r["event_type"]] = r["occurred_at"]
    hours = [
        (p["analytics.handover_released"] - p["analytics.handover_requested"]).total_seconds() / 3600
        for p in pairs.values()
        if "analytics.handover_requested" in p and "analytics.handover_released" in p
    ]
    if not hours:
        return {"status": "insufficient_data", "sample_size": 0, "note": "No completed handovers in the window."}
    return {"status": "ok", "sample_size": len(hours), "median_hours": round(statistics.median(hours), 1)}
