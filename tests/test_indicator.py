from __future__ import annotations

from datetime import UTC, datetime, timedelta

from velib_datalake.indicator import calculate_indicator
from velib_datalake.transform import Observation


def observation(
    code: str,
    collected_at: datetime,
    *,
    bikes: int | None = 1,
    renting: bool | None = True,
    age_minutes: int = 1,
    valid: bool = True,
) -> Observation:
    return Observation(
        stationcode=code,
        name=code,
        is_installed=True,
        capacity=20,
        numdocksavailable=10,
        numbikesavailable=bikes,
        mechanical=bikes,
        ebike=0,
        is_renting=renting,
        is_returning=True,
        duedate=collected_at - timedelta(minutes=age_minutes),
        is_valid=valid,
        validation_errors=() if valid else ("invalid_numbikesavailable",),
    )


def test_indicator_has_explicit_expected_result_and_exclusions() -> None:
    collected_at = datetime(2026, 10, 25, 13, 20, tzinfo=UTC)
    observations = [
        observation("zero", collected_at, bikes=0),
        observation("available", collected_at, bikes=4),
        observation("closed", collected_at, bikes=0, renting=False),
        observation("stale", collected_at, bikes=0, age_minutes=11),
        observation("missing", collected_at, bikes=None, valid=False),
    ]

    result = calculate_indicator(
        collection_id="example",
        collected_at=collected_at,
        observations=observations,
        source_record_count=6,
        missing_stationcode_count=1,
        freshness_minutes=10,
    )

    assert result.numerator_zero_bicycles == 1
    assert result.denominator_open_valid_fresh == 2
    assert result.proportion == 0.5
    assert result.excluded_invalid_or_missing == 2
    assert result.excluded_not_open_for_rental == 1
    assert result.excluded_stale == 1


def test_zero_denominator_is_unavailable_not_zero() -> None:
    collected_at = datetime(2026, 10, 25, 13, 20, tzinfo=UTC)
    result = calculate_indicator(
        collection_id="empty",
        collected_at=collected_at,
        observations=[observation("closed", collected_at, bikes=0, renting=False)],
        source_record_count=1,
        missing_stationcode_count=0,
        freshness_minutes=10,
    )

    assert result.denominator_open_valid_fresh == 0
    assert result.proportion is None


def test_future_observation_outside_clock_tolerance_is_excluded() -> None:
    collected_at = datetime(2026, 10, 25, 13, 20, tzinfo=UTC)
    future = observation("future", collected_at, bikes=0)
    future = Observation(**{**future.__dict__, "duedate": collected_at + timedelta(minutes=6)})

    result = calculate_indicator(
        collection_id="future",
        collected_at=collected_at,
        observations=[future],
        source_record_count=1,
        missing_stationcode_count=0,
        freshness_minutes=10,
    )

    assert result.denominator_open_valid_fresh == 0
    assert result.excluded_stale == 1

