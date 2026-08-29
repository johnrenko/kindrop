from types import SimpleNamespace

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


def test_settings_expose_ssh_first_koreader_defaults(tmp_path) -> None:
    app = create_app(Database(f"sqlite:///{tmp_path / 'test.db'}"))

    response = TestClient(app).get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["preset"]["kindle_profile"] == "KPW"
    assert body["ssh_host"] == "192.168.1.53"
    assert body["ssh_port"] == 2222
    assert body["ssh_user"] == "root"
    assert body["ssh_key_path"] == "/run/secrets/kindle_ssh_key"
    assert body["ssh_known_hosts_path"] == "/data/kindle_known_hosts"
    assert body["ssh_destination"] == "/mnt/us/documents/KOReader/Kindrop"
    assert body["kindle_reserve_mib"] == 100


def test_settings_update_persists_configurable_ssh_connection(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    client = TestClient(create_app(database))

    response = client.put(
        "/api/settings",
        json={
            "source_folder_id": "drive-root",
            "source_folder_name": "Kindle",
            "kindle_email": "reader@kindle.com",
            "preset": PRESET,
            "ssh_host": "kindle.home",
            "ssh_port": 2200,
            "ssh_user": "reader",
            "ssh_key_path": "/ssh/existing_key",
            "ssh_known_hosts_path": "/ssh/known_hosts",
            "ssh_destination": "/mnt/us/documents/KOReader/Kindrop",
            "kindle_reserve_mib": 125,
        },
    )

    assert response.status_code == 200
    assert response.json()["ssh_host"] == "kindle.home"
    assert response.json()["ssh_key_path"] == "/ssh/existing_key"
    assert client.get("/api/settings").json()["kindle_reserve_mib"] == 125


def test_ssh_test_probes_the_configured_kindle_and_reports_capacity(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    seen = []

    class FakeTransport:
        def probe(self):
            return SimpleNamespace(reachable=True, free_bytes=784_334_848, detail=None)

    def transport_factory(config):
        seen.append(config)
        return FakeTransport()

    client = TestClient(create_app(database, ssh_transport_factory=transport_factory))

    response = client.post("/api/ssh/test")

    assert response.status_code == 200
    assert response.json()["reachable"] is True
    assert response.json()["free_bytes"] == 784_334_848
    assert response.json()["capacity_unknown"] is False
    assert response.json()["destination"] == "/mnt/us/documents/KOReader/Kindrop"
    assert seen[0].host == "192.168.1.53"
    assert str(seen[0].key_path) == "/run/secrets/kindle_ssh_key"
    assert TestClient(client.app).get("/api/ssh/status").json()["reachable"] is True


def test_ssh_host_key_requires_explicit_matching_confirmation(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    trusted = []

    class FakeTransport:
        def inspect_host_key(self):
            return SimpleNamespace(fingerprint="SHA256:kindle-key")

        def pinned_host_key(self):
            return SimpleNamespace(fingerprint="SHA256:old-key")

        def trust_host_key(self, fingerprint):
            trusted.append(fingerprint)
            if fingerprint != "SHA256:kindle-key":
                raise ValueError("fingerprint changed")
            return SimpleNamespace(fingerprint=fingerprint)

    client = TestClient(
        create_app(database, ssh_transport_factory=lambda _config: FakeTransport())
    )

    inspected = client.post("/api/ssh/host-key")
    rejected = client.post("/api/ssh/trust", json={"fingerprint": "SHA256:wrong-key"})
    accepted = client.post("/api/ssh/trust", json=inspected.json())

    assert inspected.json() == {
        "fingerprint": "SHA256:kindle-key",
        "trusted_fingerprint": "SHA256:old-key",
    }
    assert rejected.status_code == 409
    assert accepted.json() == {
        "fingerprint": "SHA256:kindle-key",
        "trusted_fingerprint": "SHA256:kindle-key",
    }
    assert trusted == ["SHA256:wrong-key", "SHA256:kindle-key"]


def test_kindle_file_browser_lists_renames_moves_and_deletes(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    calls = []

    class FakeTransport:
        def list_storage(self, path):
            calls.append(("list", path))
            return SimpleNamespace(
                path="/mnt/us/documents",
                root="/mnt/us",
                parent="/mnt/us",
                items=[
                    SimpleNamespace(
                        name="Manga",
                        path="/mnt/us/documents/Manga",
                        kind="directory",
                        size_bytes=0,
                        modified_at=1756288800,
                    ),
                    SimpleNamespace(
                        name="book.cbz",
                        path="/mnt/us/documents/book.cbz",
                        kind="file",
                        size_bytes=4096,
                        modified_at=1756288700,
                    ),
                ],
            )

        def rename_storage_item(self, path, new_name):
            calls.append(("rename", path, new_name))
            return "/mnt/us/documents/renamed.cbz"

        def move_storage_item(self, path, destination_directory):
            calls.append(("move", path, destination_directory))
            return "/mnt/us/documents/Archive/renamed.cbz"

        def delete_storage_item(self, path):
            calls.append(("delete", path))

        def delete_storage_items(self, paths):
            calls.append(("bulk-delete", paths))

    client = TestClient(
        create_app(database, ssh_transport_factory=lambda _config: FakeTransport())
    )

    listed = client.get("/api/kindle/files", params={"path": "/mnt/us/documents"})
    renamed = client.post(
        "/api/kindle/files/rename",
        json={"path": "/mnt/us/documents/book.cbz", "new_name": "renamed.cbz"},
    )
    moved = client.post(
        "/api/kindle/files/move",
        json={
            "path": "/mnt/us/documents/renamed.cbz",
            "destination_directory": "/mnt/us/documents/Archive",
        },
    )
    deleted = client.post(
        "/api/kindle/files/delete",
        json={"path": "/mnt/us/documents/Archive/renamed.cbz"},
    )
    bulk_deleted = client.post(
        "/api/kindle/files/bulk-delete",
        json={"paths": ["/mnt/us/documents/Manga", "/mnt/us/documents/book.cbz"]},
    )

    assert listed.status_code == 200
    assert listed.json()["items"][1]["size_bytes"] == 4096
    assert renamed.json()["path"] == "/mnt/us/documents/renamed.cbz"
    assert moved.json()["path"] == "/mnt/us/documents/Archive/renamed.cbz"
    assert deleted.status_code == 204
    assert bulk_deleted.status_code == 204
    assert calls == [
        ("list", "/mnt/us/documents"),
        ("rename", "/mnt/us/documents/book.cbz", "renamed.cbz"),
        ("move", "/mnt/us/documents/renamed.cbz", "/mnt/us/documents/Archive"),
        ("delete", "/mnt/us/documents/Archive/renamed.cbz"),
        (
            "bulk-delete",
            ["/mnt/us/documents/Manga", "/mnt/us/documents/book.cbz"],
        ),
    ]


def test_kindle_bulk_delete_accepts_every_item_in_a_large_folder(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    captured = []

    class FakeTransport:
        def delete_storage_items(self, paths):
            captured.extend(paths)

    client = TestClient(
        create_app(database, ssh_transport_factory=lambda _config: FakeTransport())
    )
    paths = [f"/mnt/us/documents/book-{index}.cbz" for index in range(201)]

    response = client.post("/api/kindle/files/bulk-delete", json={"paths": paths})

    assert response.status_code == 204
    assert captured == paths


def test_kindle_file_browser_uploads_and_removes_its_local_temporary_file(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    captured = {}

    class FakeTransport:
        def upload_storage_item(self, local_path, destination_directory, filename):
            captured["content"] = local_path.read_bytes()
            captured["exists_during_upload"] = local_path.exists()
            captured["destination"] = destination_directory
            captured["filename"] = filename
            captured["local_path"] = local_path
            return SimpleNamespace(remote_path=f"{destination_directory}/{filename}")

    client = TestClient(
        create_app(database, ssh_transport_factory=lambda _config: FakeTransport())
    )

    response = client.post(
        "/api/kindle/files/upload",
        data={"destination_directory": "/mnt/us/documents/KOReader"},
        files={"file": ("manual.cbz", b"comic", "application/vnd.comicbook+zip")},
    )

    assert response.status_code == 200
    assert response.json() == {"path": "/mnt/us/documents/KOReader/manual.cbz"}
    assert captured["content"] == b"comic"
    assert captured["exists_during_upload"] is True
    assert captured["destination"] == "/mnt/us/documents/KOReader"
    assert captured["filename"] == "manual.cbz"
    assert captured["local_path"].exists() is False


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


def test_candidate_series_suggestions_group_ready_volumes_and_include_known_history(
    tmp_path,
) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        first = _seed_ready(session, "Volume 01.cbz", path="Volume 01.cbz")
        third = _seed_ready(session, "Volume 03.cbz", path="Volume 03.cbz")
        _seed_ready(
            session,
            "Volume 02.cbz",
            path="Volume 02.cbz",
            status="sent",
        )
        session.commit()

    response = TestClient(app).get("/api/candidate-series")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "generic:root",
            "suggested_series": None,
            "confidence": "needs_name",
            "ready_count": 2,
            "known_count": 3,
            "first_volume": 1,
            "last_volume": 3,
            "missing_volumes": [],
            "duplicate_volumes": [],
            "members": [
                {"candidate_id": first, "name": "Volume 01.cbz", "number": 1},
                {"candidate_id": third, "name": "Volume 03.cbz", "number": 3},
            ],
        }
    ]


def test_candidate_series_suggestions_keep_same_named_folders_separate(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        _seed_ready(
            session,
            "Volume 01 [Publisher A].cbz",
            path="Publisher A/Volumes/Volume 01 [Publisher A].cbz",
        )
        _seed_ready(
            session,
            "Volume 02 [Publisher A].cbz",
            path="Publisher A/Volumes/Volume 02 [Publisher A].cbz",
        )
        _seed_ready(
            session,
            "Volume 01 [Publisher B].cbz",
            path="Publisher B/Volumes/Volume 01 [Publisher B].cbz",
        )
        _seed_ready(
            session,
            "Volume 02 [Publisher B].cbz",
            path="Publisher B/Volumes/Volume 02 [Publisher B].cbz",
        )
        session.commit()

    response = TestClient(app).get("/api/candidate-series")

    assert response.status_code == 200
    assert {(item["id"], item["ready_count"]) for item in response.json()} == {
        ("folder:publisher-a/volumes", 2),
        ("folder:publisher-b/volumes", 2),
    }


def test_candidate_series_apply_updates_every_ready_member_from_one_match(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        volume_18 = _seed_ready(session, "Blue Lock 18.cbz")
        volume_19 = _seed_ready(session, "Blue Lock 19.cbz")
        session.commit()

    response = TestClient(app).patch(
        "/api/candidate-series",
        json={
            "group_id": "series:blue-lock",
            "candidate_ids": [volume_18, volume_19],
            "series": "Blue Lock",
            "author": "Muneyuki Kaneshiro",
            "cover_url": "https://img.anili.st/blue-lock.jpg",
        },
    )

    assert response.status_code == 200
    assert [item["resolved_title"] for item in response.json()] == [
        "Blue Lock, Tome 18",
        "Blue Lock, Tome 19",
    ]
    assert [item["metadata"]["number"] for item in response.json()] == ["18", "19"]
    assert all(
        item["metadata"]["author"] == "Muneyuki Kaneshiro" for item in response.json()
    )


def test_candidate_series_apply_rejects_a_partial_detected_group(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        volume_18 = _seed_ready(session, "Blue Lock 18.cbz")
        volume_19 = _seed_ready(session, "Blue Lock 19.cbz")
        session.commit()

    response = TestClient(app).patch(
        "/api/candidate-series",
        json={
            "group_id": "series:blue-lock",
            "candidate_ids": [volume_18],
            "series": "Blue Lock",
        },
    )

    assert response.status_code == 409
    with database.session() as session:
        candidates = session.scalars(select(Candidate).order_by(Candidate.id)).all()
        assert [candidate.comic_metadata for candidate in candidates] == [{}, {}]
        assert {candidate.id for candidate in candidates} == {volume_18, volume_19}


def test_candidate_series_folder_group_id_round_trips_for_long_paths(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    folder = "Long shelf " + "ﬃ" * 1000
    with database.session() as session:
        first = _seed_ready(
            session,
            "Volume 01 [first].cbz",
            path=f"{folder}/Volume 01 [first].cbz",
        )
        second = _seed_ready(
            session,
            "Volume 02 [second].cbz",
            path=f"{folder}/Volume 02 [second].cbz",
        )
        session.commit()

    client = TestClient(app)
    group = client.get("/api/candidate-series").json()[0]
    response = client.patch(
        "/api/candidate-series",
        json={
            "group_id": group["id"],
            "candidate_ids": [first, second],
            "series": "Long Shelf",
        },
    )

    assert len(group["id"]) > 2007
    assert response.status_code == 200


def test_candidate_status_edit_keeps_the_title_override(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    candidate_id = _seed_candidate(database)

    response = TestClient(app).patch(
        f"/api/candidates/{candidate_id}", json={"status": "ignored"}
    )

    assert response.status_code == 200
    assert response.json()["title_override"] == "My override"


def test_candidate_optimization_defaults_to_pdf_on_and_epub_passthrough(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        pdf_id = _seed_ready(session, "scan.pdf")
        epub_id = _seed_ready(session, "book.epub")
        session.commit()

    candidates = {
        item["id"]: item for item in TestClient(app).get("/api/candidates").json()
    }

    assert candidates[pdf_id]["optimize"] is True
    assert candidates[epub_id]["optimize"] is False


def test_candidate_optimization_can_be_disabled_for_a_pdf(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        candidate_id = _seed_ready(session, "scan.pdf")
        session.commit()

    response = TestClient(app).patch(
        f"/api/candidates/{candidate_id}", json={"optimize": False}
    )

    assert response.status_code == 200
    assert response.json()["optimize"] is False


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


def _seed_ready(
    session,
    name: str,
    series: str | None = None,
    *,
    path: str | None = None,
    status: str = "ready",
) -> str:
    revision = Revision(
        drive_file_id=f"drive-{name}",
        fingerprint=f"fp-{name}",
        name=name,
        path=path or f"Manga/{name}",
        size=1,
        status="candidate" if status == "ready" else status,
    )
    session.add(revision)
    session.flush()
    candidate = Candidate(
        revision_id=revision.id,
        status=status,
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


def test_merged_batch_keeps_pdf_candidates_as_individual_jobs(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        session.add(AppSettings(id=1, kindle_email="reader@kindle.com"))
        chapter = _seed_ready(session, "008 - Volume 02.cbr", "Naruto")
        pdf = _seed_ready(session, "Naruto - Volume 02.pdf", "Naruto")
        session.commit()

    response = TestClient(app).post(
        "/api/batches",
        json={
            "candidate_ids": [chapter, pdf],
            "preset": PRESET,
            "merge_by_volume": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["job_count"] == 2
    with database.session() as session:
        jobs = {job.title: job for job in session.scalars(select(Job))}
        assert jobs["Naruto, Tome 02"].merged_candidate_ids == [chapter]
        pdf_job = jobs["Naruto - Volume 02.pdf"]
        assert pdf_job.candidate_id == pdf
        assert pdf_job.merged_candidate_ids is None


def test_batch_candidate_options_snapshot_ssh_transport_and_optimization(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        session.add(AppSettings(id=1))
        pdf = _seed_ready(session, "document.pdf")
        epub = _seed_ready(session, "book.epub")
        session.commit()

    response = TestClient(app).post(
        "/api/batches",
        json={
            "candidate_ids": [pdf, epub],
            "candidate_options": [
                {"candidate_id": pdf, "optimize": False},
                {"candidate_id": epub, "optimize": False},
            ],
            "preset": PRESET,
        },
    )

    assert response.status_code == 201
    with database.session() as session:
        jobs = {job.candidate_id: job for job in session.scalars(select(Job))}
        assert jobs[pdf].optimize is False
        assert jobs[epub].optimize is False
        assert {job.delivery_transport for job in jobs.values()} == {"ssh"}


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


def test_sent_job_can_be_retried_with_a_corrected_preset(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        candidate_id = _seed_ready(session, "volume.cbz")
        batch = Batch(preset=PRESET, status="completed")
        session.add(batch)
        session.flush()
        job = Job(
            batch_id=batch.id,
            candidate_id=candidate_id,
            status="sent",
            preset=PRESET,
            title="Volume",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    corrected_preset = {
        **PRESET,
        "reading_direction": "ltr",
        "crop_mode": "none",
    }
    response = TestClient(app).post(
        f"/api/jobs/{job_id}/retry",
        json={"preset": corrected_preset},
    )

    assert response.status_code == 201
    jobs = TestClient(app).get("/api/jobs").json()
    replacement = next(job for job in jobs if job["id"] == response.json()["id"])
    original = next(job for job in jobs if job["id"] == job_id)
    assert replacement["status"] == "queued"
    assert replacement["preset"] == corrected_preset
    assert original["status"] == "sent"
    assert original["preset"] == PRESET


def test_retry_rejects_a_candidate_that_already_has_an_active_job(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        candidate_id = _seed_ready(session, "volume.cbz")
        previous_batch = Batch(preset=PRESET, status="completed")
        active_batch = Batch(preset=PRESET, status="processing")
        session.add_all([previous_batch, active_batch])
        session.flush()
        previous = Job(
            batch_id=previous_batch.id,
            candidate_id=candidate_id,
            status="sent",
            preset=PRESET,
            title="Volume",
        )
        session.add(previous)
        session.add(
            Job(
                batch_id=active_batch.id,
                candidate_id=candidate_id,
                status="queued",
                preset=PRESET,
                title="Volume",
            )
        )
        session.commit()
        previous_id = previous.id

    response = TestClient(app).post(
        f"/api/jobs/{previous_id}/retry",
        json={"preset": PRESET},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "A Conversion Job for this Candidate is already active"


def test_cancel_queued_merged_job_restores_candidates_and_completes_batch(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        lead = _seed_ready(session, "008 - Volume 02.cbr", "Naruto")
        later = _seed_ready(session, "009 - Volume 02.cbr", "Naruto")
        for candidate in session.scalars(select(Candidate)):
            candidate.status = "queued"
        batch = Batch(preset=PRESET)
        session.add(batch)
        session.flush()
        job = Job(
            batch_id=batch.id,
            candidate_id=lead,
            preset=PRESET,
            title="Naruto, Tome 02",
            merged_candidate_ids=[lead, later],
        )
        session.add(job)
        session.commit()
        job_id = job.id
        batch_id = batch.id

    response = TestClient(app).post(f"/api/jobs/{job_id}/cancel")

    assert response.status_code == 200
    assert response.json() == {"status": "cancelled"}
    with database.session() as session:
        assert session.get(Job, job_id).status == "cancelled"
        assert {candidate.status for candidate in session.scalars(select(Candidate))} == {"ready"}
        batch = session.get(Batch, batch_id)
        assert batch.status == "completed_with_errors"
        assert batch.completed_at is not None


def test_cancel_rejects_non_queued_job(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    with database.session() as session:
        candidate_id = _seed_ready(session, "volume.cbz")
        batch = Batch(preset=PRESET)
        session.add(batch)
        session.flush()
        job = Job(
            batch_id=batch.id,
            candidate_id=candidate_id,
            status="sent",
            preset=PRESET,
            title="Volume",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    response = TestClient(app).post(f"/api/jobs/{job_id}/cancel")

    assert response.status_code == 409
    assert response.json()["detail"] == "This job can no longer be cancelled"


def test_cancel_rejects_unknown_job(tmp_path) -> None:
    app = create_app(Database(f"sqlite:///{tmp_path / 'test.db'}"))

    response = TestClient(app).post("/api/jobs/unknown/cancel")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_email_fallback_cancels_pending_ssh_delivery_and_queues_gmail_job(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(database)
    artifact_file = tmp_path / "volume.cbz"
    artifact_file.write_bytes(b"cbz")
    with database.session() as session:
        session.add(AppSettings(id=1, kindle_email="reader@kindle.com"))
        candidate_id = _seed_ready(session, "volume.cbz")
        candidate = session.get(Candidate, candidate_id)
        candidate.status = "queued"
        batch = Batch(preset=PRESET)
        session.add(batch)
        session.flush()
        job = Job(
            batch_id=batch.id,
            candidate_id=candidate_id,
            status="waiting_for_space",
            preset=PRESET,
            title="Volume",
            optimize=True,
            delivery_transport="ssh",
        )
        session.add(job)
        session.flush()
        artifact = Artifact(
            job_id=job.id,
            filename="volume.cbz",
            path=str(artifact_file),
            size=3,
        )
        session.add(artifact)
        session.flush()
        delivery = Delivery(
            artifact_id=artifact.id, status="waiting_for_space", transport="ssh"
        )
        session.add(delivery)
        session.commit()
        delivery_id = delivery.id

    response = TestClient(app).post(
        f"/api/deliveries/{delivery_id}/email-fallback"
    )

    assert response.status_code == 201
    with database.session() as session:
        assert session.get(Delivery, delivery_id).status == "cancelled"
        replacement = session.get(Job, response.json()["id"])
        assert replacement.delivery_transport == "gmail"
        assert replacement.optimize is True
        assert replacement.status == "queued"
        old_delivery = session.get(Delivery, delivery_id)
        source_batch = old_delivery.artifact.job.batch
        assert source_batch.status == "completed_with_errors"
        assert source_batch.completed_at is not None
    assert not artifact_file.exists()


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
