# ReclaimAI Architecture

Modular monolith (FastAPI API) plus an asynchronous worker, a Next.js web app, and
PostgreSQL as the operational source of truth. Focused AI functions, not autonomous agents.

## Flow

```
 Owner (web chat / linked Telegram)
        │  authenticated
        ▼
 ┌──────────────┐  draft only   ┌──────────────────┐
 │   API        │──────────────▶│ Gemini (Vertex)  │  LLMProvider
 │  FastAPI     │◀──────────────│ typed JSON draft │
 │              │               └──────────────────┘
 │  authz on    │──evidence────▶ Document AI          DocumentProcessor
 │  every call  │──policy──────▶ Vertex AI Search     PolicyRetriever
 └──────┬───────┘
        │ owner confirms (separate authenticated action, not a model tool)
        ▼
 ┌──────────────────────────────┐
 │ PostgreSQL / Cloud SQL       │  one transaction:
 │ asset_versions + outbox      │  record version + outbox event
 └──────┬───────────────────────┘
        │ outbox publisher (retries)
        ▼
   Pub/Sub ──push (OIDC)──▶ Worker ──▶ projections, freshness findings,
   (EventPublisher)          idempotent   passport-stale flag, PDF jobs (GCS),
                             processed_events  minimized analytics ──▶ BigQuery
                                                                       │
                                               Vertex AI classifier ◀──┘

 Family release (backend state machine, never AI):
 PRIVATE → REQUESTED → VERIFICATION_PENDING → APPROVED → RELEASED
           (NEEDS_INFORMATION | HOLD | REJECTED)   two distinct reviewers
```

## Service boundaries

| Component | Path | Owns |
|---|---|---|
| Web | `apps/web` | UI only; calls the API server-side; holds no authorization logic |
| API | `apps/api` (`reclaim_api`) | Auth, authorization, proposals, confirmations, grants, handover, exports |
| Worker | `workers` (`reclaim_worker`) | Event consumers: projections, passports, analytics sinks |
| Providers | `reclaim_api/providers` | Interfaces + local fixtures + Google Cloud adapters |
| Migrations | `apps/api/migrations` | Alembic; the only way schema changes |

