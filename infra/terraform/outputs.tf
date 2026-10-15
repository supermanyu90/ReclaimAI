output "urls" { value = local.url }
output "artifact_registry" { value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}" }
output "cloud_sql_connection_name" { value = google_sql_database_instance.main.connection_name }
output "documents_bucket" { value = google_storage_bucket.private["documents"].name }
output "models_bucket" { value = google_storage_bucket.private["models"].name }
# No secret values are output.
