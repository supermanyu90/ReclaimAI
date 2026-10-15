# ReclaimAI — Project Brief

Version: 1.0 · Updated: 2026-10-01 · Scope: synthetic hackathon MVP

## Goal

Build a conversation-first financial legacy platform: **Prepare → Handover → Recover**.
Owners describe assets through web chat or linked Telegram, optionally attach evidence,
confirm proposed changes, and generate one versioned Family Asset Passport PDF.
After a controlled human-reviewed handover, designated family members receive only
authorized inventory sections and source-grounded claim preparation guidance.
Recovery of unrecorded dormant assets is an optional extension.

Product promise: **One maintained record, controlled family access, a clearer path to claims.**

## MVP journeys

- **Owner:** chat → proposed asset record → evidence/clarification → explicit confirmation
  → inventory/timeline update → passport generation → family visibility preview.
- **Family:** separate sign-in → handover request → evidence review → two independent
  reviewer approvals → scoped read-only inventory/PDF → institution-specific checklist.
- **Reviewer:** inspect evidence, conflicts and current sharing instructions; request more
  information, hold, reject or approve. Institutions separately decide and settle claims.
- **Analytics:** synthetic transactions → categorized records → trends and explainable
  review flags. Analytics and classification never decide entitlement.

## Implementation choices

Use a modular monolith plus asynchronous worker. Implement focused AI functions,
not a fleet of autonomous agents. Start with `APP_MODE=local` and deterministic fixtures.

| Layer | Choice | Responsibility |
|---|---|---|
| Web | Next.js / TypeScript | Chat, review cards, inventory, graph, passport, reviewer/family screens |
| API and worker | FastAPI / Python on Cloud Run | Authorization, workflow, orchestration and background jobs |
| Operational data | Local PostgreSQL; Cloud SQL for PostgreSQL in cloud | Source of truth, versions, grants, approvals, outbox and audit |
| Alternative database | AlloyDB for PostgreSQL | Future alternative only; do not deploy alongside Cloud SQL |
| Reasoning | Gemini on Vertex AI | Intent, structured draft extraction, clarification and grounded summaries |
| Documents | Document AI | OCR/configured extraction for financial and non-traditional documents |
| Retrieval | Vertex AI Search / Agent Search | Approved, dated policy and knowledge retrieval with citations |
| Classification | Vertex AI custom training/prediction | Transaction categories with uncertainty-based abstention |
| Analytics | BigQuery | Transaction trends, risk-review flags and workflow metrics |
| Events | Pub/Sub | Confirmed record changes and synthetic transaction processing |
| Files | Private Cloud Storage | Source documents and versioned passport PDFs |
| Controls | Identity Platform, IAM, Secret Manager, Cloud Logging | Authentication, service permissions, secrets and redacted observability |

Use provider interfaces for LLM, document processing, retrieval, classification,
events and object storage. Implement real cloud adapters and deterministic local
alternatives. Missing credentials mean explicitly unavailable or labeled fixture mode.
Keep model IDs, processors, datastore IDs and service locations configurable; verify
current SDKs and regional availability during implementation.

## Data and event flow

1. Authenticated web/Telegram input creates a proposal through Gemini and typed validation.
2. Optional Document AI extraction enriches the proposal with source references.
3. Owner reviews and confirms; backend validates permissions, conflicts and record version.
4. One SQL transaction commits the asset version and outbox event.
5. Outbox publisher delivers to Pub/Sub; idempotent workers update projections,
   mark passports stale, generate reports and emit minimized analytics events.
6. BigQuery holds analytical copies; Cloud SQL remains the operational source of truth.
7. Family disclosure uses a separate backend state machine and scoped exports.

Pub/Sub delivery must tolerate duplicates, retries and out-of-order events. Use event
IDs, aggregate versions, processed-event uniqueness, bounded retries and dead-letter
handling. Pub/Sub processes application events; it never initiates money transfers.
A SQL relationship table supports the evidence graph without another graph database.

## Data contract

Core entities: users, channel_links, assets, asset_versions, field_evidence, documents,
relationships, proposals, confirmations, access_grants, passport_versions,
handover_requests, review_decisions, claim_cases, audit_events, outbox_events,
processed_events. Keep synthetic transactions separate from asset-change events.

Use UUIDs, tenant/owner scope, UTC timestamps, optimistic versions, integer minor-unit
amounts, ISO currency codes and document hashes. Preserve source date independently
of ingestion date. Store field-level evidence, extraction confidence, page/span when
available and processor/model version. Corrections create new versions.

