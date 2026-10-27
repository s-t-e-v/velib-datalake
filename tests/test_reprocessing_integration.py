from __future__ import annotations

import json
import os
from datetime import UTC, datetime
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
                    "stationcode": "A",
                    "numbikesavailable": 0,
                    "is_renting": "OUI",
                    "duedate": now,
                }
            ]
        ).encode()
    )
    repository = PostgresRepository(database_url)
    try:
        repository.save_collection(metadata, transformed)
        repository.save_collection(metadata, transformed)
        with psycopg.connect(database_url) as connection:
            count = connection.execute(
                "SELECT count(*) FROM station_observation WHERE collection_id = %s",
                (collection_id,),
            ).fetchone()
        assert count is not None
        assert count[0] == 1
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                "DELETE FROM station_observation WHERE collection_id = %s", (collection_id,)
            )
            connection.execute(
                "DELETE FROM raw_collection WHERE collection_id = %s", (collection_id,)
            )

