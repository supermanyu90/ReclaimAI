# Locations are per service on purpose: not every product is offered in every region.
# Check current availability before choosing (see docs/DEPLOYMENT.md, "Regions").

variable "project_id" { type = string }
variable "region" {
  description = "Cloud Run, Cloud SQL, buckets, Artifact Registry."
  type        = string
  default     = "asia-south1"
}
variable "vertex_location" {
  description = "Gemini on Vertex AI. Verify the chosen model is offered here."
  type        = string
}
variable "gemini_model" {
  description = "Exact Gemini model ID; verify availability in vertex_location."
  type        = string
}
variable "docai_location" {
  description = "Document AI multi-region of the processors (for example \"us\" or \"eu\"); check data-residency needs."
  type        = string
}
variable "docai_processor_id" {
  description = "OCR processor created manually (docs/DEPLOYMENT.md)."
  type        = string
}
variable "docai_extractor_id" {
  type    = string
  default = ""
}
variable "search_location" {
  type    = string
  default = "global"
}
variable "search_datastore_id" {
  description = "Vertex AI Search datastore holding the approved policy corpus."
  type        = string
}
variable "bigquery_location" {
  type    = string
  default = "asia-south1"
}
variable "api_image" { type = string }
variable "worker_image" { type = string }
variable "web_image" { type = string }
variable "web_origin" {
  description = "Public URL of the web app (CORS and links)."
  type        = string
}
variable "cloud_sql_tier" {
  type    = string
  default = "db-custom-1-3840"
}
variable "max_instances" {
  description = "Upper bound per Cloud Run service (cost and abuse control)."
  type        = number
  default     = 3
}
variable "billing_account" {
  description = "Billing account ID for the budget alert."
  type        = string
}
variable "monthly_budget" {
  description = "Budget alert amount in var.budget_currency. An alert, not a spending cap."
  type        = number
  default     = 5000
}
variable "budget_currency" {
  type    = string
  default = "INR"
}
variable "alert_email" { type = string }
variable "enable_telegram" {
  description = "Wire Telegram secrets into the API. Add secret versions manually first."
  type        = bool
  default     = false
}
