"""Selects provider implementations for the configured APP_MODE."""

from dataclasses import dataclass, fields
from functools import lru_cache
from typing import Any

import httpx

from reclaim_api.config import Settings, get_settings, provider
from reclaim_api.events.publishers import LocalPushPublisher, PubSubPublisher
from reclaim_api.providers.base import CapabilityStatus
from reclaim_api.providers.documents import DocumentAIProcessor, LocalTextExtractor
from reclaim_api.providers.llm import GeminiVertexLLM, OllamaLLM, RulesFixtureLLM
from reclaim_api.providers.local import LocalObjectStore
from reclaim_api.providers.retrieval import LocalPolicyIndex, VertexSearchRetriever
from reclaim_api.providers.storage import GCSObjectStore
from reclaim_api.telegram.sender import build_sender


@dataclass(frozen=True)
class Providers:
    llm: Any
    documents: Any
    retrieval: Any
    classification: Any
    events: Any
    object_storage: Any
    telegram: Any
    warehouse: Any

    def statuses(self) -> list[CapabilityStatus]:
        return [getattr(self, f.name).status() for f in fields(self)]


def build_llm(s: Settings) -> Any:
    choice = provider(s, "llm")
    if choice == "gemini":
        return GeminiVertexLLM(project=s.gcp_project, location=s.vertex_location, model=s.gemini_model, timeout_s=s.llm_timeout_s,
                               thinking_budget=s.gemini_thinking_budget)  # fmt: skip
    if choice == "ollama":
        return OllamaLLM(base_url=s.ollama_url, model=s.ollama_model, timeout_s=s.llm_timeout_s)
    return RulesFixtureLLM()


LOCAL_PUSH_TOKEN = "reclaim-local-dev-only-push-token"


def build_warehouse(s: Settings) -> Any:
    from reclaim_api.analytics.warehouse import BigQueryWarehouse, LocalWarehouse
    from reclaim_api.db import get_engine

    if provider(s, "warehouse") == "bigquery":
        return BigQueryWarehouse(project=s.gcp_project, dataset=s.bigquery_dataset, location=s.bigquery_location)
    return LocalWarehouse(get_engine())


def build_events(s: Settings, warehouse: Any) -> Any:
    if provider(s, "events") == "pubsub":
        return PubSubPublisher(project=s.gcp_project, topics={"app-events": s.pubsub_topic, "analytics": s.analytics_topic, "transactions": s.transactions_topic})
    token = s.worker_push_token or (LOCAL_PUSH_TOKEN if s.app_mode == "local" else None)
    if token is None:
        raise RuntimeError("WORKER_PUSH_TOKEN is required for local push events outside local mode")
    return LocalPushPublisher(client=httpx.Client(base_url=s.worker_url, timeout=120), token=token, warehouse=warehouse)


def build_retrieval(s: Settings) -> Any:
    if provider(s, "retrieval") == "vertex":
        return VertexSearchRetriever(project=s.gcp_project, location=s.search_location, datastore=s.search_datastore_id)
    from reclaim_api.seed import default_fixture_path

    return LocalPolicyIndex(s.policy_corpus_dir or default_fixture_path().parent / "policies")


def build_classifier(s: Settings) -> Any:
    """Same in both modes: the trained artifact if present (local dir or a GCS mount), else rules."""
    from reclaim_api.classifier.cli import models_dir
    from reclaim_api.classifier.predict import ModelClassifier

    return ModelClassifier(models_dir())


def build_providers(s: Settings) -> Providers:
    warehouse = build_warehouse(s)
    if provider(s, "documents") == "documentai":
        documents: Any = DocumentAIProcessor(
            project=s.gcp_project, location=s.docai_location, processor_id=s.docai_processor_id, extractor_id=s.docai_extractor_id
        )
    else:
        documents = LocalTextExtractor()
    storage: Any = GCSObjectStore(project=s.gcp_project, bucket=s.gcs_bucket) if provider(s, "storage") == "gcs" else LocalObjectStore(s.local_storage_dir)
    return Providers(
        llm=build_llm(s),
        documents=documents,
        retrieval=build_retrieval(s),
        classification=build_classifier(s),
        events=build_events(s, warehouse),
        object_storage=storage,
        telegram=build_sender(s),
        warehouse=warehouse,
    )


@lru_cache
def get_providers() -> Providers:
    return build_providers(get_settings())
