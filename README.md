# ReclaimAI

Conversation-first financial legacy platform: **Prepare → Handover → Recover**.

Owners describe assets in web chat or linked Telegram, confirm proposed changes, and
generate a versioned Family Asset Passport. After a human-reviewed handover, designated
family members see only what they were granted, plus source-grounded claim guidance.

> **One maintained record, controlled family access, a clearer path to claims.**

Hackathon MVP. All demo data is synthetic. No live bank, insurer or registry integration.

## Quick start

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
make dev        # postgres → migrations → api :8100, worker :8101, web :3100
```

Open http://localhost:3100 for system status and the capability table.

## Demo runbook

```bash
make dev            # needs Ollama with gemma3:4b for real local inference
make demo-check     # 6 assets, providers, LLM live
make demo-rehearse  # optional dry run through the API (then reset!)
make demo-reset     # deterministic start: 6 assets, same IDs, files cleared
```

Script: `docs/DEMO.md` (screen by screen, 3:00). Judge notes and the live-vs-fixture table:
`docs/JUDGES.md`. Deployment (prepared, not applied): `docs/DEPLOYMENT.md`.

| Command | What it does |
|---|---|
| `make test` | Unit tests (no database) |
| `make test-db` | Starts Postgres, migrates, runs DB tests |
| `make lint` | ruff + web typecheck |
| `make pii` | PII/log-safety scan |
| `make check` | All of the above |
| `make security` | Focused security suite (docs/SECURITY.md) |
| `make demo-reset` / `demo-check` / `demo-rehearse` | Demo start state / preflight / scripted dry run |
| `make stop` | Stop Postgres (`docker compose down -v` also deletes local data) |

## Layout

```
apps/web     Next.js UI
apps/api     FastAPI API, providers, Alembic migrations
workers      Pub/Sub push worker
infra        Terraform for Google Cloud (prepared, not applied)
fixtures     Synthetic demo data, documents, policies, recovery registry
tests        pytest
docs         Architecture, ADRs
```

## Working on it

`PROJECT_BRIEF.md` is the contract. `ReclaimAI_MVP_Prompts.md` is the build sequence.
`STATUS.md` records what is actually done and verified. See `docs/architecture.md` and
`CONTRIBUTING.md`.
