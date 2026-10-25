from __future__ import annotations

from pathlib import Path

import pytest

from velib_datalake.config import DEFAULT_SOURCE_URL, Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        storage_backend="local",
        local_raw_dir=tmp_path / "raw",
        storage_account_url=None,
        storage_container="velib-raw",
        source_url=DEFAULT_SOURCE_URL,
        source_params={},
        connect_timeout_seconds=0.1,
        read_timeout_seconds=0.1,
        http_retries=0,
        http_backoff_seconds=0,
        min_records=1,
        freshness_minutes=10,
        database_url=None,
    )