## API contract (current)

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness; no dependencies |
| GET | `/readyz` | DB reachable and migrated (200) or not (503) |
| GET | `/v1/system/capabilities` | Each provider: `live` / `fixture` / `unavailable` + reason |
| GET | `/v1/me` | The authenticated actor |
| GET | `/v1/assets` | Actor-scoped current asset versions (owner: own assets) |
| GET | `/v1/assets/{id}` | One asset; out-of-scope and missing both return 404 |
| GET | `/v1/inventory/totals` | Per-currency totals, one per amount kind, with completeness notes |
| POST | `/v1/chat/messages` | Owner message → extraction → draft proposal + reply (`ok`/`clarification`/`review`/`unavailable`/`invalid_output`/`blocked`) |
| GET | `/v1/chat/history` | Last 50 turns for the actor |
| GET / PATCH | `/v1/proposals/{id}` | Review card / owner edits (validated whitelist, sourced as `owner_edit`) |
| POST | `/v1/proposals/{id}/cancel` | Discard a draft |
| POST | `/v1/proposals/{id}/confirm` | **Owner-only, requires `Idempotency-Key`.** Asset version + evidence + confirmation + outbox + audit in one transaction; replay returns the same result; stale base version → 409 |
| GET | `/v1/dev/users` | Local mode only: synthetic users for the sign-in picker |
| POST | `/v1/documents` | Owner upload (multipart). Type sniffed from bytes, ≤10 MB, quarantined, 202 + background processing |
| GET | `/v1/documents`, `/v1/documents/{id}` | Status, kind, fields with page/span/confidence/processor, review reasons, possible duplicates |
| GET | `/v1/documents/{id}/content` | Owner download, only after processing |
| POST | `/v1/proposals/{id}/documents` | Attach a processed document to a draft: fills gaps, records disagreements as conflicts |
| POST | `/v1/proposals/{id}/resolve` | Owner picks draft or document value for a conflicting field |
| POST | `/v1/claims` | Checklist for an asset (owner preview now; designated family after release, Prompt 11), pinned to a policy version |
| GET | `/v1/claims/{id}` | Pinned checklist, flags a newer policy version |
| GET | `/v1/analytics` | Owner-scoped monthly flows per currency, review flags, duplicate rate, stale records, turnaround, pipeline errors |
| GET | `/v1/analytics/review` | Owner: abstained predictions to review, least-certain suggestions, model versions |
| POST | `/v1/analytics/transactions/{id}/category` | Owner confirms a category (stored apart from predictions) |
| GET | `/v1/workspace` | Owner: assets, recorded-relationship graph, timeline, review queue, findings, family-access preview |
| POST | `/v1/passports` | Owner generates a passport (or a recipient export) from a REPEATABLE READ snapshot |
| GET | `/v1/passports`, `/v1/passports/{id}/download` | Versions with current/stale; download re-checks permission and file hash |
| POST | `/v1/handovers` | Designated family member requests a handover (needs an active post-release grant) |
| GET | `/v1/handovers/{id}` | Requester, owner or reviewer; reviewers also get evidence checks, AI summary, sharing instructions |
| POST | `/v1/handovers/{id}/evidence`, `/submit` | Requester adds documents (bumps evidence version) and submits |
| POST | `/v1/handovers/{id}/decisions` | Reviewer: approve / hold / resume / needs_information / reject |
| GET | `/v1/reviews` | Reviewer queue (never the reviewer's own family case) |
| GET | `/v1/family`, `/v1/family/assets/{id}`; POST `/v1/family/claims` | Before release: allowed sections; after release: read-only granted assets, passport, evidence index, checklists |
| GET | `/v1/findings` | Owner's freshness findings from the worker-maintained read model |
| GET | `/v1/system/events` | Outbox and consumer health (counts only) |
| POST | worker `/pubsub/push` | Pub/Sub push (OIDC from the push service account; local shared token in local mode) |
| POST | worker `/tasks/publish-outbox` | One outbox pass (Cloud Scheduler in cloud; same auth) |
| POST | `/v1/telegram/webhook` | Telegram → API. Requires `X-Telegram-Bot-Api-Secret-Token` (503 if unset, 401 if wrong) |
| POST | `/v1/channels/telegram/link-code` | Owner: single-use 8-char code, 10-min TTL, stored hashed |
| GET / POST | `/v1/channels/telegram`, `/unlink` | Link status; unlink from the web |

`/v1/` endpoints require an authenticated actor and derive tenant scope from it, never
from the request body or model output. Local mode accepts `Bearer dev:<user-uuid>` for
synthetic users only; cloud mode rejects requests until Identity Platform is wired.

## Data model

Tables (migration `0002`): tenants, users, channel_links, assets, asset_versions,
field_evidence, documents, relationships, proposals, confirmations, access_grants,
passport_versions, handover_requests, review_decisions, claim_cases, audit_events,
outbox_events, processed_events. Isolation is enforced twice: queries filter by the
actor's tenant, and composite `(tenant_id, id)` foreign keys stop a row referencing
another tenant's parent. CHECK constraints tie amount kinds to asset types (cover only on
life insurance, SIP only on mutual funds, estimates only on property) and keep references
masked. A reviewer can record one approval per evidence/grant version (partial unique index).

## Local vs cloud

| Concern | `APP_MODE=local` | `APP_MODE=cloud` |
|---|---|---|
| Database | Postgres 16 in docker compose (port 55432) | Cloud SQL for PostgreSQL |
| LLM / documents / retrieval / classifier | Deterministic labelled fixtures (built per prompt) | Gemini, Document AI, Vertex AI Search, Vertex AI prediction |
| Events | In-memory bus → SQL outbox (Prompt 6) | Outbox → Pub/Sub push to worker |
| Files | `.data/objects/` | Private Cloud Storage |
| Auth | Local dev identities (Prompt 2/3) | Identity Platform |
| Secrets | `.env` (local throwaway values) | Secret Manager |

A cloud adapter with missing config, or whose SDK integration has not been built,
reports `unavailable` with the reason. It never returns synthetic data as if live.

## Database choice

Cloud SQL for PostgreSQL. AlloyDB is documented as a future alternative only
(`docs/adrs/ADR-001-cloud-sql.md`); never provision both. BigQuery holds analytical copies.

