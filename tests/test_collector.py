from __future__ import annotations

from pathlib import Path

import pytest
import requests

from velib_datalake.collector import (
    CollectionFailed,
    collect_once,
    safe_source_parameters,
    safe_source_url,
)
from velib_datalake.config import Settings
from velib_datalake.http import ApiClient
from velib_datalake.storage import LocalRawStore


class FailingSession:
    def mount(self, _prefix: str, _adapter: object) -> None:
        pass

    def get(self, *_args: object, **_kwargs: object) -> None:
        raise requests.ConnectionError("host unavailable")


def test_network_failure_is_recorded_and_not_eligible(settings: Settings) -> None:
    store = LocalRawStore(settings.local_raw_dir)
    client = ApiClient(0, 0, 0.1, 0.1, session=FailingSession())  # type: ignore[arg-type]

    with pytest.raises(CollectionFailed):
        collect_once(settings, store, client)

    attempts = store.list_metadata()
    assert len(attempts) == 1
    assert attempts[0].failure_type == "network"
    assert attempts[0].eligible_for_processing is False
    assert attempts[0].data_location is None


def test_body_without_metadata_is_not_a_complete_collection(tmp_path: Path) -> None:
    store = LocalRawStore(tmp_path / "raw")
    orphan = store.root / "2026/10/25/orphan/response.json"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"[]")

    assert store.list_metadata() == []
    with pytest.raises(FileNotFoundError):
        store.read("orphan")


def test_secret_like_source_parameters_are_redacted() -> None:
    assert safe_source_parameters(
        {"limit": 10, "apikey": "secret-value", "Authorization": "bearer value"}
    ) == {"limit": 10, "apikey": "[REDACTED]", "Authorization": "[REDACTED]"}
    assert safe_source_url("https://example.test/export?limit=10&api_key=value") == (
        "https://example.test/export?limit=10&api_key=%5BREDACTED%5D"
    )

