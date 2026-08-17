from fastapi.testclient import TestClient
from sqlalchemy import select

from kindrop.api import create_app
from kindrop.database import Database
from kindrop.models import (
    AppSettings,
    Artifact,
    Batch,
    Candidate,
    Delivery,
    DeliveryAttempt,
    Job,
    Revision,
    Scan,
)


def test_health_reports_runtime_dependencies(tmp_path) -> None:
    app = create_app(Database(f"sqlite:///{tmp_path / 'test.db'}"))

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"


def test_batch_creation_snapshots_preset_and_queues_selected_candidates(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        session.add(AppSettings(id=1, kindle_email="reader@kindle.com"))
        revision = Revision(
            drive_file_id="drive-1",
            fingerprint="drive-1:md5:abc",
            name="volume.cbz",
            path="Manga/volume.cbz",
            size=123,
            status="candidate",
        )
        session.add(revision)
        session.flush()
        candidate = Candidate(
            revision_id=revision.id,
            status="ready",
            resolved_title="Volume 1",
            cache_path="/cache/volume.cbz",
        )
        session.add(candidate)
        session.commit()
        candidate_id = candidate.id

    response = TestClient(app).post(
        "/api/batches",
        json={
            "candidate_ids": [candidate_id],
            "preset": {
                "kindle_profile": "KPW6",
                "reading_direction": "rtl",
                "spread_mode": "both",
                "crop_mode": "margins_and_page_numbers",
            },
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["job_count"] == 1
    assert body["preset"]["kindle_profile"] == "KPW6"


def test_batch_creation_rejects_candidates_that_are_not_ready(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        revision = Revision(
            drive_file_id="drive-1",
            fingerprint="drive-1:md5:abc",
            name="volume.cbz",
            path="Manga/volume.cbz",
            size=123,
            status="ignored",
        )
        session.add(revision)
        session.flush()
        candidate = Candidate(
            revision_id=revision.id,
            status="ignored",
            resolved_title="Volume 1",
            cache_path="/cache/volume.cbz",
        )
        session.add(candidate)
        session.commit()
        candidate_id = candidate.id

    response = TestClient(app).post(
        "/api/batches",
        json={
            "candidate_ids": [candidate_id],
            "preset": {
                "kindle_profile": "KPW6",
                "reading_direction": "rtl",
                "spread_mode": "both",
                "crop_mode": "margins_and_page_numbers",
            },
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Every selected candidate must be ready"


def _seed_candidate(database: Database) -> str:
    with database.session() as session:
        revision = Revision(
            drive_file_id="drive-2",
            fingerprint="drive-2:md5:def",
            name="naruto_v03.cbz",
            path="Manga/naruto_v03.cbz",
            size=456,
            status="candidate",
        )
        session.add(revision)
        session.flush()
        candidate = Candidate(
            revision_id=revision.id,
            status="ready",
            resolved_title="Naruto Vol. 3",
            title_override="My override",
        )
        session.add(candidate)
        session.commit()
        return candidate.id


def test_candidate_metadata_edit_recomputes_the_kindle_title(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    candidate_id = _seed_candidate(database)

    response = TestClient(app).patch(
        f"/api/candidates/{candidate_id}",
        json={
            "series": "Naruto",
            "number": "3",
            "author": "Masashi Kishimoto",
            "cover_url": "https://img.anili.st/naruto.jpg",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved_title"] == "Naruto, Tome 3"
    assert body["metadata"]["author"] == "Masashi Kishimoto"
    assert body["metadata"]["cover_url"] == "https://img.anili.st/naruto.jpg"
    assert body["title_override"] == "My override"


def test_candidate_metadata_edit_rejects_plain_http_cover(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    candidate_id = _seed_candidate(database)

    response = TestClient(app).patch(
        f"/api/candidates/{candidate_id}",
        json={"cover_url": "http://img.anili.st/naruto.jpg"},
    )

    assert response.status_code == 422


def test_candidate_status_edit_keeps_the_title_override(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    candidate_id = _seed_candidate(database)

    response = TestClient(app).patch(
        f"/api/candidates/{candidate_id}", json={"status": "ignored"}
    )

    assert response.status_code == 200
    assert response.json()["title_override"] == "My override"


PRESET = {
    "kindle_profile": "KPW6",
    "reading_direction": "rtl",
    "spread_mode": "both",
    "crop_mode": "margins_and_page_numbers",
}


def test_clear_history_removes_terminal_records_and_keeps_revisions(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    artifact_file = tmp_path / "volume.kepub.epub"
    artifact_file.write_bytes(b"epub")
    with database.session() as session:
        revision = Revision(
            drive_file_id="drive-1",
            fingerprint="drive-1:md5:abc",
            name="volume.cbz",
            path="Manga/volume.cbz",
            size=123,
            status="sent",
        )
        session.add(revision)
        session.flush()
        candidate = Candidate(revision_id=revision.id, status="sent", resolved_title="Volume 1")
        session.add(candidate)
        session.add(Scan(status="completed"))
        batch = Batch(preset=PRESET, status="completed")
        session.add(batch)
        session.flush()
        job = Job(
            batch_id=batch.id,
            candidate_id=candidate.id,
            status="sent",
            preset=PRESET,
            title="Volume 1",
        )
        session.add(job)
        session.flush()
        artifact = Artifact(
            job_id=job.id, filename="volume.kepub.epub", path=str(artifact_file), size=4
        )
        session.add(artifact)
        session.flush()
        delivery = Delivery(artifact_id=artifact.id, status="sent_unconfirmed")
        session.add(delivery)
        session.flush()
        session.add(DeliveryAttempt(delivery_id=delivery.id, number=1, status="sent"))
        session.commit()

    response = TestClient(app).delete("/api/history")

    assert response.status_code == 204
    assert not artifact_file.exists()
    with database.session() as session:
        assert session.scalars(select(Job)).first() is None
        assert session.scalars(select(Batch)).first() is None
        assert session.scalars(select(Artifact)).first() is None
        assert session.scalars(select(Delivery)).first() is None
        assert session.scalars(select(DeliveryAttempt)).first() is None
        assert session.scalars(select(Scan)).first() is None
        assert session.scalars(select(Candidate)).first() is None
        assert session.scalars(select(Revision)).one().status == "sent"


def test_clear_history_resets_failed_job_candidates_for_review(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        revision = Revision(
            drive_file_id="drive-1",
            fingerprint="drive-1:md5:abc",
            name="volume.cbz",
            path="Manga/volume.cbz",
            size=123,
            status="candidate",
        )
        session.add(revision)
        scan = Scan(status="failed")
        session.add(scan)
        session.flush()
        candidate = Candidate(
            revision_id=revision.id,
            scan_id=scan.id,
            status="queued",
            resolved_title="Volume 1",
            error="KCC crashed",
        )
        session.add(candidate)
        batch = Batch(preset=PRESET, status="failed")
        session.add(batch)
        session.flush()
        session.add(
            Job(
                batch_id=batch.id,
                candidate_id=candidate.id,
                status="failed",
                preset=PRESET,
                title="Volume 1",
            )
        )
        session.commit()

    response = TestClient(app).delete("/api/history")

    assert response.status_code == 204
    with database.session() as session:
        candidate = session.scalars(select(Candidate)).one()
        assert candidate.status == "ready"
        assert candidate.error is None
        assert candidate.scan_id is None
        assert session.scalars(select(Revision)).one().status == "candidate"
        assert session.scalars(select(Job)).first() is None
        assert session.scalars(select(Scan)).first() is None


def test_clear_history_refuses_while_a_conversion_is_running(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        revision = Revision(
            drive_file_id="drive-1",
            fingerprint="drive-1:md5:abc",
            name="volume.cbz",
            path="Manga/volume.cbz",
            size=123,
            status="candidate",
        )
        session.add(revision)
        session.flush()
        candidate = Candidate(
            revision_id=revision.id, status="queued", resolved_title="Volume 1"
        )
        session.add(candidate)
        batch = Batch(preset=PRESET, status="processing")
        session.add(batch)
        session.flush()
        session.add(
            Job(
                batch_id=batch.id,
                candidate_id=candidate.id,
                status="converting",
                preset=PRESET,
                title="Volume 1",
            )
        )
        session.commit()

    response = TestClient(app).delete("/api/history")

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "History can only be cleared while no scan or conversion is running"
    )
    with database.session() as session:
        assert session.scalars(select(Job)).one().status == "converting"
