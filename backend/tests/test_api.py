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


def test_queued_scan_pauses_resumes_and_stops_immediately(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    client = TestClient(app)
    with database.session() as session:
        session.add(AppSettings(id=1, source_folder_id="root-folder"))
        session.commit()

    scan_id = client.post("/api/scans").json()["id"]

    paused = client.post(f"/api/scans/{scan_id}/pause")
    assert paused.status_code == 202
    assert paused.json()["status"] == "paused"
    with database.session() as session:
        assert session.get(Scan, scan_id).status == "paused"

    blocked = client.post("/api/scans")
    assert blocked.status_code == 409
    assert "paused" in blocked.json()["detail"]

    resumed = client.post(f"/api/scans/{scan_id}/resume")
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "queued"

    client.post(f"/api/scans/{scan_id}/pause")
    stopped = client.post(f"/api/scans/{scan_id}/cancel")
    assert stopped.status_code == 202
    assert stopped.json()["status"] == "cancelled"
    with database.session() as session:
        scan = session.get(Scan, scan_id)
        assert scan.status == "cancelled"
        assert scan.completed_at is not None

    assert client.post(f"/api/scans/{scan_id}/resume").status_code == 409
    assert client.post(f"/api/scans/{scan_id}/pause").status_code == 409
    assert client.post("/api/scans").status_code == 202


def test_running_scan_receives_pause_and_cancel_requests(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    client = TestClient(app)
    with database.session() as session:
        scan = Scan(status="scanning")
        session.add(scan)
        session.commit()
        scan_id = scan.id

    paused = client.post(f"/api/scans/{scan_id}/pause")
    assert paused.status_code == 202
    assert paused.json()["status"] == "pausing"
    with database.session() as session:
        assert session.get(Scan, scan_id).pause_requested is True

    stopped = client.post(f"/api/scans/{scan_id}/cancel")
    assert stopped.status_code == 202
    assert stopped.json()["status"] == "cancelling"
    with database.session() as session:
        assert session.get(Scan, scan_id).cancel_requested is True


def _seed_ready(session, name: str, series: str | None = None) -> str:
    revision = Revision(
        drive_file_id=f"drive-{name}",
        fingerprint=f"fp-{name}",
        name=name,
        path=f"Manga/{name}",
        size=1,
        status="candidate",
    )
    session.add(revision)
    session.flush()
    candidate = Candidate(
        revision_id=revision.id,
        status="ready",
        resolved_title=name,
        comic_metadata={"series": series} if series else {},
    )
    session.add(candidate)
    session.flush()
    return candidate.id


def test_merged_batch_groups_selected_candidates_by_volume(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        session.add(AppSettings(id=1, kindle_email="reader@kindle.com"))
        later = _seed_ready(session, "009 - Volume 02.cbr", "Naruto")
        lead = _seed_ready(session, "008 - Volume 02.cbr", "Naruto")
        lonely = _seed_ready(session, "015 - Volume 03.cbr", "Naruto")
        oneshot = _seed_ready(session, "oneshot.cbz")
        session.commit()

    response = TestClient(app).post(
        "/api/batches",
        json={
            "candidate_ids": [later, lead, lonely, oneshot],
            "preset": PRESET,
            "merge_by_volume": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["job_count"] == 3
    with database.session() as session:
        jobs = {job.title: job for job in session.scalars(select(Job))}
        assert sorted(jobs) == ["Naruto, Tome 02", "Naruto, Tome 03", "oneshot.cbz"]
        volume_two = jobs["Naruto, Tome 02"]
        assert volume_two.candidate_id == lead
        assert volume_two.merged_candidate_ids == [lead, later]
        assert jobs["Naruto, Tome 03"].merged_candidate_ids == [lonely]
        assert jobs["oneshot.cbz"].merged_candidate_ids is None
        statuses = {candidate.status for candidate in session.scalars(select(Candidate))}
        assert statuses == {"queued"}


def test_retry_requeues_every_member_of_a_merged_job(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        lead = _seed_ready(session, "008 - Volume 02.cbr", "Naruto")
        later = _seed_ready(session, "009 - Volume 02.cbr", "Naruto")
        batch = Batch(preset=PRESET, status="completed_with_errors")
        session.add(batch)
        session.flush()
        job = Job(
            batch_id=batch.id,
            candidate_id=lead,
            status="failed",
            preset=PRESET,
            title="Naruto, Tome 02",
            merged_candidate_ids=[lead, later],
        )
        session.add(job)
        session.commit()
        job_id = job.id

    response = TestClient(app).post(f"/api/jobs/{job_id}/retry")

    assert response.status_code == 201
    with database.session() as session:
        replacement = session.scalars(select(Job).where(Job.id != job_id)).one()
        assert replacement.merged_candidate_ids == [lead, later]
        statuses = {candidate.status for candidate in session.scalars(select(Candidate))}
        assert statuses == {"queued"}


def test_clear_history_handles_every_member_of_a_merged_job(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        lead = _seed_ready(session, "008 - Volume 02.cbr", "Naruto")
        later = _seed_ready(session, "009 - Volume 02.cbr", "Naruto")
        for candidate in session.scalars(select(Candidate)):
            candidate.status = "sent"
            candidate.revision.status = "sent"
        batch = Batch(preset=PRESET, status="completed")
        session.add(batch)
        session.flush()
        session.add(
            Job(
                batch_id=batch.id,
                candidate_id=lead,
                status="sent",
                preset=PRESET,
                title="Naruto, Tome 02",
                merged_candidate_ids=[lead, later],
            )
        )
        session.commit()

    response = TestClient(app).delete("/api/history")

    assert response.status_code == 204
    with database.session() as session:
        assert session.scalars(select(Candidate)).first() is None
        assert {revision.status for revision in session.scalars(select(Revision))} == {"sent"}
