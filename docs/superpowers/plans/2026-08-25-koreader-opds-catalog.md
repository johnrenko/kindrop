# KOReader OPDS Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror the full Source Folder locally and expose it as an authenticated OPDS catalog on the LAN so KOReader on the Kindle can browse and download the comics, while the existing KCC → Gmail pipeline stays untouched as an optional mode.

**Architecture:** A new `LibraryFile` entity tracks the local, KOReader-ready copy of each Drive File Revision (CBZ/PDF as-is, CBR repackaged to CBZ). The existing sequential worker gains a mirroring step; the Scan reconciles the mirror against Drive. A third compose service, `catalog`, serves a small dedicated FastAPI app (`kindrop/opds.py`) on LAN port 8788 behind mandatory HTTP Basic Auth.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 + Alembic (SQLite WAL), pytest; React 19 + TanStack Query/Router, vitest; Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-25-koreader-opds-catalog-design.md` — read it before starting.

## Global Constraints

- Python 3.12, ruff line-length 100 (`uv run ruff check .` from `backend/` must pass).
- Backend tests: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest` from `backend/`.
- Frontend: `npm run typecheck`, `npm run lint`, `npm test` from `frontend/` must pass.
- Migrations must be idempotent: `Database.__init__` runs `Base.metadata.create_all`, so a fresh database already has every table/column — migrations must inspect before altering (see `backend/migrations/versions/0005_delivery_attempt_message_id.py`).
- Use CONTEXT.md vocabulary exactly: **Catalog**, **Library File**, **Mirroring** (never "server", "share", "cache", "download" for these concepts).
- Kindrop stays read-only toward Drive. The web UI/API stays on `127.0.0.1:8787`; only the `catalog` service is exposed to the LAN.
- Do not modify the KCC/email pipeline behavior (ScanProcessor candidate flow, JobProcessor, deliveries) beyond the additive hooks described here.
- All code, comments, and docs in English (matching the codebase).

---

### Task 1: LibraryFile model, AppSettings catalog columns, migration 0006

**Files:**
- Modify: `backend/kindrop/models.py`
- Create: `backend/migrations/versions/0006_library_files.py`
- Test: `backend/tests/test_api.py` (existing file, add one test at the end)

**Interfaces:**
- Produces: `models.LibraryFile` with columns `id: str`, `revision_id: str` (FK unique), `title: str`, `series: str`, `path: str | None` (relative to cache root), `format: str | None` (`"cbz"`/`"pdf"`), `size: int`, `status: str` (`pending`/`mirroring`/`ready`/`failed`/`removed`), `error: str | None`, `mirrored_at`, `last_downloaded_at`, `download_count: int`, `created_at`, and relationship `revision: Revision`.
- Produces: `AppSettings.catalog_enabled: bool` (default False), `AppSettings.catalog_username: str | None`, `AppSettings.catalog_password: str | None`.

- [ ] **Step 1: Add the model and settings columns**

In `backend/kindrop/models.py`, add to `AppSettings` (after the `preset` column):

```python
    catalog_enabled: Mapped[bool] = mapped_column(default=False)
    catalog_username: Mapped[str | None] = mapped_column(String(100))
    catalog_password: Mapped[str | None] = mapped_column(String(100))
```

Add a new model after `Revision`:

```python
class LibraryFile(Base):
    __tablename__ = "library_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    series: Mapped[str] = mapped_column(String(500))
    path: Mapped[str | None] = mapped_column(String(2000))
    format: Mapped[str | None] = mapped_column(String(10))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    mirrored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revision: Mapped[Revision] = relationship()
```

- [ ] **Step 2: Write the idempotent migration**

Create `backend/migrations/versions/0006_library_files.py`:

```python
"""Track Library Files for the OPDS Catalog and its Basic Auth settings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 0001 runs create_all on the current models, so a fresh database
    # already has this table and these columns; only alter older databases.
    inspector = inspect(op.get_bind())
    if "library_files" not in inspector.get_table_names():
        op.create_table(
            "library_files",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "revision_id", sa.String(36), sa.ForeignKey("revisions.id"), unique=True
            ),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("series", sa.String(500), nullable=False),
            sa.Column("path", sa.String(2000), nullable=True),
            sa.Column("format", sa.String(10), nullable=True),
            sa.Column("size", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("error", sa.Text, nullable=True),
            sa.Column("mirrored_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_downloaded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("download_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_library_files_status", "library_files", ["status"])
    settings_columns = {
        column["name"] for column in inspect(op.get_bind()).get_columns("app_settings")
    }
    if "catalog_enabled" not in settings_columns:
        op.add_column(
            "app_settings",
            sa.Column("catalog_enabled", sa.Boolean, nullable=False, server_default="0"),
        )
    if "catalog_username" not in settings_columns:
        op.add_column("app_settings", sa.Column("catalog_username", sa.String(100), nullable=True))
    if "catalog_password" not in settings_columns:
        op.add_column("app_settings", sa.Column("catalog_password", sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_table("library_files")
    op.drop_column("app_settings", "catalog_enabled")
    op.drop_column("app_settings", "catalog_username")
    op.drop_column("app_settings", "catalog_password")
```

- [ ] **Step 3: Write a failing test that persists a LibraryFile**

Append to `backend/tests/test_api.py` (import `LibraryFile` in the existing `from kindrop.models import (...)` block):

```python
def test_library_file_persists_with_defaults(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    with database.session() as session:
        revision = Revision(
            drive_file_id="drive-1",
            fingerprint="drive-1:md5:abc",
            name="Naruto c700.cbz",
            path="Manga/Naruto c700.cbz",
            size=123,
            status="candidate",
        )
        session.add(revision)
        session.flush()
        session.add(LibraryFile(revision_id=revision.id, title="Naruto, Ch. 700", series="Naruto"))
        session.commit()

    with database.session() as session:
        stored = session.scalar(select(LibraryFile))
        assert stored.status == "pending"
        assert stored.download_count == 0
        assert stored.revision.name == "Naruto c700.cbz"
```

- [ ] **Step 4: Run the test to verify it fails**

Run from `backend/`: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_api.py::test_library_file_persists_with_defaults -v`
Expected: FAIL with `ImportError: cannot import name 'LibraryFile'` (before Step 1 code is saved) — if you already saved the model, it should PASS; in that case verify the model matches Step 1 exactly and move on.

- [ ] **Step 5: Run the full backend suite and ruff**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest` and `uv run ruff check .`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/kindrop/models.py backend/migrations/versions/0006_library_files.py backend/tests/test_api.py
git commit -m "feat(library): LibraryFile model and Catalog settings columns"
```

---

### Task 2: CBR → CBZ repackaging in archives.py

**Files:**
- Modify: `backend/kindrop/archives.py`
- Test: `backend/tests/test_archives.py`

**Interfaces:**
- Consumes: `extract_archive_images(archive: Path, destination: Path) -> int` (existing).
- Produces: `archives.repackage_to_cbz(source: Path, target: Path) -> None` — raises `ArchiveExtractionError` on unreadable archives; on failure the target file must not exist.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_archives.py` (it already imports `ZipFile` and helpers; add imports as needed at the top: `from kindrop.archives import repackage_to_cbz` and `pytest` if missing):

