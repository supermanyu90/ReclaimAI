# Three-minute demo

Everything shown is synthetic. Every screen reads real state from the local database; nothing
on screen is a canned response. The chat and document steps use real Gemma 3 4B through
Ollama ("not Gemini" on the status page). Cloud services are not used and are labelled
`fixture` or `unavailable` on the status page.

## Before you present

```bash
make dev            # terminal 1: postgres, api :8100, worker :8101, web :3100 (needs Ollama: gemma3:4b)
make demo-check     # terminal 2: database ready, 6 assets, provider table, LLM live
make demo-rehearse  # optional: drives the whole story through the API and checks each step
make demo-reset     # ALWAYS after a rehearsal: back to 6 assets, files cleared, analytics reloaded
```

- `demo-rehearse` ends in the post-demo state (7 assets, released handover), so always run
  `demo-reset` before going live.
- Measured on this Mac with Gemma 3 4B: about 34 s of API time per rehearsal. The slow steps
  are the two certificate uploads (about 13 s together) and the claim checklist (about 8 s).
  Talk while they run.
- Keep two browser windows: A for Ramesh, B (a private window) for Priya and the reviewers,
  so switching people needs no sign-out. Sign in at http://localhost:3100/signin.
- Files for upload are in `fixtures/documents/`: `fd_receipt.txt`, `death_certificate_mismatch.txt`
  and `death_certificate.txt`.
- If a click seems to do nothing, click once more. Automated browser runs dropped clicks;
  the dropped clicks never reached the page (no events were logged), so they came from the
  automation, not the app. Check once by hand.

## Screen by screen

| Time | Who / screen | Do | Say |
|---|---|---|---|
| 0:00 | **A** Ramesh → Workspace → **Chat** | Type: *I opened a ₹5 lakh FD for 3 years.* | "Ramesh just talks. The model drafts; nothing is recorded yet." The assistant asks which bank. The review card shows ₹5,00,000 and 36 months, each with the words it came from. |
| 0:25 | Chat | **Upload a document** → `fd_receipt.txt` → **Add to draft** → **Confirm** | "The receipt fills in the bank and the date, with page and line evidence. Ramesh confirms once; a retry can't create a second record." |
| 0:55 | **Workspace** | Scroll the table and the **How it connects** graph | "Seven assets now. The graph shows only links that were recorded. *To check* flags the term policy's **unknown** nomination (unknown is not 'none') and the 2023 savings statement as old information." |
| 1:20 | Workspace → **Family Asset Passport** | **Generate my passport**, then **Export for Priya Shah**; point at **What your family can see** | "A versioned PDF from a snapshot. Before any handover, Priya sees contacts and where papers are kept. No assets." |
| 1:45 | **B** Priya → Family | **Request handover** → **Add a document** `death_certificate_mismatch.txt` → **Submit for review** | "Later, Priya asks. Identity and death checks are simulated in this MVP." |
|  | **B** Anil (reviewer) → Reviews | Point at the name mismatch → **Hold** | "The name on the certificate differs from the record, so the case is held. A held case accepts no approvals." |
|  | **B** Priya | **Add a document** `death_certificate.txt` | |
|  | **B** Anil | **Resume review** → **Approve** (1 of 2) | "One approval never releases." |
|  | **B** Meera (reviewer) | **Approve** | "Two different reviewers on the current evidence: released. Only the five granted assets; the SGD account and the new FD stay hidden." |
| 2:20 | **B** Priya → Family | **Download your Family Asset Passport** → on *Savings account*, **Prepare claim checklist** | "Her scoped PDF. The checklist cites the bank's policy section by section, and the unknowns stay unknown. The bank decides the claim." |
| 2:45 | **A** Ramesh → **Trends** | Point at the review flags and the classifier line | "Transactions go through Pub/Sub into BigQuery-style analytics. The classifier abstains below 0.55 confidence. Synthetic data, measured on held-out accounts." |
| 3:00 | | | **"One maintained record, controlled family access, a clearer path to claims."** |

### Optional extra (outside the three minutes)

**A** Ramesh → Workspace → **Unclaimed deposits (optional)** → agree → **Check the registry**.

Show the possible ₹6,42,000 fixed deposit, scored 91/100 "strong evidence", and its evidence
path. Then show the ruled-out list, where the same name with a different date of birth is
excluded. Say: "A heuristic score, not a probability. It is not added to the inventory, and
nothing is claimed or paid."

## If something goes wrong

| Symptom | Fix |
|---|---|
| Chat says the model is unavailable | `ollama serve` and `ollama pull gemma3:4b`; or restart with `LLM_PROVIDER=fixture` (labelled as rules, not a model) |
| Workspace shows 7 assets at the start | `make demo-reset` |
| A document stays "pending" | The worker isn't running: restart `make dev` |
| Start again mid-demo | `make demo-reset`, then reload both windows |
