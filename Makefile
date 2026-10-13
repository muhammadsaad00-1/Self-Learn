# Common developer commands. Run `make help` for the list.
SHELL := /bin/bash
UV := uv --directory backend

# Load .env (if present) into every recipe's environment.
-include .env
export

.PHONY: help install up down db-reset logs run worker eval web api-types migrate migration test lint fmt typecheck check hooks-update

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install deps + git hooks
	$(UV) sync
	cd frontend && npm ci
	$(UV) run pre-commit install --hook-type pre-commit --hook-type commit-msg

up: ## Start local stack (postgres, redis, api, worker) in Docker
	docker compose up -d --build --wait postgres redis api
	docker compose up -d --build worker

down: ## Stop local stack (keeps data volume)
	docker compose down

db-reset: ## Wipe the local database volume and recreate it (reruns infra/postgres init scripts)
	docker compose down -v
	docker compose up -d --wait postgres
	$(MAKE) migrate

logs: ## Tail stack logs
	docker compose logs -f

run: ## Run API locally with auto-reload (needs `make up` for postgres/redis)
	$(UV) run uvicorn agentflow.main:create_app --factory --reload --port 8000

worker: ## Run the workflow worker locally (needs `make up` and GROQ_API_KEY in .env)
	$(UV) run python -m agentflow.worker

eval: ## Evaluate the triage agent against the real model (uses Groq tokens)
	$(UV) run python evals/run_triage.py

web: ## Run the Angular dashboard on http://localhost:4200 (needs `make run` or `make up` for the API)
	cd frontend && npm start

api-types: ## Regenerate frontend API types from the backend's OpenAPI schema (run after API changes)
	$(UV) run python -c "import json; from agentflow.main import create_app; print(json.dumps(create_app().openapi(), indent=2))" > frontend/src/app/api/openapi.json
	cd frontend && npm run api-types

migrate: ## Apply all pending DB migrations
	$(UV) run alembic -c pyproject.toml upgrade head

migration: ## Autogenerate a migration from model changes: make migration m="add x" (then REVIEW it)
	$(UV) run alembic -c pyproject.toml revision --autogenerate -m "$(m)"

test: ## Run tests (needs `make up` for postgres/redis)
	$(UV) run pytest

lint: ## Lint + format check
	$(UV) run ruff check . && $(UV) run ruff format --check .

fmt: ## Auto-fix lint + format
	$(UV) run ruff check --fix . && $(UV) run ruff format .

typecheck: ## mypy --strict
	$(UV) run mypy

check: ## Everything CI runs (backend + frontend)
	$(UV) run pre-commit run --all-files && $(MAKE) test
	cd frontend && npx prettier --check "src/**/*.{ts,css,html}" && npm run build && npm test

hooks-update: ## Bump pre-commit hook versions
	$(UV) run pre-commit autoupdate
