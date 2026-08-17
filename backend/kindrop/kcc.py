from pathlib import Path

from .domain import ConversionPreset, CropMode, ReadingDirection, SpreadMode

SPREAD_VALUES = {SpreadMode.SPLIT: "0", SpreadMode.ROTATE: "1", SpreadMode.BOTH: "2"}
CROP_VALUES = {
    CropMode.NONE: "0",
    CropMode.MARGINS: "1",
    CropMode.MARGINS_AND_PAGE_NUMBERS: "2",
}


def build_kcc_command(
    source: Path, output_directory: Path, preset: ConversionPreset, title: str
) -> list[str]:
    command = [
        "c2e",
        "--profile",
        preset.kindle_profile,
        "--format",
        "EPUB",
        "--nokepub",
    ]
    if preset.reading_direction is ReadingDirection.RTL:
        command.append("--manga-style")
    command.extend(
        [
            "--splitter",
            SPREAD_VALUES[preset.spread_mode],
            "--cropping",
            CROP_VALUES[preset.crop_mode],
            "--batchsplit",
            "1",
            "--targetsize",
            "20",
            "--title",
            title,
            "--output",
            str(output_directory),
            str(source),
        ]
    )
    return command
