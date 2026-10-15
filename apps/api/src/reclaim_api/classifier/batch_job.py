"""Vertex AI CustomJob entry point: batch prediction from BigQuery features to BigQuery.

BigQuery supplies the features (analytics/sql/features.sql) and receives predictions in
transaction_predictions, each row with the model version, confidence and abstention flag.
"""

import argparse
from datetime import UTC, datetime

from reclaim_api.analytics.warehouse import sql
from reclaim_api.classifier import model
from reclaim_api.classifier.predict import ModelClassifier
from reclaim_api.classifier.train_job import gcs_path


def main() -> None:
    from google.cloud import bigquery

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset", required=True)  # project.dataset
    args = parser.parse_args()
    client = bigquery.Client()
    cfg = bigquery.QueryJobConfig(default_dataset=args.dataset)
    rows = [dict(r.items()) for r in client.query(sql("features"), job_config=cfg).result()]
    preds = ModelClassifier(gcs_path(args.model).parent).classify(rows)
    now = datetime.now(UTC).isoformat()
    out = [{"transaction_id": r["transaction_id"], "tenant_key": r["tenant_key"], "model_version": p.model_version, "source": p.source,
            "predicted_category": p.category, "confidence": p.confidence, "abstained": p.abstained, "predicted_at": now}
           for r, p in zip(rows, preds, strict=True)]  # fmt: skip
    client.load_table_from_json(out, f"{args.dataset}.transaction_predictions").result()
    print(f"predicted {len(out)} transactions with {preds[0].model_version if preds else model.__name__}")


if __name__ == "__main__":
    main()
