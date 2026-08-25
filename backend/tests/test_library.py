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
