DATABASE_URL ?= postgresql://velib:velib@localhost:5432/velib

.PHONY: install up down migrate collect process indicator test test-all check e2e

install:
	uv sync --extra dev

up:
	docker compose up -d --wait postgres

down:
	docker compose down

migrate:
	DATABASE_URL=$(DATABASE_URL) uv run velib migrate

collect:
	DATABASE_URL=$(DATABASE_URL) uv run velib collect

process:
	DATABASE_URL=$(DATABASE_URL) uv run velib process

indicator:
	DATABASE_URL=$(DATABASE_URL) uv run velib indicator

test:
	uv run pytest

test-all: up migrate
	TEST_DATABASE_URL=$(DATABASE_URL) uv run pytest

check:
	uv run ruff check .

e2e: up migrate collect process indicator

