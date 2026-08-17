import asyncio
import json
import shutil
from collections.abc import AsyncIterator, Generator
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload
from starlette.exceptions import HTTPException as StarletteHTTPException

from .anilist import AniListError, search_manga
from .config import RuntimeSettings
from .crypto import SecretStore
from .database import Database
from .domain import ConversionPreset
from .google import GoogleDriveGateway, GoogleGmailGateway, GoogleServiceFactory
from .metadata import ArchiveMetadataError, format_kindle_title
from .models import (
    AppSettings,
    Artifact,
    Batch,
    Candidate,
    Delivery,
    DeliveryAttempt,
    Event,
    Job,
    Scan,
)
from .oauth import authorization_url, exchange_code, validate_client_config
from .preview import extract_preview
from .schemas import (
    CandidateRead,
    CandidateUpdate,
    FolderPageRead,
    GoogleClientPayload,
    JobRead,
    MangaMatchRead,
    OAuthStart,
    ScanRead,
    SettingsRead,
    SettingsUpdate,
    SetupStatus,
)

KINDLE_PROFILES = [
    {"id": "K11", "name": "Kindle 11"},
    {"id": "KPW34", "name": "Kindle Paperwhite 3 / 4"},
    {"id": "KPW5", "name": "Kindle Paperwhite 5 / Signature Edition"},
    {"id": "KPW6", "name": "Kindle Paperwhite 6"},
    {"id": "KO", "name": "Kindle Oasis 2 / 3"},
    {"id": "KCS", "name": "Kindle Colorsoft"},
    {"id": "KS", "name": "Kindle Scribe 1 / 2"},
    {"id": "KS3", "name": "Kindle Scribe 3"},
]


class BatchCreate(BaseModel):
    candidate_ids: list[str] = Field(min_length=1)
    preset: ConversionPreset


class BatchResponse(BaseModel):
    id: str
    status: str
    job_count: int
    preset: ConversionPreset


def _settings(session: Session) -> AppSettings:
    settings = session.get(AppSettings, 1)
    if not settings:
        settings = AppSettings(id=1)
        session.add(settings)
        session.flush()
    return settings


def _candidate_read(candidate: Candidate) -> CandidateRead:
    revision = candidate.revision
    return CandidateRead(
        id=candidate.id,
        status=candidate.status,
        resolved_title=candidate.resolved_title,
        title_override=candidate.title_override,
        metadata=candidate.comic_metadata,
        cache_expires_at=candidate.cache_expires_at,
        error=candidate.error,
        drive_file_id=revision.drive_file_id,
        name=revision.name,
        path=revision.path,
        size=revision.size,
        fingerprint=revision.fingerprint,
    )


def _job_read(job: Job) -> JobRead:
    deliveries = []
    for artifact in job.artifacts:
        if artifact.delivery:
            deliveries.append(
                {
                    "id": artifact.delivery.id,
                    "status": artifact.delivery.status,
                    "filename": artifact.filename,
                    "part_number": artifact.part_number,
                    "total_parts": artifact.total_parts,
                    "gmail_message_id": artifact.delivery.gmail_message_id,
                    "error_code": artifact.delivery.error_code,
                    "error_detail": artifact.delivery.error_detail,
                    "verification_url": artifact.delivery.verification_url,
                    "sent_at": artifact.delivery.sent_at,
                }
            )
    return JobRead(
        id=job.id,
        batch_id=job.batch_id,
        status=job.status,
        title=job.title,
        progress=job.progress,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
        deliveries=deliveries,
    )


