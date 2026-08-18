import logging
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from .config import RuntimeSettings
from .crypto import SecretStore
from .database import Database
from .google import GoogleDriveGateway, GoogleGmailGateway, GoogleServiceFactory
from .mail_monitor import AmazonMailMonitor, AmazonVerificationClient
from .models import Candidate, Event, Job, Scan
from .runners import KccRunner
from .services import JobProcessor, ScanProcessor

logger = logging.getLogger(__name__)


class DeliveryRateLimiter:
    def __init__(self, interval_seconds: float = 60) -> None:
        self.interval_seconds = interval_seconds
        self.last_send_started: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self.last_send_started is not None:
            remaining = self.interval_seconds - (now - self.last_send_started)
            if remaining > 0:
                time.sleep(remaining)
        self.last_send_started = time.monotonic()


class Worker:
    def __init__(self, runtime: RuntimeSettings) -> None:
        self.runtime = runtime
        self.database = Database(runtime.database_url)
        services = GoogleServiceFactory(self.database, SecretStore(runtime.secret_key_file))
        self.drive = GoogleDriveGateway(services)
        self.gmail = GoogleGmailGateway(services)
        self.rate_limiter = DeliveryRateLimiter()
        self.scans = ScanProcessor(
            self.database,
            self.drive,
            runtime.cache_root,
            between_items=self.process_pending_work,
        )
        self.jobs = JobProcessor(
            database=self.database,
            drive=self.drive,
            kcc=KccRunner(),
            gmail=self.gmail,
            cache_root=runtime.cache_root,
            wait_between_deliveries=self._wait_between_deliveries,
        )
        self.mail = AmazonMailMonitor(self.database, self.gmail, AmazonVerificationClient())
        self._last_mail_check = 0.0

    def process_pending_work(self) -> None:
        self.drain_queued_jobs()
        self.check_mail_if_due()

    def _wait_between_deliveries(self) -> None:
        self.check_mail_if_due()
        self.rate_limiter.wait()

    def check_mail_if_due(self) -> None:
        current = time.monotonic()
        if current - self._last_mail_check >= self.runtime.mail_poll_seconds:
            try:
                self.mail.run_once()
            except Exception as error:
                logger.exception("The Amazon mail monitor failed")
                self._record_mail_error(error)
            self._last_mail_check = current

    def _record_mail_error(self, error: Exception) -> None:
        detail = f"{type(error).__name__}: {error}"
        with self.database.session() as session:
            last = session.scalar(
                select(Event).where(Event.kind == "mail.error").order_by(Event.id.desc()).limit(1)
            )
            if last and last.payload.get("error") == detail:
                return
            session.add(Event(topic="mail", kind="mail.error", payload={"error": detail}))
            session.commit()

    def drain_queued_jobs(self) -> None:
        while True:
            with self.database.session() as session:
                job_id = session.scalar(
                    select(Job.id).where(Job.status == "queued").order_by(Job.created_at).limit(1)
                )
            if not job_id:
                return
            self.jobs.run(job_id)
            self.check_mail_if_due()

    def recover_interrupted_work(self) -> None:
        with self.database.session() as session:
            for scan in session.scalars(select(Scan).where(Scan.status == "scanning")):
                if scan.pause_requested:
                    scan.status = "paused"
                    scan.pause_requested = False
                else:
                    scan.status = "queued"
                    scan.error = "The previous scan was interrupted and has been resumed"
            for job in session.scalars(
                select(Job).where(Job.status.in_(["downloading", "converting", "sending"]))
            ):
                job.status = "failed"
                job.error = (
                    "The worker stopped during this job. Review existing deliveries "
                    "before retrying."
                )
                job.completed_at = datetime.now(UTC)
            session.commit()

    def purge_expired_cache(self) -> None:
        with self.database.session() as session:
            candidates = session.scalars(
                select(Candidate).where(
                    Candidate.cache_expires_at.is_not(None),
                    Candidate.cache_expires_at < datetime.now(UTC),
                    Candidate.status.in_(["ready", "ignored", "invalid"]),
                )
            ).all()
            for candidate in candidates:
                if candidate.cache_path:
                    Path(candidate.cache_path).unlink(missing_ok=True)
                    parent = Path(candidate.cache_path).parent
                    if parent.is_dir() and not any(parent.iterdir()):
                        shutil.rmtree(parent, ignore_errors=True)
                candidate.cache_path = None
                candidate.cache_expires_at = None
            session.commit()

    def run_forever(self) -> None:
        self.recover_interrupted_work()
        last_cache_check = 0.0
        while True:
            self.process_pending_work()

            with self.database.session() as session:
                scan_id = session.scalar(
                    select(Scan.id)
                    .where(Scan.status == "queued")
                    .order_by(Scan.created_at)
                    .limit(1)
                )
            if scan_id:
                self.scans.run(scan_id)

            current = time.monotonic()
            if current - last_cache_check >= 300:
                self.purge_expired_cache()
                last_cache_check = current
            time.sleep(self.runtime.worker_poll_seconds)


def main() -> None:
    Worker(RuntimeSettings()).run_forever()


if __name__ == "__main__":
    main()
