from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_SOURCE_URL = (
    "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/"
    "velib-disponibilite-en-temps-reel/exports/json"
)


class ConfigurationError(ValueError):
    """Raised when environment configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    storage_backend: str
    local_raw_dir: Path
    storage_account_url: str | None
    storage_container: str
    source_url: str
    source_params: dict[str, Any]
    connect_timeout_seconds: float
    read_timeout_seconds: float
    http_retries: int
    http_backoff_seconds: float
    min_records: int
    freshness_minutes: int
    database_url: str | None

    @classmethod
    def from_env(cls) -> Settings:
        try:
            source_params = json.loads(os.getenv("VELIB_SOURCE_PARAMS", "{}"))
        except json.JSONDecodeError as exc:
            raise ConfigurationError("VELIB_SOURCE_PARAMS must be a JSON object") from exc
        if not isinstance(source_params, dict):
            raise ConfigurationError("VELIB_SOURCE_PARAMS must be a JSON object")

        backend = os.getenv("VELIB_STORAGE_BACKEND", "local").lower()
        if backend not in {"local", "azure"}:
            raise ConfigurationError("VELIB_STORAGE_BACKEND must be 'local' or 'azure'")
        account_url = os.getenv("VELIB_STORAGE_ACCOUNT_URL")
        if backend == "azure" and not account_url:
            raise ConfigurationError("VELIB_STORAGE_ACCOUNT_URL is required for Azure storage")

        settings = cls(
            storage_backend=backend,
            local_raw_dir=Path(os.getenv("VELIB_LOCAL_RAW_DIR", "./raw")),
            storage_account_url=account_url,
            storage_container=os.getenv("VELIB_STORAGE_CONTAINER", "velib-raw"),
            source_url=os.getenv("VELIB_SOURCE_URL", DEFAULT_SOURCE_URL),
            source_params=source_params,
            connect_timeout_seconds=float(os.getenv("VELIB_CONNECT_TIMEOUT_SECONDS", "10")),
            read_timeout_seconds=float(os.getenv("VELIB_READ_TIMEOUT_SECONDS", "45")),
            http_retries=int(os.getenv("VELIB_HTTP_RETRIES", "3")),
            http_backoff_seconds=float(os.getenv("VELIB_HTTP_BACKOFF_SECONDS", "1")),
            min_records=int(os.getenv("VELIB_MIN_RECORDS", "1000")),
            freshness_minutes=int(os.getenv("VELIB_FRESHNESS_MINUTES", "45")),
            database_url=os.getenv("DATABASE_URL"),
        )
        if settings.http_retries < 0 or settings.min_records < 1:
            raise ConfigurationError(
                "Retries cannot be negative and minimum records must be positive"
            )
        if settings.freshness_minutes < 1:
            raise ConfigurationError("VELIB_FRESHNESS_MINUTES must be positive")
        return settings

