"""Policy retrieval over a curated, dated corpus.

- LocalPolicyIndex (local): reads fixtures/policies, splits documents into §-sections and
  ranks them by keyword overlap. Deterministic.
- VertexSearchRetriever (cloud): Vertex AI Search over a datastore of the same documents,
  with metadata filters in the request.

Applicability (approved, in force on the date, right jurisdiction, institution and asset
type) is enforced here in code for both, even when the index already filtered: the server
never trusts an index or a passage to decide what applies. The corpus holds no family data.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from reclaim_api.providers.base import CapabilityStatus, ProviderUnavailable

_META = re.compile(r"^<!--meta (\{.*\}) -->\s*$", re.MULTILINE)
_SECTION = re.compile(r"^## (§\d+) (.+)$", re.MULTILINE)
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "is", "for", "on", "by", "what", "do", "i", "my", "how", "be", "are", "with"}


@dataclass(frozen=True)
class PolicyMeta:
    doc_id: str
    version: str
    title: str
    institution: str
    asset_types: tuple[str, ...]
    jurisdiction: str
    effective_from: date
    effective_to: date | None
    source_url: str
    review_status: str
    fictional: bool

    @staticmethod
    def parse(raw: dict[str, Any]) -> "PolicyMeta":
        """Raises KeyError/ValueError when citation metadata is incomplete: such a document
        is never served, because it could not be cited properly."""
        for key in ("doc_id", "version", "title", "institution", "jurisdiction", "effective_from", "source_url", "review_status"):
            if raw.get(key) in (None, ""):
                raise ValueError(f"policy metadata missing {key}")
        return PolicyMeta(
            doc_id=raw["doc_id"], version=str(raw["version"]), title=raw["title"], institution=raw["institution"],
            asset_types=tuple(raw["asset_types"]), jurisdiction=raw["jurisdiction"],
            effective_from=date.fromisoformat(raw["effective_from"]),
            effective_to=date.fromisoformat(raw["effective_to"]) if raw.get("effective_to") else None,
            source_url=raw["source_url"], review_status=raw["review_status"], fictional=bool(raw.get("fictional")),
        )  # fmt: skip


@dataclass(frozen=True)
class Passage:
    meta: PolicyMeta
    section: str  # "§4"
    heading: str
    text: str
    score: float = 0.0

    @property
    def ref(self) -> str:
        return f"{self.meta.doc_id}@{self.meta.version} {self.section}"

    def citation(self) -> dict[str, Any]:
        return {"ref": self.ref, "doc_id": self.meta.doc_id, "version": self.meta.version, "section": self.section,
                "heading": self.heading, "title": self.meta.title, "source_url": self.meta.source_url,
                "effective_from": self.meta.effective_from.isoformat(), "fictional": self.meta.fictional}  # fmt: skip


@dataclass(frozen=True)
class Filters:
    jurisdiction: str
    institution: str | None = None
    asset_type: str | None = None
    as_of: date = field(default_factory=date.today)


def applicable(meta: PolicyMeta, f: Filters) -> bool:
    return (
        meta.review_status == "approved"
        and meta.jurisdiction == f.jurisdiction
        and (f.institution is None or meta.institution in (f.institution, "*"))
        and (f.asset_type is None or f.asset_type in meta.asset_types)
        and meta.effective_from <= f.as_of
        and (meta.effective_to is None or f.as_of < meta.effective_to)
    )


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def load_corpus(directory: Path) -> list[Passage]:
    passages = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta_match = _META.search(text)
        if not meta_match:
            continue  # README and other files without metadata are not policies
        meta = PolicyMeta.parse(json.loads(meta_match.group(1)))
        heads = list(_SECTION.finditer(text))
        for i, h in enumerate(heads):
            body = text[h.end() : heads[i + 1].start() if i + 1 < len(heads) else len(text)].strip()
            passages.append(Passage(meta, h.group(1), h.group(2).strip(), body))
    return passages


class LocalPolicyIndex:
    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._passages: list[Passage] | None = None

    def _all(self) -> list[Passage]:
        if self._passages is None:
            self._passages = load_corpus(self._dir)
        return self._passages

    def search(self, *, query: str, filters: Filters, limit: int = 8) -> list[Passage]:
        q = _terms(query)
        hits = []
        for p in self._all():
            if not applicable(p.meta, filters):
                continue
            overlap = len(q & _terms(p.heading + " " + p.text))
            if overlap or not q:
                hits.append(Passage(p.meta, p.section, p.heading, p.text, score=float(overlap)))
        return sorted(hits, key=lambda p: (-p.score, p.meta.doc_id, p.section))[:limit]

    def sections(self, *, doc_id: str, filters: Filters) -> list[Passage]:
        """All applicable sections of one document (the checklist rules read whole policies)."""
        return [p for p in self._all() if p.meta.doc_id == doc_id and applicable(p.meta, filters)]

    def status(self) -> CapabilityStatus:
        n = len({(p.meta.doc_id, p.meta.version) for p in self._all()})
        return CapabilityStatus("retrieval", "fixture", "local-policy-index", f"{n} synthetic policy documents; keyword ranking")


class VertexSearchRetriever:
    """Vertex AI Search. Datastore documents carry the same metadata as struct_data and one
    section per document (id "<doc_id>@<version>-<section number>")."""

    def __init__(self, *, project: str | None, location: str | None, datastore: str | None, client: Any = None) -> None:
        self._project, self._location, self._datastore = project, location, datastore
        self._client = client
        self._last_ok: bool | None = None
        self._last_error: str | None = None

    def _missing(self) -> list[str]:
        return [k for k, v in {"GCP_PROJECT": self._project, "SEARCH_LOCATION": self._location, "SEARCH_DATASTORE_ID": self._datastore}.items() if not v]

    def _get_client(self) -> Any:
        if self._client is None:
            from google.cloud import discoveryengine_v1 as de

            endpoint = None if self._location == "global" else f"{self._location}-discoveryengine.googleapis.com"
            self._client = de.SearchServiceClient(client_options={"api_endpoint": endpoint} if endpoint else None)
        return self._client

    def search(self, *, query: str, filters: Filters, limit: int = 8) -> list[Passage]:
        if missing := self._missing():
            raise ProviderUnavailable("retrieval unavailable: missing config " + ", ".join(missing))
        from google.api_core import exceptions as gexc
        from google.auth.exceptions import GoogleAuthError
        from google.cloud import discoveryengine_v1 as de

        client = self._get_client()
        expr = [f'jurisdiction: ANY("{filters.jurisdiction}")', 'review_status: ANY("approved")']
        if filters.institution:
            expr.append(f'institution: ANY("{filters.institution}", "*")')
        if filters.asset_type:
            expr.append(f'asset_types: ANY("{filters.asset_type}")')
        request = de.SearchRequest(
            serving_config=client.serving_config_path(self._project, self._location, self._datastore, "default_config"),
            query=query, page_size=limit, filter=" AND ".join(expr),
        )  # fmt: skip
        try:
            response = client.search(request=request)
        except (gexc.GoogleAPIError, GoogleAuthError) as err:
            self._last_ok, self._last_error = False, type(err).__name__
            raise ProviderUnavailable(f"retrieval unavailable ({type(err).__name__})") from None
        self._last_ok = True
        out = []
        for rank, result in enumerate(response.results):
            data = dict(result.document.struct_data)
            try:
                meta = PolicyMeta.parse(data)
            except (KeyError, ValueError):
                continue  # a document without complete metadata is never served
            if not applicable(meta, filters):
                continue
            out.append(Passage(meta, data.get("section", "§?"), data.get("heading", ""), data.get("text", ""), score=float(limit - rank)))
        return out

    def sections(self, *, doc_id: str, filters: Filters) -> list[Passage]:
        return [p for p in self.search(query=doc_id, filters=filters, limit=50) if p.meta.doc_id == doc_id]

    def status(self) -> CapabilityStatus:
        if missing := self._missing():
            return CapabilityStatus("retrieval", "unavailable", "vertex-ai-search", "missing config: " + ", ".join(missing))
        if self._last_ok:
            return CapabilityStatus("retrieval", "live", "vertex-ai-search", "last search succeeded")
        if self._last_ok is False:
            return CapabilityStatus("retrieval", "unavailable", "vertex-ai-search", f"last search failed ({self._last_error})")
        return CapabilityStatus("retrieval", "unavailable", "vertex-ai-search", "configured; not yet called, so unverified")
