from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from velib_datalake import cli
from velib_datalake.cli import collection_history, reconciliation_backlog
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
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from velib_datalake.storage import LocalRawStore

    settings = replace(settings, database_url="postgresql://example.test/velib")
    store = LocalRawStore(settings.local_raw_dir)
    saved = metadata("retained", datetime.now(UTC))

    def collect(*_args: object) -> CollectionMetadata:
        return store.save(saved, b"[]")

    repository = Mock()
    repository.save_collection.side_effect = [RuntimeError("database unavailable"), None]
    repository.processed_collection_ids.return_value = set()
    monkeypatch.setattr("sys.argv", ["velib", "collect", "--process"])
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "build_store", lambda _settings: store)
    monkeypatch.setattr(cli, "build_client", lambda _settings: Mock())
    monkeypatch.setattr(cli, "collect_once", collect)
    monkeypatch.setattr(cli, "PostgresRepository", lambda _url: repository)

    with pytest.raises(RuntimeError, match="database unavailable"):
        cli.main()

    assert store.read("retained")[1] == b"[]"
    capsys.readouterr()

    monkeypatch.setattr("sys.argv", ["velib", "reconcile", "--limit", "1"])
    cli.main()

    assert json.loads(capsys.readouterr().out) == {
        "backlog_before": 1,
        "selected_count": 1,
        "processed": ["retained"],
        "remaining_count": 0,
    }
    assert repository.save_collection.call_count == 2


def test_reconciliation_backlog_is_eligible_missing_and_chronological() -> None:
    start = datetime(2026, 10, 25, 13, 0, tzinfo=UTC)
    first = metadata("first", start)
    second = metadata("second", start + timedelta(minutes=5))
    failed = metadata("failed", start + timedelta(minutes=10))
    failed.eligible_for_processing = False
    store = Mock()
    store.list_metadata.return_value = [first, second, failed]
    repository = Mock()
    repository.processed_collection_ids.return_value = {"second"}

    backlog, processed_count = reconciliation_backlog(store, repository)

    assert [item.collection_id for item in backlog] == ["first"]
    assert processed_count == 1
    repository.processed_collection_ids.assert_called_once_with(["first", "second"])


def test_reconcile_processes_only_missing_collections_up_to_limit(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = replace(settings, database_url="postgresql://example.test/velib")
    start = datetime(2026, 10, 25, 13, 0, tzinfo=UTC)
    first = metadata("first", start)
    second = metadata("second", start + timedelta(minutes=5))
    third = metadata("third", start + timedelta(minutes=10))
    store = Mock()
    store.list_metadata.return_value = [first, second, third]
    store.read.return_value = (first, b"[]")
    repository = Mock()
    repository.processed_collection_ids.return_value = {"second"}
    monkeypatch.setattr("sys.argv", ["velib", "reconcile", "--limit", "1"])
    monkeypatch.setattr(cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(cli, "build_store", lambda _settings: store)
    monkeypatch.setattr(cli, "PostgresRepository", lambda _url: repository)

    cli.main()

    assert json.loads(capsys.readouterr().out) == {
        "backlog_before": 2,
        "selected_count": 1,
        "processed": ["first"],
        "remaining_count": 1,
    }
    store.read.assert_called_once_with("first")
    repository.save_collection.assert_called_once()

