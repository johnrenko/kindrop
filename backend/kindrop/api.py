import asyncio
import json
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator, Callable, Generator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
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
from .domain import COMIC_SUFFIXES, ConversionPreset
from .google import GoogleDriveGateway, GoogleGmailGateway, GoogleServiceFactory
from .kindle_ssh import (
    KindleCollisionError,
    KindleSshConfig,
    KindleSshError,
    KindleSshTransport,
    KindleStorageItemNotFoundError,
)
from .metadata import (
    ArchiveMetadataError,
    clean_title,
    format_kindle_title,
    inferred_series_name,
    volume_number,
)
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
    CandidateSeriesApply,
    CandidateSeriesMemberRead,
    CandidateSeriesRead,
    CandidateUpdate,
    FolderPageRead,
    GoogleClientPayload,
    JobRead,
    KindleStorageBulkDeleteRequest,
    KindleStorageDeleteRequest,
    KindleStorageListingRead,
    KindleStorageMoveRequest,
    KindleStorageMutationRead,
    KindleStorageRenameRequest,
    MangaMatchRead,
    OAuthStart,
    ScanRead,
    SettingsRead,
    SettingsUpdate,
    SetupStatus,
    SshHostKeyRead,
    SshStatus,
    SshTrustRequest,
)
from .services import LocalCandidateConflict, add_event, stage_local_candidate

KINDLE_PROFILES = [
    {"id": "KPW", "name": "Kindle Paperwhite 1 / 2"},
    {"id": "K11", "name": "Kindle 11"},
    {"id": "KPW34", "name": "Kindle Paperwhite 3 / 4"},
    {"id": "KPW5", "name": "Kindle Paperwhite 5 / Signature Edition"},
    {"id": "KPW6", "name": "Kindle Paperwhite 6"},
    {"id": "KO", "name": "Kindle Oasis 2 / 3"},
    {"id": "KCS", "name": "Kindle Colorsoft"},
    {"id": "KS", "name": "Kindle Scribe 1 / 2"},
    {"id": "KS3", "name": "Kindle Scribe 3"},
]


class CandidateOption(BaseModel):
    candidate_id: str
    optimize: bool | None = None


class BatchCreate(BaseModel):
    candidate_ids: list[str] = Field(default_factory=list)
    candidate_options: list[CandidateOption] = Field(default_factory=list)
    preset: ConversionPreset
    merge_by_volume: bool = False


class JobRetry(BaseModel):
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


def _series_key(series: str | None, path: str) -> tuple[str, str | None, str]:
    if series:
        normalized = "-".join(series.casefold().split())
        return f"series:{normalized}", series, "high"
    parent = PurePosixPath(path).parent
    if str(parent) not in {"", "."}:
        folder = clean_title(parent.name)
        normalized_path = "/".join(
            "-".join(part.casefold().split())
            for part in parent.parts
            if part not in {"", ".", "/"}
        )
        return f"folder:{normalized_path}", folder, "folder"
    return "generic:root", None, "needs_name"


def _candidate_volume_number(candidate: Candidate) -> int | None:
    metadata = dict(candidate.comic_metadata or {})
    number_text = str(metadata.get("number") or "").strip()
    return (
        int(number_text)
        if number_text.isdigit()
        else volume_number(candidate.revision.name)
    )


