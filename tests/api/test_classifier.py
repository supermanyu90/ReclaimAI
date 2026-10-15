"""Prompt 9 acceptance: reproducible training, no cross-split duplicates, low-confidence
abstention, rules-only fallback when the model is unavailable. Predictions carry a model
version and stay separate from confirmed categories; all evaluation is labelled synthetic."""

import dataclasses
import importlib
import uuid
from types import SimpleNamespace as NS

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from reclaim_api import models as m
from reclaim_api.classifier import cli, data, model, vertex
from reclaim_api.classifier.predict import ModelClassifier, RulesClassifier


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    pipe, report = model.train()
    root = tmp_path_factory.mktemp("models")
    model.save(pipe, report, root)
    return pipe, report, root


# --- training -----------------------------------------------------------------------------


def test_training_is_reproducible(trained):
    _, first, _ = trained
    _, second = model.train()
    assert second.model_version == first.model_version
    assert dataclasses.asdict(second) == dataclasses.asdict(first)


def test_no_narrative_or_account_crosses_splits(trained):
    _, report, _ = trained
    parts = data.split(data.generate())
    assert data.cross_split_duplicates(parts) == 0 == report.data["cross_split_duplicate_narratives"]
    accounts = {k: {r.account for r in v} for k, v in parts.items()}
    assert not (accounts["train"] & accounts["validation"] or accounts["train"] & accounts["test"] or accounts["validation"] & accounts["test"])
    assert all(n >= 30 for n in report.data["split_accounts"].values())


def test_report_is_complete_and_labelled_synthetic(trained):
    _, report, _ = trained
    t = report.test["all_predictions"]
    assert report.synthetic and set(t["per_class"]) == set(data.CATEGORIES)
    assert len(t["confusion_matrix"]["rows_true_cols_predicted"]) == 6 and 0 < t["macro_f1"] <= 1
    card = cli.model_card(report)
    assert "synthetic data" in card and "does not detect fraud" in card and report.model_version in card


def test_threshold_is_chosen_on_validation_not_test(trained, monkeypatch):
    _, report, _ = trained
    seen = []
    real = model.choose_threshold
    monkeypatch.setattr(model, "choose_threshold", lambda y, p, c, t: seen.append(len(y)) or real(y, p, c, t))
    model.train()
    assert seen == [report.data["split_rows"]["validation"]]


# --- inference -----------------------------------------------------------------------------------


class FakePipe:
    classes_ = np.array(list(data.CATEGORIES))

    def __init__(self, probs):
        self.probs = probs

    def predict_proba(self, _):
        return np.array(self.probs)


def test_low_confidence_predictions_abstain(tmp_path, monkeypatch):
    clf = ModelClassifier(tmp_path)
    monkeypatch.setattr(clf, "_loaded", {"pipeline": FakePipe([[0.9, 0.02, 0.02, 0.02, 0.02, 0.02], [0.3, 0.3, 0.1, 0.1, 0.1, 0.1]]),
                                         "threshold": 0.55, "model_version": "txn-clf-test"})  # fmt: skip
    rows = [{"narrative": "NEFT CR SAL", "direction": "credit", "amount_minor": 100, "day": 1}] * 2
    confident, unsure = clf.classify(rows)
    assert (confident.category, confident.abstained, confident.model_version) == ("income", False, "txn-clf-test")
    assert (unsure.category, unsure.abstained, unsure.confidence) == (None, True, 0.3)


def test_rules_only_fallback_without_a_model(tmp_path):
    clf = ModelClassifier(tmp_path / "empty")
    assert clf.status().adapter == "rules-only" and "fallback" in clf.status().detail
    out = clf.classify([{"narrative": "ACH DR KAVERI MF SIP F1234", "direction": "debit", "amount_minor": 100_000, "day": 5},
                        {"narrative": "NACH DR UMRN123456", "direction": "debit", "amount_minor": 500_000, "day": 7}])  # fmt: skip
    assert (out[0].category, out[0].source) == ("investment_contribution", "rules")
    assert out[1].abstained and out[1].category is None  # a bare mandate is never guessed
    assert RulesClassifier().status().state == "fixture"


def test_trained_model_predictions_carry_its_version(trained):
    _, report, root = trained
    out = ModelClassifier(root).classify([{"narrative": "EMI DR SAGAR BHARAT HL 482193", "direction": "debit", "amount_minor": 2_150_000, "day": 7}])
    assert out[0].model_version == report.model_version and out[0].source == "model"


# --- Vertex AI (fake SDK; nothing is submitted) -------------------------------------------------------


class FakeAiplatform:
    def __init__(self):
        self.calls = []
        outer = self

        class CustomJob:
            @staticmethod
            def from_local_script(**kw):
                outer.calls.append(("job", kw))
                return NS(run=lambda sync: outer.calls.append(("run", sync)), state="JOB_STATE_SUCCEEDED")

        class Model:
            @staticmethod
            def upload(**kw):
                outer.calls.append(("upload", kw))
                return NS(resource_name="projects/demo/locations/r/models/1")

        self.CustomJob, self.Model = CustomJob, Model

    def init(self, **kw):
        self.calls.append(("init", kw))


