# Deploying ReclaimAI to Google Cloud

**Status: prepared and validated offline, never applied.** No Google Cloud resource has been
created for this project. `tofu validate` and `tofu fmt -check` pass, the three images build
and pass local smoke tests, and the migrate job's command runs in the API image against a
local database. Nothing below has been run against a real project. Applying it creates billable
resources (Cloud SQL above all), so do it only on a separate, explicit instruction.

## What gets created (`infra/terraform`)

| File | Resources |
|---|---|
| `foundation.tf` | Required APIs, Artifact Registry repo `reclaimai`, one service account per workload (`reclaim-api`, `-worker`, `-web`, `-pubsub-push`, `-scheduler`, `-migrate`) with only the roles it needs |
| `data.tf` | Cloud SQL PostgreSQL 16 (no public authorized networks, TLS only, PITR, 14 backups, deletion protection); Secret Manager (`reclaim-database-url`, `reclaim-channel-hash-key` generated; Telegram secrets created empty); private buckets `<project>-reclaim-documents` and `<project>-reclaim-models` (uniform access, public access prevention enforced, versioning) |
| `messaging.tf` | Topics `reclaim-app-events`, `-analytics`, `-transactions`, each with a dead-letter topic and a `-dlq-hold` pull subscription; authenticated push subscription to the worker (OIDC, 10 s–600 s backoff, 8 attempts, ordered); BigQuery dataset `reclaim_analytics` with raw tables, the dedup views from `analytics/sql/view_*.sql`, and BigQuery subscriptions |
| `run.tf` | Cloud Run services `reclaim-api` (public; the API requires an Identity Platform token on every `/v1` route), `reclaim-worker` (invoker only for the push and scheduler accounts), `reclaim-web` (public); Cloud Run job `reclaim-migrate`; Cloud Scheduler job running the outbox every minute |
| `monitoring.tf` | Email channel, monthly budget alert at 50 / 90 / 100 % (an alert, **not a spending cap**), DLQ backlog and outbox dead-letter alerts, Cloud Run 5xx alert |

Service-to-service identity: the worker accepts `/pubsub/push` only with an OIDC token from
`reclaim-pubsub-push` and `/tasks/publish-outbox` only from `reclaim-scheduler`, both for its
own URL as audience (`tests/api/test_events_db.py`). No service account keys are created;
workloads use their attached identity (ADC).

## Prerequisites

1. A project with billing, and permission to create the resources above (Owner on a sandbox project is simplest).
2. `gcloud`, Docker and OpenTofu ≥ 1.6 (or Terraform ≥ 1.5).
3. **Check every region before choosing it.** Products are not all available everywhere:
   - Gemini model ID and region: Vertex AI model garden → set `gemini_model`, `vertex_location`.
   - Document AI processors exist only in multi-regions such as `us` and `eu`. Check data residency for your use case before sending documents there → `docai_location`.
   - Vertex AI Search datastores are `global`, `us` or `eu` → `search_location`.
   - Cloud Run, Cloud SQL, BigQuery and Scheduler in `region` / `bigquery_location` (default `asia-south1`).

## Order of operations

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill it in; it is gitignored
# Keep state remote and private: it contains the generated database password.
#   terraform { backend "gcs" { bucket = "<state-bucket>" prefix = "reclaimai" } }
tofu init

# 1. APIs, registry and service accounts first, so the images have somewhere to go.
tofu apply -target=google_project_service.apis -target=google_artifact_registry_repository.images

