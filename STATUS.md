# ReclaimAI — Status

Updated: 2026-10-15
Phase: Prompts 1–14 complete. Local MVP runs the full demo reproducibly; cloud packaging prepared, not deployed.
Next task: none in the build sequence. Possible next steps are listed under Next action.

## Completed

- [x] Created `ReclaimAI_MVP_Prompts.md`: prompts 0–14, runtime templates and cloud service map.
- [x] Created `PROJECT_BRIEF.md`: MVP scope, stack, data/workflow contracts and acceptance criteria.
- [x] Created this implementation handoff tracker.
- [x] Prompt 1: monorepo scaffold:
  `apps/web` (Next.js 16), `apps/api` (FastAPI, Alembic), `workers` (FastAPI push worker),
  uv workspace with `uv.lock` + `apps/web/package-lock.json`, docker compose Postgres 16,
  `.env.example`, `make dev` one-command startup, provider interfaces for LLM, documents,
  retrieval, classification, events and object storage with honest capability status,
  `/healthz`, `/readyz`, `/v1/system/capabilities`, redacted JSON logging, masking helpers,
  PII scan, CI workflow, `docs/architecture.md`, ADR-001 (Cloud SQL; AlloyDB alternative).
- [x] Prompt 2: migration `0002` with all 17 brief entities plus `tenants` (`models.py`):
  composite `(tenant_id, id)` FKs for DB-level isolation, CHECKs tying amount kinds to asset
  types, masked-reference check, one-approval-per-reviewer-per-version partial index.
  `fixtures/demo.json` + `reclaim-seed` (uuid5 IDs, ON CONFLICT DO NOTHING; `--state after`,
  `--reset`). Local dev auth (`Bearer dev:<uuid>`, synthetic users, local mode only) and
  actor-scoped `/v1/me`, `/v1/assets`, `/v1/assets/{id}`, `/v1/inventory/totals`.
- [x] Prompt 3: chat pipeline (`chat/`), LLM adapters (`providers/llm.py`: Gemini on Vertex via
  google-genai with JSON schema output; Ollama for real local inference; rules fixture for CI),
  server tools without confirmation, deterministic phrase parsing with verbatim checks,
  proposal edit/cancel/confirm with idempotency + optimistic version check + outbox/audit,
  migration `0003` (`chat_turns`, `draft` status). Web: local sign-in picker, same-origin API
  proxy (httpOnly dev cookie), `/chat` with review card (sources per field, Confirm/Edit/Cancel,
  processing/unavailable/invalid banners, one Idempotency-Key per proposal).
- [x] Prompt 4: `telegram/` (webhook, linking, sender, simulator), migration `0004`
  (`link_codes`, `telegram_updates`), link panel in web chat, `make tg-sim`.
- [x] Prompt 5: `documents/` (intake, background processing, normalization with provenance),
  `providers/documents.py` (local text/PDF extractor; Document AI OCR + optional extractor),
  attach/resolve on proposals, document evidence on confirm, migration `0005`, synthetic
  document fixtures in `fixtures/documents/`, web upload + document cards + conflict resolution.
- [x] Prompt 6: `events/` (envelope whitelist, outbox publisher with backoff/dead-letter/replay,
  local push + Pub/Sub publishers, consumers with processed_events dedup, SQL rebuild, CLI),
  worker `/pubsub/push` (OIDC in cloud) + `/tasks/publish-outbox` + local loop, migration `0006`
  (projections, findings, report jobs, dead letters, outbox topic/retry columns); document
  processing moved from the API to the worker; `/v1/findings`, `/v1/system/events`.
- [x] Prompt 7: `providers/retrieval.py` (local §-section index; Vertex AI Search adapter; applicability
  enforced in code), `claims/` (version-pinned rules, checklist with citations and evidence status,
  validated model steps, grounded chat answers), synthetic policy corpus, `/v1/claims`, `/claims` page.
- [x] Prompt 8: `analytics/` (BigQuery-dialect SQL transpiled to Postgres locally via sqlglot,
  LocalWarehouse + BigQueryWarehouse, synthetic feed, explainable flags), `infra/bigquery/schema.sql`,
  transactions topic + minimized envelopes, analytics events from rebuild, `/v1/analytics`, `/analytics`.
- [x] Prompt 9: `classifier/` (synthetic labelled data with account-grouped leak-free splits, sklearn
  model, validation-chosen abstention, rules fallback, Vertex CustomJob/Model Registry path, local batch
  prediction into the warehouse), migration `0007` (`category_confirmations`), review API + UI,
  `docs/model_card.md` generated from measured results.
- [x] Prompt 10: `workspace.py` (graph of recorded links, timeline, review queue, findings, access
  preview), `passport.py` (snapshot, deterministic PDF, versions, stale marking, permission-checked
  download), `providers/storage.py` (private GCS, create-only), `/workspace` page with SVG graph and
  passport panel; sign-in now lands on the workspace.
- [x] Prompt 11: `handover.py` (state machine, two-reviewer release, evidence checks, AI case summary),
  `family.py` (pre-release sections, released read-only scope), family claims via a scoped resolver,
  migration `0008` (`handover_evidence`, one-open-request index, `resume` decision), `/family` and
  `/reviews` pages, role-based sign-in, mismatched death-certificate fixture.

