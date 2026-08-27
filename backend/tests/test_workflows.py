import hashlib
from pathlib import Path
from zipfile import ZipFile

from kindrop.database import Database
from kindrop.domain import ConversionPreset
from kindrop.kindle_ssh import KindleDeliveryReceipt, KindleProbe
from kindrop.models import AppSettings, Batch, Candidate, Job, Revision, Scan
from kindrop.services import (
    MAX_EPUB_BYTES,
    AmbiguousSendError,
    DriveComic,
    JobProcessor,
    PermanentSendError,
    ScanProcessor,
    SshBatchDeliverer,
    TransientSendError,
)


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
        self,
        source: Path,
        output_directory: Path,
        preset: ConversionPreset,
        title: str,
        *,
        target_size_mb: int = 19,
        output_format: str = "EPUB",
    ) -> list[Path]:
        assert source.exists()
        assert preset.reading_direction.value == "rtl"
        assert target_size_mb in (18, 19)
        assert output_format == "EPUB"
        output_directory.mkdir(parents=True, exist_ok=True)
        first = output_directory / f"{title} - Part 1.epub"
        second = output_directory / f"{title} - Part 2.epub"
        first.write_bytes(b"epub-one")
        second.write_bytes(b"epub-two")
        return [first, second]


class FakeGmail:
    """Scriptable Gmail fake.

    `issues` yields one outcome per send call (None succeeds, an exception is raised);
    `sent_folder` yields one outcome per Sent-folder probe (a message id, None, or an
    exception). Both default to success / not found once exhausted.
    """

    def __init__(
        self,
        issues: list[Exception | None] | None = None,
        sent_folder: list[str | None | Exception] | None = None,
    ) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.message_ids: list[str] = []
        self.probes: list[str] = []
        self.issues = list(issues or [])
        self.sent_folder = list(sent_folder or [])

    def send_epub(
        self, recipient: str, subject: str, artifact: Path, *, rfc822_message_id: str
    ) -> str:
        self.message_ids.append(rfc822_message_id)
        if self.issues:
            issue = self.issues.pop(0)
            if issue is not None:
                raise issue
        self.sent.append((recipient, subject, artifact.name))
        return f"gmail-{len(self.sent)}"

    def find_sent_message(self, rfc822_message_id: str) -> str | None:
        self.probes.append(rfc822_message_id)
        outcome = self.sent_folder.pop(0) if self.sent_folder else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_cbz(path: Path) -> None:
    with ZipFile(path, "w") as comic:
        comic.writestr("ComicInfo.xml", "<ComicInfo><Title>Volume Seven</Title></ComicInfo>")
        comic.writestr("001.jpg", b"image")


def make_ready_job(database: Database, archive: Path, *, transport: str = "gmail") -> str:
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
            delivery_transport=transport,
        )
        session.add(job)
        session.commit()
        return job.id


class FakeKindle:
    def __init__(self, free_bytes: int) -> None:
        self.free_bytes = free_bytes
        self.delivered: list[tuple[str, str, bool]] = []

    def probe(self) -> KindleProbe:
        return KindleProbe(reachable=True, free_bytes=self.free_bytes)

    def deliver(self, local_path: Path, relative_path: str, *, replace: bool = False):
        self.delivered.append((local_path.name, relative_path, replace))
        return KindleDeliveryReceipt(
            remote_path=f"/mnt/us/documents/KOReader/Kindrop/{relative_path}",
            sha256="a" * 64,
            size_bytes=local_path.stat().st_size,
        )


def run_job(
    tmp_path: Path, database: Database, archive: Path, gmail: FakeGmail, job_id: str
) -> list[float]:
    slept: list[float] = []
    JobProcessor(
        database=database,
        drive=FakeDrive(archive),
        kcc=FakeKcc(),
        gmail=gmail,
        cache_root=tmp_path / "jobs",
        wait_between_deliveries=lambda: None,
        sleep=slept.append,
    ).run(job_id)
    return slept


def first_delivery(session, job_id: str):
    job = session.get(Job, job_id)
    artifacts = sorted(job.artifacts, key=lambda item: item.part_number)
    return job, artifacts[0].delivery


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


