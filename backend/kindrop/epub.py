"""Post-process KCC EPUB artifacts: library metadata and an optional custom cover."""

import io
import posixpath
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"


class EpubMetadataError(RuntimeError):
    pass


def _opf_path(archive: ZipFile) -> str:
    try:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
    except (KeyError, ElementTree.ParseError) as error:
        raise EpubMetadataError("The EPUB container is unreadable") from error
    rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise EpubMetadataError("The EPUB has no rootfile declaration")
    return rootfile.get("full-path")


def _set_meta(metadata: ElementTree.Element, name: str, content: str) -> None:
    for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
        if meta.get("name") == name:
            meta.set("content", content)
            return
    ElementTree.SubElement(metadata, f"{{{OPF_NS}}}meta", {"name": name, "content": content})


def _set_dc(metadata: ElementTree.Element, tag: str, value: str) -> None:
    element = metadata.find(f"{{{DC_NS}}}{tag}")
    if element is None:
        element = ElementTree.SubElement(metadata, f"{{{DC_NS}}}{tag}")
    element.text = value


def _cover_href(opf: ElementTree.Element) -> str | None:
    manifest = opf.find(f"{{{OPF_NS}}}manifest")
    if manifest is None:
        return None
    items = manifest.findall(f"{{{OPF_NS}}}item")
    for item in items:
        if "cover-image" in (item.get("properties") or "").split():
            return item.get("href")
    metadata = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata is not None:
        for meta in metadata.findall(f"{{{OPF_NS}}}meta"):
            if meta.get("name") == "cover":
                cover_id = meta.get("content")
                for item in items:
                    if item.get("id") == cover_id:
                        return item.get("href")
    return None


def _convert_cover(cover: bytes, href: str) -> bytes:
    """Re-encode the downloaded cover to match the extension the manifest declares."""
    from PIL import Image

    wants_png = href.lower().endswith(".png")
    with Image.open(io.BytesIO(cover)) as image:
        image = image.convert("RGB")
        image.thumbnail((1600, 2560))
        buffer = io.BytesIO()
        if wants_png:
            image.save(buffer, format="PNG")
        else:
            image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def apply_epub_metadata(
    path: Path,
    *,
    title: str,
    author: str | None = None,
    series: str | None = None,
    number: str | None = None,
    cover: bytes | None = None,
) -> None:
    """Rewrite the EPUB in place with library metadata and an optional replacement cover."""
    try:
        with ZipFile(path) as archive:
            opf_name = _opf_path(archive)
            opf = ElementTree.fromstring(archive.read(opf_name))
            names = archive.namelist()
    except EpubMetadataError:
        raise
    except Exception as error:
        raise EpubMetadataError("The EPUB artifact could not be opened") from error

    metadata = opf.find(f"{{{OPF_NS}}}metadata")
    if metadata is None:
        raise EpubMetadataError("The EPUB has no metadata section")

    _set_dc(metadata, "title", title)
    if author:
        _set_dc(metadata, "creator", author)
    if series:
        _set_meta(metadata, "calibre:series", series)
        if number:
            digits = "".join(ch for ch in number if ch.isdigit() or ch == ".")
            if digits:
                _set_meta(metadata, "calibre:series_index", digits)

    cover_name: str | None = None
    cover_bytes: bytes | None = None
    if cover is not None:
        href = _cover_href(opf)
        if href:
            cover_name = posixpath.normpath(posixpath.join(posixpath.dirname(opf_name), href))
            if cover_name in names:
                cover_bytes = _convert_cover(cover, href)
            else:
                cover_name = None

    ElementTree.register_namespace("", OPF_NS)
    ElementTree.register_namespace("dc", DC_NS)
    opf_bytes = ElementTree.tostring(opf, encoding="utf-8", xml_declaration=True)

    replacements = {opf_name: opf_bytes}
    if cover_name and cover_bytes is not None:
        replacements[cover_name] = cover_bytes

    with (
        ZipFile(path) as source,
        NamedTemporaryFile(dir=path.parent, suffix=".epub", delete=False) as handle,
    ):
        rewritten = Path(handle.name)
        with ZipFile(handle, "w") as target:
            if "mimetype" in names:
                target.writestr("mimetype", source.read("mimetype"), compress_type=ZIP_STORED)
            for item in source.infolist():
                if item.filename == "mimetype":
                    continue
                content = replacements.get(item.filename, source.read(item.filename))
                target.writestr(item.filename, content, compress_type=ZIP_DEFLATED)
    shutil.move(rewritten, path)
