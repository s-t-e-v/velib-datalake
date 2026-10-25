from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .transform import Observation


@dataclass(frozen=True)
class IndicatorResult:
    collection_id: str
    collected_at: str
    freshness_minutes: int
    numerator_zero_bicycles: int
    denominator_open_valid_fresh: int
    proportion: float | None
    excluded_invalid_or_missing: int
    excluded_not_open_for_rental: int
    excluded_stale: int
    source_record_count: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def calculate_indicator(
    *,
    collection_id: str,
    collected_at: datetime,
    observations: list[Observation],
    source_record_count: int,
    missing_stationcode_count: int,
    freshness_minutes: int,
) -> IndicatorResult:
    cutoff = collected_at - timedelta(minutes=freshness_minutes)
    # A small forward tolerance avoids classifying minor source/collector clock skew as fresh.
    latest_accepted = collected_at + timedelta(minutes=5)
    numerator = 0
    denominator = 0
    invalid = missing_stationcode_count
    not_open = 0
    stale = 0
    for observation in observations:
        if not observation.is_valid:
            invalid += 1
        elif observation.is_renting is not True:
            not_open += 1
        elif observation.duedate is None or not cutoff <= observation.duedate <= latest_accepted:
            stale += 1
        else:
            denominator += 1
            if observation.numbikesavailable == 0:
                numerator += 1
    return IndicatorResult(
        collection_id=collection_id,
        collected_at=collected_at.isoformat(),
        freshness_minutes=freshness_minutes,
        numerator_zero_bicycles=numerator,
        denominator_open_valid_fresh=denominator,
        proportion=numerator / denominator if denominator else None,
        excluded_invalid_or_missing=invalid,
        excluded_not_open_for_rental=not_open,
        excluded_stale=stale,
        source_record_count=source_record_count,
    )

