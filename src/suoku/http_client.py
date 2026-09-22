"""Small synchronous HTTP client with streamed uploads and bounded job polling."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException
from pathlib import Path

MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# Keep this client dependency-light so the CLI can diagnose a missing server without
# importing FastAPI or any model stack.


class ClientError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even same-host redirects are rejected, so bearer credentials cannot move.
        return None


def validate_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.port == 0
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ClientError(
            "invalid_url",
            "Use an http(s) service URL without credentials, query parameters, or fragments.",
        ) from exc
    return value.rstrip("/")


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ClientError(
            "invalid_id", "Use the 32-character asset or job ID returned by the service."
        )
    return value


def validate_wait_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or not 0 < timeout <= 86400:
        raise ClientError(
            "invalid_timeout", "Set --wait-timeout between 0 and 86400 seconds (exclusive of zero)."
        )


class HTTPClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30):
        self.base_url = validate_url(base_url)
        if (
            not token
            or not token.isascii()
            or any(ord(char) < 33 or ord(char) > 126 for char in token)
        ):
            raise ClientError(
                "missing_token",
                "Set SUOKU_API_TOKEN or run suoku init and use the same config for serve and client.",
            )
        if not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ClientError(
                "invalid_timeout", "Set --timeout between 0 and 300 seconds (exclusive of zero)."
            )
        self._token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirect())

    def request(self, method: str, path: str, *, body=None, data=None, headers=None, timeout=None):
        if not path.startswith("/v1/") or path.startswith("//"):
            raise ClientError("invalid_path", "The requested service endpoint is invalid.")
        request_headers = {"Accept": "application/json", "Authorization": "Bearer " + self._token}
        request_headers.update(headers or {})
        if body is not None:
            data = json.dumps(body, allow_nan=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=request_headers, method=method
        )
        try:
            with self.opener.open(
                request, timeout=min(self.timeout, timeout or self.timeout)
            ) as response:
                content = response.read(MAX_RESPONSE_BYTES + 1)
            if len(content) > MAX_RESPONSE_BYTES:
                raise ClientError(
                    "response_too_large",
                    "Service response exceeded the client limit. Request a smaller result set.",
                )
            result = json.loads(content)
            if not isinstance(result, (dict, list)):
                raise ValueError
            return result
        except urllib.error.HTTPError as exc:
            messages = {
                401: "Authentication failed. Check SUOKU_API_TOKEN matches the running service.",
                403: "Access denied. Check the service token and endpoint permissions.",
                404: "Endpoint or asset not found. Check the service URL, asset ID, and installed server version.",
                413: "Upload exceeds the server limit. Use a smaller video or change the server limit.",
                415: "Unsupported media. Upload an MP4, MOV, or WebM video.",
                422: "Request validation failed. Check the recipe, timestamps, asset IDs, and command arguments.",
                429: "The service queue is full or rate limited. Retry after current jobs finish.",
                503: "The service is unavailable. Start the worker and check suoku status.",
                507: "The service has no remaining media storage. Free space or increase its storage quota.",
            }
            message = (
                "Service redirected the request. Use its final URL explicitly; credentials were not forwarded."
                if 300 <= exc.code < 400
                else messages.get(
                    exc.code, "Service request failed. Check suoku status and the service logs."
                )
            )
            exc.close()
            raise ClientError("http_" + str(exc.code), message) from None
        except (urllib.error.URLError, OSError, TimeoutError, HTTPException):
            raise ClientError(
                "connection_failed",
                "Could not reach the service within the request timeout. Check --url, start suoku serve, and retry.",
            ) from None
        except (ValueError, UnicodeError) as exc:
            if isinstance(exc, ClientError):
                raise
            raise ClientError(
                "invalid_response",
                "The service returned invalid JSON. Check the URL and server version.",
            ) from None

    def upload(self, path: Path, *, camera_id=None, recorded_at=None):
        query = {"filename": path.name}
        if camera_id is not None:
            query["camera_id"] = camera_id
        if recorded_at is not None:
            query["recorded_at"] = recorded_at
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not info.st_size:
                    raise ClientError(
                        "invalid_upload", "Choose a nonempty regular MP4, MOV, or WebM file."
                    )
                return self.request(
                    "POST",
                    "/v1/videos?" + urllib.parse.urlencode(query),
                    data=iter(lambda: stream.read(65536), b""),
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(info.st_size),
                    },
                )
        except OSError:
            raise ClientError(
                "upload_read_failed", "Cannot read the upload. Check the file path and permissions."
            ) from None

    def wait(self, job: dict, *, timeout: float = 300, progress=None) -> dict:
        validate_wait_timeout(timeout)
        deadline = time.monotonic() + timeout
        previous = None
        while True:
            if not isinstance(job, dict) or job.get("state") not in {
                "queued",
                "running",
                "succeeded",
                "failed",
                "cancelled",
            }:
                raise ClientError(
                    "invalid_job", "The service returned an invalid job. Check the server version."
                )
            job_id = identifier(job.get("id"))
            state = job["state"]
            if progress is not None and state != previous:
                progress(job_id, state)
                previous = state
            if state == "succeeded":
                if not isinstance(job.get("result"), dict):
                    raise ClientError(
                        "invalid_job", "The completed job has no result. Check the server version."
                    )
                return job["result"]
            if state in {"failed", "cancelled"}:
                advice = (
                    "Check suoku status, worker configuration, and local model availability before retrying."
                    if state == "failed"
                    else "Submit a new request to retry."
                )
                raise ClientError("job_" + state, f"Job {job_id} {state}. {advice}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ClientError(
                    "job_timeout",
                    f"Timed out waiting for job {job_id}; it continues on the server. Check suoku status and GET /v1/jobs/{job_id}.",
                )
            job = self.request("GET", "/v1/jobs/" + job_id, timeout=remaining)
            if isinstance(job, dict) and job.get("state") in {"queued", "running"}:
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
