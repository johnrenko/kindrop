import io
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from PIL import Image

from kindrop.anilist import parse_search_response
from kindrop.epub import OPF_NS, apply_epub_metadata
from kindrop.metadata import format_kindle_title


def test_format_kindle_title_groups_volumes_under_the_series() -> None:
    assert format_kindle_title("Naruto", "3", "fallback") == "Naruto, Tome 3"
    assert format_kindle_title("Naruto", "03", "fallback") == "Naruto, Tome 3"
    assert format_kindle_title("Naruto", None, "fallback") == "Naruto"
    assert format_kindle_title("One Piece", "Omnibus 1", "fallback") == "One Piece, Omnibus 1"
    assert format_kindle_title(None, "3", "fallback") == "fallback"
    assert format_kindle_title("  ", None, "fallback") == "fallback"


def _image_bytes(color: str, format: str = "JPEG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 60), color).save(buffer, format=format)
    return buffer.getvalue()


OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Old title</dc:title>
    <meta name="cover" content="cover-image"/>
  </metadata>
  <manifest>
    <item id="cover-image" href="Images/cover.jpg" media-type="image/jpeg"/>
    <item id="page1" href="Images/page1.jpg" media-type="image/jpeg"/>
  </manifest>
  <spine><itemref idref="page1"/></spine>
</package>
"""

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _build_epub(path) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", OPF, compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/Images/cover.jpg", _image_bytes("black"))
        archive.writestr("OEBPS/Images/page1.jpg", _image_bytes("white"))


def test_apply_epub_metadata_rewrites_opf_and_cover(tmp_path) -> None:
    epub_path = tmp_path / "book.epub"
    _build_epub(epub_path)
    original_cover = _image_bytes("black")
    new_cover = _image_bytes("red", format="PNG")

    apply_epub_metadata(
        epub_path,
        title="Naruto, Tome 3",
        author="Masashi Kishimoto",
        series="Naruto",
        number="3",
        cover=new_cover,
    )

    with ZipFile(epub_path) as archive:
        assert archive.namelist()[0] == "mimetype"
        opf = ElementTree.fromstring(archive.read("OEBPS/content.opf"))
        metadata = opf.find(f"{{{OPF_NS}}}metadata")
        dc = "{http://purl.org/dc/elements/1.1/}"
        assert metadata.findtext(f"{dc}title") == "Naruto, Tome 3"
        assert metadata.findtext(f"{dc}creator") == "Masashi Kishimoto"
        metas = {
            meta.get("name"): meta.get("content")
            for meta in metadata.findall(f"{{{OPF_NS}}}meta")
        }
        assert metas["calibre:series"] == "Naruto"
        assert metas["calibre:series_index"] == "3"
        assert metas["cover"] == "cover-image"
        cover_bytes = archive.read("OEBPS/Images/cover.jpg")
        assert cover_bytes != original_cover
        with Image.open(io.BytesIO(cover_bytes)) as image:
            assert image.format == "JPEG"
        assert archive.read("OEBPS/Images/page1.jpg") == _image_bytes("white")


def test_apply_epub_metadata_without_cover_keeps_images(tmp_path) -> None:
    epub_path = tmp_path / "book.epub"
    _build_epub(epub_path)

    apply_epub_metadata(epub_path, title="Naruto, Tome 3", series="Naruto", number="3")

    with ZipFile(epub_path) as archive:
        assert archive.read("OEBPS/Images/cover.jpg") == _image_bytes("black")


def test_parse_search_response_extracts_story_author_and_cover() -> None:
    payload = {
        "data": {
            "Page": {
                "media": [
                    {
                        "id": 30011,
                        "format": "MANGA",
                        "startDate": {"year": 1999},
                        "title": {"romaji": "NARUTO", "english": "Naruto", "native": "ナルト"},
                        "coverImage": {"extraLarge": "https://img.anili.st/naruto.jpg"},
                        "staff": {
                            "edges": [
                                {"role": "Assistant", "node": {"name": {"full": "Somebody"}}},
                                {
                                    "role": "Story & Art",
                                    "node": {"name": {"full": "Masashi Kishimoto"}},
                                },
                            ]
                        },
                    },
                    {"id": 2, "title": {}},
                ]
            }
        }
    }

    matches = parse_search_response(payload)

    assert len(matches) == 1
    match = matches[0]
    assert match.title == "Naruto"
    assert match.native_title == "ナルト"
    assert match.author == "Masashi Kishimoto"
    assert match.cover_url == "https://img.anili.st/naruto.jpg"
    assert match.year == 1999