def create_app(
    database: Database,
    runtime: RuntimeSettings | None = None,
    service_factory: GoogleServiceFactory | None = None,
) -> FastAPI:
    runtime = runtime or RuntimeSettings()
    app = FastAPI(title="Kindrop", version="0.1.0")
    app.state.database = database
    app.state.runtime = runtime

    def session_dependency() -> Generator[Session, None, None]:
        with database.session() as session:
            yield session

    def google_services() -> GoogleServiceFactory:
        nonlocal service_factory
        if service_factory is None:
            service_factory = GoogleServiceFactory(database, SecretStore(runtime.secret_key_file))
        return service_factory

    @app.middleware("http")
    async def localhost_only(request: Request, call_next):
        hostname = request.url.hostname
        if hostname not in {"127.0.0.1", "localhost", "testserver", None}:
            return JSONResponse(
                status_code=400, content={"detail": "Kindrop only accepts localhost requests"}
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).hostname not in {"127.0.0.1", "localhost", "testserver"}:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "The request origin is not allowed"},
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        database.ping()
        return {"status": "ok", "database": "ok"}

    @app.get("/api/setup/status", response_model=SetupStatus)
    def setup_status(session: Session = Depends(session_dependency)) -> SetupStatus:
        settings = _settings(session)
        connected = bool(settings.encrypted_google_token)
        source = bool(settings.source_folder_id)
        destination = bool(settings.kindle_email)
        client = bool(settings.encrypted_google_client)
        return SetupStatus(
            client_configured=client,
            google_connected=connected,
            google_email=settings.google_email,
            source_folder_configured=source,
            kindle_destination_configured=destination,
            ready=client and connected and source and destination,
        )

    @app.post("/api/oauth/client", status_code=status.HTTP_204_NO_CONTENT)
    def save_google_client(
        payload: GoogleClientPayload, session: Session = Depends(session_dependency)
    ) -> Response:
        try:
            validate_client_config(payload.credentials)
            encrypted = SecretStore(runtime.secret_key_file).encrypt_json(payload.credentials)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        settings = _settings(session)
        settings.encrypted_google_client = encrypted
        session.commit()
        return Response(status_code=204)

    @app.get("/api/oauth/start", response_model=OAuthStart)
    def start_google_oauth(session: Session = Depends(session_dependency)) -> OAuthStart:
        settings = _settings(session)
        if not settings.encrypted_google_client:
            raise HTTPException(status_code=409, detail="Upload a Google OAuth client first")
        client = SecretStore(runtime.secret_key_file).decrypt_json(settings.encrypted_google_client)
        redirect_uri = f"{runtime.app_base_url}/api/oauth/callback"
        url, state_value, code_verifier = authorization_url(client, redirect_uri)
        settings.oauth_state = state_value
        settings.oauth_code_verifier = code_verifier
        session.commit()
        return OAuthStart(authorization_url=url)

    @app.get("/api/oauth/callback")
    def google_oauth_callback(
        code: str,
        state: str,
        session: Session = Depends(session_dependency),
    ) -> RedirectResponse:
        settings = _settings(session)
        if not settings.oauth_state or state != settings.oauth_state:
            raise HTTPException(status_code=400, detail="The Google OAuth state did not match")
        if not settings.oauth_code_verifier:
            raise HTTPException(
                status_code=400,
                detail="The Google OAuth session expired, restart the connection",
            )
        if not settings.encrypted_google_client:
            raise HTTPException(status_code=409, detail="Upload a Google OAuth client first")
        store = SecretStore(runtime.secret_key_file)
        client = store.decrypt_json(settings.encrypted_google_client)
        token = exchange_code(
            client,
            f"{runtime.app_base_url}/api/oauth/callback",
            state,
            code,
            settings.oauth_code_verifier,
        )
        settings.encrypted_google_token = store.encrypt_json(token)
        settings.oauth_state = None
        settings.oauth_code_verifier = None
        session.commit()
        try:
            settings.google_email = GoogleGmailGateway(google_services()).profile_email()
            session.commit()
        except Exception:
            pass
        return RedirectResponse(url="/settings?connected=true", status_code=303)

    @app.delete("/api/oauth", status_code=status.HTTP_204_NO_CONTENT)
    def disconnect_google(session: Session = Depends(session_dependency)) -> Response:
        settings = _settings(session)
        settings.encrypted_google_token = None
        settings.google_email = None
        settings.source_folder_id = None
        settings.source_folder_name = None
        session.commit()
        return Response(status_code=204)

    @app.get("/api/settings", response_model=SettingsRead)
    def read_settings(session: Session = Depends(session_dependency)) -> SettingsRead:
        settings = _settings(session)
        return SettingsRead(
            google_email=settings.google_email,
            source_folder_id=settings.source_folder_id,
            source_folder_name=settings.source_folder_name,
            kindle_email=settings.kindle_email,
            preset=ConversionPreset.model_validate(settings.preset),
        )

    @app.put("/api/settings", response_model=SettingsRead)
    def update_settings(
        payload: SettingsUpdate, session: Session = Depends(session_dependency)
    ) -> SettingsRead:
        settings = _settings(session)
        settings.source_folder_id = payload.source_folder_id
        settings.source_folder_name = payload.source_folder_name
        settings.kindle_email = str(payload.kindle_email) if payload.kindle_email else None
        settings.preset = payload.preset.model_dump(mode="json")
        session.commit()
        return read_settings(session)

    @app.get("/api/kindle-profiles")
    def kindle_profiles() -> list[dict[str, str]]:
        return KINDLE_PROFILES

    @app.get("/api/drive/folders", response_model=FolderPageRead)
    def list_drive_folders(
        parent_id: str = "root",
        page_token: str | None = None,
    ) -> FolderPageRead:
        try:
            page = GoogleDriveGateway(google_services()).list_folders(parent_id, page_token)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return FolderPageRead(
            folders=[{"id": item.id, "name": item.name} for item in page.folders],
            next_page_token=page.next_page_token,
        )

    @app.post("/api/scans", response_model=ScanRead, status_code=status.HTTP_202_ACCEPTED)
    def create_scan(session: Session = Depends(session_dependency)) -> Scan:
        settings = _settings(session)
        if not settings.source_folder_id:
            raise HTTPException(status_code=409, detail="Choose a Source Folder before scanning")
        active = session.scalar(select(Scan.id).where(Scan.status.in_(["queued", "scanning"])))
        if active:
            raise HTTPException(status_code=409, detail="A scan is already in progress")
        scan = Scan()
        session.add(scan)
        session.commit()
        return scan

    @app.get("/api/scans", response_model=list[ScanRead])
    def list_scans(session: Session = Depends(session_dependency)) -> list[Scan]:
        return list(session.scalars(select(Scan).order_by(desc(Scan.created_at)).limit(30)).all())

    @app.get("/api/scans/{scan_id}", response_model=ScanRead)
    def read_scan(scan_id: str, session: Session = Depends(session_dependency)) -> Scan:
        scan = session.get(Scan, scan_id)
        if not scan:
            raise HTTPException(status_code=404, detail="Scan not found")
        return scan

    @app.post("/api/scans/{scan_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    def cancel_scan(scan_id: str, session: Session = Depends(session_dependency)) -> dict[str, str]:
        scan = session.get(Scan, scan_id)
        if not scan:
            raise HTTPException(status_code=404, detail="Scan not found")
        if scan.status not in {"queued", "scanning"}:
            raise HTTPException(status_code=409, detail="Only an active scan can be cancelled")
        scan.cancel_requested = True
        session.commit()
        return {"status": "cancelling"}

    @app.get("/api/candidates", response_model=list[CandidateRead])
    def list_candidates(
        scan_id: str | None = None,
        session: Session = Depends(session_dependency),
    ) -> list[CandidateRead]:
        query = (
            select(Candidate)
            .options(selectinload(Candidate.revision))
            .order_by(desc(Candidate.created_at))
        )
        if scan_id:
            query = query.where(Candidate.scan_id == scan_id)
        return [_candidate_read(item) for item in session.scalars(query).all()]

    @app.patch("/api/candidates/{candidate_id}", response_model=CandidateRead)
    def update_candidate(
        candidate_id: str,
        payload: CandidateUpdate,
        session: Session = Depends(session_dependency),
    ) -> CandidateRead:
        candidate = session.scalar(
            select(Candidate)
            .options(selectinload(Candidate.revision))
            .where(Candidate.id == candidate_id)
        )
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
        if candidate.status not in {"ready", "ignored"}:
            raise HTTPException(status_code=409, detail="This Candidate can no longer be edited")
        provided = payload.model_fields_set
        if "title_override" in provided:
            candidate.title_override = (payload.title_override or "").strip() or None
        metadata_fields = {"series", "number", "author", "cover_url"} & provided
        if metadata_fields:
            if payload.cover_url and not payload.cover_url.startswith("https://"):
                raise HTTPException(status_code=422, detail="The cover URL must use https")
            updated = dict(candidate.comic_metadata or {})
            for field in metadata_fields:
                value = (getattr(payload, field) or "").strip()
                updated[field] = value or None
            candidate.comic_metadata = updated
            if {"series", "number"} & metadata_fields:
                candidate.resolved_title = format_kindle_title(
                    updated.get("series"),
                    updated.get("number"),
                    updated.get("title") or candidate.resolved_title,
                )
        if payload.status and payload.status != candidate.status:
            candidate.status = payload.status
            candidate.revision.status = "ignored" if payload.status == "ignored" else "candidate"
            if payload.status == "ignored" and candidate.cache_path:
                Path(candidate.cache_path).unlink(missing_ok=True)
                candidate.cache_path = None
                candidate.cache_expires_at = None
        session.commit()
        return _candidate_read(candidate)

    @app.get("/api/candidates/{candidate_id}/preview")
    def candidate_preview(
        candidate_id: str, session: Session = Depends(session_dependency)
    ) -> FileResponse:
        candidate = session.get(Candidate, candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail="Candidate not found")
        preview_path = runtime.cache_root / "previews" / f"{candidate_id}.jpg"
        if not preview_path.is_file():
            if not candidate.cache_path or not Path(candidate.cache_path).is_file():
                raise HTTPException(
                    status_code=404, detail="The cached archive for this Candidate has expired"
                )
            try:
                extract_preview(Path(candidate.cache_path), preview_path)
            except ArchiveMetadataError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        return FileResponse(preview_path, media_type="image/jpeg")

    @app.get("/api/metadata/search", response_model=list[MangaMatchRead])
    def metadata_search(query: str = Query(min_length=1, max_length=200)) -> list[MangaMatchRead]:
        try:
            matches = search_manga(query)
        except AniListError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return [MangaMatchRead(**match.__dict__) for match in matches]

    @app.post("/api/batches", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
    def create_batch(payload: BatchCreate, session: Session = Depends(session_dependency)):
        candidates = session.scalars(
            select(Candidate).where(Candidate.id.in_(payload.candidate_ids))
        ).all()
        if len(candidates) != len(set(payload.candidate_ids)) or any(
            candidate.status != "ready" for candidate in candidates
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Every selected candidate must be ready",
            )
        settings = _settings(session)
        if not settings.kindle_email:
            raise HTTPException(status_code=409, detail="Configure a Kindle Destination first")

        preset = payload.preset.model_dump(mode="json")
        batch = Batch(preset=preset)
        session.add(batch)
        session.flush()
        for candidate in candidates:
            session.add(
                Job(
                    batch_id=batch.id,
                    candidate_id=candidate.id,
                    preset=preset,
                    title=candidate.title_override or candidate.resolved_title,
                )
            )
            candidate.status = "queued"
        session.commit()
        return BatchResponse(
            id=batch.id,
            status=batch.status,
            job_count=len(candidates),
            preset=payload.preset,
        )

    @app.get("/api/jobs", response_model=list[JobRead])
    def list_jobs(session: Session = Depends(session_dependency)) -> list[JobRead]:
        jobs = session.scalars(
            select(Job)
            .options(
                selectinload(Job.artifacts).selectinload(Artifact.delivery),
            )
            .order_by(desc(Job.created_at))
            .limit(100)
        ).all()
        return [_job_read(job) for job in jobs]

    def clone_job(source_job: Job, session: Session) -> Job:
        batch = Batch(preset=source_job.preset)
        session.add(batch)
        session.flush()
        source_job.candidate.status = "queued"
        replacement = Job(
            batch_id=batch.id,
            candidate_id=source_job.candidate_id,
            preset=source_job.preset,
            title=source_job.title,
        )
        session.add(replacement)
        session.commit()
        return replacement

    @app.post("/api/jobs/{job_id}/retry", status_code=status.HTTP_201_CREATED)
    def retry_job(job_id: str, session: Session = Depends(session_dependency)) -> dict[str, str]:
        job = session.scalar(
            select(Job).options(selectinload(Job.candidate)).where(Job.id == job_id)
        )
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in {"failed", "cancelled"}:
            raise HTTPException(
                status_code=409, detail="Only failed or cancelled jobs can be retried"
            )
        replacement = clone_job(job, session)
        return {"id": replacement.id, "status": replacement.status}

    @app.post("/api/deliveries/{delivery_id}/resend", status_code=status.HTTP_201_CREATED)
    def resend_delivery(
        delivery_id: str, session: Session = Depends(session_dependency)
    ) -> dict[str, str]:
        delivery = session.scalar(
            select(Delivery)
            .options(
                selectinload(Delivery.artifact)
                .selectinload(Artifact.job)
                .selectinload(Job.candidate)
            )
            .where(Delivery.id == delivery_id)
        )
        if not delivery:
            raise HTTPException(status_code=404, detail="Delivery not found")
        replacement = clone_job(delivery.artifact.job, session)
        return {"id": replacement.id, "status": replacement.status}

    @app.delete("/api/cache", status_code=status.HTTP_204_NO_CONTENT)
    def purge_cache(session: Session = Depends(session_dependency)) -> Response:
        for child in runtime.cache_root.iterdir() if runtime.cache_root.exists() else []:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        for candidate in session.scalars(
            select(Candidate).where(Candidate.cache_path.is_not(None))
        ):
            candidate.cache_path = None
            candidate.cache_expires_at = None
        session.commit()
        return Response(status_code=204)

    @app.delete("/api/history", status_code=status.HTTP_204_NO_CONTENT)
    def clear_history(session: Session = Depends(session_dependency)) -> Response:
        active_scan = session.scalar(
            select(Scan.id).where(Scan.status.in_(["queued", "scanning"]))
        )
        active_job = session.scalar(
            select(Job.id).where(Job.status.not_in(["sent", "failed", "cancelled"]))
        )
        if active_scan or active_job:
            raise HTTPException(
                status_code=409,
                detail="History can only be cleared while no scan or conversion is running",
            )
        for attempt in session.scalars(select(DeliveryAttempt)):
            session.delete(attempt)
        for delivery in session.scalars(select(Delivery)):
            session.delete(delivery)
        for artifact in session.scalars(select(Artifact)):
            Path(artifact.path).unlink(missing_ok=True)
            session.delete(artifact)
        jobs = session.scalars(
            select(Job).options(selectinload(Job.candidate).selectinload(Candidate.revision))
        ).all()
        for job in jobs:
            candidate = job.candidate
            session.delete(job)
            if candidate.status == "sent":
                session.delete(candidate)
            else:
                candidate.status = "ready"
                candidate.error = None
                candidate.revision.status = "candidate"
        for batch in session.scalars(select(Batch)):
            session.delete(batch)
        for candidate in session.scalars(select(Candidate).where(Candidate.scan_id.is_not(None))):
            candidate.scan_id = None
        for scan in session.scalars(select(Scan)):
            session.delete(scan)
        session.commit()
        return Response(status_code=204)

    @app.get("/api/events")
    async def events(last_event_id: int = Query(default=0, ge=0)) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            cursor = last_event_id
            while True:
                with database.session() as session:
                    items = session.scalars(
                        select(Event).where(Event.id > cursor).order_by(Event.id).limit(100)
                    ).all()
                    for item in items:
                        cursor = item.id
                        payload = {
                            "id": item.id,
                            "topic": item.topic,
                            "entity_id": item.entity_id,
                            "kind": item.kind,
                            "payload": item.payload,
                            "created_at": item.created_at.isoformat(),
                        }
                        yield f"id: {item.id}\nevent: {item.kind}\ndata: {json.dumps(payload)}\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if runtime.frontend_dist.is_dir():

        class SpaStaticFiles(StaticFiles):
            async def get_response(self, path: str, scope):  # type: ignore[override]
                try:
                    response = await super().get_response(path, scope)
                except StarletteHTTPException as error:
                    if error.status_code != 404 or path.startswith("api"):
                        raise
                    return await super().get_response("index.html", scope)
                if response.status_code == 404 and not path.startswith("api"):
                    return await super().get_response("index.html", scope)
                return response

        app.mount("/", SpaStaticFiles(directory=runtime.frontend_dist, html=True), name="frontend")
    else:

        @app.get("/")
        def root() -> dict[str, str]:
            return {"name": "Kindrop API", "docs": "/docs"}

    return app
