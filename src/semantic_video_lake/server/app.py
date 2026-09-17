"""Authenticated transport only: models and decoder never run in the API process."""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import anyio
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..types import LakeError
from .jobs import Jobs


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    token: str
    max_upload_bytes: int = 2_147_483_648
    max_queued_jobs: int = 1000
    max_media_bytes: int = 21_474_836_480
    upload_timeout_seconds: float = 300

    def __post_init__(self):
        if len(self.token) < 32:
            raise ValueError("API token must contain at least 32 characters.")
        if min(self.max_upload_bytes, self.max_queued_jobs, self.max_media_bytes) <= 0:
            raise ValueError("Resource limits must be positive.")


def validate_time(value):
    if value is not None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("missing timezone")
        except (ValueError, AttributeError) as exc:
            raise ValueError("Supply an ISO 8601 timestamp with a timezone.") from exc
    return value


class Filters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    camera_id: str | None = Field(default=None, max_length=128)
    recorded_after: str | None = None
    recorded_before: str | None = None
    _timestamps = field_validator("recorded_after", "recorded_before")(validate_time)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4096)
    limit: int = Field(default=10, ge=1, le=100, strict=True)
    filters: Filters = Field(default_factory=Filters)

    @field_validator("query")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Query must not be blank.")
        return value


class ErrorBody(BaseModel):
    code: str
    message: str


class MatchBody(BaseModel):
    asset_id: str
    timestamp_ms: int
    start_ms: int
    end_ms: int
    score: float
    source_hash: str
    model_fingerprint: str
    camera_id: str | None


class JobResult(BaseModel):
    asset_id: str | None = None
    matches: list[MatchBody] | None = None


class JobBody(BaseModel):
    id: str
    kind: Literal["ingest", "search", "remove", "purge"]
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress: int
    result: JobResult | None = None
    error: ErrorBody | None = None
    created_at: float
    updated_at: float


def opaque_id(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise HTTPException(404, "Unknown identifier.")
    return value


def create_app(settings: Settings) -> FastAPI:
    root = settings.data_dir.resolve()
    media = root / "media"
    media.mkdir(parents=True, exist_ok=True, mode=0o700)
    jobs = Jobs(root)
    security = HTTPBearer(auto_error=False)

    def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode(), settings.token.encode()
        ):
            raise HTTPException(
                401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"}
            )

    app = FastAPI(
        title="Semantic Video Lake",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.jobs = jobs
    upload_slots = asyncio.Semaphore(4)
    quota_lock = asyncio.Lock()
    reserved = 0

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if request.url.path != "/healthz":
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(LakeError)
    async def lake_error(request: Request, exc: LakeError):
        status = {"not_found": 404, "queue_full": 429}.get(exc.code, 400)
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=status)

    @app.get("/healthz", operation_id="health")
    def health():
        return {"status": "ok"}

    @app.get("/openapi.json", dependencies=[Depends(authenticate)], include_in_schema=False)
    def schema():
        return app.openapi()

    @app.post(
        "/v1/videos",
        status_code=202,
        response_model=JobBody,
        dependencies=[Depends(authenticate)],
        operation_id="uploadVideo",
    )
    async def upload(
        request: Request,
        filename: str = Query(max_length=255),
        camera_id: str | None = Query(default=None, max_length=128),
        recorded_at: str | None = None,
    ):
        nonlocal reserved
        if filename != Path(filename).name or "\\" in filename or "\x00" in filename:
            raise HTTPException(422, "Use a filename without directories.")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".mp4", ".mov", ".webm"}:
            raise HTTPException(415, "Supported uploads: MP4, MOV, WebM.")
        try:
            validate_time(recorded_at)
            declared = int(request.headers.get("content-length", "0"))
            if declared < 0:
                raise ValueError("invalid length")
        except ValueError as exc:
            raise HTTPException(422, "Invalid timestamp or Content-Length.") from exc
        if declared > settings.max_upload_bytes:
            raise HTTPException(413, "Upload exceeds configured limit.")
        identifier = uuid.uuid4().hex
        destination = media / (identifier + suffix)
        temporary = media / (identifier + ".part")
        size = 0
        async with upload_slots:
            async with quota_lock:
                used = sum(
                    p.stat().st_size for p in media.iterdir() if p.is_file() and p.suffix != ".part"
                )
                if used + reserved + settings.max_upload_bytes > settings.max_media_bytes:
                    raise HTTPException(507, "Media storage quota exhausted.")
                reserved += settings.max_upload_bytes
            try:
                async with asyncio.timeout(settings.upload_timeout_seconds):
                    with temporary.open("xb") as output:
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > settings.max_upload_bytes:
                                raise HTTPException(413, "Upload exceeds configured limit.")
                            await anyio.to_thread.run_sync(output.write, chunk)
                        if size == 0 or (declared and declared != size):
                            raise HTTPException(400, "Empty or incomplete upload.")
                        output.flush()
                        os.fsync(output.fileno())
                    temporary.replace(destination)
                    return jobs.submit(
                        "ingest",
                        {
                            "asset_id": identifier,
                            "source": str(destination),
                            "camera_id": camera_id,
                            "recorded_at": recorded_at,
                        },
                        capacity=settings.max_queued_jobs,
                        upload=(identifier, suffix, size),
                    )
            except BaseException:
                temporary.unlink(missing_ok=True)
                destination.unlink(missing_ok=True)
                raise
            finally:
                async with quota_lock:
                    reserved -= settings.max_upload_bytes

    @app.post(
        "/v1/search",
        status_code=202,
        response_model=JobBody,
        dependencies=[Depends(authenticate)],
        operation_id="search",
    )
    def search(body: SearchRequest):
        jobs.expire()
        return jobs.submit("search", body.model_dump(), capacity=settings.max_queued_jobs)

    @app.get(
        "/v1/jobs/{job_id}",
        response_model=JobBody,
        dependencies=[Depends(authenticate)],
        operation_id="getJob",
    )
    def get_job(job_id: str):
        return jobs.get(opaque_id(job_id))

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        response_model=JobBody,
        dependencies=[Depends(authenticate)],
        operation_id="cancelJob",
    )
    def cancel_job(job_id: str):
        return jobs.cancel(opaque_id(job_id))

    @app.delete(
        "/v1/videos/{asset_id}",
        status_code=202,
        response_model=JobBody,
        dependencies=[Depends(authenticate)],
        operation_id="removeVideo",
    )
    def remove(asset_id: str):
        jobs.upload(opaque_id(asset_id))
        return jobs.submit("remove", {"asset_id": asset_id}, capacity=settings.max_queued_jobs)

    @app.delete(
        "/v1/videos/{asset_id}/purge",
        status_code=202,
        response_model=JobBody,
        dependencies=[Depends(authenticate)],
        operation_id="purgeVideo",
    )
    def purge(asset_id: str, delete_media: bool = False):
        jobs.upload(opaque_id(asset_id))
        return jobs.submit(
            "purge",
            {"asset_id": asset_id, "delete_media": delete_media},
            capacity=settings.max_queued_jobs,
        )

    @app.get(
        "/v1/videos/{asset_id}/content",
        dependencies=[Depends(authenticate)],
        operation_id="getVideoContent",
    )
    def content(asset_id: str):
        info = jobs.upload(opaque_id(asset_id))
        if info["state"] != "ready":
            raise HTTPException(404, "Media is not available.")
        path = media / (asset_id + info["suffix"])
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, "Media is not available.")
        return FileResponse(
            path,
            media_type="video/webm" if info["suffix"] == ".webm" else "video/mp4",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    return app