def test_scan_pauses_at_the_next_item_and_resumes_where_it_stopped(tmp_path: Path) -> None:
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

    def pause_after_first_item() -> None:
        with database.session() as session:
            scan = session.get(Scan, scan_id)
            if scan.processed_count == 1:
                scan.pause_requested = True
                session.commit()

    drive = TwoComicDrive(archive)
    processor = ScanProcessor(
        database, drive, tmp_path / "cache", between_items=pause_after_first_item
    )
    processor.run(scan_id)

    with database.session() as session:
        scan = session.get(Scan, scan_id)
        assert scan.status == "paused"
        assert scan.pause_requested is False
        assert scan.processed_count == 1
        assert scan.completed_at is None
    assert drive.download_count == 1

    with database.session() as session:
        scan = session.get(Scan, scan_id)
        scan.status = "queued"
        session.commit()

    ScanProcessor(database, drive, tmp_path / "cache").run(scan_id)

    with database.session() as session:
        scan = session.get(Scan, scan_id)
        assert scan.status == "completed"
        assert scan.processed_count == 2
        assert scan.discovered_count == 2
        assert scan.progress == 100
        assert session.query(Candidate).count() == 2
    assert drive.download_count == 2


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


def test_cancelled_job_is_not_processed(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    with database.session() as session:
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
            status="ready",
            resolved_title="Volume Seven",
        )
        session.add(candidate)
        session.flush()
        batch = Batch(preset=ConversionPreset().model_dump(mode="json"))
        session.add(batch)
        session.flush()
        job = Job(
            batch_id=batch.id,
            candidate_id=candidate.id,
            status="cancelled",
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
        gmail=FakeGmail(),
        cache_root=tmp_path / "jobs",
        wait_between_deliveries=lambda: None,
    ).run(job_id)

    with database.session() as session:
        job = session.get(Job, job_id)
        assert job.status == "cancelled"
        assert job.started_at is None
        assert job.artifacts == []


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
            delivery_transport="gmail",
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


