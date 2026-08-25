.DEFAULT_GOAL := help

UV_CACHE_DIR ?= $(CURDIR)/.runtime/uv-cache
export UV_CACHE_DIR

.PHONY: help setup env infra-up infra-down infra-status migrate api worker web format format-check lint typecheck test build check phase0-test clean

help:
	@echo "RevenueGuard development commands"
	@echo "  make setup         Install locked Python and Node dependencies"
	@echo "  make env           Create .env from .env.example if absent"
	@echo "  make infra-up      Start PostgreSQL and Redis"
	@echo "  make migrate       Apply Alembic migrations"
	@echo "  make api           Run FastAPI with reload"
	@echo "  make worker        Run the Celery worker"
	@echo "  make web           Run the Next.js dashboard"
	@echo "  make check         Run all formatting, lint, type, test, and build checks"

setup:
	uv sync --all-packages --frozen
	npm ci

env:
	@test -f .env || cp .env.example .env

infra-up:
	docker compose up -d --wait postgres redis

infra-down:
	docker compose down

infra-status:
	docker compose ps

migrate:
	uv run alembic upgrade head

api:
	uv run uvicorn revenueguard_api.main:app --host 0.0.0.0 --port 8000 --reload

worker:
	uv run celery -A revenueguard_worker.celery_app:celery_app worker --loglevel=INFO

web:
	npm run dev:web

format:
	uv run ruff format .
	uv run ruff check --fix .
	npm run format:web

format-check:
	uv run ruff format --check .
	npm run format:check:web

lint:
	uv run ruff check .
	npm run lint:web

typecheck:
	uv run mypy
	npm run typecheck:web

test:
	PYTHONDONTWRITEBYTECODE=1 uv run pytest
	npm run test:web

build:
	npm run build:web
	docker compose config --quiet

phase0-test:
	PYTHONDONTWRITEBYTECODE=1 uv run python -m unittest tests.contract.test_phase0_contracts

check: format-check lint typecheck test build

clean:
	find . -type d -name __pycache__ -prune -exec rm -r {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache apps/web/.next apps/web/coverage
