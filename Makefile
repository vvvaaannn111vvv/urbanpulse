# UrbanPulse. `make help` lists everything.
# Local targets run the sqlite / in-process-queue backend and need no Docker.
PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help venv install seed train api ingest test lint typecheck fmt \
        web-install web-dev web-build web-lint loadtest cache-report verify \
        stack stack-down stack-logs stack-smoke clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	$(PY) -m venv $(VENV)

install: venv ## Install python deps (dev extra)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

seed: ## Backfill 28 days of synthetic history (real stations + real weather)
	$(BIN)/python scripts/seed_history.py --days 28

train: ## Train LightGBM + rolling-origin CV -> results/cv_metrics.json
	$(BIN)/python -m services.forecast.train --folds 4

api: ## Run the API on :8000 (sqlite backend)
	$(BIN)/uvicorn services.api.main:app --host 127.0.0.1 --port 8000 --reload

ingest: ## Poll the live GBFS feed into the local store
	$(BIN)/python -m services.ingest --role both

test: ## Run pytest
	$(BIN)/python -m pytest -q

lint: ## ruff check
	$(BIN)/ruff check services scripts tests

typecheck: ## mypy
	$(BIN)/mypy

fmt: ## ruff format
	$(BIN)/ruff format services scripts tests

web-install: ## npm ci in web/
	cd web && npm ci

web-dev: ## Vite dev server on :5173 (proxies /api and /ws to :8000)
	cd web && npm run dev

web-build: ## Type-check and build the dashboard
	cd web && npm run build

web-lint: ## eslint
	cd web && npm run lint

loadtest: ## Locust against a running API -> results/locust_*.csv
	PATH="$(PWD)/$(BIN):$$PATH" ./scripts/run_locust.sh

cache-report: ## Measure the cache hit rate -> results/cache_hitrate.json
	$(BIN)/python scripts/measure_cache.py

verify: lint typecheck test web-lint web-build ## Everything that runs without Docker

stack: ## Bring up the full container stack and wait for health
	docker compose up -d --build --wait --wait-timeout 300

stack-smoke: ## Curl the API through the running stack
	curl -fsS http://localhost:8000/health
	curl -fsS -o /dev/null -w 'web: %{http_code}\n' http://localhost:8080/

stack-logs: ## Tail stack logs
	docker compose logs -f --tail=100

stack-down: ## Tear the stack down
	docker compose down -v

clean: ## Remove local artefacts
	rm -rf data .pytest_cache .mypy_cache .ruff_cache web/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
