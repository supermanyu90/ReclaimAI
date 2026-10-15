"""Document text extraction.

- LocalTextExtractor (local mode): plain text and text-layer PDFs only. No OCR: an image
  or scanned PDF is reported as unsupported and goes to human review.
- DocumentAIProcessor (cloud): Document AI OCR, plus an optional configured extractor
  whose entities carry their own confidence, page and bounding box.

Both return the full text with page offsets so every extracted value can point to a
page and character span. Confidence here is extraction (OCR) confidence only.
"""

import io
from dataclasses import dataclass, field
from typing import Any

from reclaim_api.providers.base import CapabilityStatus, ProviderUnavailable

TEXT_TYPES = {"text/plain"}
PDF_TYPES = {"application/pdf"}
IMAGE_TYPES = {"image/png", "image/jpeg", "image/tiff", "image/webp"}


class UnsupportedDocument(ValueError):
    """The available processor cannot read this document. Message is log-safe."""


@dataclass(frozen=True)
class Page:
    number: int
    start: int  # character offsets into ExtractedText.text
    end: int
    confidence: float | None = None


@dataclass(frozen=True)
class Entity:
    type: str
    mention: str
    confidence: float | None
    page: int | None
    start: int | None
    end: int | None
    bounding_box: list[dict[str, float]] | None = None


@dataclass
class ExtractedText:
    text: str
    pages: list[Page]
    processor_version: str
    entities: list[Entity] = field(default_factory=list)

    def page_of(self, offset: int) -> int | None:
        return next((p.number for p in self.pages if p.start <= offset < p.end), None)

    def confidence_of(self, page: int | None) -> float | None:
        return next((p.confidence for p in self.pages if p.number == page), None)


class LocalTextExtractor:
    version = "local-text-v1"

    def extract(self, *, content: bytes, mime_type: str) -> ExtractedText:
        if mime_type in TEXT_TYPES:
            text = content.decode("utf-8")
            return ExtractedText(text, [Page(1, 0, len(text))], f"{self.version}:plain")
        if mime_type in PDF_TYPES:
            from pypdf import PdfReader
            from pypdf.errors import PdfReadError

            try:
                reader = PdfReader(io.BytesIO(content))
                chunks = [(p.extract_text() or "") for p in reader.pages]
            except (PdfReadError, ValueError):
                raise UnsupportedDocument("PDF could not be read") from None
            if not "".join(chunks).strip():
                raise UnsupportedDocument("scanned PDF without a text layer; OCR is not available locally")
            pages, text, pos = [], "", 0
            for i, chunk in enumerate(chunks, start=1):
                text += chunk + "\n"
                pages.append(Page(i, pos, pos + len(chunk) + 1))
                pos += len(chunk) + 1
            return ExtractedText(text, pages, f"{self.version}:pypdf")
        raise UnsupportedDocument("images need OCR, which is not available locally (Document AI in cloud mode)")

    def status(self) -> CapabilityStatus:
        return CapabilityStatus("documents", "fixture", "local-text", "plain text and text-layer PDFs; no OCR locally")


def _anchor(anchor: Any) -> tuple[int | None, int | None]:
    segments = list(getattr(anchor, "text_segments", []) or [])
    if not segments:
        return None, None
    return int(segments[0].start_index or 0), int(segments[-1].end_index)


class DocumentAIProcessor:
    def __init__(self, *, project: str | None, location: str | None, processor_id: str | None,
                 extractor_id: str | None = None, timeout_s: float = 60, client: Any = None) -> None:  # fmt: skip
        self._project, self._location = project, location
        self._processor_id, self._extractor_id = processor_id, extractor_id
        self._timeout = timeout_s
        self._client = client
        self._last_ok: bool | None = None
        self._last_error: str | None = None

    def _missing(self) -> list[str]:
        required = {"GCP_PROJECT": self._project, "DOCAI_LOCATION": self._location, "DOCAI_PROCESSOR_ID": self._processor_id}
        return [k for k, v in required.items() if not v]

    def _get_client(self) -> Any:
        if self._client is None:
            from google.cloud import documentai_v1 as documentai

            self._client = documentai.DocumentProcessorServiceClient(client_options={"api_endpoint": f"{self._location}-documentai.googleapis.com"})
        return self._client

    def _process(self, processor_id: str, content: bytes, mime_type: str) -> Any:
        from google.api_core import exceptions as gexc
        from google.auth.exceptions import GoogleAuthError
        from google.cloud import documentai_v1 as documentai

        client = self._get_client()
        name = client.processor_path(self._project, self._location, processor_id)
        try:
            result = client.process_document(
                request=documentai.ProcessRequest(name=name, raw_document=documentai.RawDocument(content=content, mime_type=mime_type)),
                timeout=self._timeout,
            )
        except (gexc.GoogleAPIError, GoogleAuthError) as err:
            self._last_ok, self._last_error = False, type(err).__name__
            raise ProviderUnavailable(f"documents unavailable ({type(err).__name__})") from None
        self._last_ok = True
        return result.document

    def extract(self, *, content: bytes, mime_type: str) -> ExtractedText:
        if missing := self._missing():
            raise ProviderUnavailable("documents unavailable: missing config " + ", ".join(missing))
        if mime_type in TEXT_TYPES:
            text = content.decode("utf-8")
            return ExtractedText(text, [Page(1, 0, len(text))], "plain-text")
        if mime_type not in PDF_TYPES | IMAGE_TYPES:
            raise UnsupportedDocument(f"{mime_type} is not supported by the configured processor")

        doc = self._process(self._processor_id, content, mime_type)
        pages = []
        for p in doc.pages:
            start, end = _anchor(p.layout.text_anchor)
            pages.append(Page(int(p.page_number), start or 0, end or len(doc.text), float(p.layout.confidence) if p.layout.confidence else None))
        version = f"documentai:{self._processor_id}"

        entities: list[Entity] = []
        if self._extractor_id:
            # A configured extractor (e.g. a custom or form processor) adds typed entities.
            extracted = self._process(self._extractor_id, content, mime_type)
            for e in extracted.entities:
                start, end = _anchor(e.text_anchor)
                ref = e.page_anchor.page_refs[0] if e.page_anchor and e.page_anchor.page_refs else None
                box = [{"x": v.x, "y": v.y} for v in ref.bounding_poly.normalized_vertices] if ref and ref.bounding_poly else None
                entities.append(Entity(e.type_, e.mention_text, float(e.confidence), int(ref.page) + 1 if ref else None, start, end, box))
            version += f"+{self._extractor_id}"
        return ExtractedText(doc.text, pages or [Page(1, 0, len(doc.text))], version, entities)

    def status(self) -> CapabilityStatus:
        if missing := self._missing():
            return CapabilityStatus("documents", "unavailable", "document-ai", "missing config: " + ", ".join(missing))
        if self._last_ok:
            return CapabilityStatus("documents", "live", "document-ai", "last call succeeded")
        if self._last_ok is False:
            return CapabilityStatus("documents", "unavailable", "document-ai", f"last call failed ({self._last_error})")
        return CapabilityStatus("documents", "unavailable", "document-ai", "configured; not yet called, so unverified")