def test_vertex_training_submits_custom_job_and_registers_artifact():
    fake = FakeAiplatform()
    spec = vertex.training_spec(bucket="models-demo", version_tag="v1")
    result = vertex.submit(spec, project="demo", region="asia-south1", bucket="models-demo", aiplatform=fake)
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["init", "job", "run", "upload"]
    job = dict(fake.calls[1][1])
    assert job["script_path"].endswith("train_job.py") and job["container_uri"] == vertex.TRAIN_IMAGE
    upload = dict(fake.calls[3][1])
    assert upload["artifact_uri"] == "gs://models-demo/models/txn-clf/v1" and upload["labels"] == {"synthetic": "true"}
    assert result["model_resource"].endswith("/models/1")


def test_vertex_requires_configuration_and_batch_does_not_register():
    with pytest.raises(vertex.NotConfigured):
        vertex.submit(vertex.training_spec(bucket="b", version_tag="v"), project=None, region="r", bucket="b", aiplatform=FakeAiplatform())
    fake = FakeAiplatform()
    vertex.submit(vertex.batch_spec(bucket="b", model_uri="gs://b/m", dataset="demo.reclaim"), project="demo", region="r", bucket="b", aiplatform=fake)
    assert "upload" not in [c[0] for c in fake.calls]


# --- review queue and confirmations (database) -----------------------------------------------------------


@pytest.mark.db
def test_review_queue_and_confirmations_stay_separate_from_predictions(trained, monkeypatch, drain):
    from reclaim_api.analytics import synthetic
    from reclaim_api.analytics.warehouse import LocalWarehouse
    from reclaim_api.config import get_settings
    from reclaim_api.db import get_engine
    from reclaim_api.events import outbox
    from reclaim_api.seed import fixture_id, seed
    from reclaim_api.telegram.linking import hash_key

    main = importlib.import_module("reclaim_api.main")
    _, report, root = trained
    providers = dataclasses.replace(main.get_providers(), classification=ModelClassifier(root))
    monkeypatch.setattr(main, "get_providers", lambda: providers)
    key, wh = hash_key(get_settings()), LocalWarehouse(get_engine())
    wh.ensure()
    with Session(get_engine()) as s, s.begin():
        seed(s, "before", do_reset=True)
        synthetic.write_outbox(s, synthetic.generate(), analytics_key=key)

    class Sink:
        def publish(self, *, topic, envelope):
            wh.ingest(topic, envelope)

    with Session(get_engine()) as s, s.begin():
        outbox.publish_pending(s, Sink(), analytics_key=key, topics=["transactions"], batch=500)
    rows = wh.query("features", {})
    preds = providers.classification.classify(rows)
    # Force one abstention so the queue has something to review.
    preds[0] = dataclasses.replace(preds[0], category=None, abstained=True, confidence=0.4)
    wh.write_predictions([{"transaction_id": r["transaction_id"], "tenant_key": r["tenant_key"], "model_version": p.model_version, "source": p.source,
                           "predicted_category": p.category, "confidence": p.confidence, "abstained": p.abstained} for r, p in zip(rows, preds, strict=True)])  # fmt: skip

    api = TestClient(main.app)
    ramesh = {"Authorization": f"Bearer dev:{fixture_id('ramesh')}"}
    q = api.get("/v1/analytics/review", headers=ramesh).json()
    assert q["predicted"] == len(rows) and len(q["needs_review"]) == 1 and q["model_versions"] == [report.model_version]
    item = q["needs_review"][0]
    assert item["predicted_category"] is None and item["confirmed_category"] is None

    res = api.post(f"/v1/analytics/transactions/{item['transaction_id']}/category", json={"category": "other"}, headers=ramesh)
    assert res.status_code == 200 and res.json()["model_version"] == report.model_version
    with Session(get_engine()) as s:
        c = s.scalar(select(m.CategoryConfirmation).where(m.CategoryConfirmation.transaction_id == item["transaction_id"]))
    assert (c.category, c.predicted_category) == ("other", None)
    with get_engine().connect() as conn:  # the prediction itself is unchanged
        abstained = conn.execute(text("SELECT abstained FROM warehouse.transaction_predictions WHERE transaction_id = :t"), {"t": item["transaction_id"]}).scalar()
    assert abstained is True
    assert api.get("/v1/analytics/review", headers=ramesh).json()["needs_review"] == []

    kavita = {"Authorization": f"Bearer dev:{fixture_id('kavita')}"}
    assert api.get("/v1/analytics/review", headers=kavita).json()["predicted"] == 0
    assert api.post(f"/v1/analytics/transactions/{item['transaction_id']}/category", json={"category": "other"}, headers=kavita).status_code == 404
    assert api.post(f"/v1/analytics/transactions/{uuid.uuid4()}/category", json={"category": "bogus"}, headers=ramesh).status_code == 422
    assert api.get("/v1/analytics/review", headers={"Authorization": f"Bearer dev:{fixture_id('priya')}"}).status_code == 403
