"""Registry matching: block first, then explicit features, then a heuristic score.

The score is a sum of points for the evidence listed with it. It is not a calibrated
probability that a record belongs to anyone, and a name alone can earn at most 35 of 100.
Conflicts (a different date of birth, a different first name or surname) block a record
outright. Nothing here infers a family relationship or an entitlement.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

NAME_POINTS, DOB_POINTS, PINCODE_POINTS, LINE_POINTS, PERIOD_POINTS, CITY_POINTS = 35, 35, 15, 5, 5, 5
STRONG, PARTIAL = 70, 45
FIRST_NAME_SAME = 0.95  # Jaro-Winkler; below this two full first names are different names
SCORE_LABEL = "Heuristic match score: points for the evidence listed. It is not a probability that this record is yours."
_TOKEN = re.compile(r"[A-Z0-9]+")


class RegistryNotAuthorized(RuntimeError):
    pass


@dataclass(frozen=True)
class Registry:
    name: str
    version: str
    records: dict[str, dict[str, Any]]


def load_registry(path: Path) -> Registry:
    """Only a registry marked synthetic and authorized for demo matching is ever read."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("synthetic") is not True or raw.get("authorized_use") != "demo_matching":
        raise RegistryNotAuthorized("registry is not an authorized synthetic demo registry")
    return Registry(raw["registry"], raw["version"], {r["id"]: r for r in raw["records"]})


# --- string helpers ------------------------------------------------------------------------------


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.upper())