```python
def test_repackage_to_cbz_rebuilds_a_plain_zip(tmp_path) -> None:
    # A zip-encoded .cbr exercises the same extract path without needing unrar.
    source = tmp_path / "chapter.cbr"
    with ZipFile(source, "w") as archive:
        archive.writestr("pages/001.jpg", b"page-one")
        archive.writestr("pages/002.png", b"page-two")
        archive.writestr("ComicInfo.xml", b"<ComicInfo/>")
    target = tmp_path / "out" / "chapter.cbz"
    target.parent.mkdir()

    repackage_to_cbz(source, target)

    with ZipFile(target) as result:
        names = sorted(result.namelist())
    assert names == ["pages-001.jpg", "pages-002.png"]


def test_repackage_to_cbz_leaves_no_target_on_failure(tmp_path) -> None:
    source = tmp_path / "broken.cbr"
    source.write_bytes(b"not an archive at all")
    target = tmp_path / "broken.cbz"

    with pytest.raises(ArchiveExtractionError):
        repackage_to_cbz(source, target)

    assert not target.exists()
```

(`ArchiveExtractionError` is already exported by `kindrop.archives`; add it to the test file's imports if absent.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_archives.py -v`
Expected: the two new tests FAIL with `ImportError: cannot import name 'repackage_to_cbz'`.

- [ ] **Step 3: Implement repackage_to_cbz**

Append to `backend/kindrop/archives.py`:

```python
def repackage_to_cbz(source: Path, target: Path) -> None:
    """Rebuild a CBR as a plain CBZ that KOReader can open; images only, no reprocessing."""
    workdir = target.parent / f".{target.stem}.repack"
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    try:
        extract_archive_images(source, workdir)
        with ZipFile(target, "w") as archive:
            for path in sorted(workdir.iterdir()):
                if path.is_file():
                    archive.write(path, path.name)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_archives.py -v && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/kindrop/archives.py backend/tests/test_archives.py
git commit -m "feat(library): repackage CBR archives into plain CBZ"
```

---

### Task 3: series_from_title helper in metadata.py

**Files:**
- Modify: `backend/kindrop/metadata.py`
- Test: `backend/tests/test_comic_metadata.py`

**Interfaces:**
- Produces: `metadata.series_from_title(title: str) -> str` — strips trailing chapter/volume numbering from a resolved title (`"Naruto, Ch. 700"` → `"Naruto"`); returns the input unchanged when no numbering is found; never returns an empty string.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_comic_metadata.py` (add `series_from_title` to its `from kindrop.metadata import ...`):

```python
def test_series_from_title_strips_chapter_and_volume_numbering() -> None:
    assert series_from_title("Naruto, Ch. 700") == "Naruto"
    assert series_from_title("One Piece Tome 12") == "One Piece"
    assert series_from_title("Berserk Vol. 3 - The Golden Age") == "Berserk"
    assert series_from_title("Solo Standalone Story") == "Solo Standalone Story"
    assert series_from_title("Ch. 12") == "Ch. 12"
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_comic_metadata.py -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement**

In `backend/kindrop/metadata.py`, add near the other module-level regexes:

```python
_TITLE_NUMBERING = re.compile(
    r"[\s,–-]*\b(?:ch\.?|chapter|tome|vol\.?|volume)\s*\d+.*$", re.IGNORECASE
)
```

and after `format_kindle_title`:

```python
def series_from_title(title: str) -> str:
    """Derive the series shelf name from a resolved title, for Catalog grouping."""
    stripped = _TITLE_NUMBERING.sub("", title).strip(" -,–")
    return stripped or title
```

- [ ] **Step 4: Run tests + ruff**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_comic_metadata.py -v && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/kindrop/metadata.py backend/tests/test_comic_metadata.py
git commit -m "feat(library): derive a series shelf name from resolved titles"
```

---

### Task 4: LibraryMirror processor (kindrop/library.py)

**Files:**
- Create: `backend/kindrop/library.py`
- Test: `backend/tests/test_library.py`

**Interfaces:**
- Consumes: `DriveGateway` protocol, `services._checksum`, `services._safe_filename`, `services.add_event`, `archives.repackage_to_cbz`, `models.LibraryFile`.
- Produces: `library.LibraryMirror(database: Database, drive: DriveGateway, cache_root: Path)` with method `run_next() -> bool` (claims and processes one `pending` Library File; returns `False` when none are pending). Library Files end `ready` (with `path` relative to `cache_root`, `format`, `size`, `mirrored_at`) or `failed` (with `error`). Events: topic `"library"`, kinds `library.file_ready` / `library.file_failed`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_library.py`:

```python
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy import select

from kindrop.database import Database
from kindrop.library import LibraryMirror
from kindrop.models import Candidate, Event, LibraryFile, Revision


class FakeDrive:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.downloads = 0

    def walk_comics(self, _folder_id: str) -> list:
        return []

    def download(self, _file_id: str, destination: Path) -> None:
        self.downloads += 1
        destination.write_bytes(self.source.read_bytes())


def make_cbz(path: Path) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("001.jpg", b"page-one")


def seed_library_file(
    database: Database,
    *,
    name: str = "Naruto c700.cbz",
    title: str = "Naruto, Ch. 700",
    cache_path: str | None = None,
) -> str:
    with database.session() as session:
        revision = Revision(
            drive_file_id="drive-1",
            fingerprint="drive-1:md5:abc",
            name=name,
            path=f"Manga/{name}",
            size=100,
            status="candidate",
        )
        session.add(revision)
        session.flush()
        session.add(
            Candidate(
                revision_id=revision.id,
                status="ready",
                resolved_title=title,
                cache_path=cache_path,
            )
        )
        item = LibraryFile(revision_id=revision.id, title=title, series="Naruto")
        session.add(item)
        session.commit()
        return item.id


def test_run_next_returns_false_when_nothing_is_pending(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    mirror = LibraryMirror(database, FakeDrive(tmp_path / "unused"), tmp_path / "cache")

    assert mirror.run_next() is False


def test_mirror_reuses_the_scan_copy_without_a_second_download(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    scan_copy = tmp_path / "scan-copy.cbz"
    make_cbz(scan_copy)
    drive = FakeDrive(scan_copy)
    item_id = seed_library_file(database, cache_path=str(scan_copy))
    mirror = LibraryMirror(database, drive, tmp_path / "cache")

    assert mirror.run_next() is True

    assert drive.downloads == 0
    with database.session() as session:
        item = session.get(LibraryFile, item_id)
        assert item.status == "ready"
        assert item.format == "cbz"
        assert item.size > 0
        assert item.mirrored_at is not None
        target = tmp_path / "cache" / item.path
        assert target.is_file()
        assert target.parent.name == "Naruto"
        kinds = [event.kind for event in session.scalars(select(Event))]
        assert "library.file_ready" in kinds


def test_mirror_downloads_from_drive_when_no_scan_copy_exists(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    original = tmp_path / "original.cbz"
    make_cbz(original)
    drive = FakeDrive(original)
    item_id = seed_library_file(database, cache_path=None)
    mirror = LibraryMirror(database, drive, tmp_path / "cache")

    mirror.run_next()

    assert drive.downloads == 1
    with database.session() as session:
        assert session.get(LibraryFile, item_id).status == "ready"


def test_mirror_repackages_cbr_sources_into_cbz(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    # Zip-encoded .cbr: the extractor tries zipfile first, so no unrar is needed.
    original = tmp_path / "original.cbr"
    with ZipFile(original, "w") as archive:
        archive.writestr("pages/001.jpg", b"page-one")
        archive.writestr("ComicInfo.xml", b"<ComicInfo/>")
    drive = FakeDrive(original)
    item_id = seed_library_file(database, name="Naruto c700.cbr")
    mirror = LibraryMirror(database, drive, tmp_path / "cache")

    mirror.run_next()

    with database.session() as session:
        item = session.get(LibraryFile, item_id)
        assert item.status == "ready"
        assert item.format == "cbz"
        with ZipFile(tmp_path / "cache" / item.path) as result:
            assert result.namelist() == ["pages-001.jpg"]


def test_mirror_failure_records_the_error_and_no_file(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    broken = tmp_path / "broken.cbr"
    broken.write_bytes(b"not an archive")
    drive = FakeDrive(broken)
    item_id = seed_library_file(database, name="Broken c1.cbr", title="Broken, Ch. 1")
    mirror = LibraryMirror(database, drive, tmp_path / "cache")

    assert mirror.run_next() is True

    with database.session() as session:
        item = session.get(LibraryFile, item_id)
        assert item.status == "failed"
        assert item.error
        assert item.path is None
        kinds = [event.kind for event in session.scalars(select(Event))]
        assert "library.file_failed" in kinds
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_library.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kindrop.library'`.

- [ ] **Step 3: Implement kindrop/library.py**

Create `backend/kindrop/library.py`:

```python
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
```

- [ ] **Step 4: Run tests + ruff**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_library.py -v && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/kindrop/library.py backend/tests/test_library.py
git commit -m "feat(library): LibraryMirror processor materializes Library Files"
```

---

### Task 5: Scan integration — enqueue, backfill, and reconcile Library Files

**Files:**
- Modify: `backend/kindrop/services.py` (ScanProcessor)
- Test: `backend/tests/test_workflows.py`

**Interfaces:**
- Consumes: `models.LibraryFile`, `metadata.series_from_title`, `metadata.clean_title`.
- Produces: after a completed Scan, every Revision currently present in Drive (and not `failed`) has exactly one LibraryFile; LibraryFiles whose Revision vanished from Drive are `removed` (local file deleted, event `library.file_removed`).

Behavior to implement in `ScanProcessor.run`:

1. **New Revisions:** in the per-comic persistence block (where `Revision` and `Candidate` are added), when `candidate_status == "ready"`, also add a LibraryFile:

```python
                if candidate_status == "ready":
                    series = metadata_payload.get("series") or series_from_title(resolved_title)
                    session.add(
                        LibraryFile(revision_id=revision.id, title=resolved_title, series=series)
                    )
```

(Import `LibraryFile` in the models import block and `series_from_title` in the metadata import line of `services.py`.)

2. **Reconciliation + backfill:** add a private method called right after the fingerprint-dedup block computes `new_comics` (it already holds the full `comics` listing):

```python
    def _reconcile_library(self, comics: list[DriveComic]) -> None:
        """Make the Library mirror converge on the Source Folder's current contents."""
        present = {
            revision_fingerprint(comic.file_id, comic.checksum, comic.size, comic.modified_time)
            for comic in comics
        }
        with self.database.session() as session:
            tracked = session.scalars(
                select(LibraryFile).options(selectinload(LibraryFile.revision))
            ).all()
            tracked_revision_ids = set()
            for item in tracked:
                tracked_revision_ids.add(item.revision_id)
                if item.status == "removed":
                    continue
                if item.revision.fingerprint not in present:
                    if item.path:
                        (self.cache_root / item.path).unlink(missing_ok=True)
                    item.status = "removed"
                    item.path = None
                    add_event(
                        session, "library", item.id, "library.file_removed", title=item.title
                    )
            # Backfill: revisions scanned before the Catalog existed get a Library File
            # as long as their file is still in the Source Folder.
            orphans = session.scalars(
                select(Revision).where(
                    Revision.status != "failed",
                    Revision.fingerprint.in_(present),
                    Revision.id.not_in(tracked_revision_ids),
                )
            ).all()
            for revision in orphans:
                candidate = session.scalar(
                    select(Candidate).where(Candidate.revision_id == revision.id)
                )
                title = (
                    candidate.title_override or candidate.resolved_title
                    if candidate
                    else clean_title(Path(revision.name).stem)
                )
                series = (candidate.comic_metadata or {}).get("series") if candidate else None
                session.add(
                    LibraryFile(
                        revision_id=revision.id,
                        title=title,
                        series=series or series_from_title(title),
                    )
                )
            session.commit()
```

Call it in `run` right after the session block that sets `scan.discovered_count` (so it runs once per scan, after the full listing is known): `self._reconcile_library(comics)`.

Note: `selectinload` is already imported in `services.py`. `Revision.fingerprint.in_(present)` with an empty `present` is valid SQL (matches nothing).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_workflows.py` (its imports already include `Revision`, `Scan`, `AppSettings`; add `LibraryFile` to the models import and reuse the module's existing `FakeDrive` and `make_cbz` helpers):

```python
def test_scan_enqueues_a_library_file_for_each_new_revision(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    archive = tmp_path / "Naruto c700.cbz"
    make_cbz(archive)
    with database.session() as session:
        session.add(AppSettings(id=1, source_folder_id="folder"))
        scan = Scan()
        session.add(scan)
        session.commit()
        scan_id = scan.id

    ScanProcessor(database, FakeDrive(archive), tmp_path / "cache").run(scan_id)

    with database.session() as session:
        item = session.scalar(select(LibraryFile))
        assert item is not None
        assert item.status == "pending"
        assert item.series


def test_scan_backfills_and_removes_library_files_to_match_drive(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    archive = tmp_path / "Naruto c700.cbz"
    make_cbz(archive)
    drive = FakeDrive(archive)
    comic = drive.walk_comics("folder")[0]
    present_fingerprint = revision_fingerprint(
        comic.file_id, comic.checksum, comic.size, comic.modified_time
    )
    gone_file = tmp_path / "cache" / "library" / "Gone" / "Gone, Ch. 1.cbz"
    gone_file.parent.mkdir(parents=True)
    gone_file.write_bytes(b"stale")
    with database.session() as session:
        session.add(AppSettings(id=1, source_folder_id="folder"))
        # A revision from before the Catalog existed, still present on Drive.
        old = Revision(
            drive_file_id=comic.file_id,
            fingerprint=present_fingerprint,
            name=comic.name,
            path=comic.path,
            size=comic.size,
            modified_time=comic.modified_time,
            status="sent",
        )
        # A revision whose file vanished from Drive, already mirrored.
        gone = Revision(
            drive_file_id="gone-1",
            fingerprint="gone-1:md5:dead",
            name="Gone c1.cbz",
            path="Manga/Gone c1.cbz",
            size=10,
            status="sent",
        )
        session.add_all([old, gone])
        session.flush()
        session.add(
            LibraryFile(
                revision_id=gone.id,
                title="Gone, Ch. 1",
                series="Gone",
                status="ready",
                path="library/Gone/Gone, Ch. 1.cbz",
            )
        )
        scan = Scan()
        session.add(scan)
        session.commit()
        scan_id = scan.id
        old_id = old.id
        gone_id = gone.id

    ScanProcessor(database, drive, tmp_path / "cache").run(scan_id)

    with database.session() as session:
        backfilled = session.scalar(
            select(LibraryFile).where(LibraryFile.revision_id == old_id)
        )
        assert backfilled is not None and backfilled.status == "pending"
        removed = session.scalar(
            select(LibraryFile).where(LibraryFile.revision_id == gone_id)
        )
        assert removed.status == "removed"
    assert not gone_file.exists()
```

Add `from kindrop.domain import revision_fingerprint` to the test file imports if not present (it imports `ConversionPreset` from `kindrop.domain` already — extend that line).

- [ ] **Step 2: Run to verify failure**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_workflows.py -k library -v`
Expected: FAIL (no LibraryFile rows created).

- [ ] **Step 3: Implement the ScanProcessor changes described above**

- [ ] **Step 4: Run the whole backend suite + ruff**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest && uv run ruff check .`
Expected: PASS — the pre-existing scan tests must still pass (the reconciliation must not disturb candidate/dedup behavior).

- [ ] **Step 5: Commit**

```bash
git add backend/kindrop/services.py backend/tests/test_workflows.py
git commit -m "feat(scan): keep the Library mirror in step with the Source Folder"
```

---

### Task 6: Worker integration and crash recovery

**Files:**
- Modify: `backend/kindrop/worker.py`
- Test: `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: `library.LibraryMirror` (Task 4).
- Produces: `Worker.drain_pending_library_files()` — mirrors every pending Library File, yielding to jobs/mail between items; `mirroring` rows are reset to `pending` by `recover_interrupted_work`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_worker.py` (inspect its existing fixtures first: it builds `Worker`-adjacent pieces without Google; follow the file's existing pattern for constructing test doubles. If the file instantiates `Worker` directly with a `RuntimeSettings`, reuse that; otherwise test `recover_interrupted_work` through the same seam the existing interrupted-scan tests use):

```python
def test_recover_resets_interrupted_mirroring_to_pending(tmp_path, monkeypatch) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    with database.session() as session:
        revision = Revision(
            drive_file_id="drive-1",
            fingerprint="drive-1:md5:abc",
            name="Naruto c700.cbz",
            path="Manga/Naruto c700.cbz",
            size=100,
            status="candidate",
        )
        session.add(revision)
        session.flush()
        session.add(
            LibraryFile(
                revision_id=revision.id,
                title="Naruto, Ch. 700",
                series="Naruto",
                status="mirroring",
            )
        )
        session.commit()

    recover_interrupted_library_files(database)

    with database.session() as session:
        assert session.scalar(select(LibraryFile)).status == "pending"
```

To keep the test free of Google credentials, implement the reset as a module-level function `recover_interrupted_library_files(database: Database) -> None` in `worker.py` and call it from `Worker.recover_interrupted_work`. Import `LibraryFile`, `Revision`, `Database`, `select` in the test as the file's conventions dictate.

- [ ] **Step 2: Run to verify failure**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_worker.py -k mirroring -v`
Expected: FAIL with ImportError.

- [ ] **Step 3: Implement worker changes**

In `backend/kindrop/worker.py`:

```python
def recover_interrupted_library_files(database: Database) -> None:
    """Re-queue Library Files the worker left mid-mirroring; the copy restarts cleanly."""
    with database.session() as session:
        for item in session.scalars(
            select(LibraryFile).where(LibraryFile.status == "mirroring")
        ):
            item.status = "pending"
        session.commit()
```

In `Worker.__init__`, after `self.jobs = JobProcessor(...)`:

```python
        self.library = LibraryMirror(self.database, self.drive, runtime.cache_root)
```

Add a drain method:

```python
    def drain_pending_library_files(self) -> None:
        while self.library.run_next():
            self.drain_queued_jobs()
            self.check_mail_if_due()
```

In `recover_interrupted_work`, after the jobs loop's `session.commit()` block (outside the session), call `recover_interrupted_library_files(self.database)`.

In `run_forever`, after the scan block (`if scan_id: self.scans.run(scan_id)`), add:

```python
            self.drain_pending_library_files()
```

Imports: add `LibraryFile` to the models import, `from .library import LibraryMirror`.

- [ ] **Step 4: Run the suite + ruff**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/kindrop/worker.py backend/tests/test_worker.py
git commit -m "feat(worker): mirror pending Library Files in the polling loop"
```

---

### Task 7: OPDS Catalog app (kindrop/opds.py + catalog entrypoint)

**Files:**
- Create: `backend/kindrop/opds.py`
- Create: `backend/kindrop/catalog_main.py`
- Test: `backend/tests/test_opds.py`

**Interfaces:**
- Consumes: `models.LibraryFile`, `models.AppSettings`, `Database`, `RuntimeSettings`, `services.add_event`.
- Produces: `opds.create_catalog_app(database: Database, runtime: RuntimeSettings | None = None) -> FastAPI` serving:
  - `GET /opds` — navigation feed (Latest additions / By series / All)
  - `GET /opds/latest` — 50 most recent `ready` files by `mirrored_at` desc
  - `GET /opds/series` — navigation feed, one entry per distinct series
  - `GET /opds/series/{series}` — acquisition feed for one series, ordered by title
  - `GET /opds/all` — every `ready` file ordered by title
  - `GET /opds/download/{library_file_id}` — the file itself; increments `download_count`, sets `last_downloaded_at`
  - Every route requires HTTP Basic Auth against `AppSettings.catalog_username`/`catalog_password`; 503 when `catalog_enabled` is false or no password is set; downloaded entries get a `✓ ` title prefix.
- `catalog_main.py` builds the module-level `app` for uvicorn (compose uses `kindrop.catalog_main:app`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_opds.py`:

```python
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from kindrop.config import RuntimeSettings
from kindrop.database import Database
from kindrop.models import AppSettings, LibraryFile, Revision
from kindrop.opds import create_catalog_app

ATOM = "{http://www.w3.org/2005/Atom}"
AUTH = ("kindle", "correct-horse")


def build_catalog(tmp_path: Path, *, enabled: bool = True) -> TestClient:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    cache_root = tmp_path / "cache"
    with database.session() as session:
        session.add(
            AppSettings(
                id=1,
                catalog_enabled=enabled,
                catalog_username="kindle",
                catalog_password="correct-horse",
            )
        )
        for index, (title, series, downloads) in enumerate(
            [
                ("Naruto, Ch. 700", "Naruto", 0),
                ("Naruto, Ch. 701", "Naruto", 2),
                ("One Piece, Tome 1", "One Piece", 0),
            ],
            start=1,
        ):
            revision = Revision(
                drive_file_id=f"drive-{index}",
                fingerprint=f"drive-{index}:md5:x",
                name=f"{title}.cbz",
                path=f"Manga/{title}.cbz",
                size=10,
                status="candidate",
            )
            session.add(revision)
            session.flush()
            relative = f"library/{series}/{title}.cbz"
            full = cache_root / relative
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(b"comic-bytes")
            session.add(
                LibraryFile(
                    revision_id=revision.id,
                    title=title,
                    series=series,
                    status="ready",
                    path=relative,
                    format="cbz",
                    size=11,
                    mirrored_at=datetime.now(UTC),
                    download_count=downloads,
                )
            )
        session.commit()
    app = create_catalog_app(database, RuntimeSettings(cache_root=cache_root))
    return TestClient(app)


def test_catalog_requires_basic_auth(tmp_path: Path) -> None:
    client = build_catalog(tmp_path)
    assert client.get("/opds").status_code == 401
    assert client.get("/opds", auth=("kindle", "wrong")).status_code == 401
    assert client.get("/opds", auth=AUTH).status_code == 200


def test_disabled_catalog_returns_503(tmp_path: Path) -> None:
    client = build_catalog(tmp_path, enabled=False)
    assert client.get("/opds", auth=AUTH).status_code == 503


def test_root_feed_is_valid_atom_navigation(tmp_path: Path) -> None:
    client = build_catalog(tmp_path)
    response = client.get("/opds", auth=AUTH)
    assert "application/atom+xml" in response.headers["content-type"]
    feed = ElementTree.fromstring(response.text)
    titles = [entry.findtext(f"{ATOM}title") for entry in feed.findall(f"{ATOM}entry")]
    assert titles == ["Latest additions", "By series", "All"]


def test_series_feeds_group_and_mark_downloaded_files(tmp_path: Path) -> None:
    client = build_catalog(tmp_path)
    series = ElementTree.fromstring(client.get("/opds/series", auth=AUTH).text)
    names = [entry.findtext(f"{ATOM}title") for entry in series.findall(f"{ATOM}entry")]
    assert names == ["Naruto", "One Piece"]

    naruto = ElementTree.fromstring(client.get("/opds/series/Naruto", auth=AUTH).text)
    titles = [entry.findtext(f"{ATOM}title") for entry in naruto.findall(f"{ATOM}entry")]
    assert titles == ["Naruto, Ch. 700", "✓ Naruto, Ch. 701"]
    links = naruto.findall(f"{ATOM}entry/{ATOM}link")
    assert all(link.get("type") == "application/vnd.comicbook+zip" for link in links)


def test_download_streams_the_file_and_counts_it(tmp_path: Path) -> None:
    client = build_catalog(tmp_path)
    feed = ElementTree.fromstring(client.get("/opds/all", auth=AUTH).text)
    href = feed.find(f"{ATOM}entry/{ATOM}link").get("href")

    response = client.get(href, auth=AUTH)

    assert response.status_code == 200
    assert response.content == b"comic-bytes"
    latest = ElementTree.fromstring(client.get("/opds/latest", auth=AUTH).text)
    marked = [entry.findtext(f"{ATOM}title") for entry in latest.findall(f"{ATOM}entry")]
    assert any(title.startswith("✓ ") for title in marked)


def test_download_of_missing_or_unready_file_is_404(tmp_path: Path) -> None:
    client = build_catalog(tmp_path)
    assert client.get("/opds/download/unknown-id", auth=AUTH).status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_opds.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kindrop.opds'`.

- [ ] **Step 3: Implement kindrop/opds.py**

Create `backend/kindrop/opds.py`:

```python
"""The OPDS Catalog: the one Kindrop surface exposed to the LAN (see ADR 0004).

Serves OPDS 1.2 (Atom) feeds of ready Library Files behind mandatory HTTP
Basic Auth, and streams the files themselves. It never calls Google APIs and
its only database writes are the download-tracking fields.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from secrets import compare_digest
from urllib.parse import quote
from xml.etree import ElementTree

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import RuntimeSettings
from .database import Database
from .models import AppSettings, LibraryFile
from .services import add_event

ATOM_NS = "http://www.w3.org/2005/Atom"
OPDS_ACQUISITION_REL = "http://opds-spec.org/acquisition"
NAVIGATION_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQUISITION_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"
MIME_TYPES = {"cbz": "application/vnd.comicbook+zip", "pdf": "application/pdf"}
LATEST_LIMIT = 50

security = HTTPBasic()


def _display_title(item: LibraryFile) -> str:
    return f"✓ {item.title}" if item.download_count else item.title


def _feed(feed_id: str, title: str) -> ElementTree.Element:
    feed = ElementTree.Element("feed", xmlns=ATOM_NS)
    ElementTree.SubElement(feed, "id").text = f"urn:kindrop:{feed_id}"
    ElementTree.SubElement(feed, "title").text = title
    ElementTree.SubElement(feed, "updated").text = datetime.now(UTC).isoformat()
    return feed


def _navigation_entry(feed: ElementTree.Element, title: str, href: str) -> None:
    entry = ElementTree.SubElement(feed, "entry")
    ElementTree.SubElement(entry, "title").text = title
    ElementTree.SubElement(entry, "id").text = f"urn:kindrop:{href}"
    ElementTree.SubElement(entry, "updated").text = datetime.now(UTC).isoformat()
    ElementTree.SubElement(
        entry, "link", href=href, rel="subsection", type=NAVIGATION_TYPE
    )


def _acquisition_entry(feed: ElementTree.Element, item: LibraryFile) -> None:
    entry = ElementTree.SubElement(feed, "entry")
    ElementTree.SubElement(entry, "title").text = _display_title(item)
    ElementTree.SubElement(entry, "id").text = f"urn:kindrop:library-file:{item.id}"
    updated = item.mirrored_at or item.created_at
    ElementTree.SubElement(entry, "updated").text = updated.isoformat()
    ElementTree.SubElement(
        entry,
        "link",
        href=f"/opds/download/{item.id}",
        rel=OPDS_ACQUISITION_REL,
        type=MIME_TYPES.get(item.format or "cbz", "application/octet-stream"),
        length=str(item.size),
    )


def _atom_response(feed: ElementTree.Element, kind: str) -> Response:
    payload = ElementTree.tostring(feed, encoding="unicode", xml_declaration=True)
    return Response(content=payload, media_type=kind)


def create_catalog_app(database: Database, runtime: RuntimeSettings | None = None) -> FastAPI:
    runtime = runtime or RuntimeSettings()
    app = FastAPI(title="Kindrop Catalog", version="0.1.0")

    def session_dependency() -> Generator[Session, None, None]:
        with database.session() as session:
            yield session

    def authenticated(
        credentials: HTTPBasicCredentials = Depends(security),
        session: Session = Depends(session_dependency),
    ) -> None:
        settings = session.get(AppSettings, 1)
        if not settings or not settings.catalog_enabled or not settings.catalog_password:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The Catalog is disabled; enable it in Kindrop's Settings",
            )
        username_matches = compare_digest(
            credentials.username, settings.catalog_username or ""
        )
        password_matches = compare_digest(credentials.password, settings.catalog_password)
        if not (username_matches and password_matches):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Wrong Catalog credentials",
                headers={"WWW-Authenticate": 'Basic realm="Kindrop Catalog"'},
            )

    def ready_files(session: Session):
        return select(LibraryFile).where(LibraryFile.status == "ready")

    @app.get("/opds", dependencies=[Depends(authenticated)])
    def root() -> Response:
        feed = _feed("root", "Kindrop")
        _navigation_entry(feed, "Latest additions", "/opds/latest")
        _navigation_entry(feed, "By series", "/opds/series")
        _navigation_entry(feed, "All", "/opds/all")
        return _atom_response(feed, NAVIGATION_TYPE)

    @app.get("/opds/latest", dependencies=[Depends(authenticated)])
    def latest(session: Session = Depends(session_dependency)) -> Response:
        items = session.scalars(
            ready_files(session).order_by(LibraryFile.mirrored_at.desc()).limit(LATEST_LIMIT)
        ).all()
        feed = _feed("latest", "Latest additions")
        for item in items:
            _acquisition_entry(feed, item)
        return _atom_response(feed, ACQUISITION_TYPE)

    @app.get("/opds/series", dependencies=[Depends(authenticated)])
    def series_index(session: Session = Depends(session_dependency)) -> Response:
        names = session.scalars(
            select(LibraryFile.series)
            .where(LibraryFile.status == "ready")
            .distinct()
            .order_by(LibraryFile.series)
        ).all()
        feed = _feed("series", "By series")
        for name in names:
            _navigation_entry(feed, name, f"/opds/series/{quote(name)}")
        return _atom_response(feed, NAVIGATION_TYPE)

    @app.get("/opds/series/{series}", dependencies=[Depends(authenticated)])
    def series_feed(series: str, session: Session = Depends(session_dependency)) -> Response:
        items = session.scalars(
            ready_files(session)
            .where(LibraryFile.series == series)
            .order_by(LibraryFile.title)
        ).all()
        feed = _feed(f"series:{series}", series)
        for item in items:
            _acquisition_entry(feed, item)
        return _atom_response(feed, ACQUISITION_TYPE)

    @app.get("/opds/all", dependencies=[Depends(authenticated)])
    def all_files(session: Session = Depends(session_dependency)) -> Response:
        items = session.scalars(ready_files(session).order_by(LibraryFile.title)).all()
        feed = _feed("all", "All")
        for item in items:
            _acquisition_entry(feed, item)
        return _atom_response(feed, ACQUISITION_TYPE)

    @app.get("/opds/download/{library_file_id}", dependencies=[Depends(authenticated)])
    def download(
        library_file_id: str, session: Session = Depends(session_dependency)
    ) -> FileResponse:
        item = session.get(LibraryFile, library_file_id)
        if not item or item.status != "ready" or not item.path:
            raise HTTPException(status_code=404, detail="This Library File is not available")
        full_path = runtime.cache_root / item.path
        if not full_path.is_file():
            raise HTTPException(status_code=404, detail="This Library File is not available")
        item.download_count += 1
        item.last_downloaded_at = datetime.now(UTC)
        add_event(session, "library", item.id, "library.file_downloaded", title=item.title)
        session.commit()
        return FileResponse(
            full_path,
            media_type=MIME_TYPES.get(item.format or "cbz", "application/octet-stream"),
            filename=full_path.name,
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        database.ping()
        return {"status": "ok"}

    return app
```

Note: `ready_files` takes `session` only for symmetry — if ruff flags the unused parameter, make it a plain module-level `_ready_files()` helper without the parameter.

Create `backend/kindrop/catalog_main.py`:

```python
from .config import RuntimeSettings
from .database import Database
from .opds import create_catalog_app

runtime = RuntimeSettings()
app = create_catalog_app(Database(runtime.database_url), runtime)
```

- [ ] **Step 4: Run tests + ruff**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_opds.py -v && uv run ruff check .`
Expected: PASS. If `xml_declaration` is rejected by `ElementTree.tostring` for `encoding="unicode"` on this Python version, drop the `xml_declaration=True` argument (KOReader accepts feeds without the declaration).

- [ ] **Step 5: Commit**

```bash
git add backend/kindrop/opds.py backend/kindrop/catalog_main.py backend/tests/test_opds.py
git commit -m "feat(catalog): OPDS feeds and authenticated downloads for Library Files"
```

---

### Task 8: Web API — Catalog settings and Library status endpoints

**Files:**
- Modify: `backend/kindrop/schemas.py`
- Modify: `backend/kindrop/api.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `models.LibraryFile`, Task 1 settings columns.
- Produces:
  - `SettingsRead`/`SettingsUpdate` gain `catalog_enabled: bool`, and `SettingsRead` also `catalog_username: str | None`, `catalog_password: str | None`. Enabling the Catalog with no stored password generates `catalog_username="kindle"` and a `token_urlsafe(12)` password.
  - `GET /api/library` → `LibrarySummary { ready_count, pending_count, failed_count, total_bytes, failures: [{id, title, error}] }` (pending_count counts `pending` + `mirroring`; `removed` files are excluded everywhere).
  - `POST /api/library/{library_file_id}/retry` → 200 `{"status": "pending"}`; 409 if the file is not `failed`; 404 if unknown.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api.py`:

```python
def test_enabling_the_catalog_generates_credentials_once(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    client = TestClient(app)
    preset = {
        "kindle_profile": "KPW6",
        "reading_direction": "rtl",
        "spread_mode": "both",
        "crop_mode": "margins_and_page_numbers",
    }

    first = client.put(
        "/api/settings", json={"preset": preset, "catalog_enabled": True}
    ).json()
    assert first["catalog_enabled"] is True
    assert first["catalog_username"] == "kindle"
    assert first["catalog_password"]

    second = client.put(
        "/api/settings", json={"preset": preset, "catalog_enabled": True}
    ).json()
    assert second["catalog_password"] == first["catalog_password"]


def test_library_summary_counts_and_lists_failures(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        for index, (status_value, size) in enumerate(
            [("ready", 100), ("ready", 50), ("pending", 0), ("failed", 0), ("removed", 0)],
            start=1,
        ):
            revision = Revision(
                drive_file_id=f"drive-{index}",
                fingerprint=f"drive-{index}:md5:x",
                name=f"file-{index}.cbz",
                path=f"Manga/file-{index}.cbz",
                size=10,
                status="candidate",
            )
            session.add(revision)
            session.flush()
            session.add(
                LibraryFile(
                    revision_id=revision.id,
                    title=f"Title {index}",
                    series="Series",
                    status=status_value,
                    size=size,
                    error="boom" if status_value == "failed" else None,
                )
            )
        session.commit()

    summary = TestClient(app).get("/api/library").json()

    assert summary["ready_count"] == 2
    assert summary["pending_count"] == 1
    assert summary["failed_count"] == 1
    assert summary["total_bytes"] == 150
    assert summary["failures"][0]["error"] == "boom"


def test_retrying_a_failed_library_file_requeues_it(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        revision = Revision(
            drive_file_id="drive-1",
            fingerprint="drive-1:md5:x",
            name="file.cbz",
            path="Manga/file.cbz",
            size=10,
            status="candidate",
        )
        session.add(revision)
        session.flush()
        item = LibraryFile(
            revision_id=revision.id,
            title="Title",
            series="Series",
            status="failed",
            error="boom",
        )
        session.add(item)
        session.commit()
        item_id = item.id

    client = TestClient(app)
    assert client.post(f"/api/library/{item_id}/retry").status_code == 200
    with database.session() as session:
        assert session.get(LibraryFile, item_id).status == "pending"
    assert client.post(f"/api/library/{item_id}/retry").status_code == 409
    assert client.post("/api/library/unknown/retry").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest tests/test_api.py -k "catalog or library" -v`
Expected: FAIL (422 on the PUT — unknown field is fine but response lacks catalog keys; 404 on /api/library).

- [ ] **Step 3: Implement**

In `backend/kindrop/schemas.py`:

```python
class LibraryFailureRead(BaseModel):
    id: str
    title: str
    error: str | None


class LibrarySummary(BaseModel):
    ready_count: int
    pending_count: int
    failed_count: int
    total_bytes: int
    failures: list[LibraryFailureRead]
```

Extend `SettingsRead` with:

```python
    catalog_enabled: bool = False
    catalog_username: str | None = None
    catalog_password: str | None = None
```

Extend `SettingsUpdate` with:

```python
    catalog_enabled: bool = False
```

In `backend/kindrop/api.py`:

- Imports: add `LibraryFile` to the models import block, `LibrarySummary` to the schemas import, and `from secrets import token_urlsafe` at the top.
- In `read_settings` and `update_settings`, include the new fields in the returned `SettingsRead` (locate how they currently build the response — they construct `SettingsRead` from the `AppSettings` row; add the three fields there).
- In `update_settings`, before saving:

```python
        if payload.catalog_enabled and not settings.catalog_password:
            settings.catalog_username = "kindle"
            settings.catalog_password = token_urlsafe(12)
        settings.catalog_enabled = payload.catalog_enabled
```

- New endpoints (place after the deliveries endpoints):

```python
    @app.get("/api/library", response_model=LibrarySummary)
    def library_summary(session: Session = Depends(session_dependency)) -> LibrarySummary:
        files = session.scalars(
            select(LibraryFile).where(LibraryFile.status != "removed")
        ).all()
        failures = [
            {"id": item.id, "title": item.title, "error": item.error}
            for item in files
            if item.status == "failed"
        ]
        return LibrarySummary(
            ready_count=sum(1 for item in files if item.status == "ready"),
            pending_count=sum(1 for item in files if item.status in {"pending", "mirroring"}),
            failed_count=len(failures),
            total_bytes=sum(item.size for item in files if item.status == "ready"),
            failures=failures,
        )

    @app.post("/api/library/{library_file_id}/retry")
    def retry_library_file(
        library_file_id: str, session: Session = Depends(session_dependency)
    ) -> dict[str, str]:
        item = session.get(LibraryFile, library_file_id)
        if not item:
            raise HTTPException(status_code=404, detail="This Library File does not exist")
        if item.status != "failed":
            raise HTTPException(
                status_code=409, detail="Only a failed Library File can be retried"
            )
        item.status = "pending"
        item.error = None
        add_event(session, "library", item.id, "library.file_requeued")
        session.commit()
        return {"status": "pending"}
```

- [ ] **Step 4: Run the suite + ruff**

Run: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest && uv run ruff check .`
Expected: PASS (existing settings tests must still pass — `catalog_enabled` defaults to False in `SettingsUpdate`, so old payloads stay valid; note this means a settings save from a stale client disables the Catalog, which is acceptable for a single-user app).

- [ ] **Step 5: Commit**

```bash
git add backend/kindrop/schemas.py backend/kindrop/api.py backend/tests/test_api.py
git commit -m "feat(api): Catalog credentials and Library status endpoints"
```

---

### Task 9: Compose service, ADR 0004, CONTEXT.md, CLAUDE.md

**Files:**
- Modify: `compose.yaml`
- Create: `docs/adr/0004-lan-opds-catalog.md`
- Modify: `CONTEXT.md`
- Modify: `CLAUDE.md`

No automated test covers compose; verification is by review plus (optionally, if Docker is available) `docker compose config`.

- [ ] **Step 1: Add the catalog service to compose.yaml**

In `compose.yaml`, after the `worker` service:

```yaml
  catalog:
    <<: *kindrop-service
    command: ["uvicorn", "kindrop.catalog_main:app", "--host", "0.0.0.0", "--port", "8788"]
    ports:
      - "8788:8788"
    depends_on:
      web:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8788/health', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 15s
```

(The `8788:8788` mapping — without a `127.0.0.1:` prefix — is the deliberate LAN exposure; ADR 0004 documents it.)

- [ ] **Step 2: Validate the compose file**

Run: `docker compose config --quiet && echo OK`
Expected: `OK`. If Docker is unavailable in this environment, skip and note it in the commit message.

- [ ] **Step 3: Write ADR 0004**

Create `docs/adr/0004-lan-opds-catalog.md`:

```markdown
# Expose the OPDS Catalog on the LAN

KOReader on the Kindle reads CBZ and PDF natively but cannot reach Google Drive; its
remote sources are OPDS catalogs. Kindrop therefore mirrors the Source Folder into
Library Files and serves them as an OPDS Catalog from a dedicated `catalog` compose
service on port 8788, the only port published beyond localhost.

This amends ADR 0001: the web UI and API stay on `127.0.0.1:8787`, while the catalog
service is reachable from the LAN. To keep the exposure narrow, the catalog service
requires HTTP Basic Auth on every route (credentials generated by Kindrop and shown in
Settings), never calls Google APIs, never touches OAuth tokens, and its only database
writes are the download-tracking fields of Library Files. Anything wider (WAN exposure,
HTTPS, multiple users) needs a new ADR.
```

- [ ] **Step 4: Add the vocabulary to CONTEXT.md**

Append to the Language section of `CONTEXT.md`:

```markdown
**Catalog**:
The OPDS catalog exposed on the LAN that KOReader browses to fetch Library Files.
_Avoid_: server, share

**Library File**:
The local, KOReader-ready copy of a Drive File Revision: CBZ and PDF kept as-is, CBR
repackaged into CBZ without image reprocessing.
_Avoid_: cache, download

**Mirroring**:
The automatic materialization of Library Files after a Scan.
_Avoid_: sync, replication
```

- [ ] **Step 5: Update CLAUDE.md**

In `CLAUDE.md`:
- In "What Kindrop is", after the first sentence, add: `It also mirrors the Source Folder into local Library Files and serves them as an authenticated OPDS Catalog on LAN port 8788 for KOReader (see ADR 0004); the KCC/email pipeline remains as an optional mode.`
- In "Architecture", add a third container bullet: `- **catalog** — `kindrop/catalog_main.py` → `opds.create_catalog_app()` (`opds.py`), the OPDS Catalog served to the LAN on 8788 behind Basic Auth; the only service exposed beyond localhost (ADR 0004).`
- In "Safety boundaries", amend the LAN bullet to: `- Local single-user: the web UI/API stays on 127.0.0.1; only the catalog service is LAN-exposed, read-only + Basic Auth (ADR 0004). Anything wider needs a new ADR.`

- [ ] **Step 6: Commit**

```bash
git add compose.yaml docs/adr/0004-lan-opds-catalog.md CONTEXT.md CLAUDE.md
git commit -m "feat(catalog): LAN-exposed catalog service, ADR 0004 and vocabulary"
```

---

### Task 10: Frontend — Catalog settings section and Library status card

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/query.ts`
- Modify: `frontend/src/hooks/useLiveEvents.ts`
- Modify: `frontend/src/pages/DashboardPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Test: `frontend/src/test/DashboardPage.test.tsx`, `frontend/src/test/api.test.ts`

Before coding, read `frontend/src/pages/SettingsPage.tsx` and `DashboardPage.tsx` fully to match their section markup (`settings-section`, `section-heading`, existing CSS classes — reuse them, do not invent new stylesheets; if a small style is genuinely needed, follow where existing page styles live).

- [ ] **Step 1: Extend types, api client, query keys, live events**

`frontend/src/types.ts` — extend the `Settings` interface with:

```ts
  catalog_enabled: boolean;
  catalog_username: string | null;
  catalog_password: string | null;
```

and add:

```ts
export interface LibraryFailure {
  id: string;
  title: string;
  error: string | null;
}

export interface LibrarySummary {
  ready_count: number;
  pending_count: number;
  failed_count: number;
  total_bytes: number;
  failures: LibraryFailure[];
}
```

`frontend/src/api.ts` — import `LibrarySummary` from `./types` and add to the `api` object:

```ts
  library: () => request<LibrarySummary>("/api/library"),
  retryLibraryFile: (id: string) =>
    request(`/api/library/${encodeURIComponent(id)}/retry`, { method: "POST" }),
```

`frontend/src/query.ts` — add `library: ["library"] as const,` to `queryKeys`.

`frontend/src/hooks/useLiveEvents.ts` — add alongside the existing invalidations:

```ts
      void queryClient.invalidateQueries({ queryKey: queryKeys.library });
```

- [ ] **Step 2: Write the failing Dashboard test**

In `frontend/src/test/DashboardPage.test.tsx`, add `"/api/library"` to the `payloads` record:

```ts
  "/api/library": {
    ready_count: 12,
    pending_count: 2,
    failed_count: 1,
    total_bytes: 314572800,
    failures: [{ id: "lf-1", title: "Broken, Ch. 1", error: "boom" }],
  },
```

and a test (match the file's existing render helper/wrapper conventions):

```ts
  it("shows the Library mirror status", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path in payloads) return Response.json(payloads[path]);
      throw new Error(`Unexpected request: ${path}`);
    });
    renderDashboard();

    expect(await screen.findByText(/12 ready/i)).toBeInTheDocument();
    expect(screen.getByText(/300 MB/)).toBeInTheDocument();
    expect(screen.getByText(/2 mirroring/i)).toBeInTheDocument();
    expect(screen.getByText(/1 failed/i)).toBeInTheDocument();
  });
```

(Adapt `renderDashboard()` to whatever helper the file actually uses — read it first. The existing tests must keep passing, so every existing mocked fetch table also needs the `/api/library` entry once the page queries it.)

- [ ] **Step 3: Run to verify failure**

Run from `frontend/`: `npx vitest run src/test/DashboardPage.test.tsx`
Expected: new test FAILS (no library card rendered yet); pre-existing tests may also fail on the unexpected `/api/library` request — that is the signal to update their fetch tables in the same commit.

- [ ] **Step 4: Implement the Dashboard card**

In `DashboardPage.tsx`, add a query:

```tsx
  const library = useQuery({ queryKey: queryKeys.library, queryFn: api.library });
```

and render a card inside the existing workbench section (reuse `section-heading` / existing card classes), e.g.:

```tsx
      {library.data ? (
        <section className="workbench" aria-label="Library mirror">
          <div className="section-heading">
            <h2>Library mirror</h2>
          </div>
          <p>
            {library.data.ready_count} ready · {formatBytes(library.data.total_bytes)}
            {library.data.pending_count > 0 && <> · {library.data.pending_count} mirroring</>}
            {library.data.failed_count > 0 && <> · {library.data.failed_count} failed</>}
          </p>
        </section>
      ) : null}
```

(`formatBytes` is exported from `../api`.) Exact markup should follow the page's existing card patterns — adjust while keeping the visible strings the test asserts (`12 ready`, `300 MB`, `2 mirroring`, `1 failed`).

- [ ] **Step 5: Implement the Settings Catalog section**

In `SettingsPage.tsx`, add a fourth `settings-section` (number `04`, title `Open the Catalog`) containing:
- a checkbox bound to `catalog_enabled` in the page's existing settings form state (the page already builds a `Settings` object for `api.saveSettings`; include `catalog_enabled` in the PUT payload),
- when `settings.catalog_enabled` and credentials exist, a read-only display of `catalog_username`, `catalog_password`, and the text: `In KOReader: Search > OPDS catalog > add http://<your-Mac-IP>:8788/opds with these credentials.`

Follow the exact JSX structure of sections 01–03 (`settings-section__number`, `__intro`, `__body`). The PUT payload type must now include `catalog_enabled` (SettingsUpdate on the backend defaults it to false, so always send the current value to avoid silently disabling the Catalog).

- [ ] **Step 6: Run the frontend gates**

Run from `frontend/`: `npm test && npm run typecheck && npm run lint && npm run build`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(ui): Catalog settings and Library mirror status"
```

---

### Task 11: Final verification sweep

- [ ] **Step 1: Full backend suite + lint**

Run from `backend/`: `UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest && uv run ruff check .`
Expected: PASS.

- [ ] **Step 2: Full frontend suite**

Run from `frontend/`: `npm test && npm run typecheck && npm run lint && npm run build`
Expected: PASS.

- [ ] **Step 3: Spec cross-check**

Re-read `docs/superpowers/specs/2026-08-25-koreader-opds-catalog-design.md` section by section and confirm each maps to shipped code (vocabulary → CONTEXT.md; mirroring → Tasks 4–6; catalog service + auth → Tasks 7 & 9; data model → Task 1; OPDS feeds + ✓ marker → Task 7; reconciliation → Task 5; UI → Task 10; ADR → Task 9; tests → each task). Fix any gap before declaring done.

- [ ] **Step 4: Commit any leftovers and report**

Report the result honestly: which gates ran, what passed. Do not claim Kindle-side behavior (KOReader rendering) was verified — it cannot be from this environment; the user validates on the device.
