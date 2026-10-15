"""Deterministic local adapters for APP_MODE=local.

Storage and events work now. AI adapters stay `unavailable` until the prompt that
builds their fixture lands, so the capability table never claims more than exists.
"""

import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from reclaim_api.providers.base import CapabilityStatus, ProviderUnavailable


class LocalObjectStore:
    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        parts = PurePosixPath(key).parts
        if not parts or key.startswith("/") or ".." in parts:
            raise ValueError("invalid object key")
        return self._root.joinpath(*parts)

    def put(self, *, key: str, data: bytes, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"local://{key}"

    def get(self, *, key: str) -> bytes:
        return self._path(key).read_bytes()

    def status(self) -> CapabilityStatus:
        return CapabilityStatus("object_storage", "fixture", "local-filesystem", f"files under {self._root.name}/ (local only)")


class InMemoryEventPublisher:
    """Development bus. Prompt 6 replaces direct publishing with the SQL outbox."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    def publish(self, *, topic: str, envelope: dict[str, Any]) -> str:
        self.published.append((topic, envelope))
        return str(uuid.uuid4())

    def status(self) -> CapabilityStatus:
        return CapabilityStatus("events", "fixture", "in-memory", "in-process bus; no Pub/Sub")


class PendingFixture:
    """A capability whose deterministic fixture is built in a later prompt."""

    def __init__(self, name: str, prompt: int) -> None:
        self._name = name
        self._prompt = prompt

    def status(self) -> CapabilityStatus:
        return CapabilityStatus(self._name, "unavailable", "none", f"local fixture not built yet (Prompt {self._prompt})")

    def __getattr__(self, attr: str) -> Any:
        def _unavailable(**_: Any) -> Any:
            raise ProviderUnavailable(f"{self._name} unavailable: fixture arrives in Prompt {self._prompt}")

        return _unavailable