def test_ssh_job_prepares_one_cbz_then_copies_it_with_verified_transport(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    job_id = make_ready_job(database, archive, transport="ssh")
    gmail = FakeGmail()

    class KoreaderKcc:
        def run(
            self,
            source: Path,
            output_directory: Path,
            preset: ConversionPreset,
            title: str,
            *,
            output_format: str,
            target_size_mb: int | None = None,
        ) -> list[Path]:
            assert source.exists()
            assert preset.kindle_profile == "KPW"
            assert output_format == "CBZ"
            assert target_size_mb is None
            output_directory.mkdir(parents=True, exist_ok=True)
            artifact = output_directory / f"{title}.cbz"
            artifact.write_bytes(b"optimized")
            return [artifact]

    JobProcessor(
        database=database,
        drive=FakeDrive(archive),
        kcc=KoreaderKcc(),
        gmail=gmail,
        cache_root=tmp_path / "cache",
        wait_between_deliveries=lambda: None,
    ).run(job_id)

    with database.session() as session:
        job = session.get(Job, job_id)
        batch_id = job.batch_id
        assert job.status == "ready_to_deliver"
        assert job.artifacts[0].filename.endswith(".cbz")
        assert job.artifacts[0].delivery.transport == "ssh"
        job.error = "stale transport failure"
        session.commit()
    assert gmail.sent == []

    kindle = FakeKindle(free_bytes=500 * 1024 * 1024)
    SshBatchDeliverer(database, transport_for=lambda _settings: kindle).run(batch_id)

    with database.session() as session:
        job = session.get(Job, job_id)
        delivery = job.artifacts[0].delivery
        assert job.status == "copied_to_kindle"
        assert job.error is None
        assert delivery.status == "copied_to_kindle"
        assert delivery.remote_sha256 == "a" * 64
        assert delivery.remote_path.endswith("/Volume Seven.cbz")
    assert kindle.delivered == [("Volume Seven.cbz", "_Unsorted/Volume Seven.cbz", False)]
    assert not archive.exists()


def test_ssh_batch_waits_without_copying_when_reserve_would_be_breached(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    job_id = make_ready_job(database, archive, transport="ssh")

    class SmallKcc:
        def run(self, source, output_directory, preset, title, **_kwargs):
            output_directory.mkdir(parents=True, exist_ok=True)
            artifact = output_directory / f"{title}.cbz"
            artifact.write_bytes(b"optimized")
            return [artifact]

    JobProcessor(
        database=database,
        drive=FakeDrive(archive),
        kcc=SmallKcc(),
        gmail=FakeGmail(),
        cache_root=tmp_path / "cache",
        wait_between_deliveries=lambda: None,
    ).run(job_id)
    with database.session() as session:
        batch_id = session.get(Job, job_id).batch_id

    kindle = FakeKindle(free_bytes=100 * 1024 * 1024)
    SshBatchDeliverer(database, transport_for=lambda _settings: kindle).run(batch_id)

    with database.session() as session:
        job = session.get(Job, job_id)
        assert job.status == "waiting_for_space"
        assert job.artifacts[0].delivery.status == "waiting_for_space"
    assert kindle.delivered == []


def test_job_retries_an_oversized_artifact_with_more_kcc_headroom(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    job_id = make_ready_job(database, archive)
    gmail = FakeGmail()

    class RetryingKcc:
        def __init__(self) -> None:
            self.targets: list[int] = []

        def run(
            self,
            source: Path,
            output_directory: Path,
            preset: ConversionPreset,
            title: str,
            *,
            target_size_mb: int,
            output_format: str = "EPUB",
        ) -> list[Path]:
            assert source.exists()
            assert output_format == "EPUB"
            if self.targets:
                assert not output_directory.exists(), "the oversized attempt must be discarded"
            self.targets.append(target_size_mb)
            output_directory.mkdir(parents=True)
            artifact = output_directory / f"{title}.epub"
            if target_size_mb == 19:
                with artifact.open("wb") as handle:
                    handle.truncate(MAX_EPUB_BYTES + 1)
            else:
                artifact.write_bytes(b"epub")
            return [artifact]

    kcc = RetryingKcc()
    JobProcessor(
        database=database,
        drive=FakeDrive(archive),
        kcc=kcc,
        gmail=gmail,
        cache_root=tmp_path / "cache",
        wait_between_deliveries=lambda: None,
    ).run(job_id)

    with database.session() as session:
        job = session.get(Job, job_id)
        assert job.status == "sent"
        assert [artifact.size for artifact in job.artifacts] == [4]
    assert kcc.targets == [19, 18]
    assert len(gmail.sent) == 1


def test_job_retains_the_final_oversized_artifact_with_retry_diagnostics(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    job_id = make_ready_job(database, archive)
    gmail = FakeGmail()

    class OversizedKcc:
        def __init__(self) -> None:
            self.targets: list[int] = []
            self.paths: list[Path] = []

        def run(
            self,
            source: Path,
            output_directory: Path,
            preset: ConversionPreset,
            title: str,
            *,
            target_size_mb: int,
            output_format: str = "EPUB",
        ) -> list[Path]:
            assert source.exists()
            assert output_format == "EPUB"
            self.targets.append(target_size_mb)
            output_directory.mkdir(parents=True)
            artifact = output_directory / f"{title}-{target_size_mb}.epub"
            with artifact.open("wb") as handle:
                handle.truncate(MAX_EPUB_BYTES + 1)
            self.paths.append(artifact)
            return [artifact]

    kcc = OversizedKcc()
    JobProcessor(
        database=database,
        drive=FakeDrive(archive),
        kcc=kcc,
        gmail=gmail,
        cache_root=tmp_path / "cache",
        wait_between_deliveries=lambda: None,
    ).run(job_id)

    with database.session() as session:
        job = session.get(Job, job_id)
        assert job.status == "failed"
        assert job.artifacts == []
        assert "20,971,521 bytes" in job.error
        assert "target sizes 19 MB and 18 MB" in job.error
    assert kcc.targets == [19, 18]
    assert not kcc.paths[0].exists()
    assert kcc.paths[1].exists()
    assert gmail.sent == []


def test_merged_job_builds_one_volume_and_marks_every_chapter_sent(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    gmail = FakeGmail()
    chapters: list[Path] = []
    for prefix in ("008", "009"):
        archive = tmp_path / f"{prefix} - Volume 02.cbz"
        with ZipFile(archive, "w") as chapter:
            chapter.writestr(f"{prefix}-01.jpg", b"page")
        chapters.append(archive)

    class CapturingKcc(FakeKcc):
        def __init__(self) -> None:
            self.sources: list[tuple[str, list[str]]] = []

        def run(
            self,
            source: Path,
            output_directory: Path,
            preset: ConversionPreset,
            title: str,
            *,
            target_size_mb: int = 19,
            output_format: str = "EPUB",
        ) -> list[Path]:
            with ZipFile(source) as merged:
                self.sources.append((source.name, merged.namelist()))
            return super().run(
                source,
                output_directory,
                preset,
                title,
                target_size_mb=target_size_mb,
                output_format=output_format,
            )

    with database.session() as session:
        session.add(
            AppSettings(
                id=1,
                kindle_email="reader_123@kindle.com",
                preset=ConversionPreset().model_dump(mode="json"),
            )
        )
        candidate_ids: list[str] = []
        for archive in chapters:
            revision = Revision(
                drive_file_id=f"drive-{archive.stem}",
                fingerprint=f"fp-{archive.stem}",
                name=archive.name,
                path=f"Manga/{archive.name}",
                size=archive.stat().st_size,
                status="candidate",
            )
            session.add(revision)
            session.flush()
            candidate = Candidate(
                revision_id=revision.id,
                status="queued",
                resolved_title=archive.stem,
                cache_path=str(archive),
            )
            session.add(candidate)
            session.flush()
            candidate_ids.append(candidate.id)
        batch = Batch(preset=ConversionPreset().model_dump(mode="json"))
        session.add(batch)
        session.flush()
        job = Job(
            batch_id=batch.id,
            candidate_id=candidate_ids[0],
            preset=batch.preset,
            title="Naruto, Tome 02",
            merged_candidate_ids=candidate_ids,
            delivery_transport="gmail",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    kcc = CapturingKcc()
    JobProcessor(
        database=database,
        drive=FakeDrive(chapters[0]),
        kcc=kcc,
        gmail=gmail,
        cache_root=tmp_path / "work",
        wait_between_deliveries=lambda: None,
    ).run(job_id)

    assert kcc.sources == [("volume.cbz", ["001/008-01.jpg", "002/009-01.jpg"])]
    with database.session() as session:
        job = session.get(Job, job_id)
        assert job.status == "sent"
        for candidate in session.query(Candidate).all():
            assert candidate.status == "sent"
            assert candidate.cache_path is None
            assert candidate.revision.status == "sent"
    assert [entry[1] for entry in gmail.sent] == [
        "Naruto, Tome 02 — Part 1/2",
        "Naruto, Tome 02 — Part 2/2",
    ]
    assert not (tmp_path / "work" / "sources" / job_id).exists()
    assert all(not archive.exists() for archive in chapters)


def test_ambiguous_send_recovers_when_the_message_is_in_the_sent_folder(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    gmail = FakeGmail(
        issues=[AmbiguousSendError("no response"), None],
        sent_folder=["gmail-recovered"],
    )
    job_id = make_ready_job(database, archive)

    slept = run_job(tmp_path, database, archive, gmail, job_id)

    with database.session() as session:
        job, delivery = first_delivery(session, job_id)
        assert job.status == "sent"
        assert delivery.status == "sent_unconfirmed"
        assert delivery.gmail_message_id == "gmail-recovered"
        assert delivery.error_detail is None
        assert [attempt.status for attempt in delivery.attempts] == ["sent"]
    assert gmail.probes == [gmail.message_ids[0]]
    assert slept == [60.0]
    assert len(gmail.sent) == 1, "the recovered part must not be resent"


def test_ambiguous_send_missing_from_sent_folder_is_resent(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    gmail = FakeGmail(issues=[AmbiguousSendError("no response"), None, None])
    job_id = make_ready_job(database, archive)

    run_job(tmp_path, database, archive, gmail, job_id)

    with database.session() as session:
        job, delivery = first_delivery(session, job_id)
        assert job.status == "sent"
        assert delivery.status == "sent_unconfirmed"
        assert delivery.gmail_message_id == "gmail-1"
        assert [attempt.status for attempt in delivery.attempts] == ["unverified", "sent"]
    assert len(gmail.probes) == 3, "every probe must run before deciding to resend"
    assert gmail.message_ids[0] != gmail.message_ids[1], "a resend needs a fresh Message-ID"


def test_never_verified_send_fails_the_delivery_and_keeps_the_files(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    gmail = FakeGmail(issues=[AmbiguousSendError("no response")] * 3)
    job_id = make_ready_job(database, archive)

    run_job(tmp_path, database, archive, gmail, job_id)

    with database.session() as session:
        job, delivery = first_delivery(session, job_id)
        assert job.status == "failed"
        assert delivery.status == "failed"
        assert "Sent folder" in delivery.error_detail
        assert [attempt.status for attempt in delivery.attempts] == ["unverified"] * 3
        candidate = session.get(Candidate, job.candidate_id)
        assert candidate.cache_path is not None
        artifact_paths = [Path(item.path) for item in job.artifacts]
    assert archive.exists(), "the source must survive a failed delivery"
    assert all(path.exists() for path in artifact_paths), "EPUBs must survive for a resend"
    assert len(gmail.probes) == 9


def test_permanent_send_error_fails_immediately_without_resend(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    gmail = FakeGmail(issues=[PermanentSendError("Gmail rejected the send with HTTP 400")])
    job_id = make_ready_job(database, archive)

    run_job(tmp_path, database, archive, gmail, job_id)

    with database.session() as session:
        job, delivery = first_delivery(session, job_id)
        assert job.status == "failed"
        assert delivery.status == "failed"
        assert "HTTP 400" in delivery.error_detail
        assert [attempt.status for attempt in delivery.attempts] == ["failed"]
    assert len(gmail.message_ids) == 1, "a permanent rejection must not be retried"
    assert gmail.probes == []


def test_exhausted_throttling_fails_the_delivery(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    gmail = FakeGmail(issues=[TransientSendError("throttled")] * 3)
    job_id = make_ready_job(database, archive)

    run_job(tmp_path, database, archive, gmail, job_id)

    with database.session() as session:
        job, delivery = first_delivery(session, job_id)
        assert job.status == "failed"
        assert delivery.status == "failed"
        assert "throttling" in delivery.error_detail
        assert [attempt.status for attempt in delivery.attempts] == ["transient_failed"] * 3


def test_failed_probes_count_and_lead_to_a_resend(tmp_path: Path) -> None:
    archive = tmp_path / "source.cbz"
    make_cbz(archive)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    gmail = FakeGmail(
        issues=[AmbiguousSendError("no response"), None, None],
        sent_folder=[RuntimeError("wifi down")] * 3,
    )
    job_id = make_ready_job(database, archive)

    run_job(tmp_path, database, archive, gmail, job_id)

    with database.session() as session:
        job, delivery = first_delivery(session, job_id)
        assert job.status == "sent"
        assert delivery.status == "sent_unconfirmed"
        assert [attempt.status for attempt in delivery.attempts] == ["unverified", "sent"]


def test_scan_accepts_pdf_revisions(tmp_path: Path, make_pdf) -> None:
    source = tmp_path / "source.pdf"
    make_pdf(source)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    payload = source.read_bytes()

    class PdfDrive(FakeDrive):
        def walk_comics(self, _folder_id: str) -> list[DriveComic]:
            return [
                DriveComic(
                    file_id="drive-1",
                    name="Naruto v03.pdf",
                    path="Manga/Naruto v03.pdf",
                    size=len(payload),
                    checksum=hashlib.md5(payload).hexdigest(),  # noqa: S324 - Drive supplies MD5
                    modified_time="2026-08-22T10:00:00Z",
                )
            ]

    with database.session() as session:
        session.add(AppSettings(id=1, source_folder_id="root-folder"))
        scan = Scan()
        session.add(scan)
        session.commit()
        scan_id = scan.id

    ScanProcessor(database, PdfDrive(source), tmp_path / "cache").run(scan_id)

    with database.session() as session:
        candidate = session.query(Candidate).one()
        assert candidate.status == "ready"
        assert candidate.resolved_title == "Naruto Vol. 3"
        assert candidate.comic_metadata == {"title": None, "series": None, "number": None}
        assert Path(candidate.cache_path).suffix == ".pdf"
        assert Path(candidate.cache_path).exists()
