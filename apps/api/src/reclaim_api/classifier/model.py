"""Transaction category model: training, evaluation, abstention and a rules-only fallback.

Model: character n-gram TF-IDF over the digit-normalized narrative, plus direction, log
amount, round-amount and day-of-month features, into a class-balanced logistic regression.
Deterministic for a given data seed, so training is reproducible and the version is a hash
of data and parameters. The abstention threshold is chosen on validation data only; test
data is used once, for the reported numbers. All numbers are on synthetic data.
"""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, f1_score, precision_recall_fscore_support
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from reclaim_api.classifier import data

# Accepted predictions must be at least this accurate on validation; the rest go to review.
PARAMS = {"ngram_range": (2, 4), "C": 4.0, "max_iter": 3000, "target_selective_precision": 0.99}
THRESHOLD_GRID = [round(0.30 + 0.01 * i, 2) for i in range(70)]


# --- features ---------------------------------------------------------------------------


def _text(narrative: str) -> str:
    return re.sub(r"\d", "#", narrative.upper())


def frame(rows: list[dict[str, Any]]) -> dict[str, list]:
    return {
        "text": [_text(r["narrative"]) for r in rows],
        "direction": [[r["direction"]] for r in rows],
        "numeric": [[math.log1p(r["amount_minor"] / 100), float(r["amount_minor"] % 50_000 == 0), r["day"] / 31] for r in rows],
    }


def to_input(rows: list[dict[str, Any]]):
    import pandas as pd

    f = frame(rows)
    return pd.DataFrame({"text": f["text"], "direction": [d[0] for d in f["direction"]],
                         **{name: [n[i] for n in f["numeric"]] for i, name in enumerate(("log_amount", "round_amount", "day_frac"))}})  # fmt: skip


def build_pipeline() -> Pipeline:
    features = ColumnTransformer(
        [
            ("text", TfidfVectorizer(analyzer="char_wb", ngram_range=PARAMS["ngram_range"], sublinear_tf=True), "text"),
            ("direction", OneHotEncoder(handle_unknown="ignore"), ["direction"]),
            ("numeric", "passthrough", ["log_amount", "round_amount", "day_frac"]),
        ]
    )
    return Pipeline([("features", features), ("clf", LogisticRegression(C=PARAMS["C"], max_iter=PARAMS["max_iter"], class_weight="balanced"))])


# --- rules-only fallback --------------------------------------------------------------------

RULES = (
    (r"\bSIP\b|\bMF\b|\bAMC\b", "investment_contribution"),
    (r"\bPREM\b|\bLIFE\b|\bINS\b", "insurance_premium"),
    (r"\bEMI\b|\bHL\b|\bLOAN\b", "loan_payment"),
    (r"\bSAL\b|PENSION|\bINT CR\b|\bRENT\b", "income"),
    (r"^(IMPS|NEFT|RTGS) DR\b|^UPI DR \S+@|^IMPS CR\b", "transfer"),
    (r"^(UPI DR|POS|BBPS DR|ATM WDL)\b", "other"),
)


def rules_predict(row: dict[str, Any]) -> tuple[str | None, str]:
    """First matching rule, or None (abstain). Never guesses on bare mandate narratives."""
    text = row["narrative"].upper()
    for pattern, category in RULES:
        if re.search(pattern, text):
            return category, pattern
    return None, ""


# --- training and evaluation --------------------------------------------------------------------


@dataclass
class Report:
    model_version: str
    synthetic: bool
    data: dict[str, Any]
    params: dict[str, Any]
    threshold: float
    validation: dict[str, Any]
    test: dict[str, Any]
    test_rules_baseline: dict[str, Any]
    labels: list[str]


def _as_dicts(rows: list[data.Row]) -> list[dict[str, Any]]:
    return [asdict(r) for r in rows]


def _metrics(y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    labels = list(data.CATEGORIES)
    p, r, _f, s = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    return {
        "n": len(y_true),
        "macro_f1": round(float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)), 4),
        "accuracy": round(float(np.mean(np.array(y_true) == np.array(y_pred))), 4) if y_true else None,
        "per_class": {c: {"precision": round(float(p[i]), 4), "recall": round(float(r[i]), 4), "support": int(s[i])} for i, c in enumerate(labels)},
        "confusion_matrix": {"labels": labels, "rows_true_cols_predicted": confusion_matrix(y_true, y_pred, labels=labels).tolist()},
    }


