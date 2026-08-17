import hashlib
from pathlib import Path
from zipfile import ZipFile

from kindrop.database import Database
from kindrop.domain import ConversionPreset
from kindrop.models import AppSettings, Batch, Candidate, Job, Revision, Scan
from kindrop.services import DriveComic, JobProcessor, ScanProcessor


class FakeDrive:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.download_count = 0

    def walk_comics(self, _folder_id: str) -> list[DriveComic]:
        payload = self.source.read_bytes()
        return [
            DriveComic(
                file_id="drive-1",
                name="volume.cbz",
                path="Series/volume.cbz",
                size=len(payload),
                checksum=hashlib.md5(payload).hexdigest(),  # noqa: S324 - Drive supplies MD5
                modified_time="2026-08-16T10:00:00Z",
            )
        ]

    def download(self, _file_id: str, destination: Path) -> None:
        self.download_count += 1
        destination.write_bytes(self.source.read_bytes())


class FakeKcc:
    def run(
        self, source: Path, output_directory: Path, preset: ConversionPreset, title: str
    ) -> list[Path]:
        assert source.exists()
        assert preset.reading_direction.value == "rtl"
        output_directory.mkdir(parents=True, exist_ok=True)
        first = output_directory / f"{title} - Part 1.epub"
        second = output_directory / f"{title} - Part 2.epub"
        first.write_bytes(b"epub-one")
        second.write_bytes(b"epub-two")
        return [first, second]


class FakeGmail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send_epub(self, recipient: str, subject: str, artifact: Path) -> str:
        self.sent.append((recipient, subject, artifact.name))
        return f"gmail-{len(self.sent)}"


def make_cbz(path: Path) -> None:
    with ZipFile(path, "w") as comic:
        comic.writestr("ComicInfo.xml", "<ComicInfo><Title>Volume Seven</Title></ComicInfo>")
        comic.writestr("001.jpg", b"image")


def test_scan_yields_to_dispatches_between_each_inspected_item(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    payload = archive.read_bytes()
    checksum = hashlib.md5(payload).hexdigest()  # noqa: S324 - Drive supplies MD5

    class TwoComicDrive(FakeDrive):
        def walk_comics(self, _folder_id: str) -> list[DriveComic]:
            return [
                DriveComic(
                    file_id=f"drive-{index}",
                    name=f"volume-{index}.cbz",
                    path=f"Series/volume-{index}.cbz",
                    size=len(payload),
                    checksum=checksum,
                    modified_time="2026-08-16T10:00:00Z",
                )
                for index in (1, 2)
            ]

    with database.session() as session:
        session.add(AppSettings(id=1, source_folder_id="root-folder"))
        scan = Scan()
        session.add(scan)
        session.commit()
        scan_id = scan.id

    yields: list[int] = []
    processor = ScanProcessor(
        database,
        TwoComicDrive(archive),
        tmp_path / "cache",
        between_items=lambda: yields.append(1),
    )
    processor.run(scan_id)

    assert len(yields) == 2, "the scan must hand control back before each item"


def test_scan_downloads_new_revisions_and_reuses_history(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    drive = FakeDrive(archive)
    cache = tmp_path / "cache"
    with database.session() as session:
        session.add(AppSettings(id=1, source_folder_id="root-folder"))
        first_scan = Scan()
        session.add(first_scan)
        session.commit()
        first_scan_id = first_scan.id

    ScanProcessor(database, drive, cache).run(first_scan_id)

    with database.session() as session:
        candidate = session.query(Candidate).one()
        assert candidate.status == "ready"
        assert candidate.resolved_title == "Volume Seven"
        assert Path(candidate.cache_path).exists()
        second_scan = Scan()
        session.add(second_scan)
        session.commit()
        second_scan_id = second_scan.id

    ScanProcessor(database, drive, cache).run(second_scan_id)

    with database.session() as session:
        assert session.query(Candidate).count() == 1
        assert session.get(Scan, second_scan_id).discovered_count == 0
    assert drive.download_count == 1


def test_job_converts_sends_each_part_and_purges_temporary_files(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    gmail = FakeGmail()
    with database.session() as session:
        session.add(
            AppSettings(
                id=1,
                kindle_email="reader_123@kindle.com",
                preset=ConversionPreset().model_dump(mode="json"),
            )
        )
        revision = Revision(
            drive_file_id="drive-1",
            fingerprint="drive-1:md5:abc",
            name="volume.cbz",
            path="Series/volume.cbz",
            size=archive.stat().st_size,
            status="candidate",
        )
        session.add(revision)
        session.flush()
        candidate = Candidate(
            revision_id=revision.id,
            status="queued",
            resolved_title="Volume Seven",
            cache_path=str(archive),
        )
        session.add(candidate)
        session.flush()
        batch = Batch(preset=ConversionPreset().model_dump(mode="json"))
        session.add(batch)
        session.flush()
        job = Job(
            batch_id=batch.id,
            candidate_id=candidate.id,
            preset=batch.preset,
            title="Volume Seven",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    JobProcessor(
        database=database,
        drive=FakeDrive(archive),
        kcc=FakeKcc(),
        gmail=gmail,
        cache_root=tmp_path / "jobs",
        wait_between_deliveries=lambda: None,
    ).run(job_id)

    with database.session() as session:
        job = session.get(Job, job_id)
        candidate = session.get(Candidate, job.candidate_id)
        revision = session.get(Revision, candidate.revision_id)
        assert job.status == "sent"
        assert [item.delivery.status for item in job.artifacts] == [
            "sent_unconfirmed",
            "sent_unconfirmed",
        ]
        assert [
            [attempt.status for attempt in item.delivery.attempts] for item in job.artifacts
        ] == [
            ["sent"],
            ["sent"],
        ]
        assert candidate.status == "sent"
        assert revision.status == "sent"
        assert candidate.cache_path is None
    assert len(gmail.sent) == 2
    assert gmail.sent[0][1] == "Volume Seven — Part 1/2"
    assert gmail.sent[0][2] == "Volume Seven - Part 1 of 2.epub"
    assert gmail.sent[1][2] == "Volume Seven - Part 2 of 2.epub"
    assert not archive.exists()
