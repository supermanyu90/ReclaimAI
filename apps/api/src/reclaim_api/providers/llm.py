"""LLM adapters. All share one contract: generate_structured(...) -> dict, or raise.

- GeminiVertexLLM: Gemini on Vertex AI (cloud). Application Default Credentials only.
- OllamaLLM: a real local model through Ollama (local mode). Not Gemini; labelled as such.
- RulesFixtureLLM: deterministic regex rules for tests/CI. Not a model; labelled as such.

None of them is a fallback for another: an unreachable provider raises
ProviderUnavailable and the chat says so.
"""

import json
import re
import threading
import time
from typing import Any

import httpx

from reclaim_api.chat import parse
from reclaim_api.providers.base import CapabilityStatus, ProviderUnavailable


class InvalidModelOutput(ValueError):
    """The provider answered, but not with JSON we can use. Message is log-safe."""


def _parse_json(text: str | None) -> dict[str, Any]:
    if not text:
        raise InvalidModelOutput("empty model response")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise InvalidModelOutput("model response is not JSON") from None
    if not isinstance(value, dict):
        raise InvalidModelOutput("model response is not a JSON object")
    return value


class _CallTracker:
    """Capability state from observed calls, never from configuration alone."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.last_ok: bool | None = None
        self.last_error: str | None = None

    def record(self, ok: bool, error: str | None = None) -> None:
        with self._lock:
            self.last_ok, self.last_error = ok, error


class GeminiVertexLLM:
    RETRIES = 2
    BACKOFF_S = 1.0

    def __init__(self, *, project: str | None, location: str | None, model: str | None, timeout_s: float, client: Any = None, thinking_budget: int = 0) -> None:
        self._project, self._location, self._model = project, location, model
        self._thinking_budget = thinking_budget
        self._timeout_ms = int(timeout_s * 1000)
        self._client = client
        self._calls = _CallTracker()

    @property
    def model_version(self) -> str:
        return f"gemini-vertex:{self._model}"

    def _missing(self) -> list[str]:
        return [k for k, v in {"GCP_PROJECT": self._project, "VERTEX_LOCATION": self._location, "GEMINI_MODEL": self._model}.items() if not v]

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                vertexai=True, project=self._project, location=self._location, http_options=types.HttpOptions(timeout=self._timeout_ms)
            )
        return self._client

    def generate_structured(self, *, system: str, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        if missing := self._missing():
            raise ProviderUnavailable("llm unavailable: missing config " + ", ".join(missing))
        from google.auth.exceptions import GoogleAuthError
        from google.genai import errors as genai_errors
        from google.genai import types

        contents = [types.Content(role="model" if m["role"] == "assistant" else "user", parts=[types.Part(text=m["content"])]) for m in messages]
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            max_output_tokens=1024,
            response_mime_type="application/json",
            response_json_schema=schema,
            # Gemini 2.5 "thinks" by default; those tokens count against max_output_tokens and can
            # truncate the JSON. Extraction needs no reasoning trace, so it is off unless configured.
            thinking_config=types.ThinkingConfig(thinking_budget=self._thinking_budget),
        )
        # Rate limits and brief unavailability are retried a bounded number of times; anything
        # else fails at once. Only the error type and HTTP status are kept (never the message).
        for attempt in range(self.RETRIES + 1):
            try:
                response = self._get_client().models.generate_content(model=self._model, contents=contents, config=config)
                break
            except genai_errors.APIError as err:
                code = getattr(err, "code", None)
                if code in (429, 503) and attempt < self.RETRIES:
                    time.sleep(self.BACKOFF_S * 2**attempt)
                    continue
                self._calls.record(False, f"{type(err).__name__} {code}")
                raise ProviderUnavailable(f"llm call failed ({type(err).__name__} {code})") from None
            except (GoogleAuthError, httpx.HTTPError, OSError) as err:
                self._calls.record(False, type(err).__name__)
                raise ProviderUnavailable(f"llm call failed ({type(err).__name__})") from None
        self._calls.record(True)
        return _parse_json(getattr(response, "text", None))

    def status(self) -> CapabilityStatus:
        if missing := self._missing():
            return CapabilityStatus("llm", "unavailable", "gemini-vertex", "missing config: " + ", ".join(missing))
        if self._calls.last_ok:
            return CapabilityStatus("llm", "live", "gemini-vertex", f"{self._model} in {self._location}; last call succeeded")
        if self._calls.last_ok is False:
            return CapabilityStatus("llm", "unavailable", "gemini-vertex", f"last call failed ({self._calls.last_error})")
        return CapabilityStatus("llm", "unavailable", "gemini-vertex", f"{self._model} configured; not yet called, so unverified")


class OllamaLLM:
    def __init__(self, *, base_url: str, model: str, timeout_s: float, transport: httpx.BaseTransport | None = None) -> None:
        self._model = model
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s, transport=transport)

    @property
    def model_version(self) -> str:
        return f"ollama:{self._model}"

    def generate_structured(self, *, system: str, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": self._model,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system}, *messages],
        }
        try:
            res = self._client.post("/api/chat", json=body)
            res.raise_for_status()
        except httpx.HTTPError as err:
            raise ProviderUnavailable(f"local model unavailable ({type(err).__name__})") from None
        return _parse_json(res.json().get("message", {}).get("content"))

    def status(self) -> CapabilityStatus:
        try:
            res = self._client.get("/api/tags", timeout=1.0)
            res.raise_for_status()
            names = {m.get("name") for m in res.json().get("models", [])}
        except httpx.HTTPError as err:
            return CapabilityStatus("llm", "unavailable", "ollama", f"Ollama not reachable ({type(err).__name__})")
        if self._model not in names:
            return CapabilityStatus("llm", "unavailable", "ollama", f"model {self._model} not pulled")
        return CapabilityStatus("llm", "live", "ollama", f"{self._model}: real local inference (not Gemini)")


class RulesFixtureLLM:
    """Deterministic stand-in for tests and CI. Regex rules, not a language model."""

    model_version = "rules-fixture-v1"

    _INTENTS = (
        (r"\b(closed|matured|redeemed|sold|surrendered)\b", "close_asset"),
        (r"\b(actually|correction|wrong|should be|updated?|now (is|has))\b", "update_asset"),
        (r"\b(list|show|what do i have|what('s| is) recorded)\b", "list_assets"),
        (r"\b(claim|procedure|what documents|how do (we|they))\b", "policy_question"),
        (r"\b(opened|bought|started|took|have|invested|booked|got)\b", "add_asset"),
    )

    def generate_structured(self, *, system: str, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(messages[-1]["content"])
        if "document_text" in payload:
            return self._document(payload["document_text"])
        if "recovery" in payload:
            feats = payload["recovery"]["features"]
            return {"statements": [{"feature": f["key"], "text": f["detail"]} for f in feats]}
        if "case" in payload:
            case = payload["case"]
            issues = [c for c in case["checks"] if c["result"] in ("mismatch", "missing", "unreadable")]
            return {
                "case_summary": f"{len(case['documents'])} document(s) supplied; {len(issues)} discrepancy or gap found by the checks.",
                "evidence_refs": [d["ref"] for d in case["documents"]],
                "next_review": "Compare each certificate with the owner record before deciding.",
            }
        if "checklist" in payload:
            steps = [{"action": f"Gather the documents for: {p['title']}" + (f" (if {p['condition']})" if p["condition"] else ""),
                      "source_refs": [p["ref"]]} for p in payload["checklist"]]  # fmt: skip
            return {"summary": f"{len(steps)} possible path(s) apply; unknown facts decide between them.", "next_steps": steps, "unknowns": payload["unknowns"]}
        if "question" in payload:
            first = payload["sources"][0]
            return {"answer": first["text"].split(". ")[0].strip() + ".", "source_refs": [first["ref"]], "unknowns": []}
        latest = payload["latest_message"]
        low = latest.lower()
        intent = next((i for pat, i in self._INTENTS if re.search(pat, low)), "other")
        types = parse.detect_asset_types(latest)
        asset_type = next(iter(types)) if len(types) == 1 else ("savings" if re.search(r"\baccount\b", low) and not types else None)
        mentions = parse.find_institution_mentions(latest)
        amounts = parse.find_amount_phrases(latest)
        durations = parse.find_duration_phrases(latest)
        date_match = re.search(
            rf"(today|yesterday|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{4}}|\d{{1,2}}(st|nd|rd|th)?\s+({parse.MONTH_RE})\b\.?(\s+\d{{4}})?)",
            low,
        )
        out: dict[str, Any] = {
            "intent": intent,
            "asset_type": asset_type,
            "institution_text": mentions[0] if len(mentions) == 1 else None,
            "amount_text": amounts[0] if len(amounts) == 1 else None,
            "amount_kind": "sip_installment" if "sip" in low and amounts else ("balance" if amounts else None),
            "term_text": durations[0] if len(durations) == 1 else None,
            "opened_date_text": latest[date_match.start() : date_match.end()] if date_match else None,
        }
        if intent in ("update_asset", "close_asset"):
            out["asset_ref_text"] = latest
        return {k: v for k, v in out.items() if v is not None}

    @staticmethod
    def _line(text: str, pattern: str) -> str | None:
        return next((ln.strip() for ln in text.splitlines() if re.search(pattern, ln, re.IGNORECASE)), None)

    def _document(self, text: str) -> dict[str, Any]:
        low = text.lower()
        kind = next(
            (k for pat, k in ((r"certificate of death", "death_certificate"), (r"certificate of birth", "birth_certificate"),
                              (r"handwritten", "handwritten_inventory"), (r"fixed deposit", "fd_receipt"),
                              (r"policy schedule", "policy_schedule"), (r"statement", "statement")) if re.search(pat, low)),
            "other",
        )  # fmt: skip

        def first(found: list[str]) -> str | None:
            return found[0] if found else None

        amount_line = self._line(text, r"principal|current value|sum assured|balance|outstanding")
        sip_line = self._line(text, r"\bsip\b")
        def date_of(pattern: str) -> str | None:
            line = self._line(text, pattern)
            return first(parse.find_date_phrases(line)) if line else None

        ref_line = self._line(text, r"folio|policy number|deposit account|account (no|number)")
        types = parse.detect_asset_types(text)
        mentions = parse.find_institution_mentions(text)
        out = {
            "document_kind": kind,
            "asset_type": next(iter(types)) if len(types) == 1 else None,
            "institution_text": mentions[0] if mentions else None,
            "amount_text": first(parse.find_amount_phrases(amount_line)) if amount_line else None,
            "amount_kind": "insurance_cover" if amount_line and "assured" in amount_line.lower() else ("liability" if amount_line and "outstanding" in amount_line.lower() else None),
            "sip_amount_text": first(parse.find_amount_phrases(sip_line)) if sip_line else None,
            "term_text": first(parse.find_duration_phrases(self._line(text, r"tenure|term") or "")),
            "opened_date_text": date_of(r"date of deposit|opened"),
            "maturity_date_text": date_of(r"maturity"),
            "document_date_text": date_of(r"statement date|schedule date|papers list"),
            "nominee_text": self._line(text, r"nominee"),
            "reference_text": ref_line.split()[-1] if ref_line else None,
        }
        return {k: v for k, v in out.items() if v is not None}

    def status(self) -> CapabilityStatus:
        return CapabilityStatus("llm", "fixture", "rules-fixture", "deterministic regex rules for tests; not a language model")
