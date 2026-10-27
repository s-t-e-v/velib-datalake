from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dev/grafana/dashboards/velib-overview.json"
COMPOSE = ROOT / "dev/dashboard.compose.yml"
DEV_COMPOSE = ROOT / "dev/compose.yml"
DATASOURCE = ROOT / "dev/grafana/provisioning/datasources/velib-postgres.yml"
MAKEFILE = ROOT / "Makefile"
PASSWORD_SCRIPT = ROOT / "scripts/dashboard-password.sh"
TUNNEL_SCRIPT = ROOT / "scripts/dashboard-tunnel.sh"


def test_dashboard_is_portable_and_covers_initial_presentation_views() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))

    assert dashboard["uid"] == "velib-overview"
    assert dashboard["refresh"] == "5m"
    assert dashboard["timezone"] == "utc"
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {
        "Pipeline health",
        "Current zero-bicycle proportion",
        "Zero-bicycle indicator history",
        "Collection exclusions",
        "PostgreSQL database size",
    } <= titles

    queries = "\n".join(
        target["rawSql"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
    )
    for view in (
        "pipeline_health",
        "collection_quality",
        "zero_bicycle_indicator_history",
        "latest_station_status",
        "postgres_storage_usage",
    ):
        assert view in queries
    assert all(
        panel["datasource"]["uid"] == "velib-postgres" for panel in dashboard["panels"]
    )


def test_dashboard_container_is_local_only_pinned_and_credential_neutral() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    dev_compose = DEV_COMPOSE.read_text(encoding="utf-8")
    datasource = DATASOURCE.read_text(encoding="utf-8")
    makefile = MAKEFILE.read_text(encoding="utf-8")
    password_script = PASSWORD_SCRIPT.read_text(encoding="utf-8")
    tunnel_script = TUNNEL_SCRIPT.read_text(encoding="utf-8")

    assert "grafana/grafana:13.2.4" in compose
    assert '"127.0.0.1:3000:3000"' in compose
    assert '"127.0.0.1:5432:5432"' in dev_compose
    assert "GF_SECURITY_DISABLE_INITIAL_ADMIN_CREATION" in compose
    assert "VELIB_GRAFANA_DB_PASSWORD:" not in compose
    assert "velib_grafana_db_password" in compose
    assert "password: $__file{/run/secrets/velib_grafana_db_password}" in datasource
    assert "velib-postgres" in datasource
    assert "DASHBOARD_REMOTE_SECRET ?=" in makefile
    assert "openssl rand -hex 32" in password_script
    assert "chmod 604" in password_script
    assert "docker network inspect bridge" in tunnel_script
    assert '-L "$docker_gateway:15432:127.0.0.1:5432"' in tunnel_script
