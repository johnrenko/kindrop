import hashlib
import shutil
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import make_msgid
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .anilist import AniListError, download_cover
from .archives import build_volume_archive
from .database import Database
from .domain import ConversionPreset, revision_fingerprint
from .epub import EpubMetadataError, apply_epub_metadata
from .metadata import ArchiveMetadataError, clean_title, read_comic_metadata, volume_number
from .models import (
    AppSettings,
    Artifact,
    Batch,
    Candidate,
    Delivery,
    DeliveryAttempt,
    Event,
    Job,
    Revision,
    Scan,
)

MAX_EPUB_BYTES = 20 * 1024 * 1024
CACHE_TTL = timedelta(hours=24)
MAX_SEND_ATTEMPTS = 3
SENT_PROBE_COUNT = 3
# The first probe wait also absorbs Gmail's Sent-folder indexing delay.
SENT_PROBE_WAIT_SECONDS = 60.0


@dataclass(frozen=True)
class DriveComic:
    file_id: str
    name: str
    path: str
    size: int
    checksum: str | None
    modified_time: str


class DriveGateway(Protocol):
    def walk_comics(self, folder_id: str) -> Iterable[DriveComic]: ...

    def download(self, file_id: str, destination: Path) -> None: ...


class KccGateway(Protocol):
    def run(
        self, source: Path, output_directory: Path, preset: ConversionPreset, title: str
    ) -> list[Path]: ...


class GmailGateway(Protocol):
    def send_epub(
        self, recipient: str, subject: str, artifact: Path, *, rfc822_message_id: str
    ) -> str: ...

    def find_sent_message(self, rfc822_message_id: str) -> str | None: ...


class TransientSendError(RuntimeError):
    """A send failure which is known not to have created a Gmail message."""


class AmbiguousSendError(RuntimeError):
    """A send may have succeeded; the Sent folder must be checked before resending."""


class PermanentSendError(RuntimeError):
    """Gmail rejected the request outright, so no message was created and no retry can help."""


def _safe_filename(file_id: str, name: str) -> str:
    safe_id = "".join(
        character for character in file_id if character.isalnum() or character in "-_"
    )
    safe_name = Path(name).name.replace("/", "_").replace("\\", "_")
    return f"{safe_id[:80]}-{safe_name}"


def _artifact_filename(title: str, part_number: int, total_parts: int) -> str:
    stem = title if total_parts == 1 else f"{title} - Part {part_number} of {total_parts}"
    safe = "".join(" " if character in '/\\:*?"<>|' else character for character in stem)
    safe = " ".join(safe.split()).strip(". ")
    return f"{safe[:120] or 'kindrop'}.epub"


