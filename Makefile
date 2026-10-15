.PHONY: dev db migrate seed seed-after reset transactions classifier tg-sim test test-db lint pii check security demo-reset demo-check demo-rehearse stop

dev:            ## Start db, migrate, run api + worker + web
	./scripts/dev.sh

db:
	docker compose up -d --wait db

migrate: db
	cd apps/api && uv run alembic upgrade head

seed: migrate    ## Synthetic fixtures, "before" state (6 assets); safe to repeat
	uv run reclaim-seed

seed-after: migrate ## Adds the confirmed FD (7 assets)
	uv run reclaim-seed --state after

reset: migrate   ## Wipe synthetic demo tenants and reseed "before"
	uv run reclaim-seed --reset

demo-reset: migrate ## Deterministic demo start: 6 assets, synthetic files cleared, analytics feed + predictions
	uv run reclaim-demo reset

demo-check:     ## Preflight against the running stack (make dev): database, 6 assets, provider status
	uv run reclaim-demo check

demo-rehearse:  ## Drive the whole 3-minute story through the running API and verify it; then make demo-reset
	uv run reclaim-demo rehearse

transactions:   ## Synthetic transaction feed into the local warehouse (idempotent)
	uv run reclaim-transactions seed

classifier:     ## Retrain the transaction classifier (synthetic data) and rewrite docs/model_card.md
	uv run reclaim-classifier train && uv run reclaim-classifier predict

tg-sim:         ## Interactive local Telegram simulator (dry-run; needs make dev running)
	uv run reclaim-telegram-sim

test:           ## Unit tests (no database needed)
	uv run pytest -m "not db"

test-db: migrate ## Tests that need the local database
	uv run pytest -m db

lint:
	uv run ruff check apps workers tests
	cd apps/web && npx tsc --noEmit

pii:
	bash scripts/lint-pii.sh

check: lint pii test test-db

# Focused security suite (docs/SECURITY.md); needs the local database.
security:
	bash scripts/security.sh

stop:
	docker compose stop db
