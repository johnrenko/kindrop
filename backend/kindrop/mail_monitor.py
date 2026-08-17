import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .amazon_mail import classify_amazon_message, is_safe_verification_url
from .database import Database
from .google import GoogleGmailGateway
from .models import Artifact, Delivery, Event


class AmazonVerificationClient:
    # Amazon intermittently answers 503 when verification links are opened in
    # bursts, so server errors are retried with a pause before giving up.
    RETRY_DELAYS = (2.0, 5.0, 10.0)

    def __init__(self, sleep: Callable[[float], None] = time.sleep) -> None:
        self.sleep = sleep

    def verify(self, url: str) -> bool:
        current = url
        retries = iter(self.RETRY_DELAYS)
        with httpx.Client(follow_redirects=False, timeout=20) as client:
            for _ in range(15):
                if not is_safe_verification_url(current):
                    return False
                response = client.get(current)
                if 200 <= response.status_code < 300:
                    return True
                if response.status_code >= 500:
                    delay = next(retries, None)
                    if delay is None:
                        return False
                    self.sleep(delay)
                    continue
                if response.status_code not in {301, 302, 303, 307, 308}:
                    return False
                location = response.headers.get("location")
                if not location:
                    return False
                current = urljoin(current, location)
        return False


class AmazonMailMonitor:
    def __init__(
        self,
        database: Database,
        gmail: GoogleGmailGateway,
        verifier: AmazonVerificationClient,
    ) -> None:
        self.database = database
        self.gmail = gmail
        self.verifier = verifier

    def run_once(self) -> None:
        with self.database.session() as session:
            processed = set(
                session.scalars(select(Event.entity_id).where(Event.kind == "mail.processed"))
            )
        messages = [
            self.gmail.fetch_message(message_id)
            for message_id in self.gmail.list_amazon_message_ids()
            if message_id not in processed
        ]
        with self.database.session() as session:
            deliveries = session.scalars(
                select(Delivery)
                .options(selectinload(Delivery.artifact).selectinload(Artifact.job))
                .where(
                    Delivery.status.in_(
                        ["sent_unconfirmed", "verification_required", "action_required"]
                    ),
                    Delivery.sent_at >= datetime.now(UTC) - timedelta(days=7),
                )
            ).all()
            claimed: set[str] = set()
            ordered = sorted(
                messages, key=lambda m: m.internal_date or datetime.max.replace(tzinfo=UTC)
            )
            for message in ordered:
                result = classify_amazon_message(
                    sender=message.sender, subject=message.subject, text=message.text
                )
                if result.kind == "irrelevant":
                    continue
                delivery = self._correlate(deliveries, message, claimed)
                if not delivery:
                    continue
                claimed.add(delivery.id)
                session.add(
                    Event(
                        topic="mail",
                        entity_id=message.id,
                        kind="mail.processed",
                        payload={"delivery_id": delivery.id, "kind": result.kind},
                    )
                )
                if result.kind == "rejected":
                    delivery.status = "rejected"
                    delivery.error_code = result.error_code
                    delivery.error_detail = "Amazon rejected this Send to Kindle document"
                    session.add(
                        Event(
                            topic="delivery",
                            entity_id=delivery.id,
                            kind="delivery.rejected",
                            payload={"error_code": result.error_code},
                        )
                    )
                elif result.kind == "verification_required" and result.verification_url:
                    delivery.status = "verification_required"
                    delivery.verification_url = result.verification_url
                    if self.verifier.verify(result.verification_url):
                        delivery.status = "verified"
                        delivery.verification_url = None
                    else:
                        delivery.status = "action_required"
                delivery.updated_at = datetime.now(UTC)
            session.commit()

    # Amazon's notification emails carry no document identifier, so a message is
    # correlated to the delivery whose send time sits closest to the message's
    # arrival time; each message claims at most one delivery.
    ARRIVAL_TOLERANCE = timedelta(minutes=15)

    @classmethod
    def _correlate(cls, deliveries, message, claimed: set[str]) -> Delivery | None:
        available = [d for d in deliveries if d.id not in claimed]
        haystack = f"{message.subject}\n{message.text}".lower()
        by_filename = [
            delivery
            for delivery in available
            if Path(delivery.artifact.filename).name.lower() in haystack
        ]
        if len(by_filename) == 1:
            return by_filename[0]
        if message.internal_date:
            nearest: tuple[timedelta, Delivery] | None = None
            for delivery in available:
                sent_at = cls._as_utc(delivery.sent_at)
                if not sent_at:
                    continue
                distance = abs(message.internal_date - sent_at)
                if distance <= cls.ARRIVAL_TOLERANCE and (nearest is None or distance < nearest[0]):
                    nearest = (distance, delivery)
            return nearest[1] if nearest else None
        cutoff = datetime.now(UTC) - timedelta(minutes=30)
        recent = [d for d in available if (s := cls._as_utc(d.sent_at)) and s >= cutoff]
        return recent[0] if len(recent) == 1 else None

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
