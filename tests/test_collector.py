from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
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
from velib_datalake.http import ApiClient, FetchResult
from velib_datalake.storage import CollectionLockLost, LocalRawStore


class FailingSession:
    def mount(self, _prefix: str, _adapter: object) -> None:
        pass

    def get(self, *_args: object, **_kwargs: object) -> None:
        raise requests.ConnectionError("host unavailable")


class LostLock:
    def ensure_owned(self) -> None:
        raise CollectionLockLost("lease expired")


class StoreWithLostLock:
    saved = False

    @contextmanager
    def collection_lock(self):  # type: ignore[no-untyped-def]
        yield LostLock()

    def save(self, *_args: object) -> None:
        self.saved = True


class SuccessfulClient:
    def fetch(self, *_args: object, **_kwargs: object) -> FetchResult:
        now = datetime.now(UTC)
        return FetchResult(
            status_code=200,
            body=(
                b'[{"stationcode":"1","duedate":"2026-10-27T18:00:00+00:00",'
                b'"numbikesavailable":1,"is_renting":"OUI"}]'
            ),
            content_type="application/json",
            started_at=now,
            ended_at=now,
        )


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


def test_collection_that_lost_its_lease_cannot_commit_raw_data(settings: Settings) -> None:
    store = StoreWithLostLock()

    with pytest.raises(CollectionLockLost, match="lease expired"):
        collect_once(settings, store, SuccessfulClient())  # type: ignore[arg-type]

    assert store.saved is False


def test_secret_like_source_parameters_are_redacted() -> None:
    assert safe_source_parameters(
        {"limit": 10, "apikey": "secret-value", "Authorization": "bearer value"}
    ) == {"limit": 10, "apikey": "[REDACTED]", "Authorization": "[REDACTED]"}
    assert safe_source_url("https://example.test/export?limit=10&api_key=value") == (
        "https://example.test/export?limit=10&api_key=%5BREDACTED%5D"
    )