- [x] Prompt 12: Dockerfiles (api, worker, web; non-root), Terraform in `infra/terraform` (validated
  offline, never applied), Identity Platform token auth with link-by-verified-email (migration `0009`),
  request metrics logs + security headers, separate worker identities for push and scheduler,
  `reclaim-policy-export` for Vertex AI Search, `make security`, `docs/DEPLOYMENT.md`, `docs/SECURITY.md`.
- [x] Prompt 13 (optional): `recovery/` (authorized synthetic registry, blocking, explicit name/DOB/
  address features, heuristic score, checked AI explanation, separate recovery claims), migration
  `0010`, `/recovery` page, family link inside a released case, `RECOVERY_ENABLED` switch.
- [x] Prompt 14: `reclaim-demo reset|check|rehearse` (`make demo-reset`, `demo-check`, `demo-rehearse`),
  `docs/DEMO.md` (screen-by-screen 3:00 script), `docs/JUDGES.md` (architecture slide outline,
  live-vs-fixture table, judge answers), README runbook; reviewer AI summaries cached per evidence version.

Nothing has been deployed to Google Cloud. No paid resource has been created.

## Build sequence

| Prompt | Deliverable | Status |
|---|---|---|
| 0 | Shared contract | Complete in PROJECT_BRIEF.md |
| 1 | Architecture, scaffold, local startup | Complete |
| 2 | SQL models, migrations, synthetic fixtures | Complete |
| 3 | Web chat, Gemini adapter, confirmed proposals | Complete (Gemini adapter unit-tested with a fake client; not called live) |
| 4 | Telegram adapter and local simulator | Complete (dry-run only; no bot created, nothing sent) |
| 5 | Document AI adapter and evidence provenance | Complete (Document AI adapter unit-tested with a fake client; not called live) |
| 6 | Transactional outbox, Pub/Sub, idempotent workers | Complete (Pub/Sub adapter unit-tested with a fake client; not used live) |
| 7 | Policy retrieval and Claim Copilot | Complete (Vertex AI Search adapter unit-tested with a fake client; not called live) |
| 8 | BigQuery transaction/risk analytics | Complete (BigQuery adapter unit-tested with a fake client; SQL run locally via transpilation, not on BigQuery) |
| 9 | Vertex AI transaction classification | Complete (Vertex jobs dry-run only; never submitted) |
| 10 | Owner workspace and versioned passport PDF | Complete (GCS adapter unit-tested with a fake client) |
| 11 | Reviewed handover and scoped family access | Complete (identity/death verification simulated) |
| 12 | Cloud packaging, controls and verification | Complete (prepared and validated offline; not applied — no GCP project used) |
| 13 | Dormant-asset recovery | Complete (optional extension; synthetic registry only; can be switched off) |
| 14 | Resettable three-minute demo and runbook | Complete (rehearsed through the API with real Gemma; UI screens verified in earlier prompts, not re-walked end to end here) |

## Current capability state

| Area | Actual state |
|---|---|
| Local web/API/database | Running locally: web :3100, API :8100, worker :8101, Postgres :55432; migrations 0001–0010 |
| Synthetic fixtures | Generated: Patel (6 → 7 assets, 5 institutions, IN/SG) + Mehta tenant; see `fixtures/README.md` |
| Gemini / Document AI / Search | Gemini and Document AI adapters built, never called live (no GCP project). Locally: real Gemma 3 4B via Ollama for chat and document normalization; text/PDF extraction without OCR. Vertex AI Search adapter built, never called live; local policy index in use |
| Pub/Sub / Cloud Run / Cloud SQL / GCS | Terraform written and `tofu validate` clean; images build and smoke-test locally in cloud mode; no resources provisioned |
| BigQuery / Vertex AI classifier | Local warehouse schema runs the BigQuery SQL; BigQuery not provisioned. Classifier txn-clf-c291128ae5fa trained locally on synthetic data; Vertex jobs not submitted |
| Telegram | Webhook + linking + simulator working locally in dry-run; no bot exists, no message has been sent |
| Passport / handover | Passports and the reviewed handover work end to end locally; verification of identity and death is simulated |
| Automated tests / deployment | 283 pytest tests pass locally (CI uses the rules fixture, no Ollama); `make security` 50/50; no deployment |

## Decisions to preserve

- Default to `APP_MODE=local`; visible deterministic fixtures, no fake cloud success.
- Next.js + FastAPI; modular monolith plus worker; PostgreSQL/Cloud SQL as source of truth.
- BigQuery is analytical storage; AlloyDB is an alternative, not an additional database.
- Chat proposes; owner confirms; backend authorizes and commits.
- Two independent reviewers and designated grants control information release.
- Six assets at demo start; seven after FD confirmation. Keep before/after seeds distinct.
- Use synthetic data and fictional demo policy; recovery remains optional.

## Prerequisites and blockers

Local: Docker, uv (installed via Homebrew 2026-10-15; fetches Python 3.12), Node 20+.
Ports 3000/8000 were taken on the dev machine, hence 3100/8100/8101/55432 (configurable).

Cloud execution later needs a selected Google Cloud project, billing/setup authorization,
service-specific locations, configured model/processor/search identifiers and application
credentials/IAM. Not assessed. Telegram needs a bot and webhook. These do not block local work.

## Capability table (Prompt 12)

Observed, not assumed. "Local" is `/v1/system/capabilities` from the running dev stack. "Cloud
image" is the same endpoint from the API container in `APP_MODE=cloud` with no GCP configuration.

