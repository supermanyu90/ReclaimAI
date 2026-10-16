#!/usr/bin/env bash
# Hosted synthetic demo on Cloud Run (APP_MODE=demo). See docs/HOSTED_DEMO.md.
#
#   infra/demo/deploy.sh plan       what each step creates (no changes)
#   infra/demo/deploy.sh project    project, billing link, APIs, budget alert        (billable from here)
#   infra/demo/deploy.sh infra      registry, bucket, Cloud SQL, secrets, service accounts
#   infra/demo/deploy.sh build      Cloud Build: api, worker, web images
#   infra/demo/deploy.sh deploy     model upload, Cloud Run services + jobs, migrate, demo reset
#   infra/demo/deploy.sh status     URLs and readiness
#   infra/demo/deploy.sh reset      run the demo-reset job (back to 6 assets)
#   infra/demo/deploy.sh destroy    delete every ReclaimAI resource (the project itself stays)
#
# Configuration (environment): PROJECT_ID, BILLING_ACCOUNT, REGION (asia-south1),
# GEMINI_MODEL, VERTEX_LOCATION, BUDGET_AMOUNT + BUDGET_CURRENCY.
set -euo pipefail
cd "$(dirname "$0")/../.."

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"
VERTEX_LOCATION="${VERTEX_LOCATION:-$REGION}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"
BUDGET_AMOUNT="${BUDGET_AMOUNT:-2000}"
BUDGET_CURRENCY="${BUDGET_CURRENCY:-INR}"
SQL_INSTANCE=reclaim-db
BUCKET="${PROJECT_ID}-reclaim-files"
REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/reclaimai"
g() { gcloud --project "$PROJECT_ID" --quiet "$@"; }
number() { gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)'; }
url() { echo "https://reclaim-$1-$(number).${REGION}.run.app"; }
sa() { echo "reclaim-$1@${PROJECT_ID}.iam.gserviceaccount.com"; }
secret() {  # create a secret with a random value once; never print it
  g secrets describe "$1" >/dev/null 2>&1 || { openssl rand -hex "${2:-32}" | tr -d '\n' | g secrets create "$1" --data-file=- --replication-policy=user-managed --locations="$REGION"; }
}

plan() {
  cat <<PLAN
project ${PROJECT_ID} in ${REGION}, billed to ${BILLING_ACCOUNT:-<BILLING_ACCOUNT>}
  billable, always on : Cloud SQL PostgreSQL 16, db-f1-micro (shared core), 10 GB SSD, no HA
                        reclaim-worker: 1 instance kept warm (it runs the outbox loop)
  billable, per use   : Cloud Run api + web (scale to zero, max 3), Vertex AI Gemini (${GEMINI_MODEL}),
                        Cloud Build (image builds), Artifact Registry + Cloud Storage (small)
  free / config       : service accounts, IAM, Secret Manager (few versions), budget alert
  budget alert        : ${BUDGET_AMOUNT} ${BUDGET_CURRENCY}/month at 50/90/100% (an alert, NOT a cap)
  access              : web public (synthetic-person sign-in); api private (only the web service may call it)
  teardown            : infra/demo/deploy.sh destroy  (deletes the ReclaimAI resources only)
PLAN
}

project() {
  : "${BILLING_ACCOUNT:?set BILLING_ACCOUNT}"
  gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1 || gcloud projects create "$PROJECT_ID" --name="ReclaimAI demo"
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
  g services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com \
    cloudbuild.googleapis.com aiplatform.googleapis.com storage.googleapis.com iam.googleapis.com billingbudgets.googleapis.com
  gcloud --billing-project="$PROJECT_ID" billing budgets list --billing-account="$BILLING_ACCOUNT" --format='value(displayName)' | grep -qx "ReclaimAI demo" ||
    gcloud --billing-project="$PROJECT_ID" billing budgets create --billing-account="$BILLING_ACCOUNT" --display-name="ReclaimAI demo" \
      --budget-amount="${BUDGET_AMOUNT}${BUDGET_CURRENCY}" --filter-projects="projects/${PROJECT_ID}" \
      --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
}

