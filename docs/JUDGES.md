# For judges

## Claims we do not make

ReclaimAI does not:
- claim to find every asset;
- create a will or decide inheritance;
- verify that a certificate is authentic (identity and death checks are simulated in this MVP);
- connect to any bank;
- claim to be production-ready.

All data is synthetic.

## Google Cloud architecture (slide outline)

1. **Title:** Prepare → Handover → Recover. One maintained record, controlled family access, a clearer path to claims.
2. **Request path:** Browser → Cloud Run *web* → Cloud Run *API* (Identity Platform token on every call) → Cloud SQL PostgreSQL (source of truth, tenant-scoped).
3. **AI, behind typed adapters:**
   - Gemini on Vertex AI drafts changes, explains checklists and summarizes cases.
   - Document AI reads receipts and certificates.
   - Vertex AI Search retrieves dated policy sections.
   - A Vertex AI custom model categorizes transactions.
   - Every output is schema-checked, and confirmation is never a model tool.
4. **Events:** Confirm writes the record and an outbox row in one transaction → Cloud Scheduler drives the outbox → Pub/Sub → authenticated push to the Cloud Run *worker* (projections, passport staleness, document processing), with bounded retries and a dead-letter topic.
5. **Analytics:** Pub/Sub BigQuery subscriptions → raw tables → dedup views → owner trends and explainable review flags; batch predictions are written back.
6. **Controls:**
   - Private GCS (no public objects, no signed URLs).
   - Secret Manager, and one service account per workload with no keys.
   - Two-reviewer release.
   - Redacted logs, a budget alert and dead-letter alerts.
7. **Today:** runs locally end to end. The cloud adapters and Terraform exist but have not been deployed (table below).

## Live vs fixture today

| Service | In this demo | On Google Cloud |
|---|---|---|
| LLM | **Live:** Gemma 3 4B via Ollama, real inference (not Gemini) | Gemini on Vertex AI adapter, unit-tested with a fake client, never called live |
| Document reading | Fixture: text and PDF text layer, no OCR | Document AI adapter, never called live |
| Policy retrieval | Fixture: local index of a fictional, dated policy corpus | Vertex AI Search adapter plus export, never called live |
| Transaction classifier | Local scikit-learn model trained on synthetic data | Vertex custom job spec, dry-run only |
| Events | Outbox → local worker push | Pub/Sub plus OIDC push, Terraform written, not applied |
| Warehouse | The BigQuery SQL runs on a Postgres schema via sqlglot | BigQuery tables and views in Terraform, not applied |
| Storage | Local files | Private GCS adapter, not used live |
| Telegram | Dry-run: nothing sent | Bot API path exists, no bot created |

## Short answers

**Why AI?**
- People describe finances in loose language ("₹5 lakh FD for 3 years") and keep messy paper.
- The model turns words and documents into a draft and explains a bank's procedure from cited sections.
- It never records, grants or approves anything. Values must appear in the owner's own words or a document, and the owner's words override the model's labels.
- Where the model got facts wrong, we moved the work to the server. In testing, Gemma claimed documents had been received and once read "R K" as a different first initial from "Ramesh"; the server now writes those parts.

**Why Pub/Sub?**
- A confirmation has several consequences: projections, marking older passports stale, analytics and document processing.
- The API commits the record and an outbox row in one transaction, and Pub/Sub delivers the events at least once to idempotent consumers with retries and a dead-letter topic.
- A slow consumer never blocks or undoes a confirmation, and duplicate deliveries are tested not to double-count.

**Why two databases?**
- Cloud SQL PostgreSQL is the system of record: transactional, tenant-isolated with composite foreign keys, versioned assets and audits.
- BigQuery is analytical only: minimized events (keyed-hash IDs, no free text) for trends and flags.
- Nothing reads BigQuery to decide access or ownership. AlloyDB is an alternative to Cloud SQL, not a third store (ADR-001).

**Privacy**
- Family members see nothing about assets before a reviewed release, and afterwards only the assets and sections the owner granted. Hidden assets look exactly like missing ones.
- Downloads re-check permission every time.
- We never ask for passwords, PINs or OTPs. Logs carry route templates and correlation IDs, never message text (a CI scan enforces this).
- Telegram chat IDs are stored as keyed hashes. Recovery search is opt-in, and withdrawing deletes the results.

**Model evaluation** (classifier, synthetic data only)
- Split by account with no leakage. Held-out macro-F1 is 0.9408. Abstention at 0.55 keeps 99.6% of predictions, with 98.4% accuracy on those kept.
- That **missed** the 0.99 target set on validation, and we report it as missed.
- Ambiguous bank mandates are confused confidently, so owner confirmation stays in the loop. The full table is in `docs/model_card.md`.
- LLM steps are judged by tests on what reaches the database, not by a quality score.

**Institution authority**
- Institutions decide claims and payments. ReclaimAI prepares evidence and checklists that cite a pinned policy version, and it says so on every family screen.
- Reviewers release information, not money. A recovery match is "possibly yours, ask the bank", never an asset.
