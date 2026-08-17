"""Extract a small first-page preview image from a cached CBZ/CBR archive."""

import io
import subprocess
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from .metadata import ArchiveMetadataError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_PREVIEW_SOURCE_BYTES = 50 * 1024 * 1024


def _is_page(name: str) -> bool:
    path = Path(name)
    return (
        path.suffix.lower() in IMAGE_EXTENSIONS
        and not path.name.startswith(".")
        and "__macosx" not in name.lower()
    )


def _first_page_cbz(archive_path: Path) -> bytes:
    try:
        with ZipFile(archive_path) as archive:
            pages = sorted(name for name in archive.namelist() if _is_page(name))
            if not pages:
                raise ArchiveMetadataError("The archive contains no page images")
            return archive.read(pages[0])
    except BadZipFile as error:
        raise ArchiveMetadataError("The CBZ archive is corrupt") from error


def _first_page_cbr(archive_path: Path) -> bytes:
    try:
        listing = subprocess.run(
            ["7z", "l", "-ba", "-slt", str(archive_path)],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ArchiveMetadataError("The CBR archive could not be inspected") from error
    if listing.returncode != 0:
        raise ArchiveMetadataError("The CBR archive is corrupt or unsupported")
    names = [
        line[len("Path = ") :].strip()
        for line in listing.stdout.decode("utf-8", errors="replace").splitlines()
        if line.startswith("Path = ")
    ]
    pages = sorted(name for name in names if _is_page(name))
    if not pages:
        raise ArchiveMetadataError("The archive contains no page images")
    try:
        extraction = subprocess.run(
            ["7z", "e", "-so", str(archive_path), pages[0]],
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ArchiveMetadataError("The CBR page could not be extracted") from error
    if extraction.returncode != 0 or not extraction.stdout:
        raise ArchiveMetadataError("The CBR page could not be extracted")
    if len(extraction.stdout) > MAX_PREVIEW_SOURCE_BYTES:
        raise ArchiveMetadataError("The first page is unexpectedly large")
    return extraction.stdout


def extract_preview(archive_path: Path, destination: Path) -> Path:
    """Write a downscaled JPEG of the archive's first page to destination."""
    from PIL import Image

    extension = archive_path.suffix.lower()
    if extension == ".cbz":
        page = _first_page_cbz(archive_path)
    elif extension == ".cbr":
        page = _first_page_cbr(archive_path)
    else:
        raise ArchiveMetadataError("Only CBR and CBZ archives are supported")

    try:
        with Image.open(io.BytesIO(page)) as image:
            image = image.convert("RGB")
            image.thumbnail((480, 720))
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, format="JPEG", quality=78)
    except OSError as error:
        raise ArchiveMetadataError("The first page is not a readable image") from error
    return destination
