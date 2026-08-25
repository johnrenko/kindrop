"""Mirror Drive File Revisions into KOReader-ready Library Files."""

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from .archives import repackage_to_cbz
from .database import Database
from .models import Candidate, LibraryFile, Revision
from .services import DriveGateway, _checksum, _safe_filename, add_event

logger = logging.getLogger(__name__)


def _safe_component(name: str) -> str:
    safe = "".join(" " if character in '/\\:*?"<>|' else character for character in name)
    safe = " ".join(safe.split()).strip(". ")
    return safe[:120] or "kindrop"


def _library_filename(title: str, suffix: str) -> str:
    return f"{_safe_component(title)}{suffix}"


class LibraryMirror:
    def __init__(self, database: Database, drive: DriveGateway, cache_root: Path) -> None:
        self.database = database
        self.drive = drive
        self.cache_root = cache_root

    def run_next(self) -> bool:
        """Claim and mirror one pending Library File; False when none are pending."""
        with self.database.session() as session:
            item = session.scalar(
                select(LibraryFile)
                .where(LibraryFile.status == "pending")
                .order_by(LibraryFile.created_at)
                .limit(1)
            )
            if not item:
                return False
            item.status = "mirroring"
            session.commit()
            library_file_id = item.id
        try:
            self._mirror(library_file_id)
        except Exception as error:
            self._fail(library_file_id, str(error))
        return True

    def _mirror(self, library_file_id: str) -> None:
        with self.database.session() as session:
            item = session.get(LibraryFile, library_file_id)
            revision = session.get(Revision, item.revision_id)
            candidate = session.scalar(
                select(Candidate).where(Candidate.revision_id == revision.id)
            )
            title = item.title
            series = item.series
            cache_path = (
                Path(candidate.cache_path) if candidate and candidate.cache_path else None
            )
            drive_file_id = revision.drive_file_id
            name = revision.name
            checksum = revision.checksum

        suffix = Path(name).suffix.lower()
        target_format = "pdf" if suffix == ".pdf" else "cbz"
        target_directory = self.cache_root / "library" / _safe_component(series)
        target_directory.mkdir(parents=True, exist_ok=True)
        target = target_directory / _library_filename(title, f".{target_format}")
        if target.exists():
            target = target_directory / _library_filename(
                f"{title} ({library_file_id[:6]})", f".{target_format}"
            )

        workdir = self.cache_root / "library-work" / library_file_id
        try:
            source = cache_path if cache_path and cache_path.exists() else None
            if source is None:
                workdir.mkdir(parents=True, exist_ok=True)
                source = workdir / _safe_filename(drive_file_id, name)
                self.drive.download(drive_file_id, source)
                if checksum and _checksum(source).lower() != checksum.lower():
                    raise OSError("The downloaded archive checksum does not match Google Drive")
            if suffix == ".cbr":
                repackage_to_cbz(source, target)
            else:
                shutil.copyfile(source, target)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        with self.database.session() as session:
            item = session.get(LibraryFile, library_file_id)
            item.status = "ready"
            item.path = str(target.relative_to(self.cache_root))
            item.format = target_format
            item.size = target.stat().st_size
            item.mirrored_at = datetime.now(UTC)
            item.error = None
            add_event(session, "library", item.id, "library.file_ready", title=item.title)
            session.commit()

    def _fail(self, library_file_id: str, message: str) -> None:
        logger.warning("Mirroring failed for %s: %s", library_file_id, message)
        with self.database.session() as session:
            item = session.get(LibraryFile, library_file_id)
            item.status = "failed"
            item.error = message
            item.path = None
            add_event(session, "library", item.id, "library.file_failed", message=message)
            session.commit()
