# Hosted demo on Cloud Run (`APP_MODE=demo`)

A shareable copy of the local demo: synthetic data only, behind a passcode. This is not the
full cloud architecture in `docs/DEPLOYMENT.md` (Identity Platform, Document AI, Vertex AI
Search, Pub/Sub, BigQuery); it keeps those parts built in, as locally.

| Part | Hosted demo | Local | Full cloud (Terraform) |
|---|---|---|---|
| Web, API, worker | Cloud Run | your machine | Cloud Run |
| Database | Cloud SQL PostgreSQL 16, db-f1-micro | Docker Postgres | Cloud SQL |
| Sign-in | synthetic-person picker, behind a passcode | picker | Identity Platform |
| LLM | Gemini on Vertex AI | Gemma 3 4B via Ollama | Gemini |
| Files | private Cloud Storage bucket | local disk | Cloud Storage |
| Documents, policy search, analytics, events | built in (same as local) | built in | Document AI, Vertex AI Search, BigQuery, Pub/Sub |

## Access

- **Web** is public, but every page and API call needs the passcode first. The passcode is a random secret in Secret Manager; read it with the command `deploy.sh status` prints.
- **API** is private. Cloud Run admits only the web service's identity, sent as `X-Serverless-Authorization`, so the app's own `Authorization` header stays free.
- **Worker** is reachable at the network level, but every endpoint requires `WORKER_PUSH_TOKEN` (Secret Manager). The built-in local token is refused in demo mode.
- **Secrets:** no secret is in the images or the repository. `.gcloudignore` keeps `.env` and `.data/` out of Cloud Build uploads.

## Commands

```bash
export PROJECT_ID=reclaimai-demo-XXXX BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX
infra/demo/deploy.sh plan      # what will be created; changes nothing
infra/demo/deploy.sh project   # project, billing link, APIs, budget alert
infra/demo/deploy.sh infra     # registry, bucket, Cloud SQL, secrets, service accounts
infra/demo/deploy.sh build     # Cloud Build: three images
infra/demo/deploy.sh deploy    # services, jobs, migrate, demo reset
infra/demo/deploy.sh status    # URLs, plus checks that the API is private and the worker locked
infra/demo/deploy.sh reset     # before each presentation: back to 6 assets
infra/demo/deploy.sh destroy   # delete the ReclaimAI resources (Cloud SQL first): stops their charges
```

## Costs

- **Always on:** the Cloud SQL instance (shared core, 10 GB) and one warm worker instance. The worker must stay warm because it runs the outbox loop that processes documents within seconds.
- **Per use:** Cloud Run for the API and web (both scale to zero), Gemini calls, and Cloud Build runs.
- **Budget alert:** set at 50/90/100% of `BUDGET_AMOUNT`. It only sends an alert; spending continues past it.
- **Stop all charges:** `destroy` deletes every ReclaimAI resource, Cloud SQL first. The project itself is left alone, since it may hold other work.

## Things to know

- **Hash key:** don't rotate `reclaim-channel-hash-key` without clearing the `warehouse` schema. Analytics rows are keyed by it, and de-duplication keeps the older copies.
- **Old files:** the demo reset clears the database rows. Uploaded files in the bucket stay, but nothing references them any more.
- **Gemini:** the model ID and region are checked when deploying. This is the first time the Gemini adapter runs against Vertex AI, so rehearse once before presenting: `reclaim-demo rehearse` cannot reach a private API, so use the browser script in `docs/DEMO.md`.
