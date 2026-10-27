from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from velib_datalake.database import PostgresRepository, migrate
from velib_datalake.models import CollectionMetadata
from velib_datalake.transform import transform_export


@pytest.mark.integration
def test_reprocessing_same_collection_does_not_duplicate_observations() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    migrate(database_url)
    collection_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    metadata = CollectionMetadata(
        schema_version=1,
        collection_id=collection_id,
        source_url="https://example.test/export",
        source_parameters={},
        request_started_at=now,
        request_ended_at=now,
        http_status=200,
        response_content_type="application/json",
        record_count=1,
        validation_outcome="passed",
        validation_errors=[],
        validation_warnings=[],
        eligible_for_processing=True,
        data_location="raw/response.json",
        metadata_location="raw/metadata.json",
    )
    transformed = transform_export(
        json.dumps(
            [
                {
                    "stationcode": "zero",
                    "numbikesavailable": 0,
                    "is_renting": "OUI",
                    "duedate": now,
                },
                {
                    "stationcode": "available",
                    "numbikesavailable": 4,
                    "is_renting": "OUI",
                    "duedate": now,
                },
                {
                    "stationcode": "closed",
                    "numbikesavailable": 0,
                    "is_renting": "NON",
                    "duedate": now,
                },
                {
                    "stationcode": "stale",
                    "numbikesavailable": 0,
                    "is_renting": "OUI",
                    "duedate": (datetime.fromisoformat(now) - timedelta(minutes=46)).isoformat(),
                },
                {
                    "stationcode": "invalid",
                    "numbikesavailable": "unknown",
                    "is_renting": "OUI",
                    "duedate": now,
                },
                {
                    "numbikesavailable": 0,
                    "is_renting": "OUI",
                    "duedate": now,
                },
            ]
        ).encode()
    )
    repository = PostgresRepository(database_url)
    try:
        repository.save_collection(metadata, transformed, freshness_minutes=45)
        repository.save_collection(metadata, transformed, freshness_minutes=45)
        with psycopg.connect(database_url) as connection:
            count = connection.execute(
                "SELECT count(*) FROM station_observation WHERE collection_id = %s",
                (collection_id,),
            ).fetchone()
            quality = connection.execute(
                """
                SELECT freshness_minutes, source_record_count, persisted_observation_count,
                       excluded_invalid_or_missing, excluded_not_open_for_rental,
                       excluded_stale, eligible_station_count, zero_bicycle_station_count
                FROM collection_quality
                WHERE collection_id = %s
                """,
                (collection_id,),
            ).fetchone()
            indicator = connection.execute(
                """
                SELECT numerator_zero_bicycles, denominator_open_valid_fresh,
                       proportion_zero_bicycles
                FROM zero_bicycle_indicator_history
                WHERE collection_id = %s
                """,
                (collection_id,),
            ).fetchone()
            classification = connection.execute(
                """
                SELECT indicator_classification
                FROM station_availability_history
                WHERE collection_id = %s AND stationcode = 'zero'
                """,
                (collection_id,),
            ).fetchone()
            storage = connection.execute(
                """
                SELECT database_size_bytes, raw_collection_total_bytes,
                       station_observation_total_bytes
                FROM postgres_storage_usage
                """
            ).fetchone()
            health = connection.execute(
                """
                SELECT latest_processed_collection_id, health_status, is_healthy,
                       processed_collection_count, persisted_observation_count,
                       current_proportion_zero_bicycles, health_scope
                FROM pipeline_health
                """
            ).fetchone()
        assert count is not None
        assert count[0] == 5
        assert quality == (45, 6, 5, 2, 1, 1, 2, 1)
        assert indicator == (1, 2, 0.5)
        assert classification == ("eligible_zero_bicycles",)
        assert storage is not None
        assert all(size > 0 for size in storage)
        assert health is not None
        assert str(health[0]) == collection_id
        assert health[1:] == ("healthy", True, 1, 5, 0.5, "processed_sql_only")

        with psycopg.connect(database_url) as connection:
            connection.execute(
                """
                UPDATE raw_collection
                SET request_started_at = CURRENT_TIMESTAMP - interval '21 minutes',
                    request_ended_at = CURRENT_TIMESTAMP - interval '20 minutes'
                WHERE collection_id = %s
                """,
                (collection_id,),
            )
            stale_health = connection.execute(
                """
                SELECT health_status, is_healthy, current_proportion_zero_bicycles
                FROM pipeline_health
                """
            ).fetchone()
        assert stale_health == ("stale", False, None)
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                "DELETE FROM station_observation WHERE collection_id = %s", (collection_id,)
            )
            connection.execute(
                "DELETE FROM raw_collection WHERE collection_id = %s", (collection_id,)
            )

