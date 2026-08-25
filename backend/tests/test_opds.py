from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from kindrop.config import RuntimeSettings
from kindrop.database import Database
from kindrop.models import AppSettings, LibraryFile, Revision
from kindrop.opds import create_catalog_app

ATOM = "{http://www.w3.org/2005/Atom}"
AUTH = ("kindle", "correct-horse")


def build_catalog(tmp_path: Path, *, enabled: bool = True) -> TestClient:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    cache_root = tmp_path / "cache"
    with database.session() as session:
        session.add(
            AppSettings(
                id=1,
                catalog_enabled=enabled,
                catalog_username="kindle",
                catalog_password="correct-horse",
            )
        )
        for index, (title, series, downloads) in enumerate(
            [
                ("Naruto, Ch. 700", "Naruto", 0),
                ("Naruto, Ch. 701", "Naruto", 2),
                ("One Piece, Tome 1", "One Piece", 0),
            ],
            start=1,
        ):
            revision = Revision(
                drive_file_id=f"drive-{index}",
                fingerprint=f"drive-{index}:md5:x",
                name=f"{title}.cbz",
                path=f"Manga/{title}.cbz",
                size=10,
                status="candidate",
            )
            session.add(revision)
            session.flush()
            relative = f"library/{series}/{title}.cbz"
            full = cache_root / relative
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(b"comic-bytes")
            session.add(
                LibraryFile(
                    revision_id=revision.id,
                    title=title,
                    series=series,
                    status="ready",
                    path=relative,
                    format="cbz",
                    size=11,
                    mirrored_at=datetime.now(UTC),
                    download_count=downloads,
                )
            )
        session.commit()
    app = create_catalog_app(database, RuntimeSettings(cache_root=cache_root))
    return TestClient(app)


def test_catalog_requires_basic_auth(tmp_path: Path) -> None:
    client = build_catalog(tmp_path)
    assert client.get("/opds").status_code == 401
    assert client.get("/opds", auth=("kindle", "wrong")).status_code == 401
    assert client.get("/opds", auth=AUTH).status_code == 200


def test_disabled_catalog_returns_503(tmp_path: Path) -> None:
    client = build_catalog(tmp_path, enabled=False)
    assert client.get("/opds", auth=AUTH).status_code == 503


def test_root_feed_is_valid_atom_navigation(tmp_path: Path) -> None:
    client = build_catalog(tmp_path)
    response = client.get("/opds", auth=AUTH)
    assert "application/atom+xml" in response.headers["content-type"]
    feed = ElementTree.fromstring(response.text)
    titles = [entry.findtext(f"{ATOM}title") for entry in feed.findall(f"{ATOM}entry")]
    assert titles == ["Latest additions", "By series", "All"]


def test_series_feeds_group_and_mark_downloaded_files(tmp_path: Path) -> None:
    client = build_catalog(tmp_path)
    series = ElementTree.fromstring(client.get("/opds/series", auth=AUTH).text)
    names = [entry.findtext(f"{ATOM}title") for entry in series.findall(f"{ATOM}entry")]
    assert names == ["Naruto", "One Piece"]

    naruto = ElementTree.fromstring(client.get("/opds/series/Naruto", auth=AUTH).text)
    titles = [entry.findtext(f"{ATOM}title") for entry in naruto.findall(f"{ATOM}entry")]
    assert titles == ["Naruto, Ch. 700", "✓ Naruto, Ch. 701"]
    links = naruto.findall(f"{ATOM}entry/{ATOM}link")
    assert all(link.get("type") == "application/vnd.comicbook+zip" for link in links)


def test_download_streams_the_file_and_counts_it(tmp_path: Path) -> None:
    client = build_catalog(tmp_path)
    feed = ElementTree.fromstring(client.get("/opds/all", auth=AUTH).text)
    href = feed.find(f"{ATOM}entry/{ATOM}link").get("href")

    response = client.get(href, auth=AUTH)

    assert response.status_code == 200
    assert response.content == b"comic-bytes"
    latest = ElementTree.fromstring(client.get("/opds/latest", auth=AUTH).text)
    marked = [entry.findtext(f"{ATOM}title") for entry in latest.findall(f"{ATOM}entry")]
    assert any(title.startswith("✓ ") for title in marked)


def test_download_of_missing_or_unready_file_is_404(tmp_path: Path) -> None:
    client = build_catalog(tmp_path)
    assert client.get("/opds/download/unknown-id", auth=AUTH).status_code == 404


def test_series_names_with_slashes_are_routable(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    cache_root = tmp_path / "cache"
    with database.session() as session:
        session.add(
            AppSettings(
                id=1,
                catalog_enabled=True,
                catalog_username="kindle",
                catalog_password="correct-horse",
            )
        )
        revision = Revision(
            drive_file_id="drive-slash",
            fingerprint="drive-slash:md5:x",
            name="Fate_Zero_Tome_1.cbz",
            path="Manga/Fate_Zero_Tome_1.cbz",
            size=10,
            status="candidate",
        )
        session.add(revision)
        session.flush()
        relative = "library/fatezerocache/Fate_Zero_Tome_1.cbz"
        full = cache_root / relative
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(b"comic-bytes")
        session.add(
            LibraryFile(
                revision_id=revision.id,
                title="Fate/Zero, Tome 1",
                series="Fate/Zero",
                status="ready",
                path=relative,
                format="cbz",
                size=11,
                mirrored_at=datetime.now(UTC),
            )
        )
        session.commit()
    app = create_catalog_app(database, RuntimeSettings(cache_root=cache_root))
    client = TestClient(app)
    series_nav = ElementTree.fromstring(client.get("/opds/series", auth=AUTH).text)
    entries = [entry for entry in series_nav.findall(f"{ATOM}entry")]
    fate_zero_entry = next(
        (e for e in entries if e.findtext(f"{ATOM}title") == "Fate/Zero"), None
    )
    assert fate_zero_entry is not None
    href = fate_zero_entry.find(f"{ATOM}link").get("href")
    series_response = client.get(href, auth=AUTH)
    assert series_response.status_code == 200
    series_feed = ElementTree.fromstring(series_response.text)
    titles = [entry.findtext(f"{ATOM}title") for entry in series_feed.findall(f"{ATOM}entry")]
    assert "Fate/Zero, Tome 1" in titles
