terraform {
  required_version = ">= 1.6"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.10" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Configure a remote state bucket before a real apply, e.g.
  # backend "gcs" { bucket = "<state-bucket>" prefix = "reclaimai" }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "this" {}
