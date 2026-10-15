"""Private Cloud Storage for documents and passports (cloud mode).

The bucket is expected to use uniform bucket-level access with public access prevention;
objects are never made public and no signed URLs are issued. The API streams bytes after
its own permission check.
"""

from typing import Any

from reclaim_api.providers.base import CapabilityStatus, ProviderUnavailable


class GCSObjectStore:
    def __init__(self, *, project: str | None, bucket: str | None, client: Any = None) -> None:
        self._project, self._bucket_name = project, bucket
        self._client = client
        self._last_ok: bool | None = None
        self._last_error: str | None = None

    def _bucket(self) -> Any:
        if not (self._project and self._bucket_name):
            raise ProviderUnavailable("object storage unavailable: missing GCP_PROJECT or GCS_BUCKET")
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client(project=self._project)
        return self._client.bucket(self._bucket_name)

    def _call(self, fn):
        from google.api_core import exceptions as gexc
        from google.auth.exceptions import GoogleAuthError

        try:
            result = fn()
        except (gexc.GoogleAPIError, GoogleAuthError) as err:
            self._last_ok, self._last_error = False, type(err).__name__
            raise ProviderUnavailable(f"object storage unavailable ({type(err).__name__})") from None
        self._last_ok = True
        return result

    def put(self, *, key: str, data: bytes, content_type: str) -> str:
        if key.startswith("/") or ".." in key.split("/"):
            raise ValueError("invalid object key")
        blob = self._bucket().blob(key)
        self._call(lambda: blob.upload_from_string(data, content_type=content_type, if_generation_match=0))  # never overwrite
        return f"gs://{self._bucket_name}/{key}"

    def get(self, *, key: str) -> bytes:
        blob = self._bucket().blob(key)
        return self._call(blob.download_as_bytes)

    def status(self) -> CapabilityStatus:
        if not (self._project and self._bucket_name):
            return CapabilityStatus("object_storage", "unavailable", "cloud-storage", "missing config: GCP_PROJECT, GCS_BUCKET")
        if self._last_ok:
            return CapabilityStatus("object_storage", "live", "cloud-storage", "last operation succeeded")
        if self._last_ok is False:
            return CapabilityStatus("object_storage", "unavailable", "cloud-storage", f"last operation failed ({self._last_error})")
        return CapabilityStatus("object_storage", "unavailable", "cloud-storage", "configured; not yet used, so unverified")
