import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

MAX_COMICINFO_BYTES = 1_048_576
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"

_BRACKETED_TAGS = re.compile(r"\[[^\]]*\]|\{[^}]*\}|\([^)]*\)")
_VOLUME_MARKER = re.compile(r"\b(?:v(?:ol(?:ume)?)?|t(?:ome)?)[.\s]*0*(\d{1,4})\b", re.IGNORECASE)
_CHAPTER_MARKER = re.compile(r"\b(?:c(?:h(?:ap(?:ter)?)?)?)[.\s]*0*(\d{1,4})\b", re.IGNORECASE)
_VOLUME_IN_NAME = re.compile(r"\bvolume[.\s]*0*(\d{1,4})\b", re.IGNORECASE)


def _volume_label(match: re.Match[str]) -> str:
    prefix = "Tome" if match.group(0).lstrip()[0].lower() == "t" else "Vol."
    return f"{prefix} {int(match.group(1))}"


def clean_title(raw: str) -> str:
    """Turn a release-style filename stem into a readable Kindle library title."""
    text = _BRACKETED_TAGS.sub(" ", raw).replace("_", " ")
    if " " not in text.strip():
        text = text.replace(".", " ")
    was_lowercase = not any(character.isupper() for character in text)
    text = _VOLUME_MARKER.sub(_volume_label, text)
    text = _CHAPTER_MARKER.sub(lambda match: f"Ch. {int(match.group(1))}", text)
    text = re.sub(r"\b0+(\d)", r"\1", text)
    text = re.sub(r"(?:\s*-\s*){2,}", " - ", text)
    text = " ".join(text.split()).strip(" -_.~+")
    if was_lowercase:
        text = " ".join(word.capitalize() for word in text.split())
    return text or " ".join(raw.replace("_", " ").split()) or "Untitled"


def format_kindle_title(series: str | None, number: str | None, fallback: str) -> str:
    """Build the Kindle library title so volumes of one series sort together."""
    series = " ".join(series.split()) if series else None
    number = " ".join(number.split()) if number else None
    if not series:
        return fallback
    if not number:
        return series
    if number.isdigit():
        return f"{series}, Tome {int(number)}"
    return f"{series}, {number}"


def volume_number(filename: str) -> int | None:
    """Extract the volume number from a release filename, or None."""
    stem = Path(filename).stem
    match = _VOLUME_IN_NAME.search(stem) or _VOLUME_MARKER.search(stem)
    return int(match.group(1)) if match else None


class ArchiveMetadataError(ValueError):
    pass


def open_pdf_document(path: Path):
    """Open a PDF for inspection, rejecting anything KCC could not convert."""
    import pymupdf

    try:
        document = pymupdf.open(path, filetype="pdf")
    except (RuntimeError, ValueError) as error:
        raise ArchiveMetadataError("The PDF is corrupt or unsupported") from error
    if document.needs_pass:
        document.close()
        raise ArchiveMetadataError("The PDF is password-protected")
    if document.page_count == 0:
        document.close()
        raise ArchiveMetadataError("The PDF contains no pages")
    return document


@dataclass(frozen=True)
class ComicMetadata:
    title: str | None = None
    series: str | None = None
    number: str | None = None

    def resolved_title(self, fallback: str) -> str:
        if self.title:
            return self.title
        if self.series and self.number:
            return f"{self.series} {self.number}"
        if self.series:
            return self.series
        return fallback


def _read_epub(path: Path) -> ComicMetadata:
    try:
        with ZipFile(path) as archive:
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
            if rootfile is None or not rootfile.get("full-path"):
                raise ArchiveMetadataError("The EPUB has no rootfile declaration")
            package = ElementTree.fromstring(archive.read(rootfile.get("full-path")))
    except ArchiveMetadataError:
        raise
    except (BadZipFile, KeyError, ElementTree.ParseError) as error:
        raise ArchiveMetadataError("The EPUB is corrupt or unsupported") from error

    metadata = package.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        return ComicMetadata()
    title = _text(metadata, f"{{{DC_NS}}}title")
    series = None
    number = None
    for item in metadata.findall(f"{{{OPF_NS}}}meta"):
        if item.get("name") == "calibre:series":
            series = (item.get("content") or "").strip()[:500] or None
        elif item.get("name") == "calibre:series_index":
            number = (item.get("content") or "").strip()[:500] or None
    return ComicMetadata(title=title, series=series, number=number)


def _text(root: ElementTree.Element, tag: str) -> str | None:
    value = root.findtext(tag)
    if not value:
        return None
    normalized = " ".join(value.split())
    return normalized[:500] or None


def _parse_comicinfo(content: bytes) -> ComicMetadata:
    if len(content) > MAX_COMICINFO_BYTES:
        raise ArchiveMetadataError("ComicInfo.xml is larger than 1 MB")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ArchiveMetadataError("ComicInfo.xml is not valid XML") from error
    return ComicMetadata(
        title=_text(root, "Title"),
        series=_text(root, "Series"),
        number=_text(root, "Number"),
    )


def _read_cbz(path: Path) -> bytes | None:
    try:
        with ZipFile(path) as archive:
            matches = [
                item
                for item in archive.infolist()
                if Path(item.filename).name.lower() == "comicinfo.xml"
            ]
            if not matches:
                return None
            item = matches[0]
            if item.file_size > MAX_COMICINFO_BYTES:
                raise ArchiveMetadataError("ComicInfo.xml is larger than 1 MB")
            return archive.read(item)
    except BadZipFile as error:
        raise ArchiveMetadataError("The CBZ archive is corrupt") from error


def _read_cbr(path: Path) -> bytes | None:
    try:
        result = subprocess.run(
            ["7z", "e", "-so", str(path), "ComicInfo.xml"],
            check=False,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ArchiveMetadataError("The CBR metadata could not be inspected") from error
    if result.returncode != 0:
        if b"No files to process" in result.stderr:
            return None
        raise ArchiveMetadataError("The CBR archive is corrupt or unsupported")
    if len(result.stdout) > MAX_COMICINFO_BYTES:
        raise ArchiveMetadataError("ComicInfo.xml is larger than 1 MB")
    return result.stdout or None


def read_comic_metadata(path: Path) -> ComicMetadata:
    extension = path.suffix.lower()
    if extension == ".cbz":
        content = _read_cbz(path)
    elif extension == ".cbr":
        content = _read_cbr(path)
    elif extension == ".pdf":
        # PDFs carry no ComicInfo.xml; opening one only validates it for conversion.
        open_pdf_document(path).close()
        content = None
    elif extension == ".epub":
        return _read_epub(path)
    else:
        raise ArchiveMetadataError("Only CBR, CBZ, PDF and EPUB files are supported")
    return _parse_comicinfo(content) if content else ComicMetadata()
