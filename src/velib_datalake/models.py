from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


@dataclass(frozen=True)
class ValidationResult:
    outcome: str
    record_count: int | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def successful(self) -> bool:
        return self.outcome in {"passed", "passed_with_warnings"}


@dataclass
class CollectionMetadata:
    schema_version: int
    collection_id: str
    source_url: str
    source_parameters: dict[str, Any]
    request_started_at: str
    request_ended_at: str
    http_status: int | None
    response_content_type: str | None
    record_count: int | None
    validation_outcome: str
    validation_errors: list[str]
    validation_warnings: list[str]
    eligible_for_processing: bool
    failure_type: str | None = None
    failure_message: str | None = None
    data_location: str | None = None
    metadata_location: str | None = None

    def to_bytes(self) -> bytes:
        return (json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    @classmethod
    def from_bytes(cls, value: bytes) -> CollectionMetadata:
        return cls(**json.loads(value))


def validate_export(body: bytes, min_records: int) -> ValidationResult:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ValidationResult("failed", None, [f"invalid_json: {exc}"])
    if not isinstance(payload, list):
        return ValidationResult("failed", None, ["top_level_value_is_not_an_array"])

    errors: list[str] = []
    warnings: list[str] = []
    if len(payload) < min_records:
        errors.append(f"record_count_below_minimum: {len(payload)} < {min_records}")

    identifiers: list[str] = []
    missing_identifier = 0
    incomplete_observation = 0
    for record in payload:
        if not isinstance(record, dict):
            incomplete_observation += 1
            continue
        stationcode = record.get("stationcode")
        if not isinstance(stationcode, str) or not stationcode.strip():
            missing_identifier += 1
        else:
            identifiers.append(stationcode)
        if (
            parse_datetime(record.get("duedate")) is None
            or not isinstance(record.get("numbikesavailable"), int)
            or record.get("is_renting") not in {"OUI", "NON"}
        ):
            incomplete_observation += 1

    duplicates = len(identifiers) - len(set(identifiers))
    if duplicates:
        errors.append(f"duplicate_station_identifiers: {duplicates}")
    if missing_identifier:
        warnings.append(f"records_missing_stationcode: {missing_identifier}")
    if incomplete_observation:
        warnings.append(f"records_with_invalid_indicator_fields: {incomplete_observation}")

    if errors:
        outcome = "failed"
    elif warnings:
        outcome = "passed_with_warnings"
    else:
        outcome = "passed"
    return ValidationResult(outcome, len(payload), errors, warnings)

