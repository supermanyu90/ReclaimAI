locals {
  apis = [
    "run.googleapis.com", "sqladmin.googleapis.com", "secretmanager.googleapis.com", "pubsub.googleapis.com",
    "bigquery.googleapis.com", "aiplatform.googleapis.com", "documentai.googleapis.com", "discoveryengine.googleapis.com",
    "storage.googleapis.com", "iam.googleapis.com", "cloudscheduler.googleapis.com", "identitytoolkit.googleapis.com",
    "logging.googleapis.com", "monitoring.googleapis.com", "artifactregistry.googleapis.com", "billingbudgets.googleapis.com",
  ]
  pubsub_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "reclaimai"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# One identity per workload; no keys are created (Cloud Run uses its attached service account via ADC).
resource "google_service_account" "sa" {
  for_each     = toset(["api", "worker", "web", "pubsub-push", "scheduler", "migrate"])
  account_id   = "reclaim-${each.key}"
  display_name = "ReclaimAI ${each.key}"
}

locals {
  sa = { for k, v in google_service_account.sa : k => "serviceAccount:${v.email}" }
  project_roles = {
    api     = ["roles/cloudsql.client", "roles/aiplatform.user", "roles/discoveryengine.viewer", "roles/bigquery.jobUser", "roles/logging.logWriter", "roles/monitoring.metricWriter"]
    worker  = ["roles/cloudsql.client", "roles/aiplatform.user", "roles/documentai.apiUser", "roles/logging.logWriter", "roles/monitoring.metricWriter"]
    web     = ["roles/logging.logWriter"]
    migrate = ["roles/cloudsql.client", "roles/logging.logWriter"]
  }
  project_bindings = flatten([for who, roles in local.project_roles : [for r in roles : { who = who, role = r }]])
}

resource "google_project_iam_member" "workloads" {
  for_each = { for b in local.project_bindings : "${b.who}-${b.role}" => b }
  project  = var.project_id
  role     = each.value.role
  member   = local.sa[each.value.who]
}

# Pub/Sub mints the OIDC token for authenticated push as reclaim-pubsub-push.
resource "google_service_account_iam_member" "pubsub_token_creator" {
  service_account_id = google_service_account.sa["pubsub-push"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.pubsub_agent
}