def _candidate_series_suggestions(
    session: Session,
    *,
    group_id: str | None = None,
) -> list[CandidateSeriesRead]:
    candidates = session.scalars(
        select(Candidate)
        .options(selectinload(Candidate.revision))
        .where(Candidate.status.not_in(["ignored", "invalid", "failed", "cancelled"]))
        .order_by(Candidate.created_at)
    ).all()
    groups: dict[str, dict] = {}
    for candidate in candidates:
        metadata = dict(candidate.comic_metadata or {})
        number = _candidate_volume_number(candidate)
        if number is None:
            continue
        inferred = metadata.get("series") or inferred_series_name(candidate.revision.name)
        key, suggested_series, confidence = _series_key(inferred, candidate.revision.path)
        group = groups.setdefault(
            key,
            {
                "suggested_series": suggested_series,
                "confidence": confidence,
                "known": [],
                "ready": [],
            },
        )
        group["known"].append((number, candidate))
        if candidate.status == "ready":
            group["ready"].append((number, candidate))

    suggestions: list[CandidateSeriesRead] = []
    for key, group in groups.items():
        if group_id is not None and key != group_id:
            continue
        ready = group["ready"]
        known = group["known"]
        if not ready or len(known) < 2:
            continue
        if all(
            (candidate.comic_metadata or {}).get("series")
            and str((candidate.comic_metadata or {}).get("number") or "").strip()
            for _, candidate in ready
        ):
            continue
        counts: dict[int, int] = {}
        for number, _candidate in known:
            counts[number] = counts.get(number, 0) + 1
        first = min(counts)
        last = max(counts)
        suggestions.append(
            CandidateSeriesRead(
                id=key,
                suggested_series=group["suggested_series"],
                confidence=group["confidence"],
                ready_count=len(ready),
                known_count=len(known),
                first_volume=first,
                last_volume=last,
                missing_volumes=[
                    number for number in range(first, last + 1) if number not in counts
                ],
                duplicate_volumes=[number for number, count in counts.items() if count > 1],
                members=[
                    CandidateSeriesMemberRead(
                        candidate_id=candidate.id,
                        name=candidate.revision.name,
                        number=number,
                    )
                    for number, candidate in sorted(
                        ready, key=lambda item: (item[0], item[1].revision.name)
                    )
                ],
            )
        )
    return sorted(suggestions, key=lambda item: (-item.ready_count, item.id))


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
        source_type=revision.source_type,
        name=revision.name,
        path=revision.path,
        size=revision.size,
        fingerprint=revision.fingerprint,
        optimize=_candidate_optimize(candidate),
    )


def _candidate_optimize(candidate: Candidate) -> bool:
    if candidate.optimize is not None:
        return candidate.optimize
    return not candidate.revision.name.lower().endswith(".epub")


