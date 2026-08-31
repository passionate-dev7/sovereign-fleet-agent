"""Artifact writer: the "object that exists after the tab closes."

Every project writes exactly one durable artifact per successful run. Split
into LocalBackend (temp dir, offline tests) and GcsBackend (real bucket).
"""

from __future__ import annotations

import abc
import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Union

BytesOrStr = Union[bytes, str]


class ArtifactBackend(abc.ABC):
    """Pluggable artifact store. write() must be safe to call once per run."""

    @abc.abstractmethod
    def write(self, path: str, content: BytesOrStr,
               content_type: Optional[str] = None) -> str:
        """Write content at logical `path` (e.g. "incidents/<ts>/status.md").

        Returns the URI of the written artifact (local file:// path or
        gs:// URI).
        """

    @abc.abstractmethod
    def read(self, path: str) -> bytes:
        """Read back content previously written at `path`."""

    @abc.abstractmethod
    def exists(self, path: str) -> bool:
        """Whether something has been written at `path`."""

    @abc.abstractmethod
    def list_prefix(self, prefix: str) -> list[str]:
        """List logical paths under `prefix`. Used to assert artifact count."""


class LocalBackend(ArtifactBackend):
    """Writes under a local directory (defaults to a fresh temp dir).

    Used by offline tests and local dev runs of the Cloud Run job.
    """

    def __init__(self, root_dir: Optional[str] = None) -> None:
        root = Path(root_dir) if root_dir else Path(tempfile.mkdtemp(
            prefix="agentspine-artifacts-"))
        root.mkdir(parents=True, exist_ok=True)
        # Resolve now (not lazily in _full_path) so self._root and every
        # path built from it agree on symlinks -- e.g. macOS /tmp is a
        # symlink to /private/tmp, and an unresolved root here makes
        # list_prefix's relative_to() raise even though the writes
        # succeeded.
        self._root = root.resolve()

    @property
    def root_dir(self) -> str:
        return str(self._root)

    def _full_path(self, path: str) -> Path:
        full = (self._root / path).resolve()
        if self._root.resolve() not in full.parents and full != self._root.resolve():
            raise ValueError(f"path escapes artifact root: {path}")
        return full

    def write(self, path: str, content: BytesOrStr,
               content_type: Optional[str] = None) -> str:
        full = self._full_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
        with open(full, mode) as f:
            f.write(content)
        return f"file://{full}"

    def read(self, path: str) -> bytes:
        full = self._full_path(path)
        with open(full, "rb") as f:
            return f.read()

    def exists(self, path: str) -> bool:
        return self._full_path(path).exists()

    def list_prefix(self, prefix: str) -> list[str]:
        base = self._full_path(prefix) if prefix else self._root
        if not base.exists():
            return []
        if base.is_file():
            return [str(base.relative_to(self._root))]
        results = []
        for p in base.rglob("*"):
            if p.is_file():
                results.append(str(p.relative_to(self._root)))
        return sorted(results)


class GcsBackend(ArtifactBackend):
    """Writes to a real GCS bucket. Import is lazy: offline tests never
    need google-cloud-storage to be configured with credentials."""

    def __init__(self, bucket_name: str, client: Any = None,
                 prefix: str = "") -> None:
        if client is None:
            from google.cloud import storage  # local import, optional dep

            client = storage.Client()
        self._client = client
        self._bucket = client.bucket(bucket_name)
        self._bucket_name = bucket_name
        self._prefix = prefix.strip("/")

    def _blob_name(self, path: str) -> str:
        path = path.lstrip("/")
        return f"{self._prefix}/{path}" if self._prefix else path

    def write(self, path: str, content: BytesOrStr,
               content_type: Optional[str] = None) -> str:
        blob_name = self._blob_name(path)
        blob = self._bucket.blob(blob_name)
        if isinstance(content, str):
            blob.upload_from_string(content, content_type=content_type or "text/plain")
        else:
            blob.upload_from_string(content, content_type=content_type or "application/octet-stream")
        return f"gs://{self._bucket_name}/{blob_name}"

    def read(self, path: str) -> bytes:
        blob = self._bucket.blob(self._blob_name(path))
        return blob.download_as_bytes()

    def exists(self, path: str) -> bool:
        return self._bucket.blob(self._blob_name(path)).exists()

    def list_prefix(self, prefix: str) -> list[str]:
        full_prefix = self._blob_name(prefix)
        blobs = self._client.list_blobs(self._bucket_name, prefix=full_prefix)
        base = f"{self._prefix}/" if self._prefix else ""
        return sorted(b.name[len(base):] if base and b.name.startswith(base) else b.name
                      for b in blobs)