infra() {
  g artifacts repositories describe reclaimai --location="$REGION" >/dev/null 2>&1 ||
    g artifacts repositories create reclaimai --repository-format=docker --location="$REGION"
  g storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 ||
    g storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
  # Public IP with no authorized networks: only the Cloud SQL connector (IAM-authorized) can reach it.
  g sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1 ||
    g sql instances create "$SQL_INSTANCE" --database-version=POSTGRES_16 --edition=ENTERPRISE --tier=db-f1-micro \
      --region="$REGION" --storage-size=10 --storage-type=SSD --backup-start-time=20:00
  g sql databases describe reclaim --instance="$SQL_INSTANCE" >/dev/null 2>&1 || g sql databases create reclaim --instance="$SQL_INSTANCE"
  if ! g secrets describe reclaim-database-url >/dev/null 2>&1; then
    pw=$(openssl rand -hex 24)
    g sql users create reclaim --instance="$SQL_INSTANCE" --password="$pw" >/dev/null
    conn=$(g sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')
    printf 'postgresql+psycopg://reclaim:%s@/reclaim?host=/cloudsql/%s' "$pw" "$conn" |
      g secrets create reclaim-database-url --data-file=- --replication-policy=user-managed --locations="$REGION"
    unset pw
  fi
  secret reclaim-channel-hash-key
  secret reclaim-worker-push-token
  for s in api worker web; do
    g iam service-accounts describe "$(sa $s)" >/dev/null 2>&1 || g iam service-accounts create "reclaim-$s" --display-name="ReclaimAI $s"
  done
  for s in api worker; do
    for role in roles/cloudsql.client roles/aiplatform.user roles/logging.logWriter; do
      g projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$(sa $s)" --role="$role" --condition=None >/dev/null
    done
    g storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$(sa $s)" --role=roles/storage.objectUser >/dev/null
    for sec in reclaim-database-url reclaim-channel-hash-key reclaim-worker-push-token; do
      g secrets add-iam-policy-binding "$sec" --member="serviceAccount:$(sa $s)" --role=roles/secretmanager.secretAccessor >/dev/null
    done
  done
  g projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$(sa web)" --role=roles/logging.logWriter --condition=None >/dev/null
  # Cloud Build's default identity pushes images and writes build logs.
  build_sa="$(number)-compute@developer.gserviceaccount.com"
  for role in roles/artifactregistry.writer roles/logging.logWriter roles/storage.objectViewer; do
    g projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$build_sa" --role="$role" --condition=None >/dev/null
  done
}

build() {
  g builds submit --region="$REGION" --config=infra/demo/cloudbuild.yaml --substitutions="_REPO=${REPO},_TAG=latest" .
}

common_env() {
  echo "APP_MODE=demo,LOG_LEVEL=INFO,GCP_PROJECT=${PROJECT_ID},VERTEX_LOCATION=${VERTEX_LOCATION},GEMINI_MODEL=${GEMINI_MODEL},GCS_BUCKET=${BUCKET},MODELS_DIR=/models/models,WORKER_URL=$(url worker),TELEGRAM_SEND_MODE=dry_run"
}
SECRETS="DATABASE_URL=reclaim-database-url:latest,CHANNEL_HASH_KEY=reclaim-channel-hash-key:latest,WORKER_PUSH_TOKEN=reclaim-worker-push-token:latest"
python_flags() {
  local conn; conn=$(g sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')
  echo "--region=$REGION --set-cloudsql-instances=$conn --set-secrets=$SECRETS --execution-environment=gen2 \
    --add-volume=name=models,type=cloud-storage,bucket=$BUCKET,readonly=true --add-volume-mount=volume=models,mount-path=/models"
}

deploy() {
  local ver; ver=$(cat .data/models/LATEST)
  g storage cp .data/models/LATEST "gs://$BUCKET/models/LATEST"
  g storage cp -r ".data/models/$ver" "gs://$BUCKET/models/"
  # shellcheck disable=SC2046
  g run deploy reclaim-worker --image="$REPO/worker:latest" --service-account="$(sa worker)" $(python_flags) \
    --set-env-vars="$(common_env),OUTBOX_LOOP=true" --min-instances=1 --max-instances=1 --no-cpu-throttling --memory=1Gi \
    --allow-unauthenticated   # app-level: every endpoint requires WORKER_PUSH_TOKEN
  # shellcheck disable=SC2046
  g run deploy reclaim-api --image="$REPO/api:latest" --service-account="$(sa api)" $(python_flags) \
    --set-env-vars="$(common_env),WEB_ORIGIN=$(url web)" --min-instances=0 --max-instances=3 --memory=1Gi --timeout=120 \
    --no-allow-unauthenticated
  g run services add-iam-policy-binding reclaim-api --region="$REGION" --member="serviceAccount:$(sa web)" --role=roles/run.invoker >/dev/null
  g run deploy reclaim-web --image="$REPO/web:latest" --service-account="$(sa web)" --region="$REGION" \
    --set-env-vars="API_URL=$(url api),API_AUDIENCE=$(url api)" \
    --min-instances=0 --max-instances=3 --memory=512Mi --allow-unauthenticated
  for job in migrate reset; do
    if [ "$job" = migrate ]; then cmd="alembic,-c,apps/api/alembic.ini,upgrade,head"; else cmd="reclaim-demo,reset"; fi
    # shellcheck disable=SC2046
    g run jobs deploy "reclaim-$job" --image="$REPO/api:latest" --service-account="$(sa api)" $(python_flags) \
      --set-env-vars="$(common_env)" --command="${cmd%%,*}" --args="${cmd#*,}" --max-retries=0 --task-timeout=600 --memory=1Gi
  done
  g run jobs execute reclaim-migrate --region="$REGION" --wait
  g run jobs execute reclaim-reset --region="$REGION" --wait
  status
}

status() {
  echo "web    $(url web)"
  echo "api    $(url api)   (private)"
  echo "worker $(url worker)"
  curl -s -o /dev/null -w "web /signin → %{http_code}\n" "$(url web)/signin"
  curl -s -o /dev/null -w "api without identity → %{http_code} (expect 403)\n" "$(url api)/readyz"
  curl -s -o /dev/null -w "worker task without token → %{http_code} (expect 401)\n" -X POST "$(url worker)/tasks/publish-outbox"
}

reset() { g run jobs execute reclaim-reset --region="$REGION" --wait; }
destroy() {
  # Deletes only what this script created (the project may hold other work). Cloud SQL goes
  # first: it is the main ongoing cost. Billing stays linked; unlink it yourself if unused.
  g run services delete reclaim-web --region="$REGION" || true
  g run services delete reclaim-api --region="$REGION" || true
  g run services delete reclaim-worker --region="$REGION" || true
  g run jobs delete reclaim-migrate --region="$REGION" || true
  g run jobs delete reclaim-reset --region="$REGION" || true
  g sql instances delete "$SQL_INSTANCE" || true
  for sec in reclaim-database-url reclaim-channel-hash-key reclaim-worker-push-token; do g secrets delete "$sec" || true; done
  g storage rm -r "gs://$BUCKET" || true
  g artifacts repositories delete reclaimai --location="$REGION" || true
  for s in api worker web; do g iam service-accounts delete "$(sa $s)" || true; done
  gcloud --billing-project="$PROJECT_ID" billing budgets list --billing-account="${BILLING_ACCOUNT:?set BILLING_ACCOUNT}" --format='value(name,displayName)' |
    awk -F'\t' '$2=="ReclaimAI demo"{print $1}' | xargs -r -n1 gcloud --billing-project="$PROJECT_ID" billing budgets delete --quiet
}

"${1:-plan}"