# 2. Build and push images (from the repository root).
REG=asia-south1-docker.pkg.dev/$PROJECT/reclaimai; TAG=$(git rev-parse --short HEAD)
gcloud auth configure-docker asia-south1-docker.pkg.dev
for pair in api:apps/api/Dockerfile worker:workers/Dockerfile web:apps/web/Dockerfile; do
  docker build --platform linux/amd64 -f ${pair#*:} -t $REG/${pair%%:*}:$TAG .
  docker push $REG/${pair%%:*}:$TAG
done

# 3. Everything else.
tofu apply

# 4. Migrate before sending traffic, and after every release that adds a migration.
gcloud run jobs execute reclaim-migrate --region asia-south1 --wait
```

Migrations are forward-only Alembic revisions (`apps/api/migrations`); review each before a
release. Roll back by deploying the previous image; a schema rollback is a new migration, never
`downgrade` against production data. Cloud SQL PITR covers mistakes in between.

## Manual steps (not in Terraform)

1. **Identity Platform.** Enable it, turn on the Email/Password or Google provider, and add the
   web origin to authorized domains. Create each person with the **same email** as their `users`
   row: on first sign-in the API links the account by verified email, exactly once, then only by
   subject. Unknown emails are never provisioned. *Gap:* the web app's cloud sign-in screen
   (Identity Platform JS SDK) is not built yet; locally the app uses synthetic dev tokens.
2. **Document AI.** Create an OCR processor (and optionally a custom extractor) in
   `docai_location`; put their IDs in `docai_processor_id` / `docai_extractor_id`.
3. **Vertex AI Search.** Create a datastore for structured data in `search_location`, then:
   ```bash
   uv run reclaim-policy-export --out build/policies.jsonl     # approved sections only
   gcloud storage cp build/policies.jsonl gs://$PROJECT-reclaim-models/search/
   # Console: Datastore → Import → Cloud Storage → JSONL with structData.
   ```
   Mark `jurisdiction`, `institution`, `asset_types` and `review_status` filterable and `text`,
   `heading` searchable in the schema. The API re-checks applicability in code anyway.
4. **Classifier model.** Upload the model directory so the GCS volume serves it:
   `gcloud storage cp -r models/txn-clf/<version>/* gs://$PROJECT-reclaim-models/models/txn-clf/latest/`.
   Optional Vertex training/batch: `uv run reclaim-classifier vertex-train --submit` (dry-run
   without `--submit`; it creates a billable CustomJob).
5. **Telegram (optional).** Add versions to `reclaim-telegram-bot-token` and
   `reclaim-telegram-webhook-secret`, set `enable_telegram = true`, apply, then call
   `setWebhook` with `secret_token`. Leave it off for the demo; replies stay dry-run.
6. **Seed data.** Demo tenants are synthetic. Do not run `reclaim-seed` against a database that
   holds real people's data.

## Limits, retries and cost controls

- Cloud Run: `max_instances` (default 3) per service; API concurrency 40, worker 4; API timeout 60 s, worker 300 s.
- Pub/Sub push: exponential backoff 10 s–600 s, dead letter after 8 attempts; BigQuery subscriptions after 5. The outbox has its own bounded attempts (`OUTBOX_MAX_ATTEMPTS`, default 8) and dead-letters with an ERROR log that raises an alert.
- Scheduler: one outbox pass a minute, 1 retry.
- Budget: alerts only. Spending continues after 100 % unless someone acts.
- Vertex AI, Document AI and Search quotas: check them in the console and raise them before a demo. The app reports `unavailable` instead of retrying forever when a call fails.

## Data retention and deletion

- Cloud SQL: automated daily backups (14 kept) and point-in-time recovery; deletion protection on.
- Buckets: versioned, never public, no signed URLs; the API streams objects after its own permission check.
- BigQuery raw tables hold only minimized envelopes (keyed-hash IDs, no free text). Add a partition expiration if you need a retention limit.
- Logs: structured JSON with route templates and correlation IDs. No message text, document content or record IDs (`make pii` enforces this).

## Cleanup

```bash
cd infra/terraform
# Cloud SQL has deletion protection; turn it off first, deliberately:
#   set deletion_protection = false on google_sql_database_instance.main, then `tofu apply`
tofu destroy
# Buckets are not force-destroyed: empty them yourself after checking what is in them.
gcloud storage rm -r gs://$PROJECT-reclaim-documents gs://$PROJECT-reclaim-models
```
