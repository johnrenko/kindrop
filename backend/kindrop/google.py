import base64
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from .crypto import SecretStore
from .database import Database
from .models import AppSettings
from .services import AmbiguousSendError, DriveComic, TransientSendError

GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class DriveFolder:
    id: str
    name: str


@dataclass(frozen=True)
class FolderPage:
    folders: list[DriveFolder]
    next_page_token: str | None


@dataclass(frozen=True)
class GmailMessage:
    id: str
    sender: str
    subject: str
    text: str
    internal_date: datetime | None = None


class GoogleServiceFactory:
    def __init__(self, database: Database, secret_store: SecretStore) -> None:
        self.database = database
        self.secret_store = secret_store

    def credentials(self) -> Credentials:
        with self.database.session() as session:
            settings = session.get(AppSettings, 1)
            if not settings or not settings.encrypted_google_token:
                raise RuntimeError("Connect a Google account first")
            token = self.secret_store.decrypt_json(settings.encrypted_google_token)
        credentials = Credentials.from_authorized_user_info(token, GOOGLE_SCOPES)
        return credentials

    def drive(self):
        return build("drive", "v3", credentials=self.credentials(), cache_discovery=False)

    def gmail(self):
        return build("gmail", "v1", credentials=self.credentials(), cache_discovery=False)


class GoogleDriveGateway:
    def __init__(self, services: GoogleServiceFactory) -> None:
        self.services = services

    def list_folders(self, parent_id: str, page_token: str | None = None) -> FolderPage:
        response = (
            self.services.drive()
            .files()
            .list(
                q=f"'{parent_id}' in parents and mimeType='{FOLDER_MIME_TYPE}' and trashed=false",
                spaces="drive",
                corpora="user",
                fields="nextPageToken,files(id,name)",
                orderBy="name_natural",
                pageSize=100,
                pageToken=page_token,
            )
            .execute()
        )
        return FolderPage(
            folders=[
                DriveFolder(id=item["id"], name=item["name"]) for item in response.get("files", [])
            ],
            next_page_token=response.get("nextPageToken"),
        )

    def folder_name(self, folder_id: str) -> str:
        item = (
            self.services.drive()
            .files()
            .get(fileId=folder_id, fields="id,name,mimeType,trashed")
            .execute()
        )
        if item.get("trashed") or item.get("mimeType") != FOLDER_MIME_TYPE:
            raise ValueError("The selected Drive item is not an available folder")
        return item["name"]

    def walk_comics(self, folder_id: str) -> Iterator[DriveComic]:
        drive = self.services.drive()
        queue: deque[tuple[str, str]] = deque([(folder_id, "")])
        while queue:
            parent_id, parent_path = queue.popleft()
            page_token: str | None = None
            while True:
                response = (
                    drive.files()
                    .list(
                        q=f"'{parent_id}' in parents and trashed=false",
                        spaces="drive",
                        corpora="user",
                        fields=(
                            "nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime)"
                        ),
                        orderBy="folder,name_natural",
                        pageSize=1000,
                        pageToken=page_token,
                    )
                    .execute()
                )
                for item in response.get("files", []):
                    item_path = f"{parent_path}/{item['name']}".lstrip("/")
                    if item.get("mimeType") == FOLDER_MIME_TYPE:
                        queue.append((item["id"], item_path))
                    elif Path(item["name"]).suffix.lower() in {".cbr", ".cbz"}:
                        yield DriveComic(
                            file_id=item["id"],
                            name=item["name"],
                            path=item_path,
                            size=int(item.get("size", 0)),
                            checksum=item.get("md5Checksum"),
                            modified_time=item.get("modifiedTime", ""),
                        )
                page_token = response.get("nextPageToken")
                if not page_token:
                    break

    def download(self, file_id: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = self.services.drive().files().get_media(fileId=file_id)
        with destination.open("wb") as stream:
            downloader = MediaIoBaseDownload(stream, request, chunksize=4 * 1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk()


class GoogleGmailGateway:
    def __init__(self, services: GoogleServiceFactory) -> None:
        self.services = services

    def profile_email(self) -> str:
        profile = self.services.gmail().users().getProfile(userId="me").execute()
        return profile["emailAddress"]

    def send_epub(self, recipient: str, subject: str, artifact: Path) -> str:
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(
            f"Kindrop prepared {artifact.name}. This is a personal Send to Kindle delivery."
        )
        message.add_attachment(
            artifact.read_bytes(),
            maintype="application",
            subtype="epub+zip",
            filename=artifact.name,
        )
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        try:
            response = (
                self.services.gmail()
                .users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
        except HttpError as error:
            if error.resp.status == 429:
                raise TransientSendError("Gmail temporarily throttled the request") from error
            raise AmbiguousSendError(
                f"Gmail returned HTTP {error.resp.status} after the send request"
            ) from error
        except (TimeoutError, ConnectionError, OSError) as error:
            raise AmbiguousSendError("The Gmail send response was not received") from error
        return response["id"]

    def find_amazon_messages(self) -> list[GmailMessage]:
        gmail = self.services.gmail()
        response = (
            gmail.users()
            .messages()
            .list(userId="me", q='"Send to Kindle" newer_than:7d', maxResults=100)
            .execute()
        )
        messages: list[GmailMessage] = []
        for item in response.get("messages", []):
            raw = gmail.users().messages().get(userId="me", id=item["id"], format="raw").execute()
            internal_date: datetime | None = None
            if raw.get("internalDate"):
                internal_date = datetime.fromtimestamp(int(raw["internalDate"]) / 1000, UTC)
            payload = base64.urlsafe_b64decode(raw["raw"].encode())
            parsed = BytesParser(policy=policy.default).parsebytes(payload)
            text_parts: list[str] = []
            for part in parsed.walk():
                if part.get_content_type() in {"text/plain", "text/html"}:
                    try:
                        text_parts.append(part.get_content())
                    except (LookupError, UnicodeDecodeError):
                        continue
            messages.append(
                GmailMessage(
                    id=item["id"],
                    sender=str(parsed.get("From", "")),
                    subject=str(parsed.get("Subject", "")),
                    text="\n".join(text_parts),
                    internal_date=internal_date,
                )
            )
        return messages
