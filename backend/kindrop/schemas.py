from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .domain import ConversionPreset


class SetupStatus(BaseModel):
    client_configured: bool
    google_connected: bool
    google_email: str | None
    source_folder_configured: bool
    kindle_destination_configured: bool
    ssh_destination_configured: bool
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
    ssh_host: str
    ssh_port: int
    ssh_user: str
    ssh_key_path: str
    ssh_known_hosts_path: str
    ssh_destination: str
    kindle_reserve_mib: int
    preset: ConversionPreset


class SettingsUpdate(BaseModel):
    source_folder_id: str | None = None
    source_folder_name: str | None = None
    kindle_email: EmailStr | None = None
    ssh_host: str = Field(default="192.168.1.53", min_length=1, max_length=253)
    ssh_port: int = Field(default=2222, ge=1, le=65535)
    ssh_user: str = Field(default="root", min_length=1, max_length=100)
    ssh_key_path: str = Field(
        default="/run/secrets/kindle_ssh_key", min_length=1, max_length=2000
    )
    ssh_known_hosts_path: str = Field(
        default="/data/kindle_known_hosts", min_length=1, max_length=2000
    )
    ssh_destination: str = Field(
        default="/mnt/us/documents/KOReader/Kindrop", min_length=1, max_length=2000
    )
    kindle_reserve_mib: int = Field(default=100, ge=0, le=4096)
    preset: ConversionPreset


class SshStatus(BaseModel):
    configured: bool
    reachable: bool | None
    host: str | None
    port: int
    destination: str | None
    free_bytes: int | None
    capacity_unknown: bool
    detail: str | None
    tested_at: datetime | None


class SshHostKeyRead(BaseModel):
    fingerprint: str
    trusted_fingerprint: str | None = None


class SshTrustRequest(BaseModel):
    fingerprint: str = Field(min_length=8, max_length=200)


class KindleStorageItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    name: str
    path: str
    kind: Literal["directory", "file", "symlink", "other"]
    size_bytes: int
    modified_at: int | None


class KindleStorageListingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    path: str
    root: str
    parent: str | None
    items: list[KindleStorageItemRead]


class KindleStorageRenameRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    new_name: str = Field(min_length=1, max_length=255)


class KindleStorageMoveRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    destination_directory: str = Field(min_length=1, max_length=2000)


class KindleStorageDeleteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2000)


class KindleStorageMutationRead(BaseModel):
    path: str


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
    optimize: bool | None = None


class CandidateSeriesMemberRead(BaseModel):
    candidate_id: str
    name: str
    number: int


class CandidateSeriesRead(BaseModel):
    id: str
    suggested_series: str | None
    confidence: Literal["high", "folder", "needs_name"]
    ready_count: int
    known_count: int
    first_volume: int
    last_volume: int
    missing_volumes: list[int]
    duplicate_volumes: list[int]
    members: list[CandidateSeriesMemberRead]


class CandidateSeriesApply(BaseModel):
    group_id: str = Field(min_length=1)
    candidate_ids: list[str] = Field(min_length=1)
    series: str = Field(min_length=1, max_length=500)
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
    optimize: bool


class JobRead(BaseModel):
    id: str
    batch_id: str
    status: str
    title: str
    optimize: bool
    delivery_transport: str
    preset: ConversionPreset
    merged_count: int | None = None
    progress: int
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    deliveries: list[dict[str, Any]]
