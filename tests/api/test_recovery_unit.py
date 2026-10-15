"""Prompt 13: blocking, explicit features and a heuristic score (not a probability)."""

import json
from datetime import date
from pathlib import Path

import pytest

from reclaim_api.recovery import matching as mt

ROOT = Path(__file__).resolve().parents[2] / "fixtures"
REG = mt.load_registry(ROOT / "recovery" / "registry.json")
PROFILE = {
    "names": ["Ramesh Kumar Patel", "Ramesh Patel"],
    "date_of_birth": date(1958, 3, 14),
    "addresses": json.loads((ROOT / "demo.json").read_text())["recovery_profiles"][0]["addresses"],
}


def run(rid: str) -> dict:
    return mt.evaluate(REG.records[rid], PROFILE)


def test_only_an_authorized_synthetic_registry_is_read(tmp_path):
    raw = json.loads((ROOT / "recovery" / "registry.json").read_text())
    for broken in ({**raw, "synthetic": False}, {**raw, "authorized_use": "anything"}, {k: v for k, v in raw.items() if k != "synthetic"}):
        (tmp_path / "r.json").write_text(json.dumps(broken))
        with pytest.raises(mt.RegistryNotAuthorized):
            mt.load_registry(tmp_path / "r.json")


def test_blocking_keeps_unrelated_records_out():
    candidates = {rid for rid, r in REG.records.items() if mt.is_candidate(r, PROFILE)}
    assert candidates == {"DR-0001", "DR-0002", "DR-0003", "DR-0004", "DR-0005"}  # other surnames never compared


def test_the_642_lakh_deposit_has_strong_evidence_with_explicit_contributions():
    r = run("DR-0001")
    assert REG.records["DR-0001"]["amount_minor"] == 64_200_000 and REG.records["DR-0001"]["currency"] == "INR"
    assert r["blocked_by"] == [] and r["score"] >= mt.STRONG and r["band"] == "strong evidence"
    feats = {f["key"]: f for f in r["features"]}
    assert feats["date_of_birth"]["result"] == "match" and feats["address"]["result"] == "match"
    assert feats["name"]["result"] == "partial" and "first initial" in feats["name"]["detail"]  # "R K" is not "Ramesh"
    assert r["score"] == round(sum(f["points"] for f in r["features"]))  # the score is exactly the listed points


def test_same_name_with_conflicting_dob_is_blocked():
    r = run("DR-0002")
    assert {f["key"]: f["result"] for f in r["features"]}["name"] == "match"
    assert r["blocked_by"] == ["date_of_birth_conflict"] and r["score"] is None


def test_same_name_alone_is_weak_evidence():
    r = run("DR-0003")
    assert r["score"] < mt.PARTIAL and r["band"] == "weak evidence"
    assert {f["key"]: f["result"] for f in r["features"]}["date_of_birth"] == "unknown"  # unknown, not a match
    assert max(f["points"] for f in r["features"] if f["key"] == "name") <= mt.NAME_POINTS


def test_different_first_name_at_same_address_and_dob_is_blocked_not_related():
    r = run("DR-0004")  # Suresh at the same address, same DOB: no relationship is inferred, it is simply not him
    assert r["blocked_by"] == ["first_name_conflict"] and r["score"] is None
    assert not any(word in json.dumps(r).lower() for word in ("brother", "relative", "family", "related"))


def test_similar_surname_with_conflicting_dob_is_blocked():
    assert run("DR-0005")["blocked_by"] == ["date_of_birth_conflict"]


@pytest.mark.parametrize(("a", "b", "low", "high"), [("MARTHA", "MARHTA", 0.96, 0.97), ("PATEL", "PATIL", 0.85, 0.95), ("SURESH", "RAMESH", 0.7, 0.8)])
def test_jaro_winkler_reference_values(a, b, low, high):
    assert low <= mt.jaro_winkler(a, b) <= high


def test_soundex_reference_values():
    assert mt.soundex("Robert") == mt.soundex("Rupert") == "R163" and mt.soundex("Tymczak") == "T522"