def _job_read(job: Job) -> JobRead:
    deliveries = []
    for artifact in job.artifacts:
        if artifact.delivery:
            deliveries.append(
                {
                    "id": artifact.delivery.id,
                    "status": artifact.delivery.status,
                    "transport": artifact.delivery.transport,
                    "remote_path": artifact.delivery.remote_path,
                    "remote_sha256": artifact.delivery.remote_sha256,
                    "filename": artifact.filename,
                    "part_number": artifact.part_number,
                    "total_parts": artifact.total_parts,
                    "gmail_message_id": artifact.delivery.gmail_message_id,
                    "error_code": artifact.delivery.error_code,
                    "error_detail": artifact.delivery.error_detail,
                    "verification_url": artifact.delivery.verification_url,
                    "sent_at": artifact.delivery.sent_at,
                    "capacity_unknown": artifact.delivery.status
                    in {"pending", "waiting_for_kindle"},
                }
            )
    return JobRead(
        id=job.id,
        batch_id=job.batch_id,
        status=job.status,
        title=job.title,
        optimize=job.optimize,
        delivery_transport=job.delivery_transport,
        preset=ConversionPreset.model_validate(job.preset),
        merged_count=len(job.merged_candidate_ids) if job.merged_candidate_ids else None,
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
    ssh_transport_factory: Callable[[KindleSshConfig], KindleSshTransport] | None = None,
) -> FastAPI:
    runtime = runtime or RuntimeSettings()
    app = FastAPI(title="Kindrop", version="0.1.0")
    app.state.database = database
    app.state.runtime = runtime
    app.state.ssh_status = None

    if ssh_transport_factory is None:
        ssh_transport_factory = KindleSshTransport

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
        destination = bool(
            settings.ssh_host
            and settings.ssh_user
            and settings.ssh_key_path
            and settings.ssh_known_hosts_path
            and settings.ssh_destination
        )
        client = bool(settings.encrypted_google_client)
        return SetupStatus(
            client_configured=client,
            google_connected=connected,
            google_email=settings.google_email,
            source_folder_configured=source,
            kindle_destination_configured=destination,
            ssh_destination_configured=destination,
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
            ssh_host=settings.ssh_host,
            ssh_port=settings.ssh_port,
            ssh_user=settings.ssh_user,
            ssh_key_path=settings.ssh_key_path,
            ssh_known_hosts_path=settings.ssh_known_hosts_path,
            ssh_destination=settings.ssh_destination,
            kindle_reserve_mib=settings.kindle_reserve_mib,
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
        settings.ssh_host = payload.ssh_host
        settings.ssh_port = payload.ssh_port
        settings.ssh_user = payload.ssh_user
        settings.ssh_key_path = payload.ssh_key_path
        settings.ssh_known_hosts_path = payload.ssh_known_hosts_path
        settings.ssh_destination = payload.ssh_destination.rstrip("/")
        settings.kindle_reserve_mib = payload.kindle_reserve_mib
        settings.preset = payload.preset.model_dump(mode="json")
        app.state.ssh_status = None
        session.commit()
        return read_settings(session)

    def ssh_status_payload(settings: AppSettings) -> SshStatus:
        cached = app.state.ssh_status
        if cached is not None:
            return cached
        configured = bool(
            settings.ssh_host
            and settings.ssh_user
            and settings.ssh_key_path
            and settings.ssh_known_hosts_path
            and settings.ssh_destination
        )
        return SshStatus(
            configured=configured,
            reachable=None,
            host=settings.ssh_host,
            port=settings.ssh_port,
            destination=settings.ssh_destination,
            free_bytes=None,
            capacity_unknown=True,
            detail=None,
            tested_at=None,
        )

    @app.get("/api/ssh/status", response_model=SshStatus)
    def ssh_status(session: Session = Depends(session_dependency)) -> SshStatus:
        return ssh_status_payload(_settings(session))

    def configured_ssh_transport(settings: AppSettings) -> KindleSshTransport:
        return ssh_transport_factory(
            KindleSshConfig(
                host=settings.ssh_host,
                port=settings.ssh_port,
                user=settings.ssh_user,
                key_path=Path(settings.ssh_key_path),
                known_hosts_path=Path(settings.ssh_known_hosts_path),
                destination_root=PurePosixPath(settings.ssh_destination),
            )
        )

    @app.post("/api/ssh/host-key", response_model=SshHostKeyRead)
    def inspect_ssh_host_key(
        session: Session = Depends(session_dependency),
    ) -> SshHostKeyRead:
        try:
            host_key = configured_ssh_transport(_settings(session)).inspect_host_key()
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        trusted = configured_ssh_transport(_settings(session)).pinned_host_key()
        return SshHostKeyRead(
            fingerprint=host_key.fingerprint,
            trusted_fingerprint=trusted.fingerprint if trusted else None,
        )

    @app.post("/api/ssh/trust", response_model=SshHostKeyRead)
    def trust_ssh_host_key(
        payload: SshTrustRequest,
        session: Session = Depends(session_dependency),
    ) -> SshHostKeyRead:
        try:
            host_key = configured_ssh_transport(_settings(session)).trust_host_key(
                payload.fingerprint
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        app.state.ssh_status = None
        return SshHostKeyRead(
            fingerprint=host_key.fingerprint,
            trusted_fingerprint=host_key.fingerprint,
        )

    @app.post("/api/ssh/test", response_model=SshStatus)
    def test_ssh(session: Session = Depends(session_dependency)) -> SshStatus:
        settings = _settings(session)
        probe = configured_ssh_transport(settings).probe()
        result = SshStatus(
            configured=True,
            reachable=probe.reachable,
            host=settings.ssh_host,
            port=settings.ssh_port,
            destination=settings.ssh_destination,
            free_bytes=probe.free_bytes,
            capacity_unknown=probe.free_bytes is None,
            detail=probe.detail,
            tested_at=datetime.now(UTC),
        )
        app.state.ssh_status = result
        return result

    def kindle_storage_error(error: Exception) -> HTTPException:
        if isinstance(error, KindleCollisionError):
            return HTTPException(status_code=409, detail=str(error))
        if isinstance(error, KindleStorageItemNotFoundError):
            return HTTPException(status_code=404, detail=str(error))
        if isinstance(error, ValueError):
            return HTTPException(status_code=422, detail=str(error))
        if isinstance(error, KindleSshError):
            return HTTPException(status_code=502, detail=str(error))
        return HTTPException(status_code=500, detail="Kindle file operation failed")

    @app.get("/api/kindle/files", response_model=KindleStorageListingRead)
    def list_kindle_storage(
        path: str = Query(default="/mnt/us", min_length=1, max_length=2000),
        session: Session = Depends(session_dependency),
    ) -> KindleStorageListingRead:
        try:
            listing = configured_ssh_transport(_settings(session)).list_storage(path)
        except Exception as error:
            raise kindle_storage_error(error) from error
        return KindleStorageListingRead.model_validate(listing)

    @app.post("/api/kindle/files/upload", response_model=KindleStorageMutationRead)
    def upload_kindle_storage_item(
        destination_directory: str = Form(min_length=1, max_length=2000),
        file: UploadFile = File(),
        session: Session = Depends(session_dependency),
    ) -> KindleStorageMutationRead:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="kindrop-manual-upload-", suffix=".part", delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                shutil.copyfileobj(file.file, temporary)
            receipt = configured_ssh_transport(_settings(session)).upload_storage_item(
                temporary_path,
                destination_directory,
                file.filename or "",
            )
        except Exception as error:
            raise kindle_storage_error(error) from error
        finally:
            file.file.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return KindleStorageMutationRead(path=receipt.remote_path)

    @app.post("/api/kindle/files/rename", response_model=KindleStorageMutationRead)
    def rename_kindle_storage_item(
        payload: KindleStorageRenameRequest,
        session: Session = Depends(session_dependency),
    ) -> KindleStorageMutationRead:
        try:
            path = configured_ssh_transport(_settings(session)).rename_storage_item(
                payload.path, payload.new_name
            )
        except Exception as error:
            raise kindle_storage_error(error) from error
        return KindleStorageMutationRead(path=path)

    @app.post("/api/kindle/files/move", response_model=KindleStorageMutationRead)
    def move_kindle_storage_item(
        payload: KindleStorageMoveRequest,
        session: Session = Depends(session_dependency),
    ) -> KindleStorageMutationRead:
        try:
            path = configured_ssh_transport(_settings(session)).move_storage_item(
                payload.path, payload.destination_directory
            )
        except Exception as error:
            raise kindle_storage_error(error) from error
        return KindleStorageMutationRead(path=path)

    @app.post("/api/kindle/files/delete", status_code=status.HTTP_204_NO_CONTENT)
    def delete_kindle_storage_item(
        payload: KindleStorageDeleteRequest,
        session: Session = Depends(session_dependency),
    ) -> Response:
        try:
            configured_ssh_transport(_settings(session)).delete_storage_item(payload.path)
        except Exception as error:
            raise kindle_storage_error(error) from error
        return Response(status_code=204)

    @app.post("/api/kindle/files/bulk-delete", status_code=status.HTTP_204_NO_CONTENT)
    def bulk_delete_kindle_storage_items(
        payload: KindleStorageBulkDeleteRequest,
        session: Session = Depends(session_dependency),
    ) -> Response:
        try:
            configured_ssh_transport(_settings(session)).delete_storage_items(payload.paths)
        except Exception as error:
            raise kindle_storage_error(error) from error
        return Response(status_code=204)

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
        paused = session.scalar(select(Scan.id).where(Scan.status == "paused"))
        if paused:
            raise HTTPException(
                status_code=409,
                detail="A scan is paused. Resume or stop it before starting a new one",
            )
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
        if scan.status in {"queued", "paused"}:
            scan.status = "cancelled"
            scan.cancel_requested = False
            scan.pause_requested = False
            scan.completed_at = datetime.now(UTC)
            add_event(session, "scan", scan.id, "scan.cancelled")
            session.commit()
            return {"status": "cancelled"}
        if scan.status != "scanning":
            raise HTTPException(
                status_code=409, detail="Only an active or paused scan can be stopped"
            )
        scan.cancel_requested = True
        session.commit()
        return {"status": "cancelling"}

    @app.post("/api/scans/{scan_id}/pause", status_code=status.HTTP_202_ACCEPTED)
    def pause_scan(scan_id: str, session: Session = Depends(session_dependency)) -> dict[str, str]:
        scan = session.get(Scan, scan_id)
        if not scan:
            raise HTTPException(status_code=404, detail="Scan not found")
        if scan.status == "queued":
            scan.status = "paused"
            add_event(session, "scan", scan.id, "scan.paused")
            session.commit()
            return {"status": "paused"}
        if scan.status != "scanning":
            raise HTTPException(status_code=409, detail="Only an active scan can be paused")
        scan.pause_requested = True
        session.commit()
        return {"status": "pausing"}

    @app.post("/api/scans/{scan_id}/resume", status_code=status.HTTP_202_ACCEPTED)
    def resume_scan(
        scan_id: str, session: Session = Depends(session_dependency)
    ) -> dict[str, str]:
        scan = session.get(Scan, scan_id)
        if not scan:
            raise HTTPException(status_code=404, detail="Scan not found")
        if scan.status != "paused":
            raise HTTPException(status_code=409, detail="Only a paused scan can be resumed")
        scan.status = "queued"
        scan.pause_requested = False
        add_event(session, "scan", scan.id, "scan.resumed")
        session.commit()
        return {"status": "queued"}

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

    @app.post("/api/local-files", response_model=CandidateRead, status_code=status.HTTP_201_CREATED)
    def upload_local_file(
        file: UploadFile = File(...),
        session: Session = Depends(session_dependency),
    ) -> CandidateRead:
        """Stage one browser-selected archive as a Candidate without using Drive."""
        filename = Path(file.filename or "").name
        suffix = Path(filename).suffix.lower()
        if not filename or suffix not in COMIC_SUFFIXES:
            allowed = ", ".join(sorted(COMIC_SUFFIXES))
            raise HTTPException(
                status_code=422, detail=f"Choose a supported comic archive: {allowed}"
            )

        upload_directory = runtime.cache_root / "uploads"
        upload_directory.mkdir(parents=True, exist_ok=True)
        destination = upload_directory / f"{uuid.uuid4()}-{filename}"
        try:
            with destination.open("xb") as target:
                while chunk := file.file.read(1024 * 1024):
                    target.write(chunk)
            candidate = stage_local_candidate(session, destination, filename)
            session.commit()
            session.refresh(candidate)
            return _candidate_read(candidate)
        except HTTPException:
            destination.unlink(missing_ok=True)
            raise
        except LocalCandidateConflict as error:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (ArchiveMetadataError, OSError, ValueError) as error:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=str(error)) from error
        finally:
            file.file.close()

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
        if "optimize" in provided:
            candidate.optimize = payload.optimize
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

    @app.get("/api/candidate-series", response_model=list[CandidateSeriesRead])
    def candidate_series(
        session: Session = Depends(session_dependency),
    ) -> list[CandidateSeriesRead]:
        return _candidate_series_suggestions(session)

    @app.patch("/api/candidate-series", response_model=list[CandidateRead])
    def apply_candidate_series(
        payload: CandidateSeriesApply,
        session: Session = Depends(session_dependency),
    ) -> list[CandidateRead]:
        if payload.cover_url and not payload.cover_url.startswith("https://"):
            raise HTTPException(status_code=422, detail="The cover URL must use https")
        suggestions = _candidate_series_suggestions(session, group_id=payload.group_id)
        suggestion = suggestions[0] if suggestions else None
        expected_ids = (
            [member.candidate_id for member in suggestion.members] if suggestion else []
        )
        if payload.candidate_ids != expected_ids:
            raise HTTPException(
                status_code=409,
                detail="The detected series group changed; review it again before applying",
            )
        selected = session.scalars(
            select(Candidate)
            .options(selectinload(Candidate.revision))
            .where(Candidate.id.in_(payload.candidate_ids))
        ).all()
        by_id = {candidate.id: candidate for candidate in selected}
        if len(by_id) != len(payload.candidate_ids) or any(
            candidate.status != "ready" for candidate in selected
        ):
            raise HTTPException(status_code=409, detail="Every series member must be ready")
        ordered = [by_id[candidate_id] for candidate_id in payload.candidate_ids]
        numbers: list[int] = []
        for candidate in ordered:
            number = _candidate_volume_number(candidate)
            if number is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Kindrop could not infer a volume number for {candidate.revision.name}",
                )
            numbers.append(number)

        series = " ".join(payload.series.split())
        if not series:
            raise HTTPException(status_code=422, detail="The series title is required")
        for candidate, number in zip(ordered, numbers, strict=True):
            metadata = dict(candidate.comic_metadata or {})
            metadata["series"] = series
            metadata["number"] = str(number)
            if "author" in payload.model_fields_set:
                metadata["author"] = (payload.author or "").strip() or None
            if "cover_url" in payload.model_fields_set:
                metadata["cover_url"] = (payload.cover_url or "").strip() or None
            candidate.comic_metadata = metadata
            candidate.resolved_title = format_kindle_title(
                series,
                str(number),
                metadata.get("title") or clean_title(Path(candidate.revision.name).stem),
            )
        session.commit()
        return [_candidate_read(candidate) for candidate in ordered]

    @app.get("/api/metadata/search", response_model=list[MangaMatchRead])
    def metadata_search(query: str = Query(min_length=1, max_length=200)) -> list[MangaMatchRead]:
        try:
            matches = search_manga(query)
        except AniListError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        return [MangaMatchRead(**match.__dict__) for match in matches]

    @app.post("/api/batches", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
    def create_batch(payload: BatchCreate, session: Session = Depends(session_dependency)):
        option_by_id = {
            option.candidate_id: option.optimize for option in payload.candidate_options
        }
        if payload.candidate_ids and not set(option_by_id).issubset(payload.candidate_ids):
            raise HTTPException(
                status_code=422,
                detail="Candidate options must belong to the selected Candidates",
            )
        candidate_ids = payload.candidate_ids or list(option_by_id)
        if not candidate_ids:
            raise HTTPException(status_code=422, detail="Select at least one Candidate")
        candidates = session.scalars(
            select(Candidate)
            .options(selectinload(Candidate.revision))
            .where(Candidate.id.in_(candidate_ids))
        ).all()
        if len(candidates) != len(set(candidate_ids)) or any(
            candidate.status != "ready" for candidate in candidates
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Every selected candidate must be ready",
            )
        settings = _settings(session)
        if not (
            settings.ssh_host
            and settings.ssh_key_path
            and settings.ssh_known_hosts_path
            and settings.ssh_destination
        ):
            raise HTTPException(status_code=409, detail="Configure a Kindle Destination first")

        preset = payload.preset.model_dump(mode="json")
        batch = Batch(preset=preset)
        session.add(batch)
        session.flush()
        volumes: dict[int, list[Candidate]] = {}
        singles: list[Candidate] = []
        for candidate in candidates:
            # Merging builds an image archive and therefore only accepts comic archives.
            mergeable = payload.merge_by_volume and Path(
                candidate.revision.name
            ).suffix.lower() in {".cbr", ".cbz"}
            volume = volume_number(candidate.revision.name) if mergeable else None
            if volume is None:
                singles.append(candidate)
            else:
                volumes.setdefault(volume, []).append(candidate)
        job_count = 0
        for volume, members in sorted(volumes.items()):
            members.sort(key=lambda member: member.revision.name)
            lead = members[0]
            series = (lead.comic_metadata or {}).get("series")
            title = f"{series}, Tome {volume:02d}" if series else f"Volume {volume:02d}"
            session.add(
                Job(
                    batch_id=batch.id,
                    candidate_id=lead.id,
                    preset=preset,
                    title=title,
                    optimize=all(
                        option_by_id.get(member.id)
                        if option_by_id.get(member.id) is not None
                        else _candidate_optimize(member)
                        for member in members
                    ),
                    delivery_transport="ssh",
                    merged_candidate_ids=[member.id for member in members],
                )
            )
            for member in members:
                member.status = "queued"
            job_count += 1
        for candidate in singles:
            session.add(
                Job(
                    batch_id=batch.id,
                    candidate_id=candidate.id,
                    preset=preset,
                    title=candidate.title_override or candidate.resolved_title,
                    optimize=(
                        option_by_id[candidate.id]
                        if option_by_id.get(candidate.id) is not None
                        else _candidate_optimize(candidate)
                    ),
                    delivery_transport="ssh",
                )
            )
            candidate.status = "queued"
            job_count += 1
        session.commit()
        return BatchResponse(
            id=batch.id,
            status=batch.status,
            job_count=job_count,
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

    def clone_job(
        source_job: Job,
        session: Session,
        preset: ConversionPreset | None = None,
        transport: str | None = None,
    ) -> Job:
        member_ids = set(source_job.merged_candidate_ids or [source_job.candidate_id])
        active_jobs = session.scalars(
            select(Job).where(
                Job.status.not_in(["sent", "copied_to_kindle", "failed", "cancelled"])
            )
        ).all()
        if any(
            member_ids.intersection(active.merged_candidate_ids or [active.candidate_id])
            for active in active_jobs
        ):
            raise HTTPException(
                status_code=409,
                detail="A Conversion Job for this Candidate is already active",
            )
        preset_snapshot = preset.model_dump(mode="json") if preset else source_job.preset
        batch = Batch(preset=preset_snapshot)
        session.add(batch)
        session.flush()
        for member in session.scalars(select(Candidate).where(Candidate.id.in_(member_ids))):
            member.status = "queued"
        replacement = Job(
            batch_id=batch.id,
            candidate_id=source_job.candidate_id,
            preset=preset_snapshot,
            title=source_job.title,
            optimize=source_job.optimize,
            delivery_transport=transport or source_job.delivery_transport,
            merged_candidate_ids=source_job.merged_candidate_ids,
        )
        session.add(replacement)
        session.commit()
        return replacement

    @app.post("/api/jobs/{job_id}/retry", status_code=status.HTTP_201_CREATED)
    def retry_job(
        job_id: str,
        payload: JobRetry | None = None,
        session: Session = Depends(session_dependency),
    ) -> dict[str, str]:
        job = session.scalar(
            select(Job).options(selectinload(Job.candidate)).where(Job.id == job_id)
        )
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status not in {"sent", "copied_to_kindle", "failed", "cancelled"}:
            raise HTTPException(
                status_code=409, detail="Only terminal jobs can be retried"
            )
        replacement = clone_job(job, session, payload.preset if payload else None)
        return {"id": replacement.id, "status": replacement.status}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, session: Session = Depends(session_dependency)) -> dict[str, str]:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        cancellable = {"queued", "ready_to_deliver", "waiting_for_kindle", "waiting_for_space"}
        if job.status not in cancellable:
            raise HTTPException(status_code=409, detail="This job can no longer be cancelled")
        for artifact in job.artifacts:
            Path(artifact.path).unlink(missing_ok=True)
            if artifact.delivery and artifact.delivery.status not in {"copied_to_kindle", "sent"}:
                artifact.delivery.status = "cancelled"
        job.status = "cancelled"
        job.completed_at = datetime.now(UTC)
        add_event(session, "job", job.id, "job.cancelled")
        member_ids = job.merged_candidate_ids or [job.candidate_id]
        for candidate in session.scalars(select(Candidate).where(Candidate.id.in_(member_ids))):
            candidate.status = "ready"
        jobs = session.scalars(select(Job).where(Job.batch_id == job.batch_id)).all()
        if all(
            item.status in {"sent", "copied_to_kindle", "failed", "cancelled"}
            for item in jobs
        ):
            batch = session.get(Batch, job.batch_id)
            batch.status = (
                "completed"
                if all(item.status in {"sent", "copied_to_kindle"} for item in jobs)
                else "completed_with_errors"
            )
            batch.completed_at = datetime.now(UTC)
        session.commit()
        return {"status": "cancelled"}

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

    @app.post(
        "/api/deliveries/{delivery_id}/email-fallback",
        status_code=status.HTTP_201_CREATED,
    )
    def email_fallback(
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
        if delivery.transport != "ssh":
            raise HTTPException(
                status_code=409,
                detail="Only an SSH Delivery can use email fallback",
            )
        if delivery.status not in {
            "pending",
            "failed",
            "action_required",
            "waiting_for_kindle",
            "waiting_for_space",
        }:
            raise HTTPException(
                status_code=409,
                detail="Only an incomplete SSH Delivery can use email fallback",
            )
        if not _settings(session).kindle_email:
            raise HTTPException(
                status_code=409,
                detail="Configure a Send to Kindle email before using email fallback",
            )
        source_job = delivery.artifact.job
        Path(delivery.artifact.path).unlink(missing_ok=True)
        delivery.status = "cancelled"
        source_job.status = "cancelled"
        source_job.completed_at = datetime.now(UTC)
        replacement = clone_job(source_job, session, transport="gmail")
        source_batch = session.get(Batch, source_job.batch_id)
        source_jobs = session.scalars(
            select(Job).where(Job.batch_id == source_job.batch_id)
        ).all()
        if all(
            item.status in {"sent", "copied_to_kindle", "failed", "cancelled"}
            for item in source_jobs
        ):
            source_batch.status = "completed_with_errors"
            source_batch.completed_at = datetime.now(UTC)
        add_event(
            session,
            "delivery",
            delivery.id,
            "delivery.email_fallback_selected",
            replacement_job_id=replacement.id,
        )
        session.commit()
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
            members = [job.candidate]
            extra_ids = [
                member_id
                for member_id in job.merged_candidate_ids or []
                if member_id != job.candidate_id
            ]
            if extra_ids:
                members += session.scalars(
                    select(Candidate)
                    .options(selectinload(Candidate.revision))
                    .where(Candidate.id.in_(extra_ids))
                ).all()
            session.delete(job)
            for candidate in members:
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