def _selective(y_true: list[str], y_pred: list[str], conf: np.ndarray, t: float) -> dict[str, Any]:
    keep = conf >= t
    n = len(y_true)
    kept = int(keep.sum())
    correct = sum(1 for k, a, b in zip(keep, y_true, y_pred) if k and a == b)
    return {"threshold": t, "coverage": round(kept / n, 4) if n else 0, "abstained": n - kept,
            "selective_accuracy": round(correct / kept, 4) if kept else None}  # fmt: skip


def choose_threshold(y_true: list[str], y_pred: list[str], conf: np.ndarray, target: float) -> float:
    """Lowest threshold whose validation selective accuracy reaches the target (max coverage)."""
    for t in THRESHOLD_GRID:
        s = _selective(y_true, y_pred, conf, t)
        if s["selective_accuracy"] is not None and s["selective_accuracy"] >= target:
            return t
    return THRESHOLD_GRID[-1]


def version_of(rows: list[data.Row]) -> str:
    """Hash of the exact training data, parameters and library version."""
    h = hashlib.sha256(json.dumps({"params": PARAMS, "sklearn": sklearn.__version__}, sort_keys=True, default=str).encode())
    for r in rows:
        h.update(f"{r.account}|{r.narrative}|{r.direction}|{r.amount_minor}|{r.day}|{r.category}\n".encode())
    return "txn-clf-" + h.hexdigest()[:12]


def train(*, n_accounts: int = 400, seed: int = 7) -> tuple[Pipeline, Report]:
    rows = data.generate(n_accounts=n_accounts, seed=seed)
    parts = data.split(rows)
    dupes = data.cross_split_duplicates(parts)
    if dupes:
        raise RuntimeError(f"{dupes} narratives appear in more than one split")
    small = {k: len({r.account for r in v}) for k, v in parts.items() if len({r.account for r in v}) < 30}
    if small:
        raise RuntimeError(f"split too small to evaluate: {small} accounts")
    train_rows, val_rows, test_rows = (_as_dicts(parts[k]) for k in ("train", "validation", "test"))

    pipe = build_pipeline()
    pipe.fit(to_input(train_rows), [r["category"] for r in train_rows])

    def predict(rows: list[dict[str, Any]]) -> tuple[list[str], np.ndarray]:
        proba = pipe.predict_proba(to_input(rows))
        classes = pipe.classes_
        return [classes[i] for i in proba.argmax(axis=1)], proba.max(axis=1)

    val_true = [r["category"] for r in val_rows]
    val_pred, val_conf = predict(val_rows)
    t = choose_threshold(val_true, val_pred, val_conf, PARAMS["target_selective_precision"])

    test_true = [r["category"] for r in test_rows]
    test_pred, test_conf = predict(test_rows)
    keep = test_conf >= t
    rules = [rules_predict(r)[0] for r in test_rows]
    rules_kept = [(a, b) for a, b in zip(test_true, rules) if b is not None]

    report = Report(
        model_version=version_of(rows),
        synthetic=True,
        data={"rows": len(rows), "accounts": n_accounts, "seed": seed, "split_rows": {k: len(v) for k, v in parts.items()},
              "split_accounts": {k: len({r.account for r in v}) for k, v in parts.items()}, "cross_split_duplicate_narratives": dupes},
        params={k: (list(v) if isinstance(v, tuple) else v) for k, v in PARAMS.items()},
        threshold=t,
        validation={**_selective(val_true, val_pred, val_conf, t), "macro_f1_all": _metrics(val_true, val_pred)["macro_f1"]},
        test={"all_predictions": _metrics(test_true, test_pred), "with_abstention": _selective(test_true, test_pred, test_conf, t),
              "accepted_only": _metrics([a for a, k in zip(test_true, keep) if k], [b for b, k in zip(test_pred, keep) if k])},
        test_rules_baseline={"coverage": round(len(rules_kept) / len(test_true), 4),
                             "accuracy_when_matched": round(sum(a == b for a, b in rules_kept) / len(rules_kept), 4) if rules_kept else None},
        labels=list(data.CATEGORIES),
    )  # fmt: skip
    return pipe, report


def save(pipe: Pipeline, report: Report, root: Path) -> Path:
    out = root / report.model_version
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipe, "threshold": report.threshold, "model_version": report.model_version}, out / "model.joblib")
    (out / "metrics.json").write_text(json.dumps(asdict(report), indent=2))
    (root / "LATEST").write_text(report.model_version)
    return out


def load_latest(root: Path) -> dict[str, Any] | None:
    latest = root / "LATEST"
    if not latest.exists():
        return None
    path = root / latest.read_text().strip() / "model.joblib"
    return joblib.load(path) if path.exists() else None
