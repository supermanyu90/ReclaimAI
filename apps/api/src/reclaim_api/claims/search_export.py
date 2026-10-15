"""Export the policy corpus for a Vertex AI Search datastore (structured data, JSONL).

One document per § section, carrying the same citation metadata the local index uses, so
VertexSearchRetriever can rebuild a Passage and re-check applicability in code. Only
approved documents are exported; drafts are never served, so they are not indexed either.

    reclaim-policy-export --out build/policies.jsonl
    # then: gcloud storage cp build/policies.jsonl gs://<bucket>/search/ and import it (docs/DEPLOYMENT.md)
"""

import argparse
import json
import re
from pathlib import Path

from reclaim_api.providers.retrieval import Passage, load_corpus
from reclaim_api.seed import default_fixture_path

_ID_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")


def document_id(p: Passage) -> str:
    """Datastore IDs allow only [a-zA-Z0-9_-]; the citation ref stays in struct_data."""
    return _ID_UNSAFE.sub("_", f"{p.meta.doc_id}_v{p.meta.version}_s{p.section.lstrip('§')}")


def to_record(p: Passage) -> dict:
    m = p.meta
    data = {
        "doc_id": m.doc_id, "version": m.version, "title": m.title, "institution": m.institution,
        "asset_types": list(m.asset_types), "jurisdiction": m.jurisdiction,
        "effective_from": m.effective_from.isoformat(), "source_url": m.source_url,
        "review_status": m.review_status, "fictional": m.fictional,
        "section": p.section, "heading": p.heading, "text": p.text, "ref": p.ref,
    }  # fmt: skip
    if m.effective_to:
        data["effective_to"] = m.effective_to.isoformat()
    return {"id": document_id(p), "structData": data}


def export(directory: Path) -> list[dict]:
    return [to_record(p) for p in load_corpus(directory) if p.meta.review_status == "approved"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policies", type=Path, default=default_fixture_path().parent / "policies")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    records = export(args.policies)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    print(f"exported documents={len(records)} out={args.out}")


if __name__ == "__main__":
    main()