## Non-negotiable behavior

- Demo data and simulated verification are visibly synthetic. Never imply a live integration.
- AI produces drafts. Only explicit owner confirmation and backend validation commit changes.
  Confirmation is not a model-callable tool; retries must not duplicate records.
- Keep user-reported, document-extracted and institution-verified states distinct.
  Owner confirmation does not establish institution verification or document authenticity.
- Preserve unknowns and conflicts. Missing nomination data means unknown, not “no nominee.”
- Separate balances, liabilities, SIP installments and insurance cover. Keep currencies
  separate. Do not claim complete asset coverage, live balances or an arbitrary estate score.
- Trusted contact, registered nominee, executor and legal heir are distinct roles.
  A recorded family relationship does not establish ownership or a sharing grant.
- AI cannot grant access, approve handover, establish inheritance or release funds.
- Enforce tenant, actor, object and field-scope authorization server-side on every endpoint,
  tool, retrieval path, summary and download. Never trust model-provided authorization claims.
- Treat messages, documents and retrieved passages as untrusted data, never instructions.
- Never collect banking passwords, PINs, OTPs or private keys in chat or documents.
  App authentication occurs through the authentication provider's own interface.
- Telegram uses linked private chats, expiring single-use linking codes, webhook validation,
  update deduplication and masked responses. Sensitive uploads/downloads use authenticated
  web access. A web link alone is not authorization. Reject group-chat access.
- No death inference from inactivity. No dependency on the deceased owner's phone for access.
- Policy guidance cites approved, dated, applicable sources. Rules select checklists;
  Gemini explains them. Insufficient sources produce uncertainty and institutional referral.
- No legal entitlement decisions, investment recommendations, automatic payments or invented APIs.

## Handover contract

Normal path: `PRIVATE → REQUESTED → VERIFICATION_PENDING → APPROVED → RELEASED`.
Branches: `NEEDS_INFORMATION`, `HOLD`, `REJECTED`.

Require an authenticated designated recipient and two distinct authorized reviewers
approving the current evidence and grant version. Requesters cannot approve their own
requests. Material changes invalidate earlier approvals. Enforce transitions atomically;
held cases cannot release. Two approvals are a product safeguard, not a universal law.

Release only permitted inventory sections and supporting evidence. Family receives
information access, not ownership. Institutions decide claims and payments. A readable
PDF already downloaded cannot reliably be revoked.

## Passport and policy scope

Generate a deterministic PDF from an approved snapshot: recorded assets, masked references,
source dates, verification states, separate currencies/cover/liabilities, document index
and locations, contacts and next steps. Store version, snapshot reference and hash.
Produce recipient-specific filtered exports; authorized updates mark prior exports stale.

Ship one clearly fictional Indian bank-claim policy for the local demo. Real policies
require sourcing, review and effective dates before use. Overseas inventory is supported;
overseas claim guidance remains unsupported in this MVP.

## Demo fixtures and acceptance

Use Ramesh Patel, daughter Priya Shah, two reviewers and an unrelated second tenant.
The completed inventory contains seven assets across five fictional institutions and
India/Singapore: savings, FD, mutual fund, life policy, property, pension and SGD savings.
Provide two fixture states: six assets before the chat demo and seven after the FD is
confirmed. Map sample real-bank utterances to clearly labeled synthetic fixtures.
Include an old statement, unknown nomination status, duplicate document and conflicting amount.

The three-minute demo covers chat, extraction/confirmation, graph/freshness findings,
passport, visibility preview, a held handover followed by reviewed release, a cited claim
checklist and a brief analytics/classifier view. Dormant recovery is an optional extra.

Done means the local flow runs reproducibly, cloud adapters report their actual status,
and focused checks cover isolation, injection, duplicate delivery, stale confirmation,
independent approvals, scoped PDFs and retrieval grounding. Report actual model results
on synthetic held-out data; never invent performance numbers or production readiness.

## Delivery and working rules

Follow `ReclaimAI_MVP_Prompts.md` prompts 1–12, optional 13, then 14. Keep `STATUS.md`
short and factual: completed work, focused checks, blockers and one concrete next step.
Inspect relevant files, preserve existing work, implement rather than only propose,
and avoid repeating the entire architecture in responses.

Prepare Dockerfiles, Terraform, configuration examples, startup/test instructions and
cleanup guidance. Do not provision paid resources or send real messages during code
generation without a separate instruction. No secrets in code or logs. No live financial
integrations, effective will creation, automated death authentication or cross-border
inheritance decisions in this MVP.
