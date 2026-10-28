from __future__ import annotations

from unittest.mock import Mock

import pytest

from velib_datalake.storage import CollectionLockLost, _RenewableAzureLease


class LeaseError(Exception):
    def __init__(self, error_code: str | None = None) -> None:
        self.error_code = error_code


def test_renewable_lease_renews_and_resets_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    monotonic = Mock(side_effect=[100.0, 120.0, 125.0])
    monkeypatch.setattr("velib_datalake.storage.time.monotonic", monotonic)
    lease = Mock()
    renewable = _RenewableAzureLease(lease, duration_seconds=60)

    assert renewable._renew_once() == 20
    renewable.ensure_owned()

    lease.renew.assert_called_once_with()


def test_renewable_lease_retries_a_transient_failure_before_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic = Mock(side_effect=[100.0, 110.0, 111.0])
    monkeypatch.setattr("velib_datalake.storage.time.monotonic", monotonic)
    lease = Mock()
    lease.renew.side_effect = LeaseError()
    renewable = _RenewableAzureLease(lease, duration_seconds=60)

    assert renewable._renew_once() == 2
    renewable.ensure_owned()


def test_renewable_lease_fails_closed_after_renewal_errors_expire_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic = Mock(side_effect=[100.0, 161.0])
    monkeypatch.setattr("velib_datalake.storage.time.monotonic", monotonic)
    lease = Mock()
    lease.renew.side_effect = LeaseError()
    renewable = _RenewableAzureLease(lease, duration_seconds=60)

    renewable._renew_once()

    with pytest.raises(CollectionLockLost, match="expired after renewal failures"):
        renewable.ensure_owned()


def test_renewable_lease_fails_closed_when_azure_reports_lease_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("velib_datalake.storage.time.monotonic", Mock(return_value=100.0))
    lease = Mock()
    lease.renew.side_effect = LeaseError("LeaseLost")
    renewable = _RenewableAzureLease(lease)

    renewable._renew_once()

    with pytest.raises(CollectionLockLost, match="was lost"):
        renewable.ensure_owned()
