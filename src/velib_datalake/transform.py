from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .models import parse_datetime


def _integer(value: Any, field: str, errors: list[str], *, required: bool = False) -> int | None:
    if value is None and not required:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"invalid_{field}")
        return None
    return value


def _oui_non(value: Any, field: str, errors: list[str], *, required: bool = False) -> bool | None:
    if value is None and not required:
        return None
    if value == "OUI":
        return True
    if value == "NON":
        return False
    errors.append(f"invalid_{field}")
    return None


@dataclass(frozen=True)
class Observation:
    stationcode: str
    name: str | None
    is_installed: bool | None
    capacity: int | None
    numdocksavailable: int | None
    numbikesavailable: int | None
    mechanical: int | None
    ebike: int | None
    is_renting: bool | None
    is_returning: bool | None
    duedate: datetime | None
    is_valid: bool
    validation_errors: tuple[str, ...]


@dataclass(frozen=True)
class TransformResult:
    observations: list[Observation]
    source_record_count: int
    missing_stationcode_count: int


def transform_export(body: bytes) -> TransformResult:
    payload = json.loads(body)
    if not isinstance(payload, list):
        raise ValueError("Raw export is not an array")
    observations: list[Observation] = []
    missing_stationcode_count = 0
    for value in payload:
        if not isinstance(value, dict):
            missing_stationcode_count += 1
            continue
        stationcode = value.get("stationcode")
        if not isinstance(stationcode, str) or not stationcode.strip():
            missing_stationcode_count += 1
            continue
        errors: list[str] = []
        duedate = parse_datetime(value.get("duedate"))
        if duedate is None:
            errors.append("invalid_duedate")
        observation = Observation(
            stationcode=stationcode,
            name=value.get("name") if isinstance(value.get("name"), str) else None,
            is_installed=_oui_non(value.get("is_installed"), "is_installed", errors),
            capacity=_integer(value.get("capacity"), "capacity", errors),
            numdocksavailable=_integer(value.get("numdocksavailable"), "numdocksavailable", errors),
            numbikesavailable=_integer(
                value.get("numbikesavailable"), "numbikesavailable", errors, required=True
            ),
            mechanical=_integer(value.get("mechanical"), "mechanical", errors),
            ebike=_integer(value.get("ebike"), "ebike", errors),
            is_renting=_oui_non(value.get("is_renting"), "is_renting", errors, required=True),
            is_returning=_oui_non(value.get("is_returning"), "is_returning", errors),
            duedate=duedate,
            is_valid=not errors,
            validation_errors=tuple(errors),
        )
        observations.append(observation)
    return TransformResult(observations, len(payload), missing_stationcode_count)

