from pathlib import Path
from zipfile import ZipFile

import pytest

from kindrop import archives
from kindrop.archives import ArchiveExtractionError, build_volume_archive, extract_archive_images
from kindrop.metadata import volume_number


def test_volume_number_reads_release_filenames() -> None:
    assert volume_number("033 - Volume 05.cbr") == 5
    assert volume_number("700 - Volume 71.cbr") == 71
    assert volume_number("Naruto v12.cbz") == 12
    assert volume_number("oneshot.cbz") is None


def make_chapter(path: Path, prefix: str) -> None:
    with ZipFile(path, "w") as chapter:
        chapter.writestr("ComicInfo.xml", "<ComicInfo/>")
        chapter.writestr(f"{prefix}-01.jpg", b"page-one")
        chapter.writestr(f"nested/{prefix}-02.jpg", b"page-two")
        chapter.writestr("credits.txt", b"junk")


def test_extract_archive_images_keeps_only_flattened_pages(tmp_path: Path) -> None:
    archive = tmp_path / "001 - Volume 01.cbz"
    make_chapter(archive, "001")
    destination = tmp_path / "out"
    destination.mkdir()

    count = extract_archive_images(archive, destination)

    assert count == 2
    assert sorted(item.name for item in destination.iterdir()) == [
        "001-01.jpg",
        "nested-001-02.jpg",
    ]


def test_extract_archive_images_falls_back_to_unrar(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "001 - Volume 01.cbr"
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00not-a-zip")
    destination = tmp_path / "out"
    destination.mkdir()
    commands: list[str] = []

    def fake_run(command, capture_output, timeout):  # noqa: ANN001 - test stub
        commands.append(command[0])

        class Result:
            returncode = 1 if command[0] == "7z" else 0

        if command[0] == "unrar":
            staging = destination / ".staging"
            (staging / "001-01.jpg").write_bytes(b"page")
        return Result()

    monkeypatch.setattr(archives.subprocess, "run", fake_run)

    assert extract_archive_images(archive, destination) == 1
    assert commands == ["7z", "unrar"]
    assert (destination / "001-01.jpg").exists()


def test_extract_archive_images_reports_the_failing_archive(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "013 - Volume 02.cbr"
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00not-a-zip")
    destination = tmp_path / "out"
    destination.mkdir()

    def fake_run(command, capture_output, timeout):  # noqa: ANN001 - test stub
        class Result:
            returncode = 2

        return Result()

    monkeypatch.setattr(archives.subprocess, "run", fake_run)

    with pytest.raises(ArchiveExtractionError, match="013 - Volume 02.cbr"):
        extract_archive_images(archive, destination)


def test_build_volume_archive_orders_chapters(tmp_path: Path) -> None:
    first = tmp_path / "008 - Volume 02.cbz"
    second = tmp_path / "009 - Volume 02.cbz"
    make_chapter(first, "008")
    make_chapter(second, "009")
    workdir = tmp_path / "work"
    workdir.mkdir()

    merged = build_volume_archive([first, second], workdir)

    assert merged.name == "volume.cbz"
    with ZipFile(merged) as archive:
        names = archive.namelist()
    assert names == sorted(names)
    assert [name for name in names if name.startswith("001/")] == [
        "001/008-01.jpg",
        "001/nested-008-02.jpg",
    ]
    assert [name for name in names if name.startswith("002/")] == [
        "002/009-01.jpg",
        "002/nested-009-02.jpg",
    ]
    assert not (workdir / "merged").exists()
