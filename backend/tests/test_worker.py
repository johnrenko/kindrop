from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select

from kindrop.config import RuntimeSettings
from kindrop.models import Batch, Candidate, Event, Job, Revision
from kindrop.worker import Worker


def _make_worker(tmp_path: Path) -> Worker:
    key_file = tmp_path / "kindrop.key"
    key_file.write_bytes(Fernet.generate_key())
    runtime = RuntimeSettings(
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
        cache_root=tmp_path / "cache",
        secret_key_file=key_file,
        mail_poll_seconds=0.0,
    )
    return Worker(runtime)


def _add_queued_job(session, index: int) -> str:
    batch = Batch(preset={})
    session.add(batch)
    session.flush()
    revision = Revision(
        drive_file_id=f"drive-{index}",
        fingerprint=f"drive-{index}:md5:x",
        name=f"volume-{index}.cbz",
        path=f"Series/volume-{index}.cbz",
        size=1,
        status="sent",
    )
    session.add(revision)
    session.flush()
    candidate = Candidate(revision_id=revision.id, status="queued", resolved_title=f"Vol {index}")
    session.add(candidate)
    session.flush()
    job = Job(
        batch_id=batch.id,
        candidate_id=candidate.id,
        preset={},
        title=f"Vol {index}",
        status="queued",
    )
    session.add(job)
    session.flush()
    return job.id


class RecordingMail:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self) -> None:
        self.calls += 1


def test_drain_queued_jobs_checks_mail_between_jobs(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    with worker.database.session() as session:
        first = _add_queued_job(session, 1)
        second = _add_queued_job(session, 2)
        session.commit()

    mail = RecordingMail()
    worker.mail = mail
    processed: list[tuple[str, int]] = []

    class FakeJobs:
        def run(self, job_id: str) -> None:
            processed.append((job_id, mail.calls))
            with worker.database.session() as session:
                session.get(Job, job_id).status = "sent"
                session.commit()

    worker.jobs = FakeJobs()
    worker.drain_queued_jobs()

    assert [job_id for job_id, _ in processed] == [first, second]
    checks_before_second_job = processed[1][1]
    assert checks_before_second_job >= 1


def test_wait_between_deliveries_checks_mail(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    mail = RecordingMail()
    worker.mail = mail

    worker.jobs.wait_between_deliveries()

    assert mail.calls == 1


def test_mail_monitor_errors_are_recorded_as_events(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)

    class BrokenMail:
        def run_once(self) -> None:
            raise RuntimeError("Gmail exploded")

    worker.mail = BrokenMail()
    worker.check_mail_if_due()
    worker.check_mail_if_due()

    with worker.database.session() as session:
        events = session.scalars(select(Event).where(Event.kind == "mail.error")).all()
    assert len(events) == 1
    assert "Gmail exploded" in str(events[0].payload)
