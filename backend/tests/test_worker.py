from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import select

from kindrop.config import RuntimeSettings
from kindrop.models import (
    Artifact,
    Batch,
    Candidate,
    Delivery,
    DeliveryAttempt,
    Event,
    Job,
    Revision,
)
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


def _add_unknown_delivery(session, *, message_id: str | None) -> str:
    batch = Batch(preset={})
    session.add(batch)
    session.flush()
    revision = Revision(
        drive_file_id="drive-9",
        fingerprint="drive-9:md5:x",
        name="volume.cbz",
        path="Series/volume.cbz",
        size=1,
        status="sent",
    )
    session.add(revision)
    session.flush()
    candidate = Candidate(revision_id=revision.id, status="sent", resolved_title="Vol 9")
    session.add(candidate)
    session.flush()
    job = Job(batch_id=batch.id, candidate_id=candidate.id, preset={}, title="Vol 9", status="sent")
    session.add(job)
    session.flush()
    artifact = Artifact(job_id=job.id, filename="Vol 9.epub", path="/tmp/vol9.epub", size=1)
    session.add(artifact)
    session.flush()
    delivery = Delivery(
        artifact_id=artifact.id,
        status="unknown",
        sent_at=datetime.now(UTC),
        attempt_count=1,
        error_detail="Kindrop is checking Gmail's Sent folder",
    )
    session.add(delivery)
    session.flush()
    session.add(
        DeliveryAttempt(
            delivery_id=delivery.id,
            number=1,
            status="unknown",
            rfc822_message_id=message_id,
        )
    )
    return delivery.id


class ProbingGmail:
    def __init__(self, outcome: str | None | Exception) -> None:
        self.outcome = outcome
        self.probes: list[str] = []

    def find_sent_message(self, rfc822_message_id: str) -> str | None:
        self.probes.append(rfc822_message_id)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_startup_recovers_an_unknown_delivery_found_in_the_sent_folder(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    with worker.database.session() as session:
        delivery_id = _add_unknown_delivery(session, message_id="<a@kindrop.local>")
        session.commit()
    worker.gmail = ProbingGmail("gm-9")

    worker.recover_interrupted_work()

    with worker.database.session() as session:
        delivery = session.get(Delivery, delivery_id)
        assert delivery.status == "sent_unconfirmed"
        assert delivery.gmail_message_id == "gm-9"
        assert delivery.error_detail is None
        assert delivery.attempts[-1].status == "sent"
    assert worker.gmail.probes == ["<a@kindrop.local>"]


def test_startup_fails_an_unknown_delivery_missing_from_the_sent_folder(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    with worker.database.session() as session:
        delivery_id = _add_unknown_delivery(session, message_id="<a@kindrop.local>")
        session.commit()
    worker.gmail = ProbingGmail(None)

    worker.resolve_interrupted_unknowns()

    with worker.database.session() as session:
        delivery = session.get(Delivery, delivery_id)
        assert delivery.status == "failed"
        assert "resend it" in delivery.error_detail
        assert delivery.attempts[-1].status == "unverified"


def test_startup_fails_a_legacy_unknown_without_message_id(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    with worker.database.session() as session:
        delivery_id = _add_unknown_delivery(session, message_id=None)
        session.commit()
    worker.gmail = ProbingGmail("gm-9")

    worker.resolve_interrupted_unknowns()

    with worker.database.session() as session:
        delivery = session.get(Delivery, delivery_id)
        assert delivery.status == "failed"
        assert "before automatic verification" in delivery.error_detail
    assert worker.gmail.probes == [], "a delivery without Message-ID cannot be probed"


def test_startup_leaves_unknown_when_gmail_is_unreachable(tmp_path: Path) -> None:
    worker = _make_worker(tmp_path)
    with worker.database.session() as session:
        delivery_id = _add_unknown_delivery(session, message_id="<a@kindrop.local>")
        session.commit()
    worker.gmail = ProbingGmail(RuntimeError("Connect a Google account first"))

    worker.resolve_interrupted_unknowns()

    with worker.database.session() as session:
        delivery = session.get(Delivery, delivery_id)
        assert delivery.status == "unknown", "an unreachable Gmail must not settle the delivery"
