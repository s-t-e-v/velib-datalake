DATABASE_URL ?= postgresql://velib:velib@localhost:5432/velib

VM_DEPLOYMENT ?= velib-dev
SSH_KEY ?= $(HOME)/.ssh/velib-dev

.PHONY: install dev-up dev-down dev-migrate dev-collect dev-process dev-indicator
.PHONY: test test-integration check dev-e2e vm-deploy

vm-deploy:
	bash scripts/deploy-app.sh "$(VM_DEPLOYMENT)" "$(SSH_KEY)"

install:
	uv sync --extra dev

dev-up:
	docker compose -f dev/compose.yml up -d --wait postgres

dev-down:
	docker compose -f dev/compose.yml down

dev-migrate:
	DATABASE_URL=$(DATABASE_URL) uv run velib migrate

dev-collect:
	DATABASE_URL=$(DATABASE_URL) uv run velib collect

dev-process:
	DATABASE_URL=$(DATABASE_URL) uv run velib process

dev-indicator:
	DATABASE_URL=$(DATABASE_URL) uv run velib indicator

test:
	uv run pytest

test-integration: dev-up dev-migrate
	TEST_DATABASE_URL=$(DATABASE_URL) uv run pytest

check:
	uv run ruff check .

dev-e2e: dev-up dev-migrate dev-collect dev-process dev-indicator

