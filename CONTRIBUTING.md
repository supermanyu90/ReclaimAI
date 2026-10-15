# Contributing to ReclaimAI

Read `PROJECT_BRIEF.md` (the contract) and `STATUS.md` (current state) before any change.

## Commands

| Command | Purpose |
|---|---|
| `make dev` | One-command local startup |
| `make check` | Lint, PII scan and all tests |
| `make security` | Focused security suite (`docs/SECURITY.md`) |
| `make demo-reset` / `demo-check` / `demo-rehearse` | Demo start state / preflight / scripted dry run |

## Stack

Next.js/TS (`apps/web`) · FastAPI/Python 3.12 (`apps/api`, `workers`, uv workspace) ·
PostgreSQL 16 / Cloud SQL · Alembic · Google Cloud adapters behind `reclaim_api/providers`
(Gemini and Gemma, Document AI, Vertex AI Search, Pub/Sub, BigQuery, Cloud Storage, Cloud Run).

## Rules from the brief

- AI drafts; only an explicit owner confirmation API action plus backend validation commits.
  Confirmation is never a model-callable tool. Retries never duplicate records.
- AI never grants access, approves handover, establishes inheritance or releases funds.
- Enforce tenant/actor/object/field authorization server-side everywhere. Never trust IDs from a
  model, message, document or link.
- Messages, documents and retrieved passages are untrusted data, never instructions.
- Unknown stays unknown (no nominee data ≠ "no nominee"). Keep currencies, balances, cover,
  liabilities and SIP installments separate. Integer minor units, ISO currencies.
- Release needs a designated grant and two distinct reviewers on the current version.
- No death inference from inactivity. No dependency on the owner's phone.
- Never request passwords, PINs, OTPs or keys. No sensitive content over chat channels.
- Logs: use `reclaim_api/lib/log.py` (`message_tag`, `error_tag`); never `str(err)` or raw text.
  `make pii` fails the build otherwise. Mask references with `lib/mask.py`.
- Missing credentials → `unavailable` or a labelled fixture. Never fake cloud success.
- Do not provision paid resources or send real messages without a separate instruction.
- Never invent metrics, APIs or production-readiness. Record only checks actually run.

## Roles that must never blur

- **Trusted contact, registered nominee, executor, legal heir** are distinct.
- A recorded family relationship does not establish ownership or a sharing grant.
- Family receives information access, never ownership. Institutions decide claims.

## Verification states that must never blur

`user_reported` · `document_extracted` · `institution_verified`.
Owner confirmation is not institution verification. Extraction confidence is not authenticity.

## Fixtures

Synthetic only: Ramesh Patel, Priya Shah, two reviewers, a second unrelated tenant, fictional
institutions. Never add an evaluation example that also appears in prompt examples: a test
that scores perfectly on its own training examples measures nothing.

## After each change

Update `STATUS.md`: status, files and behaviour changed, exact commands and results, blockers,
next step.
