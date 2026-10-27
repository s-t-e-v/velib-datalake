from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .models import CollectionMetadata
from .transform import Observation, TransformResult


def migration_directory() -> Path:
    project_migrations = Path(__file__).resolve().parents[2] / "migrations"
    if project_migrations.is_dir():
        return project_migrations
    return Path(__file__).resolve().parent / "migrations"


def migrate(database_url: str) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for path in sorted(migration_directory().glob("*.sql")):
            exists = connection.execute(
                "SELECT 1 FROM schema_migration WHERE version = %s", (path.name,)
            ).fetchone()
            if exists:
                continue
            connection.execute(path.read_text(encoding="utf-8"))
            connection.execute("INSERT INTO schema_migration(version) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


class PostgresRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def save_collection(
        self,
        metadata: CollectionMetadata,
        transformed: TransformResult,
        *,
        freshness_minutes: int,
    ) -> None:
        if freshness_minutes < 1:
            raise ValueError("Freshness minutes must be positive")
        if not metadata.data_location or not metadata.metadata_location:
            raise ValueError("Raw locations are required before processing")
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                INSERT INTO raw_collection (
                    collection_id, source_url, source_parameters, request_started_at,
                    request_ended_at, raw_data_location, raw_metadata_location,
                    source_record_count, missing_stationcode_count, validation_outcome,
                    freshness_minutes, processed_at
                ) VALUES (
                    %(collection_id)s, %(source_url)s, %(source_parameters)s::jsonb,
                    %(request_started_at)s, %(request_ended_at)s, %(raw_data_location)s,
                    %(raw_metadata_location)s, %(source_record_count)s,
                    %(missing_stationcode_count)s, %(validation_outcome)s,
                    %(freshness_minutes)s, now()
                )
                ON CONFLICT (collection_id) DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    source_parameters = EXCLUDED.source_parameters,
                    request_started_at = EXCLUDED.request_started_at,
                    request_ended_at = EXCLUDED.request_ended_at,
                    raw_data_location = EXCLUDED.raw_data_location,
                    raw_metadata_location = EXCLUDED.raw_metadata_location,
                    source_record_count = EXCLUDED.source_record_count,
                    missing_stationcode_count = EXCLUDED.missing_stationcode_count,
                    validation_outcome = EXCLUDED.validation_outcome,
                    freshness_minutes = EXCLUDED.freshness_minutes,
                    processed_at = now()
                """,
                {
                    "collection_id": metadata.collection_id,
                    "source_url": metadata.source_url,
                    "source_parameters": psycopg.types.json.Jsonb(metadata.source_parameters),
                    "request_started_at": metadata.request_started_at,
                    "request_ended_at": metadata.request_ended_at,
                    "raw_data_location": metadata.data_location,
                    "raw_metadata_location": metadata.metadata_location,
                    "source_record_count": transformed.source_record_count,
                    "missing_stationcode_count": transformed.missing_stationcode_count,
                    "validation_outcome": metadata.validation_outcome,
                    "freshness_minutes": freshness_minutes,
                },
            )
            rows = []
            for observation in transformed.observations:
                value = asdict(observation)
                value["collection_id"] = metadata.collection_id
                value["validation_errors"] = list(observation.validation_errors)
                rows.append(value)
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO station_observation (
                        collection_id, stationcode, name, is_installed, capacity,
                        numdocksavailable, numbikesavailable, mechanical, ebike,
                        is_renting, is_returning, duedate, is_valid, validation_errors
                    ) VALUES (
                        %(collection_id)s, %(stationcode)s, %(name)s, %(is_installed)s,
                        %(capacity)s, %(numdocksavailable)s, %(numbikesavailable)s,
                        %(mechanical)s, %(ebike)s, %(is_renting)s, %(is_returning)s,
                        %(duedate)s, %(is_valid)s, %(validation_errors)s
                    )
                    ON CONFLICT (collection_id, stationcode) DO UPDATE SET
                        name = EXCLUDED.name,
                        is_installed = EXCLUDED.is_installed,
                        capacity = EXCLUDED.capacity,
                        numdocksavailable = EXCLUDED.numdocksavailable,
                        numbikesavailable = EXCLUDED.numbikesavailable,
                        mechanical = EXCLUDED.mechanical,
                        ebike = EXCLUDED.ebike,
                        is_renting = EXCLUDED.is_renting,
                        is_returning = EXCLUDED.is_returning,
                        duedate = EXCLUDED.duedate,
                        is_valid = EXCLUDED.is_valid,
                        validation_errors = EXCLUDED.validation_errors
                    """,
                    rows,
                )

    def indicator_input(
        self, collection_id: str | None = None
    ) -> tuple[str, datetime, int, int, list[Observation]]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            if collection_id is None:
                collection = connection.execute(
                    "SELECT * FROM raw_collection ORDER BY request_ended_at DESC LIMIT 1"
                ).fetchone()
            else:
                collection = connection.execute(
                    "SELECT * FROM raw_collection WHERE collection_id = %s", (collection_id,)
                ).fetchone()
            if collection is None:
                raise LookupError("No processed collection found")
            records = connection.execute(
                """
                SELECT stationcode, name, is_installed, capacity, numdocksavailable,
                       numbikesavailable, mechanical, ebike, is_renting, is_returning,
                       duedate, is_valid, validation_errors
                FROM station_observation
                WHERE collection_id = %s
                ORDER BY stationcode
                """,
                (collection["collection_id"],),
            ).fetchall()
        observations = [
            Observation(
                stationcode=row["stationcode"],
                name=row["name"],
                is_installed=row["is_installed"],
                capacity=row["capacity"],
                numdocksavailable=row["numdocksavailable"],
                numbikesavailable=row["numbikesavailable"],
                mechanical=row["mechanical"],
                ebike=row["ebike"],
                is_renting=row["is_renting"],
                is_returning=row["is_returning"],
                duedate=row["duedate"],
                is_valid=row["is_valid"],
                validation_errors=tuple(row["validation_errors"]),
            )
            for row in records
        ]
        return (
            str(collection["collection_id"]),
            collection["request_ended_at"],
            collection["source_record_count"],
            collection["missing_stationcode_count"],
            observations,
        )

