from zipfile import ZipFile

import pytest
from PIL import Image

from kindrop.metadata import ArchiveMetadataError
from kindrop.preview import extract_preview


def test_extract_preview_downscales_the_first_cbz_page(tmp_path) -> None:
    archive = tmp_path / "volume.cbz"
    page = tmp_path / "page.jpg"
    Image.new("RGB", (1200, 1800), "white").save(page, format="JPEG")
    with ZipFile(archive, "w") as comic:
        comic.write(page, "001.jpg")

    destination = extract_preview(archive, tmp_path / "previews" / "volume.jpg")

    with Image.open(destination) as image:
        assert image.format == "JPEG"
        assert image.width <= 480
        assert image.height <= 720


def test_extract_preview_renders_the_first_pdf_page(tmp_path, make_pdf) -> None:
    pdf = tmp_path / "volume.pdf"
    make_pdf(pdf)

    destination = extract_preview(pdf, tmp_path / "previews" / "volume.jpg")

    with Image.open(destination) as image:
        assert image.format == "JPEG"
        assert image.width <= 480
        assert image.height <= 720


def test_extract_preview_rejects_a_corrupt_pdf(tmp_path) -> None:
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a pdf at all")

    with pytest.raises(ArchiveMetadataError, match="corrupt or unsupported"):
        extract_preview(pdf, tmp_path / "previews" / "broken.jpg")


def test_extract_preview_rejects_unknown_extensions(tmp_path) -> None:
    other = tmp_path / "volume.txt"
    other.write_bytes(b"text")

    with pytest.raises(ArchiveMetadataError, match="Only CBR, CBZ and PDF"):
        extract_preview(other, tmp_path / "previews" / "volume.jpg")
