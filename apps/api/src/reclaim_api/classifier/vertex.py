"""Vertex AI jobs for the classifier. Dry-run by default: nothing is submitted (or billed)
unless --submit is passed explicitly with a configured project, region and staging bucket.

- Training: a Vertex AI CustomJob runs `reclaim_api.classifier.train_job` in the prebuilt
  scikit-learn training container and writes the artifact to Cloud Storage; the artifact is
  then registered in the Model Registry.
- Batch prediction: a CustomJob runs `reclaim_api.classifier.batch_job`, reading features from
  the BigQuery `features` query and writing `transaction_predictions` (with probabilities and
  the model version). A CustomJob is used rather than a prebuilt sklearn prediction container
  because those return labels without probabilities, and abstention needs probabilities.
- Online endpoint: optional and not deployed, to avoid idle cost.
"""

from dataclasses import dataclass, field
from typing import Any

TRAIN_IMAGE = "us-docker.pkg.dev/vertex-ai/training/sklearn-cpu.1-6:latest"
SERVE_IMAGE = "us-docker.pkg.dev/vertex-ai/prediction/sklearn-cpu.1-6:latest"


class NotConfigured(RuntimeError):
    pass


@dataclass
class JobSpec:
    kind: str
    display_name: str
    script: str
    image: str
    args: list[str]
    machine_type: str = "n1-standard-4"
    extra: dict[str, Any] = field(default_factory=dict)


def _require(**values: str | None) -> None:
    missing = [k for k, v in values.items() if not v]
    if missing:
        raise NotConfigured("missing config: " + ", ".join(missing))


def training_spec(*, bucket: str, version_tag: str) -> JobSpec:
    return JobSpec("training", f"reclaim-txn-clf-{version_tag}", "src/reclaim_api/classifier/train_job.py", TRAIN_IMAGE,
                   ["--output", f"gs://{bucket}/models/txn-clf/{version_tag}"])  # fmt: skip


def batch_spec(*, bucket: str, model_uri: str, dataset: str) -> JobSpec:
    return JobSpec("batch_prediction", "reclaim-txn-clf-batch", "src/reclaim_api/classifier/batch_job.py", TRAIN_IMAGE,
                   ["--model", model_uri, "--dataset", dataset], extra={"staging": f"gs://{bucket}/staging"})  # fmt: skip


def submit(spec: JobSpec, *, project: str | None, region: str | None, bucket: str | None, aiplatform: Any = None) -> dict[str, Any]:
    """Submit a CustomJob and, for training, register the artifact. Only called with --submit."""
    _require(GCP_PROJECT=project, VERTEX_LOCATION=region, MODELS_BUCKET=bucket)
    if aiplatform is None:
        from google.cloud import aiplatform  # imported lazily: large SDK, cloud path only
    aiplatform.init(project=project, location=region, staging_bucket=f"gs://{bucket}/staging")
    job = aiplatform.CustomJob.from_local_script(
        display_name=spec.display_name, script_path=spec.script, container_uri=spec.image, args=spec.args,
        requirements=["sqlglot", "google-cloud-bigquery", "pandas", "joblib"], machine_type=spec.machine_type,
    )  # fmt: skip
    job.run(sync=True)
    result: dict[str, Any] = {"job": spec.display_name, "state": str(getattr(job, "state", "unknown"))}
    if spec.kind == "training":
        model = aiplatform.Model.upload(
            display_name=spec.display_name, artifact_uri=spec.args[1], serving_container_image_uri=SERVE_IMAGE,
            labels={"synthetic": "true"}, description="Transaction categories; trained on synthetic data only.",
        )  # fmt: skip
        result["model_resource"] = getattr(model, "resource_name", None)
    return result