## Chat pipeline (Prompt 3)

```
owner text ─▶ redact 9+ digit runs, refuse OTP/PIN/password ─▶ LLMProvider.generate_structured
          (bounded history, authorized asset summaries, open draft; typed JSON schema)
   ─▶ Extraction (pydantic, extra="forbid")  ── invalid ──▶ "nothing was changed"
   ─▶ server parses verbatim phrases (amount/term/date/institution); a phrase not found in the
      owner's text is dropped; owner's explicit words override the model's type/intent labels
   ─▶ tool propose_asset_change(actor from session) ─▶ draft/awaiting_confirmation
   ─▶ reply from server templates (next missing field, ambiguity, or review)
owner clicks Confirm ─▶ POST /confirm (not a tool) ─▶ SQL transaction
```

LLM providers: `gemini` (Vertex AI, cloud), `ollama` (real local inference, default in local
mode, labelled "not Gemini"), `fixture` (regex rules for tests/CI, labelled "not a model").
None silently falls back to another. Chat tools: `list_authorized_assets`,
`propose_asset_change`, `get_proposal`, `retrieve_policy`; confirmation is deliberately absent.

## Telegram channel (Prompt 4)

```
Telegram ─▶ /v1/telegram/webhook ─ secret header (constant-time) ─▶ one transaction:
   insert update_id (ON CONFLICT → duplicate, stop) ─▶ private chat only (groups: no reply)
   ─▶ ≤20 updates/min per chat ─▶ /start /help /link CODE /unlink
   ─▶ linked owner? ─ no ─▶ "not linked" (no lookups)
                    └ yes ─▶ chat.service.handle_message(channel="telegram")
   ─▶ masked render: type/status only, link to signed-in web app
commit ─▶ TelegramSender.send()  (DryRunSender by default; BotApiSender only with
          TELEGRAM_SEND_MODE=live + token)
```

Identity comes only from a code issued to a signed-in web user; usernames, names and
phone numbers are ignored. Chat IDs are stored as HMAC-SHA256 with `CHANNEL_HASH_KEY`.
Confirmation, documents and passports are never available over Telegram.
Local simulator: `make tg-sim` or `uv run reclaim-telegram-sim "/start"`.

## Documents and evidence (Prompt 5)

```
upload ─▶ sniff type from bytes · size limit · sha256 (re-upload → duplicate_of, stored once)
       ─▶ object store quarantine/<tenant>/<doc> · status pending · outbox document.uploaded
background (local: FastAPI task; cloud: worker via Pub/Sub, Prompt 6)
       ─▶ DocumentProcessor: LocalTextExtractor (text, text-layer PDF; no OCR)
                              DocumentAIProcessor (OCR + optional configured extractor)
       ─▶ LLM classifies + points at verbatim phrases (no tools; document text is data)
       ─▶ server: locate phrase → page + span (+ entity bbox/confidence), parse, enforce
          amount kind ↔ asset type, absent nominee stays unknown, reference → ••••last4
       ─▶ processed | needs_review (certificates, handwriting, unclear/unverified fields, low OCR)
owner "Add to draft" ─▶ gaps filled; equal values corroborate; different values → conflict
       that blocks confirmation until the owner keeps the draft value or uses the document's
confirm ─▶ field_evidence rows (document_id, page, span, confidence, processor version, source date)
          verification_state = document_extracted only if a document states the recorded amount
```

Confidence is extraction confidence only. Certificates are classified and sent to human
review; they cannot be attached to asset drafts and never change handover state.

## Events (Prompt 6)

```
API transaction: change + outbox row ─commit─▶ outbox (pending)
publisher pass (local: worker thread every 1 s; cloud: Cloud Scheduler → /tasks/publish-outbox)
   SELECT … FOR UPDATE SKIP LOCKED ─▶ envelope (whitelisted payload) ─▶ Pub/Sub (ordering key =
   aggregate id) │ local: Pub/Sub-shaped push to the worker
   fail ─▶ attempts+1, backoff 2^n s (≤300 s) ─▶ after OUTBOX_MAX_ATTEMPTS: dead-lettered (stats, replay)
worker /pubsub/push ─ auth ─▶ per consumer, one transaction each:
   INSERT processed_events(consumer, event_id) ON CONFLICT → duplicate, skip
   asset-projection · freshness · passport-stale · report-queue · analytics-export · document-processor
   any failure ─▶ that consumer rolls back ─▶ 500 ─▶ redelivery (Pub/Sub retry policy + DLQ topic)
   malformed / unknown type ─▶ dead_letters row, 204 (retrying cannot fix it)
analytics-export ─▶ outbox(topic=analytics) ─▶ minimized envelope (keyed-hash tenant/aggregate,
   no correlation id) ─▶ BigQuery subscription (Prompt 8; local JSONL file)
```

