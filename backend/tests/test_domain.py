from pathlib import Path
from zipfile import ZipFile

import pytest

from kindrop.amazon_mail import classify_amazon_message
from kindrop.domain import (
    ConversionPreset,
    CropMode,
    ReadingDirection,
    SpreadMode,
    revision_fingerprint,
)
from kindrop.kcc import build_kcc_command
from kindrop.metadata import ArchiveMetadataError, clean_title, read_comic_metadata


def test_revision_fingerprint_prefers_drive_checksum() -> None:
    assert revision_fingerprint("drive-1", "ABC123", 42, "2026-08-16T10:00:00Z") == (
        "drive-1:md5:abc123"
    )


def test_revision_fingerprint_falls_back_to_size_and_modified_time() -> None:
    assert revision_fingerprint("drive-1", None, 42, "2026-08-16T10:00:00Z") == (
        "drive-1:fallback:42:2026-08-16T10:00:00Z"
    )


def test_reads_comicinfo_title_without_extracting_archive(tmp_path: Path) -> None:
    archive = tmp_path / "volume.cbz"
    with ZipFile(archive, "w") as comic:
        comic.writestr("pages/001.jpg", b"image")
        comic.writestr(
            "ComicInfo.xml",
            "<ComicInfo><Series>Witch Hat Atelier</Series><Number>07</Number>"
            "<Title>The Atelier</Title></ComicInfo>",
        )

    metadata = read_comic_metadata(archive)

    assert metadata.title == "The Atelier"
    assert metadata.series == "Witch Hat Atelier"
    assert metadata.number == "07"


def test_rejects_oversized_comicinfo(tmp_path: Path) -> None:
    archive = tmp_path / "volume.cbz"
    with ZipFile(archive, "w") as comic:
        comic.writestr("ComicInfo.xml", b"x" * (1_048_576 + 1))

    with pytest.raises(ArchiveMetadataError, match="larger than 1 MB"):
        read_comic_metadata(archive)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Berserk_v01_(1990)_[Dark Horse]", "Berserk Vol. 1"),
        ("one_piece_c1045_[TCBScans]", "One Piece Ch. 1045"),
        ("20th.Century.Boys.T03", "20th Century Boys Tome 3"),
        ("Witch Hat Atelier - Chapter 007", "Witch Hat Atelier - Ch. 7"),
        ("kaguya-sama__vol.05", "Kaguya-sama Vol. 5"),
        ("Solo Leveling 012", "Solo Leveling 12"),
        ("[Group] (digital)", "[Group] (digital)"),
        ("___", "Untitled"),
    ],
)
def test_clean_title_makes_release_filenames_readable(raw: str, expected: str) -> None:
    assert clean_title(raw) == expected


def test_kcc_command_is_safe_and_reproducible() -> None:
    preset = ConversionPreset(
        kindle_profile="KPW6",
        reading_direction=ReadingDirection.RTL,
        spread_mode=SpreadMode.BOTH,
        crop_mode=CropMode.MARGINS_AND_PAGE_NUMBERS,
    )

    assert build_kcc_command(
        Path("/cache/My Volume.cbz"), Path("/cache/job"), preset, "My Volume"
    ) == [
        "c2e",
        "--profile",
        "KPW6",
        "--format",
        "EPUB",
        "--nokepub",
        "--manga-style",
        "--splitter",
        "2",
        "--cropping",
        "2",
        "--batchsplit",
        "1",
        "--targetsize",
        "20",
        "--tempdir",
        "--title",
        "My Volume",
        "--output",
        "/cache/job",
        "/cache/My Volume.cbz",
    ]


def test_amazon_rejection_is_classified_by_documented_error_code() -> None:
    result = classify_amazon_message(
        sender="Amazon Kindle <do-not-reply@amazon.com>",
        subject="There was a problem with the document you sent to Kindle",
        text="Your document could not be delivered. Error code: E007.",
    )

    assert result.kind == "rejected"
    assert result.error_code == "E007"


def test_amazon_verification_only_accepts_https_amazon_links() -> None:
    result = classify_amazon_message(
        sender="Amazon Kindle <do-not-reply@amazon.com>",
        subject="Verify your Send to Kindle request",
        text="Verify within 48 hours: https://www.amazon.com/gp/sendtokindle/verify?token=safe",
    )

    assert result.kind == "verification_required"
    assert result.verification_url == ("https://www.amazon.com/gp/sendtokindle/verify?token=safe")


def test_non_amazon_message_cannot_trigger_verification() -> None:
    result = classify_amazon_message(
        sender="Attacker <notice@example.com>",
        subject="Verify your Send to Kindle request",
        text="https://www.amazon.com/gp/sendtokindle/verify?token=stolen",
    )

    assert result.kind == "irrelevant"
