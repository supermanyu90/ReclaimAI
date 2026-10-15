"""Category prediction with abstention and a rules-only fallback.

ModelClassifier uses the trained artifact (local directory, or one copied down from Cloud
Storage in cloud mode). Without an artifact it answers with RulesClassifier and says so:
no model is invented. Predictions always carry their source and model version, and a
prediction is never a confirmed category.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reclaim_api.classifier import model
from reclaim_api.providers.base import CapabilityStatus

RULES_VERSION = "rules-v1"


@dataclass(frozen=True)
class Prediction:
    category: str | None  # None when abstained
    confidence: float | None
    abstained: bool
    source: str  # "model" | "rules"
    model_version: str


class RulesClassifier:
    def classify(self, rows: list[dict[str, Any]]) -> list[Prediction]:
        out = []
        for r in rows:
            category, _ = model.rules_predict(r)
            out.append(Prediction(category, None, category is None, "rules", RULES_VERSION))
        return out

    def status(self) -> CapabilityStatus:
        return CapabilityStatus("classification", "fixture", "rules-only", "keyword rules; abstains when no rule matches")


class ModelClassifier:
    def __init__(self, models_dir: str | Path) -> None:
        self._dir = Path(models_dir)
        self._loaded: dict[str, Any] | None = None
        self._rules = RulesClassifier()

    def _artifact(self) -> dict[str, Any] | None:
        if self._loaded is None:
            self._loaded = model.load_latest(self._dir)
        return self._loaded

    def classify(self, rows: list[dict[str, Any]]) -> list[Prediction]:
        art = self._artifact()
        if art is None or not rows:
            return self._rules.classify(rows)  # rules-only fallback
        proba = art["pipeline"].predict_proba(model.to_input(rows))
        classes = art["pipeline"].classes_
        out = []
        for p in proba:
            i = int(p.argmax())
            conf = float(p[i])
            keep = conf >= art["threshold"]
            out.append(Prediction(classes[i] if keep else None, round(conf, 4), not keep, "model", art["model_version"]))
        return out

    def status(self) -> CapabilityStatus:
        art = self._artifact()
        if art is None:
            return CapabilityStatus("classification", "fixture", "rules-only", "no trained model found; rules-only fallback (run reclaim-classifier train)")
        return CapabilityStatus("classification", "fixture", "local-sklearn",
                                f"{art['model_version']} trained on synthetic data; abstains below {art['threshold']}")  # fmt: skip
