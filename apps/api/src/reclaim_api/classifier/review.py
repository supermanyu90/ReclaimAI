"""Owner review of predicted categories. A prediction is a suggestion with a model version;
a confirmation is the owner's decision, stored separately and never overwritten by a model."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api import proposals, records
from reclaim_api.auth import Actor
from reclaim_api.classifier.data import CATEGORIES
from reclaim_api.events.envelope import pseudonym


class Forbidden(PermissionError):
    pass


def queue(session: Session, actor: Actor, *, warehouse: Any, analytics_key: bytes) -> dict[str, Any]:
    if actor.role != "owner":
        raise Forbidden
    tenant = pseudonym(actor.tenant_id, analytics_key)
    labels = {pseudonym(a["id"], analytics_key): a["label"] for a in records.list_assets(session, actor)}
    rows = warehouse.query("review_queue", {"tenant": tenant})
    confirmed = dict(
        session.execute(select(m.CategoryConfirmation.transaction_id, m.CategoryConfirmation.category).where(m.CategoryConfirmation.tenant_id == actor.tenant_id)).all()
    )
    items = [
        {
            "transaction_id": r["transaction_id"], "account": labels.get(r["account_token"], "Unrecorded account"),
            "narrative": r["narrative"], "direction": r["direction"], "amount": proposals.format_money(int(r["amount_minor"]), r["currency"]),
            "booked_on": str(r["booked_at"])[:10], "predicted_category": r["predicted_category"],
            "confidence": r["confidence"], "abstained": r["abstained"], "source": r["source"], "model_version": r["model_version"],
            "confirmed_category": confirmed.get(r["transaction_id"]),
        }
        for r in rows
    ]  # fmt: skip
    return {
        "needs_review": [i for i in items if i["abstained"] and not i["confirmed_category"]],
        "recent": [i for i in items if not i["abstained"]][:10],
        "predicted": len(items),
        "abstained": sum(i["abstained"] for i in items),
        "model_versions": sorted({i["model_version"] for i in items}),
        "notice": "Categories are model suggestions on synthetic data until you confirm them.",
    }


def confirm(session: Session, actor: Actor, transaction_id: str, category: str, *, warehouse: Any, analytics_key: bytes) -> dict[str, Any]:
    if actor.role != "owner":
        raise Forbidden
    if category not in CATEGORIES:
        raise ValueError("unknown category")
    tenant = pseudonym(actor.tenant_id, analytics_key)
    row = next((r for r in warehouse.query("review_queue", {"tenant": tenant}) if r["transaction_id"] == transaction_id), None)
    if row is None:
        raise records.NotFound  # not this owner's transaction (or never predicted)
    stmt = insert(m.CategoryConfirmation).values(
        tenant_id=actor.tenant_id, transaction_id=transaction_id, category=category,
        predicted_category=row["predicted_category"], model_version=row["model_version"], confirmed_by_user_id=actor.user_id,
    )  # fmt: skip
    session.execute(stmt.on_conflict_do_update(index_elements=["tenant_id", "transaction_id"], set_={"category": category, "confirmed_by_user_id": actor.user_id}))
    session.flush()
    return {"transaction_id": transaction_id, "confirmed_category": category, "predicted_category": row["predicted_category"], "model_version": row["model_version"]}
