"""The OPDS Catalog: the one Kindrop surface exposed to the LAN (see ADR 0004).

Serves OPDS 1.2 (Atom) feeds of ready Library Files behind mandatory HTTP
Basic Auth, and streams the files themselves. It never calls Google APIs and
its only database writes are the download-tracking fields.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from secrets import compare_digest
from urllib.parse import quote
from xml.etree import ElementTree

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import RuntimeSettings
from .database import Database
from .models import AppSettings, LibraryFile
from .services import add_event

ATOM_NS = "http://www.w3.org/2005/Atom"
OPDS_ACQUISITION_REL = "http://opds-spec.org/acquisition"
NAVIGATION_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQUISITION_TYPE = "application/atom+xml;profile=opds-catalog;kind=acquisition"
MIME_TYPES = {"cbz": "application/vnd.comicbook+zip", "pdf": "application/pdf"}
LATEST_LIMIT = 50

security = HTTPBasic()


def _display_title(item: LibraryFile) -> str:
    return f"✓ {item.title}" if item.download_count else item.title


def _feed(feed_id: str, title: str) -> ElementTree.Element:
    feed = ElementTree.Element("feed", xmlns=ATOM_NS)
    ElementTree.SubElement(feed, "id").text = f"urn:kindrop:{feed_id}"
    ElementTree.SubElement(feed, "title").text = title
    ElementTree.SubElement(feed, "updated").text = datetime.now(UTC).isoformat()
    return feed


def _navigation_entry(feed: ElementTree.Element, title: str, href: str) -> None:
    entry = ElementTree.SubElement(feed, "entry")
    ElementTree.SubElement(entry, "title").text = title
    ElementTree.SubElement(entry, "id").text = f"urn:kindrop:{href}"
    ElementTree.SubElement(entry, "updated").text = datetime.now(UTC).isoformat()
    ElementTree.SubElement(
        entry, "link", href=href, rel="subsection", type=NAVIGATION_TYPE
    )


def _acquisition_entry(feed: ElementTree.Element, item: LibraryFile) -> None:
    entry = ElementTree.SubElement(feed, "entry")
    ElementTree.SubElement(entry, "title").text = _display_title(item)
    ElementTree.SubElement(entry, "id").text = f"urn:kindrop:library-file:{item.id}"
    updated = item.mirrored_at or item.created_at
    ElementTree.SubElement(entry, "updated").text = updated.isoformat()
    ElementTree.SubElement(
        entry,
        "link",
        href=f"/opds/download/{item.id}",
        rel=OPDS_ACQUISITION_REL,
        type=MIME_TYPES.get(item.format or "cbz", "application/octet-stream"),
        length=str(item.size),
    )


def _atom_response(feed: ElementTree.Element, kind: str) -> Response:
    payload = ElementTree.tostring(feed, encoding="unicode", xml_declaration=True)
    return Response(content=payload, media_type=kind)


def create_catalog_app(database: Database, runtime: RuntimeSettings | None = None) -> FastAPI:
    runtime = runtime or RuntimeSettings()
    app = FastAPI(title="Kindrop Catalog", version="0.1.0")

    def session_dependency() -> Generator[Session, None, None]:
        with database.session() as session:
            yield session

    def authenticated(
        credentials: HTTPBasicCredentials = Depends(security),
        session: Session = Depends(session_dependency),
    ) -> None:
        settings = session.get(AppSettings, 1)
        if not settings or not settings.catalog_enabled or not settings.catalog_password:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The Catalog is disabled; enable it in Kindrop's Settings",
            )
        username_matches = compare_digest(
            credentials.username, settings.catalog_username or ""
        )
        password_matches = compare_digest(credentials.password, settings.catalog_password)
        if not (username_matches and password_matches):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Wrong Catalog credentials",
                headers={"WWW-Authenticate": 'Basic realm="Kindrop Catalog"'},
            )

    def _ready_files():
        return select(LibraryFile).where(LibraryFile.status == "ready")

    @app.get("/opds", dependencies=[Depends(authenticated)])
    def root() -> Response:
        feed = _feed("root", "Kindrop")
        _navigation_entry(feed, "Latest additions", "/opds/latest")
        _navigation_entry(feed, "By series", "/opds/series")
        _navigation_entry(feed, "All", "/opds/all")
        return _atom_response(feed, NAVIGATION_TYPE)

    @app.get("/opds/latest", dependencies=[Depends(authenticated)])
    def latest(session: Session = Depends(session_dependency)) -> Response:
        items = session.scalars(
            _ready_files().order_by(LibraryFile.mirrored_at.desc()).limit(LATEST_LIMIT)
        ).all()
        feed = _feed("latest", "Latest additions")
        for item in items:
            _acquisition_entry(feed, item)
        return _atom_response(feed, ACQUISITION_TYPE)

    @app.get("/opds/series", dependencies=[Depends(authenticated)])
    def series_index(session: Session = Depends(session_dependency)) -> Response:
        names = session.scalars(
            select(LibraryFile.series)
            .where(LibraryFile.status == "ready")
            .distinct()
            .order_by(LibraryFile.series)
        ).all()
        feed = _feed("series", "By series")
        for name in names:
            _navigation_entry(feed, name, f"/opds/series/{quote(name)}")
        return _atom_response(feed, NAVIGATION_TYPE)

    @app.get("/opds/series/{series}", dependencies=[Depends(authenticated)])
    def series_feed(series: str, session: Session = Depends(session_dependency)) -> Response:
        items = session.scalars(
            _ready_files()
            .where(LibraryFile.series == series)
            .order_by(LibraryFile.title)
        ).all()
        feed = _feed(f"series:{series}", series)
        for item in items:
            _acquisition_entry(feed, item)
        return _atom_response(feed, ACQUISITION_TYPE)

    @app.get("/opds/all", dependencies=[Depends(authenticated)])
    def all_files(session: Session = Depends(session_dependency)) -> Response:
        items = session.scalars(_ready_files().order_by(LibraryFile.title)).all()
        feed = _feed("all", "All")
        for item in items:
            _acquisition_entry(feed, item)
        return _atom_response(feed, ACQUISITION_TYPE)

    @app.get("/opds/download/{library_file_id}", dependencies=[Depends(authenticated)])
    def download(
        library_file_id: str, session: Session = Depends(session_dependency)
    ) -> FileResponse:
        item = session.get(LibraryFile, library_file_id)
        if not item or item.status != "ready" or not item.path:
            raise HTTPException(status_code=404, detail="This Library File is not available")
        full_path = runtime.cache_root / item.path
        if not full_path.is_file():
            raise HTTPException(status_code=404, detail="This Library File is not available")
        item.download_count += 1
        item.last_downloaded_at = datetime.now(UTC)
        add_event(session, "library", item.id, "library.file_downloaded", title=item.title)
        session.commit()
        return FileResponse(
            full_path,
            media_type=MIME_TYPES.get(item.format or "cbz", "application/octet-stream"),
            filename=full_path.name,
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        database.ping()
        return {"status": "ok"}

    return app
