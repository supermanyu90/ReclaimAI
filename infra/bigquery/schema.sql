-- BigQuery dataset for ReclaimAI analytics (apply with `bq query --use_legacy_sql=false`,
-- after replacing ${DATASET}). Nothing here has been applied to a real project yet.
--
-- Raw tables are written by Pub/Sub BigQuery subscriptions with "use table schema" and
-- "write metadata" enabled: envelope fields map to columns, publish_time/message_id/
-- subscription_name/attributes come from Pub/Sub. Delivery is at least once, so raw tables can
-- contain repeated event_ids; every query reads the deduplicating views.
-- Transaction events carry the booking time as occurred_at, so partitioning by
-- DATE(occurred_at) is partitioning by event date: late events land in their own partition.

CREATE TABLE IF NOT EXISTS `${DATASET}.transactions_raw` (
  event_id STRING NOT NULL, schema_version INT64 NOT NULL, tenant_id STRING NOT NULL,  -- tenant_id is a keyed hash
  aggregate_type STRING NOT NULL, aggregate_id STRING NOT NULL, aggregate_version INT64 NOT NULL,
  event_type STRING NOT NULL, occurred_at TIMESTAMP NOT NULL, correlation_id STRING, payload JSON NOT NULL,
  subscription_name STRING, message_id STRING, publish_time TIMESTAMP NOT NULL, attributes JSON
)
PARTITION BY DATE(occurred_at)
CLUSTER BY tenant_id, event_type
OPTIONS (require_partition_filter = FALSE, partition_expiration_days = 1095);

CREATE TABLE IF NOT EXISTS `${DATASET}.analytics_raw` (
  event_id STRING NOT NULL, schema_version INT64 NOT NULL, tenant_id STRING NOT NULL,
  aggregate_type STRING NOT NULL, aggregate_id STRING NOT NULL, aggregate_version INT64 NOT NULL,
  event_type STRING NOT NULL, occurred_at TIMESTAMP NOT NULL, correlation_id STRING, payload JSON NOT NULL,
  subscription_name STRING, message_id STRING, publish_time TIMESTAMP NOT NULL, attributes JSON
)
PARTITION BY DATE(occurred_at)
CLUSTER BY tenant_id, event_type;

-- Written by a subscription on each topic's dead-letter topic: messages BigQuery rejected.
CREATE TABLE IF NOT EXISTS `${DATASET}.ingest_errors` (
  topic STRING NOT NULL, reason STRING NOT NULL, event_id STRING, received_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(received_at);

-- Views: same SQL as apps/api/src/reclaim_api/analytics/sql/view_*.sql.
-- CREATE OR REPLACE VIEW `${DATASET}.transactions` AS <view_transactions.sql>;
-- CREATE OR REPLACE VIEW `${DATASET}.asset_events` AS <view_asset_events.sql>;

-- Written by the batch prediction job (classifier/batch_job.py). A prediction is never a
-- confirmed category; owner confirmations live in the operational database.
CREATE TABLE IF NOT EXISTS `${DATASET}.transaction_predictions` (
  transaction_id STRING NOT NULL, tenant_key STRING NOT NULL, model_version STRING NOT NULL, source STRING NOT NULL,
  predicted_category STRING, confidence FLOAT64, abstained BOOL NOT NULL, predicted_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(predicted_at)
CLUSTER BY tenant_key;
