"""Build a merged CBZ source from several chapter archives."""

import shutil
import subprocess
from pathlib import Path
from zipfile import BadZipFile, ZipFile

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
JUNK_NAMES = {"__MACOSX", ".DS_Store", "Thumbs.db", "thumbs.db"}
EXTRACT_TIMEOUT_SECONDS = 120


class ArchiveExtractionError(RuntimeError):
    pass


def _run_extractor(command: list[str]) -> bool:
    try:
        result = subprocess.run(command, capture_output=True, timeout=EXTRACT_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _extract_zip(archive: Path, staging: Path) -> bool:
    try:
        with ZipFile(archive) as source:
            source.extractall(staging)
        return True
    except BadZipFile:
        return False


def extract_archive_images(archive: Path, destination: Path) -> int:
    """Extract only the image pages of a CBR/CBZ into destination, flattened.

    CBZ archives are read with the standard library; anything else goes through
    7z first, then unrar — the RAR method used by many CBR releases is not
    supported by p7zip but is by unrar, so the fallback is load-bearing.
    """
    if archive.suffix.lower() == ".pdf":
        raise ArchiveExtractionError(f"{archive.name} is a PDF and cannot be merged into a volume")
    staging = destination / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    extracted = (
        _extract_zip(archive, staging)
        or _run_extractor(["7z", "x", "-y", f"-o{staging}", str(archive)])
        or _run_extractor(["unrar", "x", "-y", str(archive), f"{staging}/"])
    )
    if not extracted:
        shutil.rmtree(staging, ignore_errors=True)
        raise ArchiveExtractionError(f"{archive.name} could not be extracted with 7z or unrar")
    count = 0
    for path in sorted(staging.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(staging).parts
        if any(part in JUNK_NAMES for part in parts):
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        shutil.move(str(path), destination / "-".join(parts))
        count += 1
    shutil.rmtree(staging, ignore_errors=True)
    if count == 0:
        raise ArchiveExtractionError(f"{archive.name} contains no images")
    return count


def build_volume_archive(members: list[Path], workdir: Path) -> Path:
    """Merge chapter archives, in reading order, into one CBZ.

    Each chapter lands in a zero-padded subdirectory so KCC keeps the page
    order across chapters and can mark chapter breaks.
    """
    merged_root = workdir / "merged"
    for index, member in enumerate(members, start=1):
        chapter_directory = merged_root / f"{index:03d}"
        chapter_directory.mkdir(parents=True, exist_ok=True)
        extract_archive_images(member, chapter_directory)
    target = workdir / "volume.cbz"
    with ZipFile(target, "w") as archive:
        for path in sorted(merged_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(merged_root).as_posix())
    shutil.rmtree(merged_root, ignore_errors=True)
    return target
