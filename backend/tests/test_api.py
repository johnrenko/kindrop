from fastapi.testclient import TestClient

from kindrop.api import create_app
from kindrop.database import Database
from kindrop.models import AppSettings, Candidate, Revision


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
