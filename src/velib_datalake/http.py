from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


@dataclass(frozen=True)
class FetchResult:
    status_code: int
    body: bytes
    content_type: str | None
    started_at: datetime
    ended_at: datetime


class SourceRequestError(RuntimeError):
    def __init__(self, message: str, started_at: datetime, ended_at: datetime) -> None:
        super().__init__(message)
        self.started_at = started_at
        self.ended_at = ended_at


class ApiClient:
    def __init__(
        self,
        retries: int,
        backoff_seconds: float,
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        session: requests.Session | None = None,
    ) -> None:
        self.session = session or requests.Session()
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            allowed_methods=frozenset({"GET"}),
            status_forcelist=(408, 429, 500, 502, 503, 504),
            backoff_factor=backoff_seconds,
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.timeout = (connect_timeout_seconds, read_timeout_seconds)

    def fetch(self, url: str, params: dict[str, Any]) -> FetchResult:
        started_at = datetime.now(UTC)
        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
                headers={"Accept": "application/json", "User-Agent": "velib-datalake/0.1"},
            )
        except requests.RequestException as exc:
            ended_at = datetime.now(UTC)
            raise SourceRequestError(type(exc).__name__, started_at, ended_at) from exc
        ended_at = datetime.now(UTC)
        return FetchResult(
            status_code=response.status_code,
            body=response.content,
            content_type=response.headers.get("Content-Type"),
            started_at=started_at,
            ended_at=ended_at,
        )

