# Common developer commands. Run `make help` for the list.
SHELL := /bin/bash
UV := uv --directory backend

# Load .env (if present) into every recipe's environment.
-include .env
export

.PHONY: help install up down logs run migrate migration test lint fmt typecheck check hooks-update

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install deps + git hooks
	$(UV) sync
	$(UV) run pre-commit install --hook-type pre-commit --hook-type commit-msg

up: ## Start local stack (postgres, redis, api) in Docker
	docker compose up -d --build --wait

down: ## Stop local stack (keeps data volume)
	docker compose down

logs: ## Tail stack logs
	docker compose logs -f

run: ## Run API locally with auto-reload (needs `make up` for postgres/redis)
	$(UV) run uvicorn agentflow.main:create_app --factory --reload --port 8000

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

check: ## Everything CI runs
	$(UV) run pre-commit run --all-files && $(MAKE) test

hooks-update: ## Bump pre-commit hook versions
	$(UV) run pre-commit autoupdate
