from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from .config import Settings
from .http import ApiClient, SourceRequestError
from .models import CollectionMetadata, validate_export
from .storage import RawStore

LOGGER = logging.getLogger(__name__)


class CollectionFailed(RuntimeError):
    def __init__(self, message: str, collection_id: str) -> None:
        super().__init__(message)
        self.collection_id = collection_id


SENSITIVE_NAME_FRAGMENTS = ("key", "token", "password", "secret", "authorization")


def _sensitive_name(name: str) -> bool:
    return any(fragment in name.lower() for fragment in SENSITIVE_NAME_FRAGMENTS)


def safe_source_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        key: "[REDACTED]" if _sensitive_name(key) else value
        for key, value in parameters.items()
    }


def safe_source_url(url: str) -> str:
    parts = urlsplit(url)
    safe_query_values = [
        (key, "[REDACTED]" if _sensitive_name(key) else value)
        for key, value in parse_qsl(parts.query)
    ]
    query = urlencode(
        safe_query_values,
        doseq=True,
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _metadata(
    collection_id: str,
    settings: Settings,
    started_at: datetime,
    ended_at: datetime,
    *,
    status: int | None,
    content_type: str | None,
    record_count: int | None,
    outcome: str,
    errors: list[str],
    warnings: list[str],
    failure_type: str | None = None,
    failure_message: str | None = None,
) -> CollectionMetadata:
    return CollectionMetadata(
        schema_version=1,
        collection_id=collection_id,
        source_url=safe_source_url(settings.source_url),
        source_parameters=safe_source_parameters(settings.source_params),
        request_started_at=started_at.isoformat(),
        request_ended_at=ended_at.isoformat(),
        http_status=status,
        response_content_type=content_type,
        record_count=record_count,
        validation_outcome=outcome,
        validation_errors=errors,
        validation_warnings=warnings,
        eligible_for_processing=False,
        failure_type=failure_type,
        failure_message=failure_message,
    )


def collect_once(settings: Settings, store: RawStore, client: ApiClient) -> CollectionMetadata:
    collection_id = str(uuid4())
    with store.collection_lock():
        LOGGER.info("collection_started collection_id=%s", collection_id)
        try:
            response = client.fetch(settings.source_url, settings.source_params)
        except SourceRequestError as exc:
            metadata = _metadata(
                collection_id,
                settings,
                exc.started_at,
                exc.ended_at,
                status=None,
                content_type=None,
                record_count=None,
                outcome="failed",
                errors=["network_request_failed"],
                warnings=[],
                failure_type="network",
                failure_message=str(exc),
            )
            store.save(metadata, None)
            LOGGER.exception(
                "collection_failed collection_id=%s failure_type=network", collection_id
            )
            raise CollectionFailed("Source request failed", collection_id) from exc

        if not 200 <= response.status_code < 300:
            metadata = _metadata(
                collection_id,
                settings,
                response.started_at,
                response.ended_at,
                status=response.status_code,
                content_type=response.content_type,
                record_count=None,
                outcome="failed",
                errors=[f"unexpected_http_status: {response.status_code}"],
                warnings=[],
                failure_type="http",
                failure_message=f"HTTP {response.status_code}",
            )
            store.save(metadata, response.body)
            LOGGER.error(
                "collection_failed collection_id=%s failure_type=http status=%s",
                collection_id,
                response.status_code,
            )
            raise CollectionFailed(f"Source returned HTTP {response.status_code}", collection_id)

        validation = validate_export(response.body, settings.min_records)
        metadata = _metadata(
            collection_id,
            settings,
            response.started_at,
            response.ended_at,
            status=response.status_code,
            content_type=response.content_type,
            record_count=validation.record_count,
            outcome=validation.outcome,
            errors=validation.errors,
            warnings=validation.warnings,
            failure_type=None if validation.successful else "validation",
            failure_message=None if validation.successful else "; ".join(validation.errors),
        )
        store.save(metadata, response.body)
        if not validation.successful:
            LOGGER.error(
                "collection_failed collection_id=%s failure_type=validation", collection_id
            )
            raise CollectionFailed("Response validation failed", collection_id)
        LOGGER.info(
            "collection_succeeded collection_id=%s record_count=%s validation=%s",
            collection_id,
            metadata.record_count,
            metadata.validation_outcome,
        )
        return metadata


def utc_now() -> datetime:
    return datetime.now(UTC)

