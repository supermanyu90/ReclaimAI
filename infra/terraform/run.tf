# Cloud Run URLs are deterministic (<service>-<project number>.<region>.run.app), which lets the
# worker know its own OIDC audience without a dependency cycle.
locals {
  url        = { for s in ["api", "worker", "web"] : s => "https://reclaim-${s}-${data.google_project.this.number}.${var.region}.run.app" }
  models_dir = "/models/models/txn-clf/latest"
  common_env = {
    APP_MODE            = "cloud"
    LOG_LEVEL           = "INFO"
    GCP_PROJECT         = var.project_id
    VERTEX_LOCATION     = var.vertex_location
    GEMINI_MODEL        = var.gemini_model
    DOCAI_LOCATION      = var.docai_location
    DOCAI_PROCESSOR_ID  = var.docai_processor_id
    DOCAI_EXTRACTOR_ID  = var.docai_extractor_id
    SEARCH_LOCATION     = var.search_location
    SEARCH_DATASTORE_ID = var.search_datastore_id
    PUBSUB_TOPIC        = google_pubsub_topic.topic["app-events"].name
    ANALYTICS_TOPIC     = google_pubsub_topic.topic["analytics"].name
    TRANSACTIONS_TOPIC  = google_pubsub_topic.topic["transactions"].name
    GCS_BUCKET          = google_storage_bucket.private["documents"].name
    BIGQUERY_DATASET    = google_bigquery_dataset.analytics.dataset_id
    BIGQUERY_LOCATION   = var.bigquery_location
    WEB_ORIGIN          = var.web_origin
    MODELS_DIR          = local.models_dir
  }
  common_secrets = { DATABASE_URL = "database-url", CHANNEL_HASH_KEY = "channel-hash-key" }
}

resource "google_cloud_run_v2_service" "api" {
  name                = "reclaim-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL" # public endpoint; every /v1 route requires an Identity Platform token
  deletion_protection = false
  template {
    service_account                  = google_service_account.sa["api"].email
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    timeout                          = "60s"
    max_instance_request_concurrency = 40
    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }
    containers {
      image = var.api_image
      ports { container_port = 8080 }
      resources {
        limits            = { cpu = "1", memory = "1Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.common_secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.generated[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
      dynamic "env" {
        for_each = var.enable_telegram ? { TELEGRAM_BOT_TOKEN = "telegram-bot-token", TELEGRAM_WEBHOOK_SECRET = "telegram-webhook-secret" } : {}
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.manual[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      volume_mounts {
        name       = "models"
        mount_path = "/models"
      }
      startup_probe {
        http_get { path = "/healthz" }
      }
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance { instances = [google_sql_database_instance.main.connection_name] }
    }
    volumes {
      name = "models"
      gcs {
        bucket    = google_storage_bucket.private["models"].name
        read_only = true
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.access, google_project_iam_member.workloads]
}

resource "google_cloud_run_v2_service" "worker" {
  name                = "reclaim-worker"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL" # Pub/Sub push needs a reachable URL; IAM limits who may invoke it
  deletion_protection = false
  template {
    service_account                  = google_service_account.sa["worker"].email
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    timeout                          = "300s"
    max_instance_request_concurrency = 4
    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }
    containers {
      image = var.worker_image
      ports { container_port = 8080 }
      resources {
        limits = { cpu = "1", memory = "2Gi" }
      }
      dynamic "env" {
        for_each = merge(local.common_env, {
          OUTBOX_LOOP               = "false"
          WORKER_AUDIENCE           = local.url["worker"]
          PUSH_SERVICE_ACCOUNT      = google_service_account.sa["pubsub-push"].email
          SCHEDULER_SERVICE_ACCOUNT = google_service_account.sa["scheduler"].email
        })
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = local.common_secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.generated[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      volume_mounts {
        name       = "models"
        mount_path = "/models"
      }
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance { instances = [google_sql_database_instance.main.connection_name] }
    }
    volumes {
      name = "models"
      gcs {
        bucket    = google_storage_bucket.private["models"].name
        read_only = true
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.access, google_project_iam_member.workloads]
}

resource "google_cloud_run_v2_service" "web" {
  name                = "reclaim-web"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  template {
    service_account = google_service_account.sa["web"].email
    scaling {
      min_instance_count = 0
      max_instance_count = var.max_instances
    }
    containers {
      image = var.web_image
      ports { container_port = 8080 }
      env {
        name  = "API_URL"
        value = local.url["api"]
      }
    }
  }
}

# Migrations: run explicitly (gcloud run jobs execute reclaim-migrate) before shifting traffic.
resource "google_cloud_run_v2_job" "migrate" {
  name                = "reclaim-migrate"
  location            = var.region
  deletion_protection = false
  template {
    template {
      service_account = google_service_account.sa["migrate"].email
      max_retries     = 0
      timeout         = "600s"
      containers {
        image   = var.api_image
        command = ["alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"]
        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.generated["database-url"].secret_id
              version = "latest"
            }
          }
        }
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
      volumes {
        name = "cloudsql"
        cloud_sql_instance { instances = [google_sql_database_instance.main.connection_name] }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.access]
}

# Invokers: API and web are public (the API enforces its own authentication); the worker is not.
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = { api = google_cloud_run_v2_service.api.name, web = google_cloud_run_v2_service.web.name }
  name     = each.value
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "worker_invokers" {
  for_each = toset(["pubsub-push", "scheduler"])
  name     = google_cloud_run_v2_service.worker.name
  location = var.region
  role     = "roles/run.invoker"
  member   = local.sa[each.key]
}

# Outbox sweep: one publish pass per minute (the API never publishes directly).
resource "google_cloud_scheduler_job" "outbox" {
  name             = "reclaim-outbox-publish"
  region           = var.region
  schedule         = "* * * * *"
  attempt_deadline = "120s"
  retry_config { retry_count = 1 }
  http_target {
    http_method = "POST"
    uri         = "${local.url["worker"]}/tasks/publish-outbox"
    oidc_token {
      service_account_email = google_service_account.sa["scheduler"].email
      audience              = local.url["worker"]
    }
  }
  depends_on = [google_project_service.apis]
}
