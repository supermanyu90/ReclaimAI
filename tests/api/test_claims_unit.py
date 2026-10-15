from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from reclaim_api.claims import rules
from reclaim_api.providers.base import ProviderUnavailable
from reclaim_api.providers.retrieval import Filters, LocalPolicyIndex, VertexSearchRetriever

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "policies"
TODAY = date(2026, 10, 15)
RS = rules.RULES[("sbb-deceased-claims", "2")]


def keys(choices):
    return [c.path.key for c in choices]


# --- retrieval filters ----------------------------------------------------------------------


def test_wrong_jurisdiction_unapproved_and_outdated_documents_are_excluded():
    index = LocalPolicyIndex(CORPUS)
    in_today = {p.meta.doc_id + "@" + p.meta.version for p in index.search(query="", filters=Filters("IN", as_of=TODAY), limit=50)}
    assert in_today == {"sbb-deceased-claims@2", "sbb-faq@3"}  # no v1, no UK, no SG draft
    assert index.search(query="", filters=Filters("SG", "Merlion Harbour Bank", as_of=TODAY)) == []  # unreviewed draft
    assert {p.meta.doc_id for p in index.search(query="", filters=Filters("GB", as_of=TODAY))} == {"uk-bereavement"}


def test_institution_and_asset_type_scope_retrieval():
    index = LocalPolicyIndex(CORPUS)
    assert index.search(query="claim", filters=Filters("IN", "Kaveri Mutual Fund", "mutual_fund", TODAY)) == []
    assert index.search(query="claim", filters=Filters("IN", "Sagar Bharat Bank", "pension", TODAY)) == []


# --- rules -------------------------------------------------------------------------------------


def test_rules_branch_without_assuming_succession_certificate():
    choose = rules.choose
    assert keys(choose(RS, nomination="recorded", survivorship="no", dispute="no", balance_minor=10)[0]) == ["nominee"]
    assert keys(choose(RS, nomination="not_recorded", survivorship="no", dispute="no", balance_minor=150_000_000)[0]) == ["heirs_small"]
    assert keys(choose(RS, nomination="not_recorded", survivorship="no", dispute="no", balance_minor=150_000_001)[0]) == ["heirs_large"]
    assert keys(choose(RS, nomination="recorded", survivorship="yes", dispute="no", balance_minor=None)[0]) == ["survivor"]
    assert keys(choose(RS, nomination="recorded", survivorship="yes", dispute="yes", balance_minor=None)[0]) == ["disputed"]
    paths, unknowns = choose(RS, nomination="not_recorded", survivorship="no", dispute="no", balance_minor=None)
    assert keys(paths) == ["heirs_small", "heirs_large"] and all(c.condition for c in paths) and unknowns


# --- Vertex AI Search adapter (fake client) -------------------------------------------------------


def _doc(**overrides):
    data = {"doc_id": "sbb-deceased-claims", "version": "2", "title": "t", "institution": "Sagar Bharat Bank", "asset_types": ["savings"],
            "jurisdiction": "IN", "effective_from": "2025-04-01", "effective_to": None, "source_url": "https://example.invalid/x",
            "review_status": "approved", "fictional": True, "section": "§2", "heading": "Registered nominee", "text": "nominee text"} | overrides  # fmt: skip
    return NS(document=NS(struct_data=data))


class FakeSearch:
    def __init__(self, results=None, error=None):
        self.results, self.error, self.requests = results or [], error, []

    def serving_config_path(self, project, location, datastore, config):
        return f"projects/{project}/locations/{location}/dataStores/{datastore}/servingConfigs/{config}"

    def search(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return NS(results=self.results)


def test_vertex_search_sends_filters_and_still_refilters_results():
    fake = FakeSearch([_doc(), _doc(jurisdiction="GB"), _doc(review_status="draft"), _doc(effective_to="2025-01-01"), _doc(version=None)])
    r = VertexSearchRetriever(project="demo", location="global", datastore="policies", client=fake)
    hits = r.search(query="nominee", filters=Filters("IN", "Sagar Bharat Bank", "savings", TODAY))
    assert [h.ref for h in hits] == ["sbb-deceased-claims@2 §2"]  # index results that don't apply are dropped here too
    f = fake.requests[0].filter
    assert 'jurisdiction: ANY("IN")' in f and 'review_status: ANY("approved")' in f and 'asset_types: ANY("savings")' in f
    assert r.status().state == "live"


def test_vertex_search_unconfigured_or_failing_is_unavailable():
    from google.api_core import exceptions as gexc

    with pytest.raises(ProviderUnavailable):
        VertexSearchRetriever(project=None, location="global", datastore="p", client=FakeSearch()).search(query="x", filters=Filters("IN"))
    r = VertexSearchRetriever(project="demo", location="global", datastore="p", client=FakeSearch(error=gexc.PermissionDenied("no access to projects/demo")))
    with pytest.raises(ProviderUnavailable) as exc:
        r.search(query="x", filters=Filters("IN"))
    assert "projects/demo" not in str(exc.value) and r.status().state == "unavailable"


def test_citation_refs_must_resolve_to_exactly_one_retrieved_section():
    from reclaim_api.claims.copilot import resolve_refs

    allowed = {"sbb-faq@3 §1", "sbb-deceased-claims@2 §2", "sbb-deceased-claims@2 §4"}
    assert resolve_refs(["sbb-faq@3"], allowed) == ["sbb-faq@3 §1"]
    assert resolve_refs(["sbb-deceased-claims@2"], allowed) == []  # ambiguous: not a citation
    assert resolve_refs(["[sbb-deceased-claims@2 §4]", "made-up@1 §9"], allowed) == ["sbb-deceased-claims@2 §4"]


# --- Vertex AI Search export ---------------------------------------------------------------------


def test_policy_export_round_trips_through_the_vertex_adapter():
    import json as _json
    import re as _re

    from reclaim_api.claims import search_export

    records = search_export.export(CORPUS)
    local = LocalPolicyIndex(CORPUS)._all()
    approved = [p for p in local if p.meta.review_status == "approved"]
    assert len(records) == len(approved) and len(records) < len(local)  # drafts are not indexed
    ids = [r["id"] for r in records]
    assert len(set(ids)) == len(ids) and all(_re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", i) for i in ids)
    _json.dumps(records)  # JSONL-serialisable
    fake = FakeSearch([NS(document=NS(struct_data=r["structData"])) for r in records])
    r = VertexSearchRetriever(project="demo", location="global", datastore="policies", client=fake)
    f = Filters("IN", "Sagar Bharat Bank", "savings", TODAY)
    via_vertex = {(h.ref, h.text) for h in r.search(query="x", filters=f, limit=len(records))}
    via_local = {(h.ref, h.text) for h in LocalPolicyIndex(CORPUS).search(query="", filters=f, limit=100)}
    assert via_vertex == via_local  # same applicable sections, same text, same citation refs
