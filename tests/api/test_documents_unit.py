from datetime import date
from types import SimpleNamespace as NS

import pytest

from reclaim_api.documents import extract, service
from reclaim_api.providers.base import ProviderUnavailable
from reclaim_api.providers.documents import DocumentAIProcessor, ExtractedText, LocalTextExtractor, Page, UnsupportedDocument


@pytest.mark.parametrize(
    ("content", "mime"),
    [(b"%PDF-1.7 ...", "application/pdf"), (b"\x89PNG\r\n\x1a\nxx", "image/png"), (b"\xff\xd8\xff\xe0", "image/jpeg"),
     (b"RIFF\x00\x00\x00\x00WEBPVP8", "image/webp"), ("Principal ₹5,00,000\n".encode(), "text/plain"),
     (b"MZ\x90\x00", None), (b"\x00\x01\x02\x03" * 50, None)],
)  # fmt: skip
def test_sniff_uses_bytes_not_names(content, mime):
    assert service.sniff(content) == mime


@pytest.mark.parametrize(
    ("phrase", "status", "review"),
    [(None, "unknown", None), ("Nominee: not registered", "not_recorded", None), ("Nominee: Priya Shah", "recorded", None),
     ("Nominee section: [illegible on scan]", "unknown", "nominee section unclear"), ("NIL", "not_recorded", None)],
)  # fmt: skip
def test_nomination_never_turns_absence_into_no(phrase, status, review):
    assert extract.nomination_from(phrase) == (status, review)


def test_document_heading_outranks_model_kind():
    assert extract.detect_kind("SYNTHETIC\nCertificate of Death\n...") == "death_certificate"
    assert extract.detect_kind("Sagar Bharat Bank\nFixed Deposit Advice") == "fd_receipt"
    assert extract.detect_kind("random letter") is None


def test_hallucinated_phrases_go_to_review_not_into_fields():
    text = "Fixed Deposit Advice\nPrincipal amount: Rs. 5,00,000.00\n"
    ext = ExtractedText(text, [Page(1, 0, len(text), confidence=0.97)], "test")
    ex = extract.DocumentExtraction(document_kind="fd_receipt", amount_text="Rs. 9,00,000.00", reference_text="Statement")
    out = extract.normalize(ex, ext, document_id="d", today=date(2026, 10, 15))
    assert "balance_minor" not in out.fields and "masked_reference" not in out.fields
    assert any("unverified" in r for r in out.review)


def test_low_ocr_confidence_requires_review():
    text = "Fixed Deposit Advice\nPrincipal amount: Rs. 5,00,000.00\n"
    ext = ExtractedText(text, [Page(1, 0, len(text), confidence=0.42)], "test")
    out = extract.normalize(extract.DocumentExtraction(document_kind="fd_receipt", amount_text="Rs. 5,00,000.00"), ext, document_id="d", today=date(2026, 10, 15))
    assert out.fields["balance_minor"] == 50000000 and "low OCR confidence" in out.review


def test_local_extractor_refuses_images_and_scans():
    with pytest.raises(UnsupportedDocument):
        LocalTextExtractor().extract(content=b"\x89PNG", mime_type="image/png")


# --- Document AI adapter with a fake client ---------------------------------------------


class FakeDocAI:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def processor_path(self, project, location, processor):
        return f"projects/{project}/locations/{location}/processors/{processor}"

    def process_document(self, request, timeout):
        self.calls.append(request.name)
        if self.error:
            raise self.error
        text = "Fixed Deposit Advice\nPrincipal amount: Rs. 5,00,000.00\n"
        seg = NS(start_index=0, end_index=len(text))
        page = NS(page_number=1, layout=NS(text_anchor=NS(text_segments=[seg]), confidence=0.93))
        entity = NS(type_="principal_amount", mention_text="Rs. 5,00,000.00", confidence=0.88,
                    text_anchor=NS(text_segments=[NS(start_index=39, end_index=54)]),
                    page_anchor=NS(page_refs=[NS(page=0, bounding_poly=NS(normalized_vertices=[NS(x=0.1, y=0.2), NS(x=0.5, y=0.2)]))]))  # fmt: skip
        return NS(document=NS(text=text, pages=[page], entities=[entity]))


def docai(client, **cfg):
    params = {"project": "demo", "location": "eu", "processor_id": "ocr1"} | cfg
    return DocumentAIProcessor(**params, client=client)


def test_docai_missing_config_is_unavailable_without_calling():
    client = FakeDocAI()
    proc = docai(client, processor_id=None)
    with pytest.raises(ProviderUnavailable):
        proc.extract(content=b"%PDF", mime_type="application/pdf")
    assert client.calls == [] and "DOCAI_PROCESSOR_ID" in proc.status().detail


def test_docai_maps_pages_entities_and_versions():
    client = FakeDocAI()
    proc = docai(client, extractor_id="fd-extractor")
    assert "unverified" in proc.status().detail
    ext = proc.extract(content=b"%PDF", mime_type="application/pdf")
    assert client.calls == ["projects/demo/locations/eu/processors/ocr1", "projects/demo/locations/eu/processors/fd-extractor"]
    assert ext.processor_version == "documentai:ocr1+fd-extractor" and ext.pages[0].confidence == 0.93
    e = ext.entities[0]
    assert (e.page, e.start, e.end, e.confidence) == (1, 39, 54, 0.88) and e.bounding_box[0] == {"x": 0.1, "y": 0.2}
    assert proc.status().state == "live"

    out = extract.normalize(extract.DocumentExtraction(document_kind="fd_receipt", amount_text="Rs. 5,00,000.00"), ext, document_id="d", today=date(2026, 10, 15))
    ev = out.evidence[0]
    assert ev["confidence"] == 0.88 and ev["bounding_box"] and ev["processor_version"] == "documentai:ocr1+fd-extractor"


def test_docai_errors_are_unavailable_and_unsupported_types_refused():
    from google.api_core import exceptions as gexc

    proc = docai(FakeDocAI(error=gexc.PermissionDenied("caller lacks permission on projects/demo")))
    with pytest.raises(ProviderUnavailable) as exc:
        proc.extract(content=b"%PDF", mime_type="application/pdf")
    assert "projects/demo" not in str(exc.value) and proc.status().state == "unavailable"
    with pytest.raises(UnsupportedDocument):
        docai(FakeDocAI()).extract(content=b"PK", mime_type="application/zip")


def test_amount_kind_must_fit_asset_type_and_currency_marker_is_kept():
    text = "Sagar Bharat Bank\nFixed Deposit Advice\nPrincipal amount: Rs. 5,00,000.00\n"
    ext = ExtractedText(text, [Page(1, 0, len(text))], "test")
    # A model labels an FD principal as insurance cover and drops the "Rs." prefix.
    ex = extract.DocumentExtraction(document_kind="fd_receipt", amount_text="5,00,000.00", amount_kind="insurance_cover")
    out = extract.normalize(ex, ext, document_id="d", today=date(2026, 10, 15))
    assert out.fields["balance_minor"] == 50000000 and "insurance_cover_minor" not in out.fields
    assert out.fields["currency"] == "INR" and out.evidence[0]["quote"] == "Rs. 5,00,000.00"


def test_document_words_supply_a_missing_asset_type():
    text = "Kaveri Mutual Fund - Consolidated Account Statement\nSIP: Rs. 10,000 per month\n"
    ext = ExtractedText(text, [Page(1, 0, len(text))], "test")
    out = extract.normalize(extract.DocumentExtraction(document_kind="statement"), ext, document_id="d", today=date(2026, 10, 15))
    assert out.asset_type == "mutual_fund"