def _checksum(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - Google Drive exposes MD5 as its integrity checksum
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_event(session, topic: str, entity_id: str, kind: str, **payload) -> None:
    session.add(Event(topic=topic, entity_id=entity_id, kind=kind, payload=payload))


class ScanProcessor:
    def __init__(
        self,
        database: Database,
        drive: DriveGateway,
        cache_root: Path,
        between_items: Callable[[], None] | None = None,
    ) -> None:
        self.database = database
        self.drive = drive
        self.cache_root = cache_root
        self.between_items = between_items

    def run(self, scan_id: str) -> None:
        with self.database.session() as session:
            scan = session.get(Scan, scan_id)
            settings = session.get(AppSettings, 1)
            if not scan or not settings or not settings.source_folder_id:
                if scan:
                    scan.status = "failed"
                    scan.error = "Choose a Source Folder before scanning"
                    session.commit()
                return
            if scan.cancel_requested:
                scan.status = "cancelled"
                scan.completed_at = datetime.now(UTC)
                add_event(session, "scan", scan.id, "scan.cancelled")
                session.commit()
                return
            scan.status = "scanning"
            add_event(session, "scan", scan.id, "scan.started")
            session.commit()
            folder_id = settings.source_folder_id
            already_processed = scan.processed_count

        try:
            comics = list(self.drive.walk_comics(folder_id))
        except Exception as error:
            self._fail(scan_id, f"Google Drive could not be scanned: {error}")
            return

        new_comics: list[tuple[DriveComic, str]] = []
        with self.database.session() as session:
            scan = session.get(Scan, scan_id)
            for comic in comics:
                fingerprint = revision_fingerprint(
                    comic.file_id, comic.checksum, comic.size, comic.modified_time
                )
                exists = session.scalar(
                    select(Revision.id).where(Revision.fingerprint == fingerprint)
                )
                if not exists:
                    new_comics.append((comic, fingerprint))
            scan.discovered_count = already_processed + len(new_comics)
            session.commit()

        scan_directory = self.cache_root / "scans" / scan_id
        scan_directory.mkdir(parents=True, exist_ok=True)
        for index, (comic, fingerprint) in enumerate(new_comics, start=1):
            if self.between_items:
                self.between_items()
            with self.database.session() as session:
                scan = session.get(Scan, scan_id)
                if scan.cancel_requested:
                    scan.status = "cancelled"
                    scan.completed_at = datetime.now(UTC)
                    add_event(session, "scan", scan.id, "scan.cancelled")
                    session.commit()
                    return
                if scan.pause_requested:
                    scan.status = "paused"
                    scan.pause_requested = False
                    add_event(session, "scan", scan.id, "scan.paused")
                    session.commit()
                    return

            destination = scan_directory / _safe_filename(comic.file_id, comic.name)
            error_message: str | None = None
            metadata_payload: dict[str, str | None] = {}
            resolved_title = clean_title(Path(comic.name).stem)
            try:
                required_space = max(comic.size * 2, 256 * 1024 * 1024)
                if shutil.disk_usage(scan_directory).free < required_space:
                    raise OSError("Not enough cache space for this archive")
                self.drive.download(comic.file_id, destination)
                if comic.checksum and _checksum(destination).lower() != comic.checksum.lower():
                    raise OSError("The downloaded archive checksum does not match Google Drive")
                metadata = read_comic_metadata(destination)
                metadata_payload = {
                    "title": metadata.title,
                    "series": metadata.series,
                    "number": metadata.number,
                }
                resolved_title = metadata.resolved_title(resolved_title)
                candidate_status = "ready"
                revision_status = "candidate"
            except (ArchiveMetadataError, OSError, ValueError) as error:
                error_message = str(error)
                candidate_status = "invalid"
                revision_status = "failed"
                destination.unlink(missing_ok=True)

            with self.database.session() as session:
                revision = Revision(
                    drive_file_id=comic.file_id,
                    fingerprint=fingerprint,
                    checksum=comic.checksum,
                    name=comic.name,
                    path=comic.path,
                    size=comic.size,
                    modified_time=comic.modified_time,
                    status=revision_status,
                )
                session.add(revision)
                session.flush()
                session.add(
                    Candidate(
                        revision_id=revision.id,
                        scan_id=scan_id,
                        status=candidate_status,
                        comic_metadata=metadata_payload,
                        resolved_title=resolved_title,
                        cache_path=str(destination) if destination.exists() else None,
                        cache_expires_at=datetime.now(UTC) + CACHE_TTL
                        if destination.exists()
                        else None,
                        error=error_message,
                    )
                )
                scan = session.get(Scan, scan_id)
                scan.processed_count = already_processed + index
                total = already_processed + len(new_comics)
                scan.progress = round(scan.processed_count / total * 100) if total else 100
                add_event(
                    session,
                    "scan",
                    scan_id,
                    "scan.file_processed",
                    name=comic.name,
                    status=candidate_status,
                )
                session.commit()

        with self.database.session() as session:
            scan = session.get(Scan, scan_id)
            scan.status = "completed"
            scan.progress = 100
            scan.completed_at = datetime.now(UTC)
            add_event(session, "scan", scan.id, "scan.completed", count=scan.processed_count)
            session.commit()

    def _fail(self, scan_id: str, message: str) -> None:
        with self.database.session() as session:
            scan = session.get(Scan, scan_id)
            if scan:
                scan.status = "failed"
                scan.error = message
                scan.completed_at = datetime.now(UTC)
                add_event(session, "scan", scan.id, "scan.failed", message=message)
                session.commit()


class JobProcessor:
    def __init__(
        self,
        *,
        database: Database,
        drive: DriveGateway,
        kcc: KccGateway,
        gmail: GmailGateway,
        cache_root: Path,
        wait_between_deliveries: Callable[[], None],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.database = database
        self.drive = drive
        self.kcc = kcc
        self.gmail = gmail
        self.cache_root = cache_root
        self.wait_between_deliveries = wait_between_deliveries
        self.sleep = sleep

    def run(self, job_id: str) -> None:
        source_path: Path | None = None
        output_directory = self.cache_root / "jobs" / job_id
        try:
            with self.database.session() as session:
                job = session.scalar(
                    select(Job)
                    .options(selectinload(Job.candidate).selectinload(Candidate.revision))
                    .where(Job.id == job_id)
                )
                if job and job.status != "queued":
                    return
                settings = session.get(AppSettings, 1)
                if not job or not settings or not settings.kindle_email:
                    raise ValueError("Configure a Kindle Destination before processing jobs")
                job.status = "downloading"
                job.started_at = datetime.now(UTC)
                job.progress = 5
                add_event(session, "job", job.id, "job.started")
                session.commit()
                recipient = settings.kindle_email
                source_path = Path(job.candidate.cache_path) if job.candidate.cache_path else None
                revision = job.candidate.revision
                title = job.title
                comic_metadata = dict(job.candidate.comic_metadata or {})
                merged_ids = list(job.merged_candidate_ids or [])
                preset = ConversionPreset.model_validate(job.preset)

            if merged_ids:
                volume = volume_number(revision.name)
                if volume is not None:
                    comic_metadata["number"] = f"Tome {volume:02d}"
                source_path = self._prepare_merged_source(job_id, merged_ids)
            elif source_path is None or not source_path.exists():
                source_directory = self.cache_root / "sources" / job_id
                source_directory.mkdir(parents=True, exist_ok=True)
                source_path = source_directory / _safe_filename(
                    revision.drive_file_id, revision.name
                )
                self.drive.download(revision.drive_file_id, source_path)

            with self.database.session() as session:
                job = session.get(Job, job_id)
                job.status = "converting"
                job.progress = 20
                add_event(session, "job", job.id, "job.converting")
                session.commit()

            artifact_paths = self.kcc.run(source_path, output_directory, preset, title)
            if not artifact_paths:
                raise RuntimeError("KCC did not produce an EPUB artifact")
            for path in artifact_paths:
                if path.suffix.lower() != ".epub" or not path.is_file():
                    raise RuntimeError("KCC produced an unsupported artifact")

            self._apply_metadata(job_id, artifact_paths, title, comic_metadata)

            for path in artifact_paths:
                if path.stat().st_size > MAX_EPUB_BYTES:
                    raise RuntimeError("KCC produced an EPUB larger than the 20 MB safety limit")

            renamed_paths: list[Path] = []
            for index, path in enumerate(artifact_paths, start=1):
                target = path.with_name(_artifact_filename(title, index, len(artifact_paths)))
                if target != path:
                    path.replace(target)
                renamed_paths.append(target)
            artifact_paths = renamed_paths

            with self.database.session() as session:
                job = session.get(Job, job_id)
                job.status = "sending"
                job.progress = 70
                total = len(artifact_paths)
                for index, path in enumerate(artifact_paths, start=1):
                    artifact = Artifact(
                        job_id=job.id,
                        filename=path.name,
                        path=str(path),
                        size=path.stat().st_size,
                        part_number=index,
                        total_parts=total,
                    )
                    session.add(artifact)
                    session.flush()
                    session.add(Delivery(artifact_id=artifact.id))
                session.commit()

            for index, path in enumerate(artifact_paths, start=1):
                subject = (
                    title
                    if len(artifact_paths) == 1
                    else f"{title} — Part {index}/{len(artifact_paths)}"
                )
                self._send_artifact(job_id, index, recipient, subject, path)

            with self.database.session() as session:
                job = session.scalar(
                    select(Job)
                    .options(selectinload(Job.candidate).selectinload(Candidate.revision))
                    .where(Job.id == job_id)
                )
                job.status = "sent"
                job.progress = 100
                job.completed_at = datetime.now(UTC)
                member_ids = list(job.merged_candidate_ids or [job.candidate_id])
                members = session.scalars(
                    select(Candidate)
                    .options(selectinload(Candidate.revision))
                    .where(Candidate.id.in_(member_ids))
                ).all()
                for member in members:
                    if member.cache_path:
                        Path(member.cache_path).unlink(missing_ok=True)
                    member.status = "sent"
                    member.cache_path = None
                    member.cache_expires_at = None
                    member.revision.status = "sent"
                add_event(session, "job", job.id, "job.sent")
                session.commit()
            source_path.unlink(missing_ok=True)
            shutil.rmtree(self.cache_root / "sources" / job_id, ignore_errors=True)
            shutil.rmtree(output_directory, ignore_errors=True)
            self._update_batch(job_id)
        except Exception as error:
            self._fail(job_id, str(error))

    def _prepare_merged_source(self, job_id: str, member_ids: list[str]) -> Path:
        """Download every member chapter and merge them into one CBZ source."""
        with self.database.session() as session:
            members = {
                candidate.id: (
                    candidate.cache_path,
                    candidate.revision.drive_file_id,
                    candidate.revision.name,
                )
                for candidate in session.scalars(
                    select(Candidate)
                    .options(selectinload(Candidate.revision))
                    .where(Candidate.id.in_(member_ids))
                )
            }
        missing = [member_id for member_id in member_ids if member_id not in members]
        if missing:
            raise ValueError("A merged chapter no longer exists; recreate the batch")
        source_directory = self.cache_root / "sources" / job_id
        source_directory.mkdir(parents=True, exist_ok=True)
        member_paths: list[Path] = []
        for member_id in member_ids:
            cache_path, drive_file_id, name = members[member_id]
            path = Path(cache_path) if cache_path else None
            if path is None or not path.exists():
                path = source_directory / _safe_filename(drive_file_id, name)
                self.drive.download(drive_file_id, path)
            member_paths.append(path)
        return build_volume_archive(member_paths, source_directory)

    def _apply_metadata(
        self, job_id: str, artifact_paths: list[Path], title: str, metadata: dict
    ) -> None:
        """Stamp library metadata and the chosen cover; a failure never blocks the send."""
        cover: bytes | None = None
        if metadata.get("cover_url"):
            try:
                cover = download_cover(metadata["cover_url"])
            except AniListError as error:
                self._metadata_warning(job_id, str(error))
        for index, path in enumerate(artifact_paths, start=1):
            part_title = (
                title
                if len(artifact_paths) == 1
                else f"{title} — Part {index}/{len(artifact_paths)}"
            )
            try:
                apply_epub_metadata(
                    path,
                    title=part_title,
                    author=metadata.get("author"),
                    series=metadata.get("series"),
                    number=metadata.get("number"),
                    cover=cover,
                )
            except EpubMetadataError as error:
                self._metadata_warning(job_id, str(error))

    def _metadata_warning(self, job_id: str, message: str) -> None:
        with self.database.session() as session:
            add_event(session, "job", job_id, "job.metadata_warning", message=message)
            session.commit()

    def _send_artifact(
        self, job_id: str, part_number: int, recipient: str, subject: str, path: Path
    ) -> None:
        for attempt_number in range(1, MAX_SEND_ATTEMPTS + 1):
            rfc822_message_id = make_msgid(domain="kindrop.local")
            with self.database.session() as session:
                artifact = session.scalar(
                    select(Artifact)
                    .options(selectinload(Artifact.delivery))
                    .where(
                        Artifact.job_id == job_id,
                        Artifact.part_number == part_number,
                    )
                )
                attempt = DeliveryAttempt(
                    delivery_id=artifact.delivery.id,
                    number=attempt_number,
                    # Committed before the send so an interrupted verification can
                    # still be resolved after a restart.
                    rfc822_message_id=rfc822_message_id,
                )
                session.add(attempt)
                artifact.delivery.attempt_count = attempt_number
                session.commit()
                attempt_id = attempt.id
                delivery_id = attempt.delivery_id

            self.wait_between_deliveries()
            try:
                message_id = self.gmail.send_epub(
                    recipient, subject, path, rfc822_message_id=rfc822_message_id
                )
            except TransientSendError as error:
                self._finish_attempt(attempt_id, "transient_failed", error=str(error))
                if attempt_number == MAX_SEND_ATTEMPTS:
                    self._fail_delivery(
                        delivery_id, "Gmail kept throttling this message; resend it later."
                    )
                    raise RuntimeError("Gmail send failed after three safe retries") from error
                continue
            except PermanentSendError as error:
                self._finish_attempt(attempt_id, "failed", error=str(error))
                self._fail_delivery(delivery_id, str(error))
                raise
            except AmbiguousSendError as error:
                if self._verify_ambiguous_send(attempt_id, delivery_id, str(error)):
                    return
                if attempt_number == MAX_SEND_ATTEMPTS:
                    self._fail_delivery(
                        delivery_id,
                        "The message never appeared in Gmail's Sent folder after "
                        f"{MAX_SEND_ATTEMPTS} sends.",
                    )
                    raise RuntimeError(
                        "The Gmail send could not be confirmed after three attempts"
                    ) from error
                with self.database.session() as session:
                    add_event(
                        session,
                        "delivery",
                        delivery_id,
                        "delivery.resend",
                        attempt=attempt_number + 1,
                    )
                    session.commit()
                continue
            except Exception as error:
                self._finish_attempt(attempt_id, "failed", error=str(error))
                raise

            with self.database.session() as session:
                attempt = session.get(DeliveryAttempt, attempt_id)
                delivery = session.get(Delivery, attempt.delivery_id)
                attempt.status = "sent"
                attempt.gmail_message_id = message_id
                attempt.completed_at = datetime.now(UTC)
                delivery.status = "sent_unconfirmed"
                delivery.gmail_message_id = message_id
                delivery.sent_at = datetime.now(UTC)
                add_event(
                    session,
                    "delivery",
                    delivery.id,
                    "delivery.sent",
                    gmail_message_id=message_id,
                )
                session.commit()
            return

    def _verify_ambiguous_send(self, attempt_id: str, delivery_id: str, error: str) -> bool:
        """Probe Gmail's Sent folder to learn whether an ambiguous send actually left."""
        with self.database.session() as session:
            attempt = session.get(DeliveryAttempt, attempt_id)
            delivery = session.get(Delivery, delivery_id)
            attempt.status = "unknown"
            attempt.error = error
            attempt.completed_at = datetime.now(UTC)
            rfc822_message_id = attempt.rfc822_message_id
            delivery.status = "unknown"
            delivery.error_detail = (
                "Kindrop is checking Gmail's Sent folder and will resend "
                "automatically if the message never left."
            )
            delivery.sent_at = datetime.now(UTC)
            add_event(session, "delivery", delivery.id, "delivery.verifying")
            session.commit()

        message_id: str | None = None
        for _probe in range(SENT_PROBE_COUNT):
            self.sleep(SENT_PROBE_WAIT_SECONDS)
            try:
                message_id = self.gmail.find_sent_message(rfc822_message_id)
            except Exception:  # an unreachable Gmail counts as a failed probe
                continue
            if message_id:
                break

        if not message_id:
            self._finish_attempt(attempt_id, "unverified", error=error)
            return False

        with self.database.session() as session:
            attempt = session.get(DeliveryAttempt, attempt_id)
            delivery = session.get(Delivery, delivery_id)
            attempt.status = "sent"
            attempt.gmail_message_id = message_id
            delivery.status = "sent_unconfirmed"
            delivery.gmail_message_id = message_id
            delivery.error_detail = None
            delivery.sent_at = datetime.now(UTC)
            add_event(
                session,
                "delivery",
                delivery.id,
                "delivery.recovered",
                gmail_message_id=message_id,
            )
            session.commit()
        return True

    def _fail_delivery(self, delivery_id: str, detail: str) -> None:
        with self.database.session() as session:
            delivery = session.get(Delivery, delivery_id)
            delivery.status = "failed"
            delivery.error_detail = detail
            add_event(session, "delivery", delivery.id, "delivery.failed", message=detail)
            session.commit()

    def _finish_attempt(self, attempt_id: str, status: str, *, error: str) -> None:
        with self.database.session() as session:
            attempt = session.get(DeliveryAttempt, attempt_id)
            attempt.status = status
            attempt.error = error
            attempt.completed_at = datetime.now(UTC)
            session.commit()

    def _fail(self, job_id: str, message: str) -> None:
        with self.database.session() as session:
            job = session.get(Job, job_id)
            if job:
                job.status = "failed"
                job.error = message
                job.completed_at = datetime.now(UTC)
                add_event(session, "job", job.id, "job.failed", message=message)
                session.commit()
        self._update_batch(job_id)

    def _update_batch(self, job_id: str) -> None:
        with self.database.session() as session:
            job = session.get(Job, job_id)
            if not job:
                return
            jobs = session.scalars(select(Job).where(Job.batch_id == job.batch_id)).all()
            if all(item.status in {"sent", "failed", "cancelled"} for item in jobs):
                batch = session.get(Batch, job.batch_id)
                batch.status = (
                    "completed"
                    if all(item.status == "sent" for item in jobs)
                    else "completed_with_errors"
                )
                batch.completed_at = datetime.now(UTC)
                session.commit()
