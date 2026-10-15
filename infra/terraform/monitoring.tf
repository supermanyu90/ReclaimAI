resource "google_monitoring_notification_channel" "email" {
  display_name = "ReclaimAI alerts"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

# A budget ALERT. It does not stop spending; it emails at 50 %, 90 % and 100 %.
resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "ReclaimAI monthly"
  budget_filter { projects = ["projects/${data.google_project.this.number}"] }
  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.monthly_budget)
    }
  }
  threshold_rules { threshold_percent = 0.5 }
  threshold_rules { threshold_percent = 0.9 }
  threshold_rules { threshold_percent = 1.0 }
  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.email.id]
    disable_default_iam_recipients   = false
  }
  depends_on = [google_project_service.apis]
}

# The outbox logs at ERROR when an event is dead-lettered after bounded retries.
resource "google_logging_metric" "outbox_dead_letter" {
  name   = "reclaim/outbox_dead_lettered"
  filter = "resource.type=\"cloud_run_revision\" AND severity>=ERROR AND jsonPayload.message:\"outbox event dead-lettered\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "dead_letters" {
  display_name          = "ReclaimAI: messages in a dead-letter queue"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]
  conditions {
    display_name = "DLQ backlog > 0"
    condition_threshold {
      filter          = "resource.type=\"pubsub_subscription\" AND resource.labels.subscription_id=monitoring.regex.full_match(\"reclaim-.*-dlq-hold\") AND metric.type=\"pubsub.googleapis.com/subscription/num_undelivered_messages\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }
  conditions {
    display_name = "Outbox dead-lettered an event"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.outbox_dead_letter.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}

resource "google_monitoring_alert_policy" "errors" {
  display_name          = "ReclaimAI: Cloud Run 5xx responses"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]
  conditions {
    display_name = "5xx rate"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=monitoring.regex.full_match(\"reclaim-.*\") AND metric.type=\"run.googleapis.com/request_count\" AND metric.labels.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}
