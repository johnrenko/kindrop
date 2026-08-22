from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .domain import ConversionPreset


class SetupStatus(BaseModel):
    client_configured: bool
    google_connected: bool
    google_email: str | None
    source_folder_configured: bool
    kindle_destination_configured: bool
    ready: bool


class GoogleClientPayload(BaseModel):
    credentials: dict[str, Any]


class OAuthStart(BaseModel):
    authorization_url: str


class SettingsRead(BaseModel):
    google_email: str | None
    source_folder_id: str | None
    source_folder_name: str | None
    kindle_email: str | None
    preset: ConversionPreset


class SettingsUpdate(BaseModel):
    source_folder_id: str | None = None
    source_folder_name: str | None = None
    kindle_email: EmailStr | None = None
    preset: ConversionPreset


class FolderRead(BaseModel):
    id: str
    name: str


class FolderPageRead(BaseModel):
    folders: list[FolderRead]
    next_page_token: str | None


class ScanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: str
    progress: int
    discovered_count: int
    processed_count: int
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class CandidateUpdate(BaseModel):
    """Omitted fields are left unchanged; an empty string clears the stored value."""

    title_override: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default=None, pattern="^(ready|ignored)$")
    series: str | None = Field(default=None, max_length=500)
    number: str | None = Field(default=None, max_length=50)
    author: str | None = Field(default=None, max_length=500)
    cover_url: str | None = Field(default=None, max_length=2000)


class MangaMatchRead(BaseModel):
    anilist_id: int
    title: str
    native_title: str | None
    author: str | None
    cover_url: str | None
    format: str | None
    year: int | None


class CandidateRead(BaseModel):
    id: str
    status: str
    resolved_title: str
    title_override: str | None
    metadata: dict[str, Any]
    cache_expires_at: datetime | None
    error: str | None
    drive_file_id: str
    name: str
    path: str
    size: int
    fingerprint: str


class JobRead(BaseModel):
    id: str
    batch_id: str
    status: str
    title: str
    preset: ConversionPreset
    merged_count: int | None = None
    progress: int
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    deliveries: list[dict[str, Any]]
