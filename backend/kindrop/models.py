import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    encrypted_google_client: Mapped[str | None] = mapped_column(Text)
    encrypted_google_token: Mapped[str | None] = mapped_column(Text)
    google_email: Mapped[str | None] = mapped_column(String(320))
    oauth_state: Mapped[str | None] = mapped_column(String(200))
    oauth_code_verifier: Mapped[str | None] = mapped_column(String(200))
    source_folder_id: Mapped[str | None] = mapped_column(String(200))
    source_folder_name: Mapped[str | None] = mapped_column(String(500))
    kindle_email: Mapped[str | None] = mapped_column(String(320))
    preset: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "kindle_profile": "KPW6",
            "reading_direction": "rtl",
            "spread_mode": "both",
            "crop_mode": "margins_and_page_numbers",
        },
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    pause_requested: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Revision(Base):
    __tablename__ = "revisions"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_revision_fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    drive_file_id: Mapped[str] = mapped_column(String(200), index=True)
    fingerprint: Mapped[str] = mapped_column(String(500))
    checksum: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(500))
    path: Mapped[str] = mapped_column(String(2000))
    size: Mapped[int] = mapped_column(BigInteger)
    modified_time: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(ForeignKey("revisions.id"), unique=True)
    scan_id: Mapped[str | None] = mapped_column(ForeignKey("scans.id"))
    status: Mapped[str] = mapped_column(String(32), default="ready", index=True)
    comic_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    resolved_title: Mapped[str] = mapped_column(String(500))
    title_override: Mapped[str | None] = mapped_column(String(500))
    cache_path: Mapped[str | None] = mapped_column(String(2000))
    cache_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revision: Mapped[Revision] = relationship()


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    preset: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jobs: Mapped[list["Job"]] = relationship(back_populates="batch")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    preset: Mapped[dict[str, Any]] = mapped_column(JSON)
    title: Mapped[str] = mapped_column(String(500))
    merged_candidate_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    batch: Mapped[Batch] = relationship(back_populates="jobs")
    candidate: Mapped[Candidate] = relationship()
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="job")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    path: Mapped[str] = mapped_column(String(2000))
    size: Mapped[int] = mapped_column(BigInteger)
    part_number: Mapped[int] = mapped_column(Integer, default=1)
    total_parts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    job: Mapped[Job] = relationship(back_populates="artifacts")
    delivery: Mapped["Delivery | None"] = relationship(back_populates="artifact")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    gmail_message_id: Mapped[str | None] = mapped_column(String(200))
    error_code: Mapped[str | None] = mapped_column(String(20))
    error_detail: Mapped[str | None] = mapped_column(Text)
    verification_url: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    artifact: Mapped[Artifact] = relationship(back_populates="delivery")
    attempts: Mapped[list["DeliveryAttempt"]] = relationship(
        back_populates="delivery", order_by="DeliveryAttempt.number"
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    delivery_id: Mapped[str] = mapped_column(ForeignKey("deliveries.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="started")
    gmail_message_id: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery: Mapped[Delivery] = relationship(back_populates="attempts")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(100), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