| Capability | Local (observed) | Cloud image, unconfigured (observed) | Cloud, configured |
|---|---|---|---|
| LLM | live: Ollama gemma3:4b (not Gemini) | unavailable: missing GCP_PROJECT, VERTEX_LOCATION, GEMINI_MODEL | Gemini on Vertex. Never called live |
| Documents | fixture: local text/PDF text layer, no OCR | unavailable: missing config named | Document AI. Never called live |
| Retrieval | fixture: local policy index | unavailable: missing config named | Vertex AI Search. Never called live; export tested by round trip through the adapter |
| Classification | fixture: local sklearn txn-clf-c291128ae5fa | fixture: rules fallback (no model mounted) | Same model from the GCS volume. Not deployed |
| Events | fixture: outbox → local push | unavailable: missing config named | Pub/Sub + OIDC push. Never used live |
| Object storage | fixture: local filesystem | unavailable: missing config named | Private GCS. Never used live |
| Telegram | fixture: dry-run | fixture: dry-run | Bot API only with `TELEGRAM_SEND_MODE=live`. No bot exists |
| Warehouse | fixture: Postgres schema running BigQuery SQL | unavailable: missing config named | BigQuery. Never queried live |

Nothing in this table is "live" on Google Cloud.

## Verification record

Hosted demo on Google Cloud, project `hackathonapac` (the account's project quota prevented a new project), 2026-10-15:

| Step | Result |
|---|---|
| `deploy.sh project` | Billing linked to "My Billing Account"; APIs enabled; budget "ReclaimAI demo" ₹2,000/month at 50/90/100% (alert only) |
| Gemini availability probe | `gemini-2.5-flash` answers in `asia-south1` (200); `gemini-2.5-flash-lite` 404 in asia-south1 but available globally |
| **First live Gemini run:** full `reclaim-demo rehearse` in-process with GeminiVertexLLM (gemini-2.5-flash, asia-south1, thinking off) | Every step passed; 10.0 s API time (Gemma locally: ~34 s); status `live: last call succeeded` |
| Fix found | Gemini 2.5 thinks by default and those tokens count against max output; now `thinking_budget=0` (setting `GEMINI_THINKING_BUDGET`) |
| `deploy.sh infra` | Artifact Registry `reclaimai`; private bucket `hackathonapac-reclaim-files`; Cloud SQL `reclaim-db` (PostgreSQL 16, db-f1-micro, RUNNABLE); 4 generated secrets; 3 service accounts |
| `deploy.sh build` (Cloud Build) | api, worker, web images built and pushed (about 3 min) |
| `deploy.sh deploy` | Services: web https://reclaim-web-646340175107.asia-south1.run.app (passcode), api (private), worker; migrate job → 0010; reset job → 6 assets |
| Access checks | api without identity → 403; worker task without token → 401; web /gate → 200 |
| **Full rehearsal against the cloud API** (owner identity token in X-Serverless-Authorization) | Every step passed; 21.6 s API time; capabilities afterwards: llm **live** (Gemini 2.5 Flash, asia-south1), object_storage **live** (GCS) |
| Cloud Logging, severity ≥ ERROR since deploy | none; no chat or registry text found in logs |
| `deploy.sh reset` after rehearsal | 6 assets again |
| Loose ends fixed (2026-10-16) | Reviewer AI summary: documents sent to the model as D1/D2, IDs scrubbed, labels replaced by file names; discrepancies and missing items written by the server from its checks; a summary claiming a mismatch the checks did not find is hidden; overlong fields trimmed at a sentence instead of discarding the summary. Confirmation banner now says "from your words and the document you added" when a document supplied evidence |
| Gemini on Cloud Run | First reviewer-summary calls failed with `ClientError`; the adapter now retries 429/503 twice with backoff and records the HTTP status. After redeploy: summary shown, `llm live`, no IDs, discrepancies = the one real mismatch |
| Latent test bug | `test_schema_errors_are_recorded_as_pipeline_errors` used a fixed window ending 2026-10-15 and failed once the date changed; window now includes today. 291 passed |
| GitHub | History squashed to one commit (references removed); 32 Actions runs for the old commits deleted (run IDs cited above for Prompts 1–14 no longer exist on GitHub) |
| Passcode removed (2026-10-16) | `DEMO_PASSCODE` taken off `reclaim-web` (revision reclaim-web-00005); `/workspace` → 307 to `/signin`, `/signin` 200 listing the synthetic people; secret `reclaim-demo-passcode` deleted; deploy script no longer sets it. API stays private, worker stays token-locked |
| Not done | Web flow through the passcode gate in a browser on the cloud URL (I don't type a live credential into a public site; verified locally with a test passcode) |

Hosted demo preparation (`APP_MODE=demo`), run 2026-10-15. Nothing on Google Cloud yet.

| Check | Result |
|---|---|
| `tests/api/test_demo_mode_db.py` | 4 passed: per-mode provider defaults and overrides; demo has no built-in push token; demo accepts synthetic sign-in, cloud does not; demo worker refuses the local dev token |
| Three containers locally in demo mode (shared volume standing in for GCS, rules fixture standing in for Gemini) | `reclaim-demo reset` → 6 assets; `reclaim-demo rehearse --api` against the API container passed every step |
| Web container with `DEMO_PASSCODE` | `/workspace` → 307 to `/gate`; `/api/v1/me` → 401; wrong passcode shows an error; right passcode → sign-in → workspace with 7 assets (Chrome) |
| `gcloud meta list-files-for-upload .` | 228 files; no `.env`, `.data`, `node_modules` or `.git` |
| Full suite | ruff, PII scan, tsc clean; 287 passed |
| Found while testing | A per-run random hash key left analytics rows that de-duplication preferred. Cloud uses one fixed secret; documented in HOSTED_DEMO.md |

Prompt 14, run 2026-10-15 (local; Ollama gemma3:4b):

| Command | Result |
|---|---|
| `uv run pytest tests/api/test_demo_db.py` | 3 passed: full rehearsal from reset (rules fixture), deterministic second reset (same IDs, 6 assets, files removed, no handovers), rehearsal again from that reset, wrong-state refusal, preflight |
| Mutation: reset keeps the tenants' files | demo test fails; restored |
| `make demo-reset && make demo-check` (live stack) | `ramesh_assets=6`, 141 transactions published, 134 predicted (model); LLM live (gemma3:4b); "ready for the demo" |
| `make demo-rehearse` (live stack, real Gemma), 3 runs before the cache fix | All passed, 59.9–62.3 s API time; handover step 38.9–41.5 s, because each reviewer view re-ran the AI summary and the rehearsal polled that view |
| Same, 2 runs after caching summaries per (case, evidence version, checks, model) and polling the requester view | All passed, 34.0 and 34.7 s. Per step: chat 2.8–3.5 s, receipt + confirm 5.3–5.8 s, handover 12.6–12.8 s, claim checklist 8.4 s, optional recovery 4.3 s |
| Rehearsal facts (every run) | Bank and date came from the receipt; retried confirmation replayed; 7 assets; findings unknown_nomination + stale_source + missing_document_location; passport 7 assets, Priya's export 5; HOLD refused an approval; released after 2 reviewers with 5 granted assets; Priya's PDF downloaded; checklist cites sbb-deceased-claims@2 §2/§3/§5; 2 analytics flags; recovery DR-0001 91/100 and still 7 assets |
| First-click check in `next start` (production build, port 3101) | A mouse click from the automation did nothing, even 3 s after load. A JS-dispatched click on the same button signed in and navigated to /workspace. Capture listeners recorded **no** pointer or click events for two automated clicks on a link. So the dropped clicks never reached the page (an automation or focus artefact, not an app bug). Not confirmed with a human click; DEMO.md says to check once by hand |
| `make check` | ruff clean, PII scan clean, 112 unit + 171 db = 283 passed |

GitHub Actions run 38754654742 (Prompt 14 commit): success, both jobs. The runner was slow to start: about 10 min in npm ci and container setup.

Prompt 14 acceptance mapping:
- Deterministic reset command and demo fixtures: `make demo-reset`; IDs are uuid5 from fixture keys; `test_full_demo_rehearsal_from_reset`.
- Same Ramesh/Priya IDs and a consistent inventory: 6 assets, then the new FD becomes the seventh (rehearsal checks both).
- Progress comes from real state changes: every rehearsal step reads the API after acting. Cloud services are labelled `fixture` or `unavailable`, never faked.
- Screen-by-screen script for 0:00–3:00 and the optional extra: `docs/DEMO.md`.
- README runbook: README "Demo runbook".
- Architecture slide outline, live-vs-fixture table and judge answers: `docs/JUDGES.md`.
- No promises of complete assets, automatic inheritance, verified authenticity or production readiness: the first section of `docs/JUDGES.md`.

Not done in Prompt 14:
- A single human-driven browser pass through all 3:00 of screens. Each screen was browser-checked in its own prompt (3, 5, 10, 11, 13), but not in one sitting after the final changes.

Prompt 13, run 2026-10-15 (local; Ollama gemma3:4b for the live-model checks):

| Command | Result |
|---|---|
| `alembic upgrade head` / `downgrade 0009` / `upgrade head`; `alembic check` | Clean round trip (0010); no drift |
| `uv run pytest tests/api/test_recovery_unit.py tests/api/test_recovery_db.py` | 24 passed |
| Mutation: drop the DOB conflict block | 4 tests fail; restored |
| Mutation: drop the forbidden-language filter on explanations | 1 test fails; restored |
| Mutation: drop the released-state / grant checks for family search | 1 test fails each; restored |
| Real Gemma 3 4B explanation, DR-0001 and DR-0003 (3 runs of the first version, 2 runs after each fix) | First version: every statement dropped (model labelled all of them `"features"`); fixed with an enum of feature keys. Then the model wrote "differ in first initial" for "R K" vs "Ramesh" (false) and called a matched DOB "missing"; fixed with a contradiction check and server-derived "missing". Final runs: the wrong name statement is dropped, the DOB and address statements are kept and accurate |
| Browser (Chrome, `make dev`, real Gemma) | Ramesh → /recovery → consent → "Check the registry" → ₹6,42,000 fixed deposit, 91/100 "strong evidence", evidence path name +30.7 / DOB +35 / address +25, AI explanation with label; "Prepare a recovery claim" shows a separate checklist; weak (1) and ruled out (3) listed. `/v1/assets` still 6 assets afterwards; dev log has no profile or registry text |
| `make check` | ruff clean, PII scan clean, 112 unit + 167 db = 279 passed |
| `make security` | 10 groups PASS, including recovery (optional) 6 |

GitHub Actions run 38749298260 (Prompt 13 commit): attempt 1 stuck in "Initialize containers" (Postgres service) for 14+ minutes before any step ran, so I cancelled it; attempt 2 succeeded, both jobs.

Prompt 13 acceptance mapping:
- The ₹6.42 lakh deposit and its evidence path are shown to Ramesh: `test_ramesh_sees_the_642_lakh_deposit_with_its_evidence_path`, browser run above.
- Blocking happens first, then explicit features; the score is heuristic and labelled as not a probability: `test_blocking_keeps_unrelated_records_out`, `test_the_642_lakh_deposit_has_strong_evidence_with_explicit_contributions`.
- Same-name hard negatives and a conflicting DOB lower or block a match: `test_same_name_with_conflicting_dob_is_blocked`, `test_same_name_alone_is_weak_evidence`, `test_different_first_name_at_same_address_and_dob_is_blocked_not_related`, `test_hard_negatives_are_ruled_out_or_weak`.
- The AI explains the available evidence only, with no relationship or entitlement: `test_explanations_that_claim_ownership_relationship_or_invent_numbers_are_dropped`, `test_explanations_that_contradict_the_evidence_are_dropped`.
- Matches stay outside the inventory and use separate claim cases: `test_matches_and_claims_never_touch_inventory_access_or_money`, `test_priya_claim_is_a_separate_case_and_releases_nothing`.
- Priya can search only her authorized case: `test_priya_searches_only_inside_her_released_case`, `test_priya_needs_the_owners_opt_in_and_cannot_change_consent`.
- No match triggers access or money release: the state snapshots in both claim tests (assets, totals, grants, handovers and asset outbox events are unchanged).
- Optional, and the core works without it: `test_recovery_can_be_switched_off_without_affecting_the_core_flow`.

Limits:
- The registry is a fixture, not a real registry, and was never connected to one.
- The score weights are hand-set, not calibrated.
- The contradiction check is keyword-based: it caught the observed Gemma error but will not catch every misstatement. Because of this the server-written evidence is always shown, and the AI text is labelled as possibly wrong.

Prompt 12, run 2026-10-15 (local only; no Google Cloud project used):

| Command | Result |
|---|---|
| `docker build` api / worker / web | Built. Sizes 1.26 GB / 1.26 GB / 311 MB (api and worker are large; not optimized) |
| API container, `APP_MODE=cloud`, local DB | `/readyz` ready; capabilities unavailable with missing config named; dev token → 401; runs as uid 10001 |
| Worker container, push without OIDC | 401 |
| Web container | 200 with security headers |
| Migrate job command in the API image (`alembic -c apps/api/alembic.ini upgrade head`) | Ran; `current` = 0009 (head) |
| `tofu init -backend=false && tofu validate && tofu fmt -check` (OpenTofu 1.13.1) | Valid, formatted. **Never planned or applied against a project** |
| `uv run reclaim-policy-export --out …` | 10 approved sections exported (drafts excluded) |
| `make security` | 9 groups PASS: tenant isolation 7, tool authorization 4, injection 5, duplicate delivery 5, stale confirmation 2, two-reviewer release 4, scoped PDF 5, retrieval grounding 4, cloud identity 8 |
| Mutation: drop the issuer check in cloud auth | `test_wrong_issuer_or_unverified_email_never_links` fails; restored |
| Mutation: allow relinking an already-linked user by email | `test_first_sign_in_links_by_verified_email_then_by_subject_only` fails; restored |
| `make check` | ruff clean, PII scan clean, 101 unit + 155 db = 256 passed |

GitHub Actions run 38745700701 (Prompt 12 commit): success, both jobs.

Prompt 12 acceptance mapping:
- Dockerfiles: `apps/api/Dockerfile`, `workers/Dockerfile`, `apps/web/Dockerfile`.
- Terraform for Cloud Run, Cloud SQL, private GCS, Pub/Sub with dead-letter, BigQuery, service accounts and Secret Manager: `infra/terraform/*.tf`.
- Least privilege and no keys: per-workload service accounts with only the roles they need; ADC only; no SA keys.
- Authenticated push and restricted invoker: worker IAM, OIDC audience and one identity per endpoint (`test_cloud_outbox_task_accepts_only_the_scheduler_identity`).
- Redacted logs, correlation IDs and metrics: `test_request_log_has_route_template_not_ids_or_query`.
- Quotas, max instances, retry bounds, budget alerts (stated as not a cap), cleanup, backups, retention and migration notes: `docs/DEPLOYMENT.md`.
- Focused security tests: `docs/SECURITY.md`, `make security`.
- Capability table: above.

Gaps after Prompt 12:
- No Google API has ever been called live, and `tofu plan` has not run against a project.
- There is no web sign-in UI for Identity Platform, so a cloud deployment can't be used from the browser until it is built.
- The api and worker images are about 1.26 GB.
- No container or dependency scan has been run, and there is no rate limiting beyond max instances.

Prompt 11, run 2026-10-15:

| Command | Result |
|---|---|
| `uv run pytest` | 248 passed (14 handover db tests new) |
| Mutations: 1 approval releases / ignore evidence+grant versions / remove row lock | Caught by 7 tests / 1 test / the simultaneous-approval test (3 of 3 runs); restored |
| Browser (real Gemma): Priya requests, uploads mismatched certificate, submits | "Waiting for two independent reviewers (0 of 2)"; pre-release view shows contacts and paper locations only |
| Browser: Anil's review | Check "certificate name differs from the owner record"; Gemma summary names the same discrepancy, labelled as deciding nothing; sharing shown without amounts; Hold → Approve no longer offered |
| API (Chrome extension hit a usage limit, then disconnected): Priya adds correct certificate; Anil resumes + approves; Meera approves | Checks: mismatch / match; 1 approval → still pending; 2nd → RELEASED with "Inventory access granted; institutions decide claims." |
| API: Priya after release | 5 granted assets, no SGD in list or totals; passport export download 200; evidence index 5; audit events for each step |

GitHub Actions run 38739714024 (Prompt 11 commit): success.

Observation: in `next dev`, the first click right after a page load was repeatedly lost (hydration still
in progress); a second click works. Not seen as a functional bug in tests; worth checking in `next start`
before the demo (Prompt 14).

Prompt 11 acceptance mapping: requester can't approve (`test_requester_and_owner_cannot_approve`); one
reviewer can't approve twice (`test_one_reviewer_cannot_approve_twice`); one approval can't release and
held cases stay blocked (`test_held_case_corrected_then_released_by_two_reviewers`); wrong recipient/direct
URL (`test_wrong_recipient_and_direct_urls_fail`); simultaneous approvals release once
(`test_simultaneous_approvals_release_once`); summaries exclude hidden assets (same + family claims test);
nothing automatic (`test_no_model_or_tool_can_move_a_case`).


Prompt 10, run 2026-10-15:

| Command | Result |
|---|---|
| `uv run pytest` | 234 passed (13 workspace/passport db + 2 storage unit new) |
| Mutations: drop the release check on recipient download / ignore grant scope in exports | Each caught; restored |
| Owner passport (synthetic) | 2-page PDF; 6 assets; INR totals per kind and SGD separate; ****-masked refs; sources D1…; snapshot hash in footer |
| Priya export | 5 assets; no SGD account, no document locations, no contacts; downloadable only after a RELEASED handover |
| Determinism | Same snapshot + version → byte-identical PDF |
| Browser `/workspace` | Asset table with finding chips; graph (Priya → you, nominee → savings; each asset → its own institution); passport + Priya export generated, both current; download through the web proxy returns the PDF |

GitHub Actions run 38719880319 (Prompt 10 commit): success.

Prompt 10 acceptance mapping: stale on updates (`test_new_generation_and_confirmed_changes_mark_exports_stale`);
scoped exports (`test_recipient_export_excludes_hidden_records_and_sections`); permission at download time
(`test_download_checks_permission_at_download_time`, `test_recipient_cannot_download_a_stale_export`,
`test_tampered_file_is_not_served`); readable, traceable, no secrets (`test_owner_passport_is_readable_traceable_and_masked`);
graph integrity (`test_graph_holds_only_recorded_relationships`); revocation note printed in every PDF.


Prompt 9, run 2026-10-15 (all figures on **synthetic** data, from `docs/model_card.md`):

| Item | Result |
|---|---|
| `uv run pytest` | 219 passed (10 classifier tests new; training runs in ~10 s, twice, for reproducibility) |
| Model | txn-clf-c291128ae5fa: 69215 rows, 400 accounts; split accounts {'train': 279, 'validation': 52, 'test': 69}; 0 narratives across splits |
| Abstention | threshold 0.55 chosen on validation (coverage 0.9886, kept accuracy 0.9901) |
| Held-out test | macro-F1 0.9408 (all), 0.9459 (kept); coverage 0.9959; kept accuracy 0.9838 vs 0.99 target: **not met** on test |
| Rules baseline | coverage 0.9453, accuracy when matched 0.9213 |
| Mutations: split by row / ignore threshold | Training guard refuses (7469 narratives across splits) / abstention test fails |
| Vertex | Fake-SDK tests: CustomJob from train_job.py in the sklearn image, Model.upload with `synthetic=true`; CLI dry-run prints the spec |
| Browser `/analytics` | Category section with model version, review queue (empty), least-certain suggestions |

GitHub Actions run 38716392919 (Prompt 9 commit): success.

How the numbers got honest: the first generator produced macro-F1 **1.0** (its "ambiguous" cases were
separable); ambiguity was made real (shared mandate dates/amounts, plain rent credits, shop UPI handles).
Next the abstention target was met at full coverage on a too-small validation split, so splits were
enlarged (and 4-digit reference collisions that merged every account into one group were fixed).
The demo feed then exposed a vocabulary gap (12 family transfers predicted as SIPs at 0.54); the
generator gained common real forms, not the feed's strings. Demo-feed agreement is not a metric.


Prompt 8, run 2026-10-15:

| Command | Result |
|---|---|
| `uv run pytest` | 207 passed (10 analytics db + 13 analytics unit new) |
| Mutations: flows read the raw table / bucket by ingestion time | Duplicate and late-event tests fail; restored |
| `reclaim-transactions seed` | 134 synthetic transactions + 7 asset analytics events published; 5 simulated redeliveries |
| `/v1/analytics` (Ramesh) | INR and SGD separate; 139 deliveries / 134 distinct; flags: SIP missing 2026-07 (11 of 12 months), ₹2,50,000 debit vs ₹7,071.88 mean of 83; stale 2 of 6; turnaround insufficient data |
| `/v1/analytics` (Kavita) | insufficient_data, sample 0 |
| Browser `/analytics` | Flags with rule/threshold/sample/window; monthly tables per currency |

GitHub Actions: run 38713909309 failed (the test fixture assumed the warehouse schema existed; CI starts
from an empty database). Fixed so a fresh database creates it; verified locally by dropping the schema;
run 38714175041 succeeded.

Bug found while building: gaps were first flagged for October 2026 (current, incomplete month with no
feed data yet); gaps now only count months that are complete and covered by the feed.

Prompt 8 acceptance mapping: duplicates don't inflate (`test_duplicate_deliveries_do_not_inflate_totals`);
INR/SGD separate (`test_inr_and_sgd_stay_separate`); late events (`test_late_event_lands_in_its_own_month`);
empty histories (`test_empty_history_is_insufficient_data`); explainable flags
(`test_flags_are_explainable_review_requests`); actor scope (`test_scope_comes_from_the_actor_only`).


Prompt 7, run 2026-10-15:

| Command | Result |
|---|---|
| `uv run pytest` | 184 passed (17 claims db + 7 claims unit new) |
| Mutations: ignore effective_to / ignore jurisdiction / skip citation check on explanation | Each caught; restored |
| Retrieval filters | Today → v2 only; as of 2024-06-01 → v1 (rules not reviewed → referral); SG draft and GB doc never served for IN |
| Real Gemma Q&A ×2 | "Where do I submit…", nominee documents, settlement time: grounded, correct citations; FD interest rate → no authoritative answer; injected FAQ passage retrieved but not obeyed |
| Browser `/claims` (real Gemma) | SGD → unsupported referral; savings (nominee) → §2 checklist, account proof completed from the 2023 statement, others pending |

Real-model findings fixed: Gemma cited "sbb-faq@3" without the section (accepted only when it maps to
exactly one retrieved section); its free-text summary claimed pending documents were already received
and told the owner "that's you" (summary is now server-written; steps claiming progress are dropped;
statuses are described in plain words, after which its steps were accurate and stable). Remaining
limit: for conditional checklists Gemma's steps cover only the first path; the server summary states
how many paths are possible.

GitHub Actions run 38712207877 (Prompt 7 commit): success.

Prompt 7 acceptance mapping: requirements link to sources (`test_every_requirement_links_to_a_source_section`);
outdated / wrong-jurisdiction excluded (`test_outdated_policy_is_never_used`,
`test_wrong_jurisdiction_unapproved_and_outdated_documents_are_excluded`); retrieval injection cannot change
authorization (`test_injected_passage_cannot_widen_what_the_answer_covers`,
`test_explanation_steps_must_cite_checklist_sections`); no source → no authoritative answer
(`test_no_approved_guidance_means_referral_not_invention`, `test_question_without_sources_gets_no_authoritative_answer`);
UI separates inventory access from entitlement (`/claims` banner + notice on every checklist).


Prompt 6, run 2026-10-15:

| Command | Result |
|---|---|
| `alembic` 0006 up / down / up | Clean |
| `uv run pytest` | 161 passed (16 event db + 4 event unit new); clean run 14–19 s |
| Mutations: drop processed_events dedup / project from event version without guard | Duplicate and out-of-order tests fail respectively; restored |
| `make dev` live: pension correction via real Gemma → confirm; MF statement upload | Worker loop delivered both; projection v3 = 152000000; 1 report job; document processed in ~6 s; analytics JSONL has hashed tenant/aggregate keys and no amounts/labels; 0 log lines with message/document text |

GitHub Actions run 38710004548 (Prompt 6 commit): success.

Prompt 6 acceptance mapping: duplicate delivery (`test_duplicate_delivery_is_processed_once`);
crash/retry (`test_crash_mid_delivery_retries_without_double_applying`); out-of-order
(`test_out_of_order_events_never_regress_the_projection`); failed publishing retried + dead-lettered +
replay (`test_failed_publishing_is_retried_then_dead_lettered_and_replayable`); observable
(`/v1/system/events`, `dead_letters`, structured warnings).

Note: two slow test runs during development were caused by overlapping test processes sharing the
database (and uv's environment lock), not by the code; a clean run is 14–19 s.


Prompt 5, run 2026-10-15:

| Command | Result |
|---|---|
| `alembic` 0005 up / down / up | Clean |
| `uv run pytest` | 141 passed (21 document db + 21 document unit tests new) |
| Mutations: drop conflict gate / absent nominee → "not_recorded" / document overwrites draft | Each caught; restored |
| Real Gemma on all 8 fixture documents (×2 for the asset documents) | Kinds correct; amounts, dates, references correct and identical across runs; certificates and handwriting → review; injection text inert |
| Browser (`make dev`, real Gemma): FD chat → upload conflicting receipt → Add to draft → conflict shown, Confirm disabled → Keep ₹5,00,000 → Confirm | Recorded; DB: balance 50000000 `user_reported`, document evidence 55000000 kept with `conflicts_with_current`, opened_on from document p.1 span 158, nominee `unknown` |
| `grep` dev log for document text | 0 lines |

Real-model findings fixed server-side: Gemma labelled an FD principal "insurance cover" (amount
kinds now must fit the asset type), dropped the "Rs." before amounts (adjacent currency marker
now included), and returned no asset type for a statement (document's own words supply it).

GitHub Actions run 38698565597 (Prompt 5 commit): success.

Prompt 5 acceptance mapping: receipt attaches to chat proposal
(`test_receipt_enriches_the_chat_draft_and_nominee_stays_unknown`); conflicting amount needs
resolution (`test_conflicting_amount_blocks_confirmation_until_resolved`); absent nominee stays
unknown (same test + `test_nomination_never_turns_absence_into_no`); inconsistent evidence cannot
approve handover (`test_death_certificate_never_moves_a_handover`); document instructions cannot
invoke tools (`test_document_instructions_cannot_invoke_tools`,
`test_model_output_outside_schema_sends_document_to_review`).


Prompt 4, run 2026-10-15:

| Command | Result |
|---|---|
| `alembic` 0004 up / down / up | Clean |
| `uv run pytest` | 99 passed (20 new Telegram db tests) |
| Mutations: drop single-use check / group check / update dedup | Each caught by its test; restored |
| `make dev` + `reclaim-telegram-sim` against real Gemma | /start onboarding; unlinked query refused; group ignored; web-issued code links once, replay fails; FD draft → masked "Draft ready" + web link; repeated update_id → `duplicate`; asset list = count only; /unlink → not linked |
| Browser: /chat → Telegram panel → Link Telegram | Shows single-use code and expiry |

GitHub Actions run 38694893873 (Prompt 4 commit): success.

Prompt 4 acceptance mapping: expired/replayed codes fail (`test_expired_code_fails`,
`test_code_is_single_use_and_stored_hashed`); duplicate updates don't duplicate drafts
(`test_duplicate_update_does_not_duplicate_draft`); unlinked chat can't query
(`test_unlinked_chat_cannot_query_assets`, `test_username_or_phone_never_links_a_chat`).


Prompt 3, run 2026-10-15 (local Postgres 16; Ollama gemma3:4b on this Mac):

| Command | Result |
|---|---|
| `alembic upgrade head` / `downgrade 0002` / `upgrade head` | Clean round trip (0003) |
| `uv run pytest` | 79 passed (31 chat unit, 16 chat db, earlier 32) |
| Mutation: remove "already confirmed" replay guard | retry + concurrent-confirm tests fail; restored |
| Mutation: skip verbatim phrase check | hallucinated-value test fails; restored |
| Real model, scripted 5-turn conversation ×3 runs | All 3 runs correct: FD draft → date → confirm (7 assets); "2 lakh for 1 year" asks for bank; pension correction → update proposal with recorded vs proposed; SGD close proposal |
| Browser (Chrome, `make dev`, real Gemma) | Sign in as Ramesh → FD message → date question + review card with per-field sources and synthetic-mapping note → date → Confirm → "Recorded (version 1)"; API then lists 7 assets |
| `grep` dev log for message text | 0 lines |
| `ruff`, `scripts/lint-pii.sh`, `tsc`, `next build` | Clean |

Prompt 3 acceptance mapping: ambiguous institution/date → clarification
(`test_missing_institution_and_ambiguous_date_trigger_clarification`); injection cannot change
permissions (`test_injection_cannot_reach_another_tenants_asset`,
`test_model_output_with_extra_fields_is_invalid_and_commits_nothing`,
`test_other_roles_and_tenants_cannot_chat_or_confirm`); invalid JSON never commits
(`test_invalid_json_from_model_never_commits`); retry creates one version
(`test_retrying_confirmation_creates_exactly_one_version`, `test_concurrent_confirmations_record_once`);
visible states (`test_unavailable_model_changes_nothing_and_says_so`, UI banners).

Findings from real-model runs that shaped the design: Gemma 3 4B omits optional schema keys
(fixed by requiring every key, nullable), decorates phrases ("₹2 lakh" for "2 lakh"), and
carries topics over from history (labelled a pension correction as an FD). The server
therefore treats model labels as hints: values must be found in the owner's text, and the
owner's explicit type/closing words override the model.

GitHub Actions run 38691707042 (Prompt 3 commit): success, both jobs.

Earlier prompts: Prompt 2 — 31 tests, seed idempotency, CI run 38689437034 success.
Prompt 1 — CI run 38688423881 success.

Known limits: Gemini never called against Vertex (needs a GCP project + ADC); receipt
enrichment of a chat draft waits for Prompt 5 (Document AI); family/reviewer views wait for
Prompts 10–11; chat replies are English-only templates.

## Next action

The build sequence is complete. Candidates, each needing its own instruction:
1. A human run-through of `docs/DEMO.md` in the browser, timed against the three minutes.
2. Cloud: pick a project, check regional availability, then follow `docs/DEPLOYMENT.md`. This creates paid resources, so it needs explicit approval.
3. A web sign-in screen for Identity Platform (needed before any cloud deployment is usable from a browser).

## Resume prompt

```text
Read PROJECT_BRIEF.md and STATUS.md. Pick up the chosen item from "Next action"
in STATUS.md. Inspect relevant files and preserve existing work.
Start in local mode. Run focused checks and record exact results, blockers
and the next step in STATUS.md. Return a concise handoff.
```

## Update discipline

After each implementation task: change its status, summarize files/behavior changed,
record checks actually run, identify remaining risks/blockers, and set the next prompt.
Replace stale progress notes rather than appending long session transcripts.
