"""Three-minute demo: deterministic reset, preflight check and a scripted rehearsal.

    reclaim-demo reset       wipe synthetic tenants (and their local files), reseed "before"
    reclaim-demo check       preflight: database, 6 assets, provider status
    reclaim-demo rehearse    drive the whole demo through the running API and verify each
                             state change (leaves the database in the post-demo state)

The rehearsal uses the same endpoints the web app calls, so a passing rehearsal means the
screens have real state to show. It never sends messages or touches cloud resources.
"""

import argparse
import os
import shutil
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reclaim_api.seed import default_fixture_path, fixture_id

FIXTURES = default_fixture_path().parent
DOCS = FIXTURES / "documents"
OPENING_LINE = "I opened a ₹5 lakh FD for 3 years."
CLOSING_LINE = "One maintained record, controlled family access, a clearer path to claims."
USERS = ("ramesh", "priya", "reviewer_anil", "reviewer_meera")


class RehearsalFailed(AssertionError):
    pass


def _h(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer dev:{fixture_id(user)}"}


# --- reset and check ----------------------------------------------------------------------------


def reset() -> dict[str, int]:
    from sqlalchemy.orm import Session

    from reclaim_api.config import get_settings
    from reclaim_api.db import get_engine
    from reclaim_api.seed import load_fixture, seed

    settings = get_settings()
    if settings.app_mode not in ("local", "demo"):
        raise SystemExit("demo reset runs in local or hosted-demo mode only")
    with Session(get_engine()) as s, s.begin():
        counts = seed(s, "before", do_reset=True)
    # Local files of the synthetic tenants only (uploads, extracted text, passports).
    root = Path(settings.local_storage_dir)
    removed = 0
    for t in load_fixture()["tenants"]:
        for prefix in ("quarantine", "extracted", "passports"):
            target = root / prefix / str(fixture_id(t["key"]))
            if target.is_dir():
                shutil.rmtree(target)
                removed += 1
    # The analytics panel: synthetic transaction feed, then batch classification.
    from reclaim_api.analytics.synthetic import load_feed
    from reclaim_api.classifier.cli import predict_all

    _, published, _ = load_feed()
    predicted, _, source = predict_all()
    from sqlalchemy import func, select

    from reclaim_api import models as m

    with Session(get_engine()) as s:
        ramesh = s.scalar(select(func.count()).select_from(m.Asset).where(m.Asset.owner_user_id == fixture_id("ramesh")))
    return (
        {"ramesh_assets": ramesh}
        | counts
        | {"local_dirs_removed": removed, "transactions_published": published, "predicted": predicted, "classifier": source}
    )


def check(client: Any) -> list[str]:
    problems = []
    ready = client.get("/readyz").json()
    if ready.get("status") != "ready":
        problems.append("API not ready (database?)")
    assets = client.get("/v1/assets", headers=_h("ramesh")).json().get("assets", [])
    if len(assets) != 6:
        problems.append(f"expected 6 assets at demo start, found {len(assets)} (run: make demo-reset)")
    caps = client.get("/v1/system/capabilities").json()
    for c in caps["capabilities"]:
        print(f"  {c['name']:<15} {c['state']:<12} {c['adapter']:<22} {c['detail']}")
    if next((c for c in caps["capabilities"] if c["name"] == "llm"), {}).get("state") == "unavailable":
        problems.append("LLM unavailable: start Ollama (ollama serve; ollama pull gemma3:4b) or set LLM_PROVIDER=fixture")
    return problems


# --- rehearsal ------------------------------------------------------------------------------------


def rehearse(
    client: Any, *, settle: Callable[[], None] | None = None, optional: bool = True, log: Callable[[str], None] = print
) -> dict[str, Any]:
    """Run the demo story. `settle` lets tests drain the outbox; against a running stack the
    worker does it and the rehearsal polls."""
    out: dict[str, Any] = {}

    def ok(res: Any, code: int = 200) -> Any:
        if res.status_code != code:
            # Method, path, status and the API's error code only; never the response body.
            try:
                detail = res.json().get("detail")
            except ValueError:
                detail = None
            reason = detail.get("code") if isinstance(detail, dict) else (detail if isinstance(detail, str) and len(detail) < 80 else "")
            raise RehearsalFailed(f"{res.request.method} {res.request.url.path} → {res.status_code} {reason}".rstrip())
        return res.json() if res.headers.get("content-type", "").startswith("application/json") else res

    def expect(cond: bool, what: str) -> None:
        if not cond:
            raise RehearsalFailed(what)

    last = [time.monotonic()]

    def step(at: str, text: str) -> None:
        now = time.monotonic()
        log(f"[{at}] ✓ {text} ({now - last[0]:.1f} s)")
        last[0] = now

    def wait_doc(doc_id: str, who: str) -> dict:
        deadline = time.monotonic() + 90
        while True:
            if settle:
                settle()
            doc = ok(client.get(f"/v1/documents/{doc_id}", headers=_h(who)))
            if doc["status"] not in ("pending",) or time.monotonic() > deadline:
                return doc
            time.sleep(0.5)

    def upload(path: str, who: str = "ramesh") -> dict:
        res = client.post("/v1/documents", files={"file": (path, (DOCS / path).read_bytes(), "text/plain")}, headers=_h(who))
        return wait_doc(ok(res, 202)["id"], who)

    def evidence(case_id: str, name: str) -> None:
        ok(
            client.post(
                f"/v1/handovers/{case_id}/evidence", files={"file": (name, (DOCS / name).read_bytes(), "text/plain")}, headers=_h("priya")
            )
        )
        deadline = time.monotonic() + 90
        while True:
            if settle:
                settle()
            docs = ok(client.get(f"/v1/handovers/{case_id}", headers=_h("priya")))["evidence"]  # requester view: no model call
            if all(d["status"] != "pending" for d in docs) or time.monotonic() > deadline:
                return
            time.sleep(0.5)

    started = time.monotonic()
    last[0] = started
    expect(len(ok(client.get("/v1/assets", headers=_h("ramesh")))["assets"]) == 6, "demo must start with 6 assets (make demo-reset)")

    # 0:00–0:25 chat
    turn = ok(client.post("/v1/chat/messages", json={"text": OPENING_LINE}, headers=_h("ramesh")))
    p = turn["proposal"]
    expect(p is not None and p["status"] == "draft" and not p["can_confirm"], "the opening line should create an unconfirmable draft")
    fields = {f["field"]: f["raw"] for f in p["fields"]}
    expect(fields.get("balance_minor") == 50_000_000 and fields.get("term_months") == 36, f"draft values wrong: {fields}")
    step("0:00", f"chat draft: ₹5,00,000 FD, 36 months; assistant asks: {turn['reply']!r}; missing {p['missing_fields']}")

    # 0:25–0:55 receipt evidence, confirm once
    doc = upload("fd_receipt.txt")
    expect(doc["status"] in ("processed", "needs_review"), f"receipt not processed: {doc['status']}")
    view = ok(client.post(f"/v1/proposals/{p['id']}/documents", json={"document_id": doc["id"]}, headers=_h("ramesh")))
    bank_from = "receipt"
    if not view["can_confirm"] and view["missing_fields"] == ["institution_name"]:
        turn = ok(client.post("/v1/chat/messages", json={"text": "It is with Sagar Bharat Bank"}, headers=_h("ramesh")))
        view, bank_from = turn["proposal"], "owner's reply"
    expect(view["can_confirm"], f"draft still not confirmable: missing {view['missing_fields']}, conflicts {view.get('conflicts')}")
    by = {f["field"]: f for f in view["fields"]}
    expect(any(s["source"] == "document" for s in by["opened_on"]["sources"]), "opening date should come from the receipt")
    key = str(uuid.uuid4())
    first = ok(client.post(f"/v1/proposals/{p['id']}/confirm", headers={**_h("ramesh"), "Idempotency-Key": key}))
    again = ok(client.post(f"/v1/proposals/{p['id']}/confirm", headers={**_h("ramesh"), "Idempotency-Key": key}))
    expect(again["replayed"] and again["asset_id"] == first["asset_id"], "a retried confirmation must not create a second record")
    out["fd_asset_id"] = first["asset_id"]
    step("0:25", f"receipt added evidence (opened {by['opened_on']['raw']}); bank {by['institution_name']['raw']} from the {bank_from}; "
                 "confirmed once (retry replayed)")  # fmt: skip

    # 0:55–1:20 inventory, graph, findings
    if settle:
        settle()
    ws = ok(client.get("/v1/workspace", headers=_h("ramesh")))
    assets = ok(client.get("/v1/assets", headers=_h("ramesh")))["assets"]
    expect(len(assets) == 7, f"expected 7 assets after confirmation, found {len(assets)}")
    kinds = {(f["asset"], f["kind"]) for f in ws["findings"]}
    expect(any(k == "unknown_nomination" for _, k in kinds) and any(k == "stale_source" for _, k in kinds), f"findings missing: {kinds}")
    expect(any(n["id"] == f"asset:{first['asset_id']}" for n in ws["graph"]["nodes"]), "new FD missing from the graph")
    step("0:55", f"7 assets; graph has the new FD; findings: {sorted({k for _, k in kinds})}")

    # 1:20–1:45 passport and Priya's preview
    owner_pv = ok(client.post("/v1/passports", json={}, headers=_h("ramesh")), 201)
    priya_pv = ok(client.post("/v1/passports", json={"recipient_user_id": str(fixture_id("priya"))}, headers=_h("ramesh")), 201)
    preview = ws["access_preview"][0]
    expect(preview["pre_handover"]["assets"] == [], "Priya must see no assets before handover")
    expect(
        client.get(f"/v1/passports/{priya_pv['id']}/download", headers=_h("priya")).status_code == 404,
        "Priya cannot download before release",
    )
    step("1:20", f"passport v{owner_pv['version']} ({owner_pv['assets']} assets) + Priya's export ({priya_pv['assets']} assets); "
                 f"before handover she sees only {preview['pre_handover']['sections']}")  # fmt: skip

    # 1:45–2:20 handover: held, corrected, two reviewers, scoped release
    case = ok(client.post("/v1/handovers", json={"owner_user_id": str(fixture_id("ramesh"))}, headers=_h("priya")), 201)["id"]
    evidence(case, "death_certificate_mismatch.txt")
    ok(client.post(f"/v1/handovers/{case}/submit", headers=_h("priya")))
    review = ok(client.get(f"/v1/handovers/{case}", headers=_h("reviewer_anil")))
    expect(any(c["result"] == "mismatch" for c in review["checks"]), "the mismatched certificate should be flagged")
    ok(
        client.post(
            f"/v1/handovers/{case}/decisions", json={"decision": "hold", "note": "Name on certificate differs"}, headers=_h("reviewer_anil")
        )
    )
    blocked = client.post(f"/v1/handovers/{case}/decisions", json={"decision": "approve"}, headers=_h("reviewer_meera"))
    expect(blocked.status_code == 409, "a held case must not accept approvals")
    evidence(case, "death_certificate.txt")
    ok(client.post(f"/v1/handovers/{case}/decisions", json={"decision": "resume"}, headers=_h("reviewer_anil")))
    one = ok(client.post(f"/v1/handovers/{case}/decisions", json={"decision": "approve"}, headers=_h("reviewer_anil")))
    expect(one["state"] != "RELEASED", "one approval must not release")
    two = ok(client.post(f"/v1/handovers/{case}/decisions", json={"decision": "approve"}, headers=_h("reviewer_meera")))
    expect(two["state"] == "RELEASED", f"two approvals should release, state {two['state']}")
    fam = ok(client.get("/v1/family", headers=_h("priya")))["people"][0]["after_release"]
    labels = {a["label"] for a in fam["assets"]}
    expect("SGD savings account" not in labels and len(labels) == 5, f"scoped release wrong: {labels}")
    out["case_id"] = case
    step("1:45", f"mismatch → HOLD (approval refused) → corrected → 2 reviewers → RELEASED; Priya sees {len(labels)} granted assets")

    # 2:20–2:45 Priya: passport and cited checklist
    pdf = client.get(f"/v1/passports/{priya_pv['id']}/download", headers=_h("priya"))
    expect(pdf.status_code == 200 and pdf.content[:4] == b"%PDF", "Priya should download her passport export now")
    claim = ok(client.post("/v1/family/claims", json={"asset_id": str(fixture_id("savings"))}, headers=_h("priya")))
    refs = [p_["citation"]["ref"] for p_ in claim["paths"]]
    expect(refs and all("@" in r and "§" in r for r in refs), f"checklist paths must cite a section: {refs}")
    step("2:20", f"Priya downloaded her PDF ({len(pdf.content)} bytes); checklist cites {', '.join(refs)}")

    # 2:45–3:00 analytics and classifier
    an = ok(client.get("/v1/analytics", headers=_h("ramesh")))
    expect(len(an.get("flags", [])) > 0, "analytics should show review flags (make demo-reset loads the synthetic feed)")
    caps = {c["name"]: c for c in ok(client.get("/v1/system/capabilities"))["capabilities"]}
    step(
        "2:45",
        f"analytics: {len(an.get('flags', []))} review flags; classifier {caps['classification']['state']}: {caps['classification']['detail']}",
    )
    log(f'        close: "{CLOSING_LINE}"')

    if optional:
        ok(client.post("/v1/recovery/opt-in", headers=_h("ramesh")))
        rec = ok(client.post("/v1/recovery/search", json={}, headers=_h("ramesh")))
        top = rec["matches"][0] if rec["matches"] else None
        expect(
            top is not None and top["record"]["amount_minor"] == 64_200_000,
            "optional: the ₹6.42 lakh registry record should be the top match",
        )
        expect(len(ok(client.get("/v1/assets", headers=_h("ramesh")))["assets"]) == 7, "optional: a match must not become an asset")
        step(
            "extra",
            f"optional recovery: {top['record']['id']} ₹6,42,000, {top['score']}/100 {top['band']}; {len(rec['ruled_out'])} ruled out; still 7 assets",
        )

    out["seconds"] = round(time.monotonic() - started, 1)
    log(f"rehearsal passed in {out['seconds']} s of API time (the spoken demo is 3 minutes)")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="ReclaimAI three-minute demo")
    parser.add_argument("command", choices=["reset", "check", "rehearse"])
    parser.add_argument("--api", default=None, help="API base URL (default: API_URL or http://localhost:8100)")
    parser.add_argument("--no-optional", action="store_true", help="skip the optional recovery step")
    args = parser.parse_args()
    if args.command == "reset":
        counts = reset()
        print("demo reset: " + " ".join(f"{k}={v}" for k, v in counts.items()))
        return
    import httpx

    base = args.api or os.environ.get("API_URL") or "http://localhost:8100"
    with httpx.Client(base_url=base, timeout=120) as client:
        try:
            if args.command == "check":
                problems = check(client)
                for p in problems:
                    print(f"  ✗ {p}")
                print("ready for the demo" if not problems else "not ready")
                sys.exit(1 if problems else 0)
            rehearse(client, optional=not args.no_optional)
        except httpx.HTTPError as err:
            print(f"cannot reach the API at {base} ({type(err).__name__}); start it with make dev")
            sys.exit(2)
        except RehearsalFailed as err:
            print("✗ rehearsal failed:", err.args[0])
            sys.exit(1)


if __name__ == "__main__":
    main()
