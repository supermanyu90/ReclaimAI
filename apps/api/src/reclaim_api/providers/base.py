"""Provider interfaces. Every external dependency sits behind one of these.

Each provider reports an honest capability state:
  live        — a real cloud service is configured and was reached
  fixture     — a deterministic local implementation, visibly synthetic
  unavailable — not configured or not yet implemented; callers must degrade visibly

Missing credentials never produce fabricated success.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

CapabilityState = Literal["live", "fixture", "unavailable"]


@dataclass(frozen=True)
class CapabilityStatus:
    name: str
    state: CapabilityState
    adapter: str
    detail: str


class ProviderUnavailable(RuntimeError):
    """Raised when a capability is called but not available. Message is log-safe."""


class Provider(Protocol):
    def status(self) -> CapabilityStatus: ...


class LLMProvider(Provider, Protocol):
    """Gemini on Vertex AI. Produces drafts and explanations, never commits or authorizes."""

    def generate_structured(self, *, system: str, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]: ...


class DocumentProcessor(Provider, Protocol):
    """Document AI. Extraction confidence describes extraction, not authenticity."""

    def extract(self, *, content: bytes, mime_type: str) -> dict[str, Any]: ...


class PolicyRetriever(Provider, Protocol):
    """Vertex AI Search over approved, dated policy documents. No private family records."""

    def search(self, *, query: str, filters: dict[str, str]) -> list[dict[str, Any]]: ...


class TransactionClassifier(Provider, Protocol):
    """Vertex AI custom model. Abstains below threshold; never decides entitlement."""

    def classify(self, *, narratives: list[str]) -> list[dict[str, Any]]: ...


class EventPublisher(Provider, Protocol):
    """Pub/Sub. Moves application events; never moves money."""

    def publish(self, *, topic: str, envelope: dict[str, Any]) -> str: ...


class ObjectStore(Provider, Protocol):
    """Private Cloud Storage for source documents and passport PDFs."""

    def put(self, *, key: str, data: bytes, content_type: str) -> str: ...

    def get(self, *, key: str) -> bytes: ...
