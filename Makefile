DATABASE_URL ?= postgresql://velib:velib@localhost:5432/velib

VM_DEPLOYMENT ?= velib-dev
SSH_KEY ?= $(HOME)/.ssh/velib-dev
DASHBOARD_REMOTE_SECRET ?= $(CURDIR)/.secrets/velib-dashboard-password

.PHONY: install dev-up dev-down dev-migrate dev-collect dev-process dev-indicator
.PHONY: test test-integration check dev-e2e vm-deploy
.PHONY: dashboard-up dashboard-remote-up dashboard-down dashboard-logs

vm-deploy:
	bash scripts/deploy-app.sh "$(VM_DEPLOYMENT)" "$(SSH_KEY)"

dashboard-up: dev-up
	DATABASE_URL=$(DATABASE_URL) uv run velib migrate
	docker compose -f dev/dashboard.compose.yml up -d --wait

dashboard-remote-up:
	test -f "$(DASHBOARD_REMOTE_SECRET)"
	VELIB_GRAFANA_DB_HOST=host.docker.internal \
	VELIB_GRAFANA_DB_PORT=15432 \
	VELIB_GRAFANA_DB_NAME=velib \
	VELIB_GRAFANA_DB_USER=velib_dashboard \
	VELIB_GRAFANA_DB_SSL_MODE=disable \
	VELIB_GRAFANA_DB_PASSWORD_FILE="$(DASHBOARD_REMOTE_SECRET)" \
	docker compose -f dev/dashboard.compose.yml up -d --wait

dashboard-down:
	docker compose -f dev/dashboard.compose.yml down

dashboard-logs:
	docker compose -f dev/dashboard.compose.yml logs -f grafana

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

