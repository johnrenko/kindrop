from pathlib import Path

from .domain import ConversionPreset, CropMode, ReadingDirection, SpreadMode

SPREAD_VALUES = {SpreadMode.SPLIT: "0", SpreadMode.ROTATE: "1", SpreadMode.BOTH: "2"}
CROP_VALUES = {
    CropMode.NONE: "0",
    CropMode.MARGINS: "1",
    CropMode.MARGINS_AND_PAGE_NUMBERS: "2",
}


def build_kcc_command(
    source: Path,
    output_directory: Path,
    preset: ConversionPreset,
    title: str,
    *,
    target_size_mb: int = 19,
    output_format: str = "EPUB",
) -> list[str]:
    command = [
        "c2e",
        "--profile",
        preset.kindle_profile,
        "--format",
        output_format,
    ]
    if output_format == "EPUB":
        command.append("--nokepub")
    if preset.reading_direction is ReadingDirection.RTL:
        command.append("--manga-style")
    command.extend(
        [
            "--splitter",
            SPREAD_VALUES[preset.spread_mode],
            "--cropping",
            CROP_VALUES[preset.crop_mode],
            # The containers mount /tmp as a 256 MiB tmpfs (compose.yaml); KCC's
            # per-page renders easily exceed it, so keep the workdir beside the
            # source on the /cache volume.
            "--tempdir",
            "--title",
            title,
            "--output",
            str(output_directory),
            str(source),
        ]
    )
    if output_format == "EPUB":
        tempdir_index = command.index("--tempdir")
        command[tempdir_index:tempdir_index] = [
            "--batchsplit",
            "1",
            "--targetsize",
            str(target_size_mb),
        ]
    return command
