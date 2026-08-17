from enum import StrEnum

from pydantic import BaseModel, Field


class ReadingDirection(StrEnum):
    RTL = "rtl"
    LTR = "ltr"


class SpreadMode(StrEnum):
    SPLIT = "split"
    ROTATE = "rotate"
    BOTH = "both"


class CropMode(StrEnum):
    NONE = "none"
    MARGINS = "margins"
    MARGINS_AND_PAGE_NUMBERS = "margins_and_page_numbers"


class ConversionPreset(BaseModel):
    kindle_profile: str = Field(default="KPW6", min_length=1, max_length=32)
    reading_direction: ReadingDirection = ReadingDirection.RTL
    spread_mode: SpreadMode = SpreadMode.BOTH
    crop_mode: CropMode = CropMode.MARGINS_AND_PAGE_NUMBERS


def revision_fingerprint(
    drive_file_id: str, checksum: str | None, size: int, modified_time: str
) -> str:
    if checksum:
        return f"{drive_file_id}:md5:{checksum.lower()}"
    return f"{drive_file_id}:fallback:{size}:{modified_time}"
