from __future__ import annotations

import json

from velib_datalake.models import validate_export
from velib_datalake.transform import transform_export


def test_missing_value_is_invalid_not_zero() -> None:
    body = json.dumps(
        [
            {
                "stationcode": "A",
                "is_renting": "OUI",
                "duedate": "2026-10-25T13:15:00+00:00",
            }
        ]
    ).encode()

    result = transform_export(body)

    assert result.observations[0].numbikesavailable is None
    assert result.observations[0].is_valid is False
    assert "invalid_numbikesavailable" in result.observations[0].validation_errors


def test_duplicate_stationcode_fails_collection_validation() -> None:
    body = json.dumps(
        [
            {
                "stationcode": "A",
                "is_renting": "OUI",
                "numbikesavailable": 1,
                "duedate": "2026-10-25T13:15:00+00:00",
            },
            {
                "stationcode": "A",
                "is_renting": "OUI",
                "numbikesavailable": 2,
                "duedate": "2026-10-25T13:15:00+00:00",
            },
        ]
    ).encode()

    validation = validate_export(body, min_records=1)

    assert validation.outcome == "failed"
    assert validation.errors == ["duplicate_station_identifiers: 1"]

