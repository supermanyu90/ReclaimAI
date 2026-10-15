"""Runtime configuration. Every cloud identifier is configurable; none is assumed."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Empty values in .env mean "not set", so optional settings fall back to defaults.
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), extra="ignore", env_ignore_empty=True)

    # local: this machine. cloud: everything on Google Cloud (Identity Platform sign-in).
    # demo: a hosted synthetic demo. Synthetic-person sign-in like local, behind the web app's
    # passcode gate and a private API; each provider is chosen below (defaults in PROVIDER_DEFAULTS).
    app_mode: Literal["local", "cloud", "demo"] = "local"
    api_host: str = "127.0.0.1"
    api_port: int = 8100
    database_url: str = "postgresql+psycopg://reclaim:reclaim@localhost:55432/reclaim"
    web_origin: str = "http://localhost:3100"
    log_level: str = "INFO"

    # Local adapters
    local_storage_dir: str = ".data/objects"

    # Chat / LLM. Default: real local inference (ollama) in local mode, Gemini in cloud.
    # "fixture" is deterministic regex rules for tests/CI and is labelled as not a model.
    llm_provider: Literal["ollama", "fixture", "gemini"] | None = None
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    llm_timeout_s: float = 30.0
    chat_history_turns: int = 6

    # Telegram. In cloud the token and secrets come from Secret Manager (mounted as env).
    # Without TELEGRAM_WEBHOOK_SECRET the webhook refuses every request.
    telegram_webhook_secret: str | None = None
    telegram_bot_token: str | None = None
    telegram_bot_username: str | None = None
    # dry_run records replies instead of sending; live needs a token and an explicit opt-in.
    telegram_send_mode: Literal["dry_run", "live"] = "dry_run"
    telegram_rate_limit_per_min: int = 20
    link_code_ttl_min: int = 10
    # HMAC key for storing chat IDs as keyed hashes. Required outside local mode.
    channel_hash_key: str | None = None

    # Events. Local: the worker's loop publishes the outbox by pushing to WORKER_URL.
    # Cloud: Pub/Sub push to the worker with an OIDC token from PUSH_SERVICE_ACCOUNT.
    worker_url: str = "http://localhost:8101"
    worker_push_token: str | None = None  # local mode only
    worker_audience: str | None = None  # cloud: expected OIDC audience (the worker URL)
    push_service_account: str | None = None  # cloud: the only identity allowed to push
    scheduler_service_account: str | None = None  # cloud: the only identity allowed to run /tasks/*
    outbox_loop: bool = True

    # Optional dormant-asset recovery (Prompt 13). Off → its endpoints are 404 and the UI hides it.
    recovery_enabled: bool = True
    outbox_poll_s: float = 1.0
    outbox_batch: int = 50
    outbox_max_attempts: int = 8

    # Per-service provider choice; empty means the mode's default (see PROVIDER_DEFAULTS).
    documents_provider: Literal["local", "documentai"] | None = None
    retrieval_provider: Literal["local", "vertex"] | None = None
    events_provider: Literal["local", "pubsub"] | None = None
    warehouse_provider: Literal["local", "bigquery"] | None = None
    storage_provider: Literal["local", "gcs"] | None = None

    # Google Cloud. Locations are per service: do not assume one region supports all.
    gcp_project: str | None = None
    vertex_location: str | None = None
    gemini_model: str | None = None
    gemini_thinking_budget: int = 0  # tokens; 0 turns Gemini 2.5 thinking off
    docai_location: str | None = None
    docai_processor_id: str | None = None  # OCR processor
    docai_extractor_id: str | None = None  # optional extractor (custom/form) for typed entities
    search_location: str | None = None
    search_datastore_id: str | None = None
    policy_corpus_dir: str | None = None  # local index; default <repo>/fixtures/policies
    classifier_endpoint_id: str | None = None  # optional online endpoint (not deployed by default)
    models_dir: str | None = None  # classifier artifacts; default <repo>/.data/models (a GCS mount in cloud)
    models_bucket: str | None = None  # Vertex training output and staging
    pubsub_topic: str | None = None  # application events → worker push subscription
    analytics_topic: str | None = None  # minimized analytics → BigQuery subscription
    transactions_topic: str | None = None  # synthetic transaction feed → BigQuery subscription
    bigquery_dataset: str | None = None
    bigquery_location: str | None = None
    gcs_bucket: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()


PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "local": {"llm": "ollama", "documents": "local", "retrieval": "local", "events": "local", "warehouse": "local", "storage": "local"},
    "cloud": {"llm": "gemini", "documents": "documentai", "retrieval": "vertex", "events": "pubsub", "warehouse": "bigquery", "storage": "gcs"},
    # Cloud Run instances have their own disks, so files must be shared through GCS.
    "demo": {"llm": "gemini", "documents": "local", "retrieval": "local", "events": "local", "warehouse": "local", "storage": "gcs"},
}


def provider(s: Settings, name: str) -> str:
    explicit = getattr(s, f"{name}_provider", None)
    return explicit or PROVIDER_DEFAULTS[s.app_mode][name]


def synthetic_signin(s: Settings) -> bool:
    """Synthetic-person sign-in (dev tokens): local machine, or the gated hosted demo."""
    return s.app_mode in ("local", "demo")
