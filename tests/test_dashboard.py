from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dev/grafana/dashboards/velib-overview.json"
COMPOSE = ROOT / "dev/dashboard.compose.yml"
DATASOURCE = ROOT / "dev/grafana/provisioning/datasources/velib-postgres.yml"


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
    datasource = DATASOURCE.read_text(encoding="utf-8")

    assert "grafana/grafana:13.2.4" in compose
    assert '"127.0.0.1:3000:3000"' in compose
    assert "GF_SECURITY_DISABLE_INITIAL_ADMIN_CREATION" in compose
    assert "VELIB_GRAFANA_DB_PASSWORD" in compose
    assert "password: $__env{VELIB_GRAFANA_DB_PASSWORD}" in datasource
    assert "velib-postgres" in datasource
