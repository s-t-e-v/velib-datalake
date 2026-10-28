from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any

from .collector import collect_once
from .config import ConfigurationError, Settings
from .database import PostgresRepository, migrate
from .http import ApiClient
from .indicator import calculate_indicator
from .models import CollectionMetadata
from .storage import AzureBlobRawStore, LocalRawStore, RawStore
from .transform import transform_export


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
    )
    # Azure SDK request/response logs are extremely verbose at INFO and obscure the pipeline's
    # own operational events during a full replay. Warnings and errors remain visible.
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.Formatter.converter = __import__("time").gmtime


def build_store(settings: Settings) -> RawStore:
    if settings.storage_backend == "local":
        return LocalRawStore(settings.local_raw_dir)
    assert settings.storage_account_url is not None
    return AzureBlobRawStore(settings.storage_account_url, settings.storage_container)


def build_client(settings: Settings) -> ApiClient:
    return ApiClient(
        retries=settings.http_retries,
        backoff_seconds=settings.http_backoff_seconds,
        connect_timeout_seconds=settings.connect_timeout_seconds,
        read_timeout_seconds=settings.read_timeout_seconds,
    )


def require_database_url(settings: Settings) -> str:
    if not settings.database_url:
        raise ConfigurationError("DATABASE_URL is required for this command")
    return settings.database_url


def collection_history(items: list[Any], expected_interval_minutes: int = 5) -> dict[str, Any]:
    attempts = [asdict(item) for item in items]
    gaps = []
    for previous, current in zip(items, items[1:], strict=False):
        previous_time = datetime.fromisoformat(previous.request_started_at)
        current_time = datetime.fromisoformat(current.request_started_at)
        seconds = (current_time - previous_time).total_seconds()
        missed = max(0, int(seconds // (expected_interval_minutes * 60)) - 1)
        if missed:
            gaps.append(
                {
                    "after_collection_id": previous.collection_id,
                    "before_collection_id": current.collection_id,
                    "duration_seconds": seconds,
                    "estimated_missing_runs": missed,
                }
            )
    return {"attempts": attempts, "detected_gaps": gaps}


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def reconciliation_backlog(
    store: RawStore, repository: PostgresRepository
) -> tuple[list[CollectionMetadata], int]:
    eligible = [item for item in store.list_metadata() if item.eligible_for_processing]
    processed = repository.processed_collection_ids([item.collection_id for item in eligible])
    return [item for item in eligible if item.collection_id not in processed], len(processed)


def backlog_report(
    backlog: list[CollectionMetadata], *, eligible_count: int, processed_count: int
) -> dict[str, Any]:
    return {
        "eligible_raw_count": eligible_count,
        "processed_eligible_count": processed_count,
        "backlog_count": len(backlog),
        "backlog": [
            {
                "collection_id": item.collection_id,
                "request_started_at": item.request_started_at,
                "request_ended_at": item.request_ended_at,
            }
            for item in backlog
        ],
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="velib")
    commands = value.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="perform one collection and exit")
    collect.add_argument(
        "--process",
        action="store_true",
        help="load this snapshot into PostgreSQL after saving raw data",
    )
    commands.add_parser("migrate", help="apply PostgreSQL migrations")
    process = commands.add_parser("process", help="reprocess eligible raw collections")
    process.add_argument("--collection-id")
    commands.add_parser("backlog", help="list eligible raw collections missing from PostgreSQL")
    reconcile = commands.add_parser("reconcile", help="process missing collections oldest first")
    reconcile.add_argument("--limit", type=positive_integer, default=100)
    indicator = commands.add_parser("indicator", help="calculate the zero-bicycle indicator")
    indicator.add_argument("--collection-id")
    commands.add_parser("collections", help="list successful and failed collection metadata")
    return value


def main() -> None:
    configure_logging()
    args = parser().parse_args()
    settings = Settings.from_env()
    store = build_store(settings)

    if args.command == "collect":
        metadata = collect_once(settings, store, build_client(settings))
        print(json.dumps(asdict(metadata), ensure_ascii=False, indent=2))
        if args.process:
            repository = PostgresRepository(require_database_url(settings))
            metadata, body = store.read(metadata.collection_id)
            repository.save_collection(
                metadata,
                transform_export(body),
                freshness_minutes=settings.freshness_minutes,
            )
    elif args.command == "migrate":
        applied = migrate(require_database_url(settings))
        print(json.dumps({"applied": applied}))
    elif args.command == "collections":
        print(json.dumps(collection_history(store.list_metadata()), indent=2))
    elif args.command == "backlog":
        repository = PostgresRepository(require_database_url(settings))
        backlog, processed_count = reconciliation_backlog(store, repository)
        print(
            json.dumps(
                backlog_report(
                    backlog,
                    eligible_count=processed_count + len(backlog),
                    processed_count=processed_count,
                ),
                indent=2,
            )
        )
    elif args.command == "reconcile":
        repository = PostgresRepository(require_database_url(settings))
        backlog, _processed_count = reconciliation_backlog(store, repository)
        selected = backlog[: args.limit]
        processed = []
        for item in selected:
            metadata, body = store.read(item.collection_id)
            repository.save_collection(
                metadata,
                transform_export(body),
                freshness_minutes=settings.freshness_minutes,
            )
            processed.append(item.collection_id)
        print(
            json.dumps(
                {
                    "backlog_before": len(backlog),
                    "selected_count": len(selected),
                    "processed": processed,
                    "remaining_count": len(backlog) - len(processed),
                },
                indent=2,
            )
        )
    elif args.command == "process":
        repository = PostgresRepository(require_database_url(settings))
        if args.collection_id:
            identifiers = [args.collection_id]
        else:
            identifiers = [
                item.collection_id for item in store.list_metadata() if item.eligible_for_processing
            ]
        processed = []
        for collection_id in identifiers:
            metadata, body = store.read(collection_id)
            repository.save_collection(
                metadata,
                transform_export(body),
                freshness_minutes=settings.freshness_minutes,
            )
            processed.append(collection_id)
        print(json.dumps({"processed": processed, "count": len(processed)}))
    elif args.command == "indicator":
        repository = PostgresRepository(require_database_url(settings))
        collection_id, collected_at, source_count, missing_count, observations = (
            repository.indicator_input(args.collection_id)
        )
        result = calculate_indicator(
            collection_id=collection_id,
            collected_at=collected_at,
            observations=observations,
            source_record_count=source_count,
            missing_stationcode_count=missing_count,
            freshness_minutes=settings.freshness_minutes,
        )
        print(json.dumps(result.as_dict(), indent=2))


if __name__ == "__main__":
    main()