def soundex(word: str) -> str:
    codes = {
        **dict.fromkeys("BFPV", "1"),
        **dict.fromkeys("CGJKQSXZ", "2"),
        **dict.fromkeys("DT", "3"),
        "L": "4",
        **dict.fromkeys("MN", "5"),
        "R": "6",
    }
    word = re.sub(r"[^A-Z]", "", word.upper())
    if not word:
        return ""
    out, last = word[0], codes.get(word[0], "")
    for ch in word[1:]:
        code = codes.get(ch, "")
        if code and code != last:
            out += code
        if ch not in "HW":
            last = code
    return (out + "000")[:4]


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(max(len(a), len(b)) // 2 - 1, 0)
    a_hit, b_hit = [False] * len(a), [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not b_hit[j] and b[j] == ch:
                a_hit[i] = b_hit[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    a_m = [ch for ch, hit in zip(a, a_hit, strict=True) if hit]
    b_m = [ch for ch, hit in zip(b, b_hit, strict=True) if hit]
    transpositions = sum(x != y for x, y in zip(a_m, b_m, strict=True)) / 2
    jaro = (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4], strict=False):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def _token_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if len(a) == 1 or len(b) == 1:
        return 0.7 if a[0] == b[0] else 0.0  # an initial is compatible, not the same as a full match
    return jaro_winkler(a, b)


def _split(name: str) -> tuple[str, list[str], str]:
    t = tokens(name)
    return (t[0], t[1:-1], t[-1]) if len(t) > 1 else (t[0] if t else "", [], "")


# --- features ------------------------------------------------------------------------------------


def name_feature(record_name: str, variants: list[str]) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    r_first, r_mid, r_last = _split(record_name)
    for variant in variants:
        p_first, p_mid, p_last = _split(variant)
        s_last, s_first = _token_score(r_last, p_last), _token_score(r_first, p_first)
        parts = [(0.5, s_last), (0.35, s_first)]
        if r_mid and p_mid:
            parts.append((0.15, _token_score(r_mid[0], p_mid[0])))
        sim = sum(w * s for w, s in parts) / sum(w for w, _ in parts)
        if best is None or sim > best["similarity"]:
            best = {"similarity": round(sim, 3), "variant": variant, "surname": s_last, "first": s_first}
    assert best is not None
    conflicts = []
    # Two different full first names are a conflict, however alike they look ("Suresh"/"Ramesh").
    if best["first"] < 0.7 or (0.7 < best["first"] < FIRST_NAME_SAME):
        conflicts.append("first_name_conflict")
    if best["surname"] < 0.85:
        conflicts.append("surname_conflict")
    how = []
    how.append(
        "same surname" if best["surname"] == 1 else ("surname spelled differently" if best["surname"] >= 0.85 else "different surname")
    )
    how.append(
        "same first name"
        if best["first"] == 1
        else (
            "matching first initial only"
            if best["first"] == 0.7
            else ("first name spelled slightly differently" if best["first"] >= FIRST_NAME_SAME else "different first name")
        )
    )
    return {
        "key": "name", "label": "Name", "registry_value": record_name, "profile_value": best["variant"],
        "result": "conflict" if conflicts else ("match" if best["similarity"] == 1 else "partial"),
        "similarity": best["similarity"], "points": 0 if conflicts else round(best["similarity"] * NAME_POINTS, 1), "max_points": NAME_POINTS,
        "detail": f"Registry name {record_name!r} compared with {best['variant']!r}: {', '.join(how)}.", "conflicts": conflicts,
    }  # fmt: skip


def dob_feature(record_dob: str | None, profile_dob: date | None) -> dict[str, Any]:
    base = {"key": "date_of_birth", "label": "Date of birth", "registry_value": record_dob,
            "profile_value": profile_dob.isoformat() if profile_dob else None, "max_points": DOB_POINTS, "conflicts": []}  # fmt: skip
    if not record_dob or not profile_dob:
        return base | {
            "result": "unknown",
            "points": 0,
            "detail": "No date of birth to compare, so this neither supports nor rules out the record.",
        }
    if date.fromisoformat(record_dob) == profile_dob:
        return base | {
            "result": "match",
            "points": DOB_POINTS,
            "detail": "Date of birth in the registry record is the same as in the profile.",
        }
    return base | {"result": "conflict", "points": 0, "conflicts": ["date_of_birth_conflict"],
                   "detail": "Date of birth in the registry record differs from the profile; this record is ruled out."}  # fmt: skip


def address_feature(record: dict[str, Any], addresses: list[dict[str, Any]]) -> dict[str, Any]:
    r_tokens, r_city = set(tokens(record["address"])), (tokens(record["address"]) or [""])[-1]
    active = date.fromisoformat(record["last_activity"]) if record.get("last_activity") else None
    best: dict[str, Any] = {"points": 0, "result": "no_match", "profile_value": None,
                            "detail": "No address in the profile shares a postcode or city with the registry record."}  # fmt: skip
    for a in addresses:
        p_tokens = set(tokens(a["line"]))
        overlap = round(len(r_tokens & p_tokens) / len(r_tokens | p_tokens), 2) if r_tokens | p_tokens else 0.0
        lived = (
            (date.fromisoformat(a["from"]) if a.get("from") else date.min)
            <= (active or date.min)
            <= (date.fromisoformat(a["to"]) if a.get("to") else date.max)
        )
        if record.get("pincode") and record["pincode"] == a.get("pincode"):
            pts = PINCODE_POINTS + (LINE_POINTS if overlap >= 0.5 else 0) + (PERIOD_POINTS if active and lived else 0)
            detail = (
                f"Same postcode {record['pincode']}"
                + (", same street address" if overlap >= 0.5 else "")
                + (", and the profile lists this address for the time the account was last active" if active and lived else "")
                + "."
            )
            cand = {
                "points": pts,
                "result": "match" if pts == PINCODE_POINTS + LINE_POINTS + PERIOD_POINTS else "partial",
                "detail": detail,
            }
        elif r_city and r_city in p_tokens:
            cand = {"points": CITY_POINTS, "result": "partial", "detail": f"Same city ({r_city.title()}) but a different postcode."}
        else:
            continue
        if cand["points"] > best["points"]:
            best = cand | {"profile_value": f"{a['line']} {a.get('pincode', '')}".strip(), "line_overlap": overlap}
    return {"key": "address", "label": "Address", "registry_value": f"{record['address']} {record.get('pincode', '')}".strip(),
            "max_points": PINCODE_POINTS + LINE_POINTS + PERIOD_POINTS, "conflicts": []} | best  # fmt: skip


# --- blocking and scoring ----------------------------------------------------------------------------


def is_candidate(record: dict[str, Any], profile: dict[str, Any]) -> bool:
    """Blocking: same surname sound, and either a shared postcode area or the same birth year.
    Records outside the block are never compared or shown."""
    surnames = {soundex(_split(v)[2]) for v in profile["names"]}
    if soundex(_split(record["holder_name"])[2]) not in surnames:
        return False
    areas = {a["pincode"][:3] for a in profile["addresses"] if a.get("pincode")}
    same_area = bool(record.get("pincode")) and record["pincode"][:3] in areas
    dob = profile.get("date_of_birth")
    same_year = bool(record.get("date_of_birth")) and dob is not None and record["date_of_birth"][:4] == str(dob.year)
    return same_area or same_year


def band(score: int | None) -> str | None:
    if score is None:
        return None
    return "strong evidence" if score >= STRONG else ("partial evidence" if score >= PARTIAL else "weak evidence")


def evaluate(record: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    features = [name_feature(record["holder_name"], profile["names"]), dob_feature(record.get("date_of_birth"), profile.get("date_of_birth")),
                address_feature(record, profile["addresses"])]  # fmt: skip
    blocked_by = [c for f in features for c in f["conflicts"]]
    score = None if blocked_by else min(100, round(sum(f["points"] for f in features)))
    return {"features": features, "blocked_by": blocked_by, "score": score, "band": band(score)}