Projections and findings are rebuilt from SQL, so an old event arriving late cannot regress
them (`reclaim-events rebuild` does the same for everything). `reclaim-events stats|drain|replay`.
Delivery means an application event was handled; it never means money moved or settled.

## Policy retrieval and Claim Copilot (Prompt 7)

```
asset (actor-scoped) ─▶ jurisdiction supported? ── no ─▶ "unsupported" referral (overseas)
   ─▶ PolicyRetriever (LocalPolicyIndex | Vertex AI Search) + applicable() in code:
      approved · jurisdiction · institution ("*" allowed) · asset type · in force on the date
   ─▶ none ─▶ "no_guidance" │ two versions ─▶ "conflicting_sources" │ no reviewed rules ─▶ referral
   ─▶ rules (pinned to doc@version) choose paths from nomination / survivorship / dispute / balance;
      unknown facts give conditional paths; every item cites its § and has completed/pending/unknown
   ─▶ claim_case stores the pinned checklist; later reads flag a newer policy version
   ─▶ model writes plain-language steps; each must cite a checklist § (else dropped); steps claiming
      progress are dropped; the summary is written by the server from the checklist
chat policy questions ─▶ same retrieval for the owner's institutions ─▶ answer must cite a retrieved §
```

Corpus: `fixtures/policies/` (synthetic; one current fictional Indian bank policy plus superseded,
unreviewed, wrong-jurisdiction and injection test documents). No family data is ever indexed.

## Analytics (Prompt 8)

```
synthetic feed (reclaim-transactions) ─▶ outbox topic "transactions" (occurred_at = booking time)
asset changes ─▶ analytics-export consumer ─▶ outbox topic "analytics"
   ─▶ minimized envelopes (keyed-hash tenant/aggregate/account; whitelisted payload)
   ─▶ cloud: Pub/Sub → BigQuery subscription → *_raw (partitioned by event date, clustered by tenant)
      local: LocalPushPublisher → LocalWarehouse (Postgres schema "warehouse"), schema-checked;
             rejects → ingest_errors
   ─▶ views dedupe by event_id ─▶ analytics/sql/*.sql (BigQuery dialect; transpiled to Postgres locally)
   ─▶ analytics.service: actor → tenant key; flags with window, sample, rule, threshold, explanation
```

Transactions are a separate stream from asset events and never feed the inventory. Flags request
review; they are not fraud findings and never affect claims or access.

## Transaction classifier (Prompt 9)

```
classifier/data.py   labelled synthetic narratives (deliberate ambiguity) → account split with
                     identical-narrative merging (0 narratives across splits; ≥30 accounts per split)
classifier/model.py  TF-IDF char n-grams + direction/amount/day → balanced logistic regression;
                     threshold chosen on validation; test used once; rules baseline; version = data hash
reclaim-classifier train      local, deterministic → .data/models/<version>/ + docs/model_card.md
reclaim-classifier predict    warehouse `features` → ModelClassifier → warehouse transaction_predictions
reclaim-classifier vertex-*   Vertex AI CustomJob (train_job.py / batch_job.py), Model Registry upload;
                              dry-run unless --submit; online endpoint optional and not deployed
ModelClassifier      artifact present → model with abstention; absent → rules-only fallback (labelled)
owner review         abstained → queue → confirmation row (category_confirmations), prediction untouched
```

Metrics are synthetic only (see `docs/model_card.md`). Categories never affect inventory, claims or access.

## Workspace and passport (Prompt 10)

