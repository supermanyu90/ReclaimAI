# --- Cloud SQL (operational source of truth) ----------------------------------------------
resource "google_sql_database_instance" "main" {
  name                = "reclaimai-pg"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = true
  settings {
    tier                        = var.cloud_sql_tier
    availability_type           = "ZONAL" # REGIONAL for production HA
    deletion_protection_enabled = true
    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings { retained_backups = 14 }
    }
    ip_configuration {
      ipv4_enabled = true # reachable only through the Cloud SQL connector; no authorized networks
      ssl_mode     = "ENCRYPTED_ONLY"
    }
    insights_config { query_insights_enabled = true }
    maintenance_window {
      day  = 7
      hour = 21
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "app" {
  name     = "reclaim"
  instance = google_sql_database_instance.main.name
}

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "google_sql_user" "app" {
  name     = "reclaim"
  instance = google_sql_database_instance.main.name
  password = random_password.db.result
}

# --- Secrets (never in images, env files or Terraform outputs) -------------------------------
resource "random_password" "channel_hash_key" {
  length  = 48
  special = false
}

locals {
  database_url = "postgresql+psycopg://reclaim:${random_password.db.result}@/reclaim?host=/cloudsql/${google_sql_database_instance.main.connection_name}"
  generated_secrets = {
    "database-url"     = local.database_url
    "channel-hash-key" = random_password.channel_hash_key.result
  }
  manual_secrets = ["telegram-bot-token", "telegram-webhook-secret"] # add versions by hand if Telegram is enabled
}

resource "google_secret_manager_secret" "generated" {
  for_each  = local.generated_secrets
  secret_id = "reclaim-${each.key}"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "generated" {
  for_each    = local.generated_secrets
  secret      = google_secret_manager_secret.generated[each.key].id
  secret_data = each.value
}

resource "google_secret_manager_secret" "manual" {
  for_each  = toset(local.manual_secrets)
  secret_id = "reclaim-${each.key}"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

locals {
  secret_access = {
    api     = ["database-url", "channel-hash-key"]
    worker  = ["database-url", "channel-hash-key"]
    migrate = ["database-url"]
  }
  secret_bindings = flatten([for who, names in local.secret_access : [for n in names : { who = who, name = n }]])
}

resource "google_secret_manager_secret_iam_member" "access" {
  for_each  = { for b in local.secret_bindings : "${b.who}-${b.name}" => b }
  secret_id = google_secret_manager_secret.generated[each.value.name].id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.sa[each.value.who]
}

resource "google_secret_manager_secret_iam_member" "telegram" {
  for_each  = var.enable_telegram ? toset(local.manual_secrets) : toset([])
  secret_id = google_secret_manager_secret.manual[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.sa["api"]
}

# --- Private buckets (documents and passports; model artifacts) --------------------------------
resource "google_storage_bucket" "private" {
  for_each                    = toset(["documents", "models"])
  name                        = "${var.project_id}-reclaim-${each.key}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  lifecycle_rule {
    condition {
      num_newer_versions = 3
      with_state         = "ARCHIVED"
    }
    action { type = "Delete" }
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "documents" {
  for_each = toset(["api", "worker"])
  bucket   = google_storage_bucket.private["documents"].name
  role     = "roles/storage.objectUser"
  member   = local.sa[each.key]
}

resource "google_storage_bucket_iam_member" "models_read" {
  for_each = toset(["api", "worker"])
  bucket   = google_storage_bucket.private["models"].name
  role     = "roles/storage.objectViewer"
  member   = local.sa[each.key]
}
