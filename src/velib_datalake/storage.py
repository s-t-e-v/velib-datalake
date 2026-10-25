from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from .models import CollectionMetadata


class CollectionAlreadyRunning(RuntimeError):
    """Raised when another collector owns the lock."""


class RawStore(Protocol):
    def save(self, metadata: CollectionMetadata, body: bytes | None) -> CollectionMetadata: ...

    def read(self, collection_id: str) -> tuple[CollectionMetadata, bytes]: ...

    def list_metadata(self) -> list[CollectionMetadata]: ...

    @contextmanager
    def collection_lock(self) -> Iterator[None]: ...


class LocalRawStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _collection_dir(self, metadata: CollectionMetadata) -> Path:
        day = metadata.request_started_at[:10].replace("-", "/")
        return self.root / day / metadata.collection_id

    @staticmethod
    def _atomic_write(path: Path, value: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with temporary.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def save(self, metadata: CollectionMetadata, body: bytes | None) -> CollectionMetadata:
        directory = self._collection_dir(metadata)
        data_path = directory / "response.json"
        metadata_path = directory / "metadata.json"
        if body is not None:
            self._atomic_write(data_path, body)
            metadata.data_location = str(data_path)
        metadata.metadata_location = str(metadata_path)
        metadata.eligible_for_processing = bool(
            body is not None
            and metadata.validation_outcome in {"passed", "passed_with_warnings"}
        )
        self._atomic_write(metadata_path, metadata.to_bytes())
        return metadata

    def read(self, collection_id: str) -> tuple[CollectionMetadata, bytes]:
        matches = list(self.root.glob(f"*/*/*/{collection_id}/metadata.json"))
        if len(matches) != 1:
            raise FileNotFoundError(f"Collection {collection_id!r} was not found uniquely")
        metadata = CollectionMetadata.from_bytes(matches[0].read_bytes())
        if not metadata.eligible_for_processing or not metadata.data_location:
            raise ValueError(f"Collection {collection_id!r} is not eligible for processing")
        return metadata, Path(metadata.data_location).read_bytes()

    def list_metadata(self) -> list[CollectionMetadata]:
        metadata = [
            CollectionMetadata.from_bytes(path.read_bytes())
            for path in self.root.glob("*/*/*/*/metadata.json")
        ]
        return sorted(metadata, key=lambda item: item.request_started_at)

    @contextmanager
    def collection_lock(self) -> Iterator[None]:
        import fcntl

        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".collection.lock"
        with lock_path.open("a+b") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CollectionAlreadyRunning("Another local collection is running") from exc
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class AzureBlobRawStore:
    def __init__(self, account_url: str, container_name: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        self.container = service.get_container_client(container_name)

    @staticmethod
    def _prefix(metadata: CollectionMetadata) -> str:
        day = metadata.request_started_at[:10].replace("-", "/")
        return f"collections/{day}/{metadata.collection_id}"

    def save(self, metadata: CollectionMetadata, body: bytes | None) -> CollectionMetadata:
        from azure.storage.blob import ContentSettings

        prefix = self._prefix(metadata)
        data_name = f"{prefix}/response.json"
        metadata_name = f"{prefix}/metadata.json"
        if body is not None:
            self.container.upload_blob(
                data_name,
                body,
                overwrite=False,
                content_settings=ContentSettings(content_type="application/json"),
            )
            metadata.data_location = data_name
        metadata.metadata_location = metadata_name
        metadata.eligible_for_processing = bool(
            body is not None
            and metadata.validation_outcome in {"passed", "passed_with_warnings"}
        )
        # Metadata is the commit marker and is uploaded only after the body succeeds.
        self.container.upload_blob(
            metadata_name,
            metadata.to_bytes(),
            overwrite=False,
            content_settings=ContentSettings(content_type="application/json"),
        )
        return metadata

    def read(self, collection_id: str) -> tuple[CollectionMetadata, bytes]:
        matches = [
            blob.name
            for blob in self.container.list_blobs(name_starts_with="collections/")
            if blob.name.endswith(f"/{collection_id}/metadata.json")
        ]
        if len(matches) != 1:
            raise FileNotFoundError(f"Collection {collection_id!r} was not found uniquely")
        metadata = CollectionMetadata.from_bytes(self.container.download_blob(matches[0]).readall())
        if not metadata.eligible_for_processing or not metadata.data_location:
            raise ValueError(f"Collection {collection_id!r} is not eligible for processing")
        return metadata, self.container.download_blob(metadata.data_location).readall()

    def list_metadata(self) -> list[CollectionMetadata]:
        items = []
        for blob in self.container.list_blobs(name_starts_with="collections/"):
            if blob.name.endswith("/metadata.json"):
                value = self.container.download_blob(blob.name).readall()
                items.append(CollectionMetadata.from_bytes(value))
        return sorted(items, key=lambda item: item.request_started_at)

    @contextmanager
    def collection_lock(self) -> Iterator[None]:
        from azure.core.exceptions import HttpResponseError, ResourceExistsError
        from azure.storage.blob import BlobLeaseClient

        lock_blob = self.container.get_blob_client("locks/collector.lock")
        try:
            lock_blob.upload_blob(b"lock", overwrite=False)
        except ResourceExistsError:
            pass
        lease = BlobLeaseClient(lock_blob)
        try:
            lease.acquire(lease_duration=-1)
        except HttpResponseError as exc:
            if getattr(exc, "error_code", None) != "LeaseAlreadyPresent":
                raise
            raise CollectionAlreadyRunning(
                "Another Azure collection owns the blob lease"
            ) from exc
        try:
            yield
        finally:
            lease.release()


def metadata_as_json(metadata: CollectionMetadata) -> str:
    return json.dumps(metadata.__dict__, ensure_ascii=False, sort_keys=True)

