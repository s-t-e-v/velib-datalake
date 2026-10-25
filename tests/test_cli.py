from __future__ import annotations

from datetime import UTC, datetime, timedelta

from velib_datalake.cli import collection_history
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

