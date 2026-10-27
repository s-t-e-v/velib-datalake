from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from velib_datalake import cli
from velib_datalake.cli import collection_history
from velib_datalake.config import Settings
from velib_datalake.models import CollectionMetadata


def metadata(collection_id: str, started_at: datetime) -> CollectionMetadata:
    timestamp = started_at.isoformat()
    return CollectionMetadata(
        schema_version=1,
        collection_id=collection_id,
        source_url="https://example.test",
        source_parameters={},
        request_started_at=timestamp,
        request_ended_at=timestamp,
        http_status=200,
        response_content_type="application/json",
        record_count=1,
        validation_outcome="passed",
        validation_errors=[],
        validation_warnings=[],
        eligible_for_processing=True,
    )


def test_collection_history_surfaces_missing_intervals() -> None:
    start = datetime(2026, 10, 25, 13, 0, tzinfo=UTC)

    history = collection_history(
        [metadata("first", start), metadata("third", start + timedelta(minutes=11))]
    )

    assert history["detected_gaps"] == [
        {
            "after_collection_id": "first",
            "before_collection_id": "third",
            "duration_seconds": 660.0,
            "estimated_missing_runs": 1,
        }
    ]


@pytest.mark.parametrize("process", [False, True])
def test_collect_processes_only_current_saved_snapshot(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, process: bool
) -> None:
    settings = replace(settings, database_url="postgresql://example.test/velib")
    saved = metadata("current", datetime.now(UTC))
    store = Mock()
    store.read.return_value = (saved, b"[]")
    repository = Mock()
    events = []

    def collect(*_args: object) -> CollectionMetadata:
        events.append("raw_saved")
        return saved

    repository.save_collection.side_effect = lambda *_args, **_kwargs: events.append("sql_saved")
    monkeypatch.setattr("sys.argv", ["velib", "collect"] + (["--process"] if process else []))
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "build_store", lambda _settings: store)
    monkeypatch.setattr(cli, "build_client", lambda _settings: Mock())
    monkeypatch.setattr(cli, "collect_once", collect)
    monkeypatch.setattr(cli, "PostgresRepository", lambda _url: repository)

    cli.main()

    assert events == (["raw_saved", "sql_saved"] if process else ["raw_saved"])
    store.list_metadata.assert_not_called()
    if process:
        store.read.assert_called_once_with("current")
        repository.save_collection.assert_called_once()
    else:
        store.read.assert_not_called()
        repository.save_collection.assert_not_called()


def test_collect_keeps_raw_snapshot_when_database_load_fails(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from velib_datalake.storage import LocalRawStore

    settings = replace(settings, database_url="postgresql://example.test/velib")
    store = LocalRawStore(settings.local_raw_dir)
    saved = metadata("retained", datetime.now(UTC))

    def collect(*_args: object) -> CollectionMetadata:
        return store.save(saved, b"[]")

    repository = Mock()
    repository.save_collection.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr("sys.argv", ["velib", "collect", "--process"])
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "build_store", lambda _settings: store)
    monkeypatch.setattr(cli, "build_client", lambda _settings: Mock())
    monkeypatch.setattr(cli, "collect_once", collect)
    monkeypatch.setattr(cli, "PostgresRepository", lambda _url: repository)

    with pytest.raises(RuntimeError, match="database unavailable"):
        cli.main()

    assert store.read("retained")[1] == b"[]"

