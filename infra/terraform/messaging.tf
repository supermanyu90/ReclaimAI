# --- Pub/Sub ------------------------------------------------------------------------------------
resource "google_pubsub_topic" "topic" {
  for_each   = toset(["app-events", "analytics", "transactions"])
  name       = "reclaim-${each.key}"
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic" "dead_letter" {
  for_each = toset(["app-events", "analytics", "transactions"])
  name     = "reclaim-${each.key}-dlq"
}

# Dead letters are kept for inspection and replay; an alert fires when any arrive.
resource "google_pubsub_subscription" "dead_letter" {
  for_each                   = google_pubsub_topic.dead_letter
  name                       = "${each.value.name}-hold"
  topic                      = each.value.id
  message_retention_duration = "604800s"
  ack_deadline_seconds       = 60
}

resource "google_pubsub_topic_iam_member" "dlq_publisher" {
  for_each = google_pubsub_topic.dead_letter
  topic    = each.value.id
  role     = "roles/pubsub.publisher"
  member   = local.pubsub_agent
}

resource "google_pubsub_topic_iam_member" "worker_publishes" {
  for_each = google_pubsub_topic.topic
  topic    = each.value.id
  role     = "roles/pubsub.publisher"
  member   = local.sa["worker"] # the outbox publisher runs in the worker
}

# Application events → worker, authenticated push (OIDC as reclaim-pubsub-push), bounded retries.
resource "google_pubsub_subscription" "worker_push" {
  name                       = "reclaim-app-events-worker"
  topic                      = google_pubsub_topic.topic["app-events"].id
  ack_deadline_seconds       = 120
  message_retention_duration = "604800s"
  enable_message_ordering    = true
  push_config {
    push_endpoint = "${google_cloud_run_v2_service.worker.uri}/pubsub/push"
    oidc_token {
      service_account_email = google_service_account.sa["pubsub-push"].email
      audience              = google_cloud_run_v2_service.worker.uri
    }
  }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter["app-events"].id
    max_delivery_attempts = 8
  }
}

resource "google_pubsub_subscription_iam_member" "dlq_forwarder" {
  for_each     = { worker = google_pubsub_subscription.worker_push.name, analytics = google_pubsub_subscription.bq["analytics"].name, transactions = google_pubsub_subscription.bq["transactions"].name }
  subscription = each.value
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}

# --- BigQuery (analytical copies only; Cloud SQL stays the source of truth) ------------------------
resource "google_bigquery_dataset" "analytics" {
  dataset_id                  = "reclaim_analytics"
  location                    = var.bigquery_location
  delete_contents_on_destroy  = false
  default_table_expiration_ms = null
  depends_on                  = [google_project_service.apis]
}

locals {
  envelope_schema = jsonencode([
    { name = "event_id", type = "STRING", mode = "REQUIRED" },
    { name = "schema_version", type = "INT64", mode = "REQUIRED" },
    { name = "tenant_id", type = "STRING", mode = "REQUIRED", description = "keyed hash, never the tenant UUID" },
    { name = "aggregate_type", type = "STRING", mode = "REQUIRED" },
    { name = "aggregate_id", type = "STRING", mode = "REQUIRED" },
    { name = "aggregate_version", type = "INT64", mode = "REQUIRED" },
    { name = "event_type", type = "STRING", mode = "REQUIRED" },
    { name = "occurred_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "correlation_id", type = "STRING", mode = "NULLABLE" },
    { name = "payload", type = "JSON", mode = "REQUIRED" },
    { name = "subscription_name", type = "STRING", mode = "NULLABLE" },
    { name = "message_id", type = "STRING", mode = "NULLABLE" },
    { name = "publish_time", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "attributes", type = "JSON", mode = "NULLABLE" },
  ])
  sql_dir = "${path.module}/../../apps/api/src/reclaim_api/analytics/sql"
}

resource "google_bigquery_table" "raw" {
  for_each            = toset(["transactions", "analytics"])
  dataset_id          = google_bigquery_dataset.analytics.dataset_id
  table_id            = "${each.key}_raw"
  schema              = local.envelope_schema
  clustering          = ["tenant_id", "event_type"]
  deletion_protection = true
  time_partitioning {
    type  = "DAY"
    field = "occurred_at" # event date: late events land in their own partition
  }
}

resource "google_bigquery_table" "predictions" {
  dataset_id          = google_bigquery_dataset.analytics.dataset_id
  table_id            = "transaction_predictions"
  deletion_protection = true
  clustering          = ["tenant_key"]
  time_partitioning {
    type  = "DAY"
    field = "predicted_at"
  }
  schema = jsonencode([
    { name = "transaction_id", type = "STRING", mode = "REQUIRED" },
    { name = "tenant_key", type = "STRING", mode = "REQUIRED" },
    { name = "model_version", type = "STRING", mode = "REQUIRED" },
    { name = "source", type = "STRING", mode = "REQUIRED" },
    { name = "predicted_category", type = "STRING", mode = "NULLABLE" },
    { name = "confidence", type = "FLOAT64", mode = "NULLABLE" },
    { name = "abstained", type = "BOOL", mode = "REQUIRED" },
    { name = "predicted_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

# Views reuse the exact SQL the application runs (and the tests run locally via sqlglot).
resource "google_bigquery_table" "view" {
  for_each            = { transactions = "transactions_raw", asset_events = "analytics_raw" }
  dataset_id          = google_bigquery_dataset.analytics.dataset_id
  table_id            = each.key
  deletion_protection = false
  view {
    use_legacy_sql = false
    query = replace(file("${local.sql_dir}/view_${each.key}.sql"), "FROM ${each.value}",
    "FROM `${var.project_id}.${google_bigquery_dataset.analytics.dataset_id}.${each.value}`")
  }
  depends_on = [google_bigquery_table.raw]
}

resource "google_bigquery_dataset_iam_member" "pubsub_writer" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = local.pubsub_agent
}

resource "google_bigquery_dataset_iam_member" "readers" {
  for_each   = toset(["api", "worker"])
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  role       = each.key == "worker" ? "roles/bigquery.dataEditor" : "roles/bigquery.dataViewer"
  member     = local.sa[each.key]
}

resource "google_pubsub_subscription" "bq" {
  for_each = toset(["analytics", "transactions"])
  name     = "reclaim-${each.key}-bigquery"
  topic    = google_pubsub_topic.topic[each.key].id
  bigquery_config {
    table            = "${var.project_id}.${google_bigquery_dataset.analytics.dataset_id}.${google_bigquery_table.raw[each.key].table_id}"
    use_table_schema = true
    write_metadata   = true
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter[each.key].id
    max_delivery_attempts = 5
  }
  depends_on = [google_bigquery_dataset_iam_member.pubsub_writer]
}
