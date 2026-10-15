import json
from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from reclaim_api.chat import parse, tools
from reclaim_api.chat.schema import Extraction, extraction_json_schema
from reclaim_api.providers.base import ProviderUnavailable
from reclaim_api.providers.llm import GeminiVertexLLM, InvalidModelOutput, OllamaLLM

TODAY = date(2026, 10, 15)


# --- parsing --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "minor", "currency"),
    [("₹5 lakh", 50000000, "INR"), ("5,00,000", 50000000, None), ("Rs. 2.5 crore", 2500000000, "INR"),
     ("2 lakh", 20000000, "INR"), ("S$18,250", 1825000, "SGD"), ("₹15,20,000", 152000000, "INR")],
)  # fmt: skip
def test_parse_amount(phrase, minor, currency):
    assert parse.parse_amount(phrase) == parse.Amount(minor, currency)


def test_bare_numbers_and_durations_are_not_amounts():
    assert parse.find_amount_phrases("I opened an FD for 3 years") == []
    assert parse.parse_amount("₹5 lakh and ₹2 lakh") is None


@pytest.mark.parametrize(("phrase", "months"), [("3 years", 36), ("one year", 12), ("18 months", 18), ("3 years 6 months", None)])
def test_parse_term(phrase, months):
    assert parse.parse_term_months(phrase) == months


@pytest.mark.parametrize(
    ("phrase", "expected", "ambiguous"),
    [("today", TODAY, False), ("yesterday", date(2026, 10, 14), False), ("10 October 2026", date(2026, 10, 10), False),
     ("on 10 Oct", date(2026, 10, 10), False), ("20 December", date(2025, 12, 20), False), ("25/06/2026", date(2026, 6, 25), False),
     ("05/06/2026", None, True), ("last week", None, True), ("31 February 2026", None, True)],
)  # fmt: skip
def test_parse_date(phrase, expected, ambiguous):
    result = parse.parse_date(phrase, TODAY)
    assert (result.value, result.ambiguous) == (expected, ambiguous)


def test_locate_returns_the_messages_own_wording():
    msg = "I opened an FD for 2 lakh for 1 year"
    assert parse.locate("₹2 lakh", msg) == "2 lakh"
    assert parse.locate("₹9 lakh", msg) is None
    assert parse.locate("1 YEAR", msg) == "1 year"


def test_redaction_masks_long_digit_runs_only():
    text, redacted = parse.redact_long_numbers("acct 1234 5678 9012 and ₹5,00,000 for 3 years")
    assert redacted and "1234 5678 9012" not in text and "••••9012" in text and "₹5,00,000" in text


def test_institution_resolution_labels_demo_alias_mapping():
    inst = parse.resolve_institution("SBI")
    assert inst.name == "Sagar Bharat Bank" and inst.mapped_from == "SBI" and inst.jurisdiction == "IN"
    assert parse.resolve_institution("my bank") is None
    assert parse.resolve_institution("Axis Bank").name == "Axis Bank"  # owner's own wording, unmapped


# --- schema -----------------------------------------------------------------------


def test_extraction_rejects_fields_outside_the_schema():
    with pytest.raises(ValidationError):
        Extraction.model_validate({"intent": "add_asset", "tenant_id": "x"})
    with pytest.raises(ValidationError):
        Extraction.model_validate({"intent": "grant_access"})


def test_provider_schema_requires_every_key():
    schema = extraction_json_schema()
    assert set(schema["required"]) == set(schema["properties"]) and schema["additionalProperties"] is False


def test_confirmation_is_not_a_chat_tool():
    assert set(tools.TOOLS) == {"list_authorized_assets", "propose_asset_change", "get_proposal", "retrieve_policy"}
    assert not any("confirm" in name for name in tools.TOOLS)
    with pytest.raises(ValidationError):
        tools.ProposeArgs.model_validate({"change_kind": "create", "fields": {}, "tenant_id": "x"})


# --- Gemini adapter (fake client; no network) ---------------------------------------


class FakeModels:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return type("R", (), {"text": self.text})()


def gemini(models: FakeModels, **cfg) -> GeminiVertexLLM:
    params = {"project": "demo-project", "location": "asia-south1", "model": "gemini-x", "timeout_s": 5} | cfg
    return GeminiVertexLLM(**params, client=type("C", (), {"models": models})())


def test_gemini_missing_config_is_unavailable_without_calling():
    models = FakeModels(text="{}")
    llm = gemini(models, project=None)
    with pytest.raises(ProviderUnavailable):
        llm.generate_structured(system="s", messages=[{"role": "user", "content": "hi"}], schema={})
    assert models.calls == [] and llm.status().state == "unavailable" and "GCP_PROJECT" in llm.status().detail


def test_gemini_requests_structured_json_and_reports_live_only_after_success():
    models = FakeModels(text=json.dumps({"intent": "other"}))
    llm = gemini(models)
    assert "not yet called" in llm.status().detail and llm.status().state == "unavailable"
    out = llm.generate_structured(system="sys", messages=[{"role": "user", "content": "hi"}], schema={"type": "object"})
    assert out == {"intent": "other"}
    call = models.calls[0]
    assert call["model"] == "gemini-x"
    cfg = call["config"]
    assert cfg.response_mime_type == "application/json" and cfg.response_json_schema == {"type": "object"} and cfg.temperature == 0
    assert cfg.system_instruction == "sys"
    assert llm.status().state == "live"


def test_gemini_non_json_is_invalid_output_and_api_errors_are_unavailable():
    with pytest.raises(InvalidModelOutput):
        gemini(FakeModels(text="Sure! Here you go")).generate_structured(system="", messages=[], schema={})
    from google.genai import errors

    llm = gemini(FakeModels(error=errors.ClientError(403, {"error": {"message": "permission denied"}})))
    with pytest.raises(ProviderUnavailable) as exc:
        llm.generate_structured(system="", messages=[], schema={})
    assert "permission denied" not in str(exc.value)  # upstream text never propagates
    assert llm.status().state == "unavailable" and "ClientError" in llm.status().detail


# --- Ollama adapter (mock transport) -----------------------------------------------------


def test_ollama_sends_schema_and_zero_temperature():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma3:4b"}]})
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"intent": "other"}'}})

    llm = OllamaLLM(base_url="http://ollama", model="gemma3:4b", timeout_s=5, transport=httpx.MockTransport(handler))
    assert llm.generate_structured(system="sys", messages=[{"role": "user", "content": "x"}], schema={"type": "object"}) == {"intent": "other"}
    assert seen["format"] == {"type": "object"} and seen["options"]["temperature"] == 0 and seen["messages"][0]["role"] == "system"
    assert llm.status().state == "live" and "not Gemini" in llm.status().detail


def test_ollama_without_model_pulled_is_unavailable():
    llm = OllamaLLM(base_url="http://ollama", model="gemma3:4b", timeout_s=5,
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"models": []})))  # fmt: skip
    assert llm.status().state == "unavailable"