```
graph       flat nodes + edges from SQL only: owner→asset (owns), asset→institution (held_at, per
            name+jurisdiction), owner→person (as recorded), person→asset (registered_nominee),
            document→asset/relationship (supports, with field). No asset→asset edges, no score.
passport    owner action → snapshot (REPEATABLE READ; confirmed current versions; recipient export =
            post-release grant assets + allowed sections) → canonical JSON SHA-256 → ReportLab invariant
            PDF (ASCII money, masked refs, notices, totals per currency per kind, sources D1…, contacts,
            next steps) → object store (local dir | private GCS, create-only) → passport_versions
            (previous current for that audience → stale). Asset changes → worker marks stale.
download    owner: any version; recipient: current export only, active post-release grant AND a
            RELEASED handover they requested; stored-file hash must match. Copies cannot be revoked.
```

## Handover (Prompt 11)

```
PRIVATE ─request (family, active post-release grant)─▶ REQUESTED ─evidence + submit─▶ VERIFICATION_PENDING
VERIFICATION_PENDING ─hold─▶ HOLD ─resume─▶ VERIFICATION_PENDING      (approve refused while HOLD)
VERIFICATION_PENDING|HOLD ─needs_information─▶ NEEDS_INFORMATION ─evidence + submit─▶ VERIFICATION_PENDING
any open state ─reject─▶ REJECTED (final)
VERIFICATION_PENDING ─approve─▶ count distinct reviewers on current evidence_version AND grant version
   2 ─▶ APPROVED ─▶ RELEASED (same transaction; analytics.handover_released once)
```

Every action takes the handover row lock (`SELECT … FOR UPDATE`); a partial unique index stops one
reviewer approving the same versions twice; adding evidence or changing the grant invalidates earlier
approvals. Reviewers are tenant users with the reviewer role and never the owner or requester. Evidence
checks (certificate name vs owner record, registration number) and the AI summary only inform reviewers;
a summary that leans toward a decision or scores itself is suppressed. No timer, no chat tool and no
model output can move a case. Identity and death verification are simulated and labelled.

## Cloud packaging (Prompt 12)

```
Browser ──► reclaim-web (Cloud Run, public) ──► reclaim-api (Cloud Run, public; Identity Platform token per request)
                                                   │  Cloud SQL (unix socket)  ·  GCS (private)  ·  Vertex AI / Search
                                                   ▼
                                    outbox rows ──(Cloud Scheduler, OIDC: scheduler SA)──► reclaim-worker /tasks/publish-outbox
                                                   ▼
                     Pub/Sub reclaim-app-events ──(push, OIDC: pubsub-push SA)──► reclaim-worker /pubsub/push
                     Pub/Sub reclaim-analytics / -transactions ──(BigQuery subscriptions)──► reclaim_analytics
                     every subscription ──(bounded retries)──► *-dlq topic ──► *-dlq-hold (alerted)
```

- Same images, different `APP_MODE`. In cloud mode dev tokens are refused. Identity Platform
  users are linked by verified email once, then by `identity_subject`.
- The worker runs no background loop in cloud (`OUTBOX_LOOP=false`); the scheduler drives it.
- Infrastructure as code in `infra/terraform`; runbook in `docs/DEPLOYMENT.md`; controls in
  `docs/SECURITY.md`. Prepared and validated offline only.

## Optional dormant-asset recovery (Prompt 13)

Off the core path: `RECOVERY_ENABLED=false` turns its endpoints into 404s and hides its UI, and
the Prepare → Handover flow never reads its tables.

```
fixtures/recovery/registry.json   synthetic, authorized for demo matching only (refused otherwise)
        │
recovery_profiles (opt-in) ──► blocking: surname sound + (postcode area or birth year)
        │                        └─ outside the block: never compared or shown
        ▼
explicit features: name (Jaro-Winkler, initials 0.7) · date of birth · address (postcode, street, period)
        │  conflicts (DOB, first name, surname) → ruled out, no score
        ▼
heuristic score = sum of listed points (name ≤ 35) → recovery_matches (never assets)
        │  ≥ 45: AI explanation, checked: feature keys only, no ownership/relationship/probability
        │        words, no new numbers, no statement that contradicts the evidence
        ▼
owner / designated family in a released case → "not a match" | recovery_claims (separate checklist)
```

Search takes no names. The subject comes from the actor (owner) or the released handover case
(family). Nothing here creates assets, grants, handover changes, outbox asset events or payments.
