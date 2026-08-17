from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from kindrop.database import Database
from kindrop.google import GmailMessage
from kindrop.mail_monitor import AmazonMailMonitor, AmazonVerificationClient
from kindrop.models import Artifact, Batch, Candidate, Delivery, Job, Revision

SENT_BASE = datetime(2026, 8, 17, 15, 1, tzinfo=UTC)

VERIFICATION_BODY = (
    "Cher client, chère cliente, Nous avons reçu une demande d'envoi d'un document "
    "à votre compte Kindle. Merci de vérifier cette demande : "
    "https://www.amazon.com/sendtokindle/verification/confirm/TOKEN-{n}"
)


class FakeGmail:
    def __init__(self, messages: list[GmailMessage]) -> None:
        self.messages = messages

    def find_amazon_messages(self) -> list[GmailMessage]:
        return self.messages


class FakeVerifier:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def verify(self, url: str) -> bool:
        self.calls.append(url)
        return True


def _add_delivery(session, index: int, sent_at: datetime) -> str:
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
        status="sent",
    )
    session.add(job)
    session.flush()
    artifact = Artifact(
        job_id=job.id,
        filename=f"driveid{index}-Vol {index}.epub",
        path=f"/cache/driveid{index}-Vol {index}.epub",
        size=10,
    )
    session.add(artifact)
    session.flush()
    delivery = Delivery(artifact_id=artifact.id, status="sent_unconfirmed", sent_at=sent_at)
    session.add(delivery)
    session.flush()
    return delivery.id


def _verification_message(index: int, received_at: datetime) -> GmailMessage:
    return GmailMessage(
        id=f"msg-{index}",
        sender="Amazon Kindle Support <do-not-reply@amazon.com>",
        subject="Vérifiez votre demande de document Send to Kindle",
        text=VERIFICATION_BODY.format(n=index),
        internal_date=received_at,
    )


def test_verifier_retries_transient_server_errors(monkeypatch) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503 if calls["count"] < 3 else 200)

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original_client(transport=transport)
    )
    slept: list[float] = []
    client = AmazonVerificationClient(sleep=slept.append)

    verified = client.verify("https://www.amazon.com/sendtokindle/verification/confirm/X")

    assert verified is True
    assert calls["count"] == 3
    assert slept == [2.0, 5.0]


def test_verification_emails_correlate_by_arrival_time(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    with database.session() as session:
        first = _add_delivery(session, 1, SENT_BASE)
        second = _add_delivery(session, 2, SENT_BASE + timedelta(minutes=1))
        session.commit()

    messages = [
        _verification_message(1, SENT_BASE + timedelta(seconds=25)),
        _verification_message(2, SENT_BASE + timedelta(minutes=1, seconds=25)),
    ]
    verifier = FakeVerifier()
    monitor = AmazonMailMonitor(database, FakeGmail(messages), verifier)
    monitor.run_once()

    with database.session() as session:
        assert session.get(Delivery, first).status == "verified"
        assert session.get(Delivery, second).status == "verified"
    assert len(verifier.calls) == 2
    assert verifier.calls[0].endswith("TOKEN-1")
    assert verifier.calls[1].endswith("TOKEN-2")


def test_processed_messages_are_not_verified_twice(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    with database.session() as session:
        _add_delivery(session, 1, SENT_BASE)
        session.commit()

    verifier = FakeVerifier()
    monitor = AmazonMailMonitor(
        database,
        FakeGmail([_verification_message(1, SENT_BASE + timedelta(seconds=25))]),
        verifier,
    )
    monitor.run_once()
    monitor.run_once()

    assert len(verifier.calls) == 1


def test_message_far_from_any_delivery_is_left_alone(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    with database.session() as session:
        delivery_id = _add_delivery(session, 1, SENT_BASE)
        session.commit()

    verifier = FakeVerifier()
    monitor = AmazonMailMonitor(
        database,
        FakeGmail([_verification_message(1, SENT_BASE + timedelta(hours=3))]),
        verifier,
    )
    monitor.run_once()

    with database.session() as session:
        assert session.get(Delivery, delivery_id).status == "sent_unconfirmed"
    assert verifier.calls == []
