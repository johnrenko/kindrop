from __future__ import annotations

import base64
import binascii
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal

DEFAULT_KINDROP_ROOT = PurePosixPath("/mnt/us/documents/KOReader/Kindrop")
DEFAULT_KINDLE_STORAGE_ROOT = PurePosixPath("/mnt/us")
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*$", re.IGNORECASE)
_STORAGE_COLLISION_EXIT = 42
_STORAGE_NOT_FOUND_EXIT = 44
_STORAGE_BOUNDARY_EXIT = 45
_SFTP_MIN_TRANSFER_BYTES_PER_SECOND = 64 * 1024
_SFTP_TRANSFER_SETUP_SECONDS = 60
_SFTP_MAX_TRANSFER_TIMEOUT_SECONDS = 60 * 60


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class KindleSshConfig:
    host: str
    key_path: Path
    known_hosts_path: Path
    port: int = 2222
    user: str = "root"
    destination_root: PurePosixPath = DEFAULT_KINDROP_ROOT
    storage_root: PurePosixPath = DEFAULT_KINDLE_STORAGE_ROOT
    connect_timeout_seconds: int = 10
    command_timeout_seconds: int = 120

    def __post_init__(self) -> None:
        if not _HOST_PATTERN.fullmatch(self.host):
            raise ValueError("SSH host must be a hostname or IP address")
        if not _USER_PATTERN.fullmatch(self.user):
            raise ValueError("SSH user contains unsupported characters")
        if not 1 <= self.port <= 65535:
            raise ValueError("SSH port must be between 1 and 65535")
        if not self.destination_root.is_absolute():
            raise ValueError("Kindle destination root must be absolute")
        if ".." in self.destination_root.parts:
            raise ValueError("Kindle destination root contains a parent traversal")
        try:
            self.destination_root.relative_to(DEFAULT_KINDROP_ROOT)
        except ValueError as error:
            raise ValueError(
                f"Kindle destination root must stay beneath {DEFAULT_KINDROP_ROOT}"
            ) from error
        if not self.storage_root.is_absolute():
            raise ValueError("Kindle storage root must be absolute")


@dataclass(frozen=True)
class KindleProbe:
    reachable: bool
    free_bytes: int | None
    detail: str | None = None


@dataclass(frozen=True)
class KindleSshTransferReceipt:
    remote_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class KindleHostKey:
    fingerprint: str
    known_hosts_line: str


@dataclass(frozen=True)
class KindleStorageItem:
    name: str
    path: str
    kind: Literal["directory", "file", "symlink", "other"]
    size_bytes: int
    modified_at: int | None


@dataclass(frozen=True)
class KindleStorageListing:
    path: str
    root: str
    parent: str | None
    items: list[KindleStorageItem]


class KindleSshError(RuntimeError):
    pass


class KindleCollisionError(KindleSshError):
    def __init__(self, remote_path: str) -> None:
        self.remote_path = remote_path
        super().__init__(f"An item already exists at {remote_path}")


class KindleIntegrityError(KindleSshError):
    pass


class KindleStorageItemNotFoundError(KindleSshError):
    pass


def _subprocess_runner(
    command: Sequence[str], *, timeout: int, input: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input,
    )


class KindleSshTransport:
    def __init__(
        self,
        config: KindleSshConfig,
        *,
        runner: CommandRunner = _subprocess_runner,
    ) -> None:
        self.config = config
        self._runner = runner

    def probe(self) -> KindleProbe:
        command = f"df -Pk {shlex.quote(str(self.config.storage_root))}"
        try:
            result = self._run(
                self._ssh_command(command), timeout=self.config.command_timeout_seconds
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return KindleProbe(reachable=False, free_bytes=None, detail=str(error))
        if result.returncode != 0:
            return KindleProbe(
                reachable=False,
                free_bytes=None,
                detail=(result.stderr or result.stdout or "SSH probe failed").strip(),
            )
        try:
            free_kib = int(result.stdout.strip().splitlines()[-1].split()[3])
        except (IndexError, ValueError):
            return KindleProbe(
                reachable=False,
                free_bytes=None,
                detail="Kindle returned an invalid free-space response",
            )
        return KindleProbe(reachable=True, free_bytes=free_kib * 1024)

    def list_storage(self, path: str | PurePosixPath) -> KindleStorageListing:
        target = self._storage_path(path)
        target_argument = shlex.quote(str(target))
        command = (
            f"target={target_argument}; "
            f"{self._storage_guard('target')}; "
            "if [ ! -d \"$target\" ]; then "
            f"printf 'NOT_DIRECTORY\\n' >&2; exit {_STORAGE_NOT_FOUND_EXIT}; fi; "
            "for entry in \"$target\"/* \"$target\"/.[!.]* \"$target\"/..?*; do "
            "if [ ! -e \"$entry\" ] && [ ! -L \"$entry\" ]; then continue; fi; "
            "name=${entry##*/}; "
            "if [ -L \"$entry\" ]; then kind=symlink; size=0; "
            "elif [ -d \"$entry\" ]; then kind=directory; size=0; "
            "elif [ -f \"$entry\" ]; then kind=file; "
            "size=$(stat -c %s \"$entry\" 2>/dev/null || wc -c < \"$entry\"); "
            "else kind=other; size=0; fi; "
            "modified=$(stat -c %Y \"$entry\" 2>/dev/null || printf 0); "
            "printf '%s\\0%s\\0%s\\0%s\\0' \"$name\" \"$kind\" \"$size\" \"$modified\"; "
            "done"
        )
        result = self._run_storage_checked(command, operation="Kindle directory listing")
        fields = result.stdout.split("\0")
        if fields and fields[-1] == "":
            fields.pop()
        if len(fields) % 4:
            raise KindleSshError("Kindle returned an invalid directory listing")
        items = []
        for index in range(0, len(fields), 4):
            name, kind, size_value, modified_value = fields[index : index + 4]
            if not name or "/" in name or kind not in {"directory", "file", "symlink", "other"}:
                raise KindleSshError("Kindle returned an invalid directory entry")
            try:
                size_bytes = max(0, int(size_value))
                modified = int(modified_value)
            except ValueError as error:
                raise KindleSshError("Kindle returned invalid file metadata") from error
            items.append(
                KindleStorageItem(
                    name=name,
                    path=str(target / name),
                    kind=kind,
                    size_bytes=size_bytes,
                    modified_at=modified if modified > 0 else None,
                )
            )
        items.sort(key=lambda item: (item.kind != "directory", item.name.casefold(), item.name))
        root = self.config.storage_root
        return KindleStorageListing(
            path=str(target),
            root=str(root),
            parent=None if target == root else str(target.parent),
            items=items,
        )

    def rename_storage_item(self, path: str | PurePosixPath, new_name: str) -> str:
        source = self._mutable_storage_path(path)
        safe_name = self._file_name(new_name)
        destination = source.parent / safe_name
        command = (
            f"source={shlex.quote(str(source))}; "
            f"source_parent={shlex.quote(str(source.parent))}; "
            f"destination={shlex.quote(str(destination))}; "
            f"{self._storage_guard('source_parent')}; "
            "if [ ! -e \"$source\" ] && [ ! -L \"$source\" ]; then "
            f"printf 'NOT_FOUND\\n' >&2; exit {_STORAGE_NOT_FOUND_EXIT}; fi; "
            "if [ -e \"$destination\" ] || [ -L \"$destination\" ]; then "
            f"printf 'COLLISION\\n' >&2; exit {_STORAGE_COLLISION_EXIT}; fi; "
            "mv \"$source\" \"$destination\""
        )
        self._run_storage_checked(
            command,
            operation="Kindle rename",
            collision_path=str(destination),
        )
        return str(destination)

    def move_storage_item(
        self,
        path: str | PurePosixPath,
        destination_directory: str | PurePosixPath,
    ) -> str:
        source = self._mutable_storage_path(path)
        destination_parent = self._storage_path(destination_directory)
        if destination_parent == source or destination_parent.is_relative_to(source):
            raise ValueError("An item cannot be moved inside itself")
        destination = destination_parent / source.name
        command = (
            f"source={shlex.quote(str(source))}; "
            f"source_parent={shlex.quote(str(source.parent))}; "
            f"destination_parent={shlex.quote(str(destination_parent))}; "
            f"destination={shlex.quote(str(destination))}; "
            f"{self._storage_guard('source_parent')}; "
            f"{self._storage_guard('destination_parent')}; "
            "if [ ! -e \"$source\" ] && [ ! -L \"$source\" ]; then "
            f"printf 'NOT_FOUND\\n' >&2; exit {_STORAGE_NOT_FOUND_EXIT}; fi; "
            "if [ ! -d \"$destination_parent\" ]; then "
            f"printf 'NOT_DIRECTORY\\n' >&2; exit {_STORAGE_NOT_FOUND_EXIT}; fi; "
            "if [ -e \"$destination\" ] || [ -L \"$destination\" ]; then "
            f"printf 'COLLISION\\n' >&2; exit {_STORAGE_COLLISION_EXIT}; fi; "
            "mv \"$source\" \"$destination\""
        )
        self._run_storage_checked(
            command,
            operation="Kindle move",
            collision_path=str(destination),
        )
        return str(destination)

    def delete_storage_item(self, path: str | PurePosixPath) -> None:
        self.delete_storage_items([path])

    def delete_storage_items(self, paths: Sequence[str | PurePosixPath]) -> None:
        targets = [self._mutable_storage_path(path) for path in paths]
        if not targets:
            raise ValueError("At least one Kindle storage item must be selected")
        if len(set(targets)) != len(targets):
            raise ValueError("Each Kindle storage item can only be selected once")

        assignments: list[str] = []
        preflight: list[str] = []
        delete_arguments: list[str] = []
        for index, target in enumerate(targets):
            target_variable = f"target_{index}"
            parent_variable = f"target_parent_{index}"
            assignments.extend(
                [
                    f"{target_variable}={shlex.quote(str(target))}",
                    f"{parent_variable}={shlex.quote(str(target.parent))}",
                ]
            )
            preflight.extend(
                [
                    self._storage_guard(parent_variable),
                    f'if [ ! -e "${target_variable}" ] && [ ! -L "${target_variable}" ]; '
                    f"then printf 'NOT_FOUND\\n' >&2; exit {_STORAGE_NOT_FOUND_EXIT}; fi",
                ]
            )
            delete_arguments.append(f'"${target_variable}"')

        script = "; ".join(
            [*assignments, *preflight, f"rm -rf {' '.join(delete_arguments)}"]
        ) + "\n"
        self._run_storage_checked(
            "sh -s",
            operation="Kindle bulk delete",
            input_text=script,
        )

    def upload_storage_item(
        self,
        local_path: Path,
        destination_directory: str | PurePosixPath,
        filename: str,
    ) -> KindleSshTransferReceipt:
        if not local_path.is_file():
            raise ValueError(f"Upload source does not exist: {local_path}")
        destination_parent = self._storage_path(destination_directory)
        safe_name = self._file_name(filename)
        command = (
            f"destination_parent={shlex.quote(str(destination_parent))}; "
            f"{self._storage_guard('destination_parent')}; "
            "if [ ! -d \"$destination_parent\" ]; then "
            f"printf 'NOT_DIRECTORY\\n' >&2; exit {_STORAGE_NOT_FOUND_EXIT}; fi"
        )
        self._run_storage_checked(command, operation="Kindle upload destination check")
        return self._upload_to_remote_path(local_path, destination_parent / safe_name)

    def inspect_host_key(self) -> KindleHostKey:
        result = self._run_command_checked(
            [
                "ssh-keyscan",
                "-T",
                str(self.config.connect_timeout_seconds),
                "-p",
                str(self.config.port),
                self.config.host,
            ],
            timeout=self.config.connect_timeout_seconds + 2,
            operation="Kindle host-key scan",
        )
        lines = sorted(
            line.strip()
            for line in result.stdout.splitlines()
            if line and not line.startswith("#")
        )
        known_hosts_line = lines[0] if lines else ""
        return self._parse_host_key(known_hosts_line)

    def pinned_host_key(self) -> KindleHostKey | None:
        target = self.config.known_hosts_path
        if not target.is_file():
            return None
        lines = sorted(
            line.strip()
            for line in target.read_text().splitlines()
            if line and not line.startswith("#")
        )
        return self._parse_host_key(lines[0]) if lines else None

    @staticmethod
    def _parse_host_key(known_hosts_line: str) -> KindleHostKey:
        fields = known_hosts_line.split()
        if len(fields) < 3:
            raise KindleSshError("Kindle returned an invalid SSH host key")
        try:
            key_bytes = base64.b64decode(fields[2], validate=True)
        except (ValueError, binascii.Error) as error:
            raise KindleSshError("Kindle returned an invalid SSH host key") from error
        fingerprint = base64.b64encode(sha256(key_bytes).digest()).decode().rstrip("=")
        return KindleHostKey(
            fingerprint=f"SHA256:{fingerprint}",
            known_hosts_line=known_hosts_line,
        )

    def trust_host_key(self, expected_fingerprint: str) -> KindleHostKey:
        host_key = self.inspect_host_key()
        if host_key.fingerprint != expected_fingerprint:
            raise ValueError("Kindle SSH host-key fingerprint changed during confirmation")
        target = self.config.known_hosts_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.kindrop-tmp")
        try:
            temporary.write_text(f"{host_key.known_hosts_line}\n")
            temporary.chmod(0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return host_key

    def deliver(
        self,
        local_path: Path,
        relative_path: str | PurePosixPath,
        *,
        replace: bool = False,
    ) -> KindleSshTransferReceipt:
        if not local_path.is_file():
            raise ValueError(f"Delivery source does not exist: {local_path}")
        remote_path = self._remote_path(relative_path)
        self._run_ssh_checked(f"mkdir -p {shlex.quote(str(remote_path.parent))}")
        return self._upload_to_remote_path(local_path, remote_path, replace=replace)

    def _upload_to_remote_path(
        self,
        local_path: Path,
        remote_path: PurePosixPath,
        *,
        replace: bool = False,
    ) -> KindleSshTransferReceipt:
        quoted_path = shlex.quote(str(remote_path))
        collision = self._run_ssh_checked(
            f"if [ -e {quoted_path} ]; then printf 'EXISTS\\n'; "
            "else printf 'MISSING\\n'; fi"
        )
        local_sha256 = self._local_sha256(local_path)
        if collision.stdout.strip() == "EXISTS" and not replace:
            raise KindleCollisionError(str(remote_path))
        temporary_path = remote_path.parent / (
            f".{remote_path.name}.{local_sha256[:12]}.kindrop-part"
        )
        self._run_command_checked(
            self._sftp_command(),
            timeout=self._sftp_transfer_timeout(local_path),
            operation="Kindle SFTP upload",
            input_text=(
                f"put {self._sftp_quote(str(local_path))} "
                f"{self._sftp_quote(str(temporary_path))}\nquit\n"
            ),
        )
        hash_result = self._run_ssh_checked(
            f"sha256sum {shlex.quote(str(temporary_path))}"
        )
        remote_hash = hash_result.stdout.split()[0] if hash_result.stdout.split() else ""
        if remote_hash != local_sha256:
            self._remove_temporary_file(temporary_path)
            raise KindleIntegrityError("Remote SHA-256 does not match the local artifact")
        temporary_argument = shlex.quote(str(temporary_path))
        remote_argument = shlex.quote(str(remote_path))
        if replace:
            self._run_ssh_checked(f"mv -f {temporary_argument} {remote_argument}")
        else:
            publish = self._run_ssh_checked(
                f"if [ -e {remote_argument} ]; then printf 'EXISTS\\n'; "
                f"else mv -f {temporary_argument} {remote_argument} && "
                "printf 'PUBLISHED\\n'; fi"
            )
            if publish.stdout.strip() != "PUBLISHED":
                self._remove_temporary_file(temporary_path)
                raise KindleCollisionError(str(remote_path))
        return KindleSshTransferReceipt(
            remote_path=str(remote_path),
            sha256=local_sha256,
            size_bytes=local_path.stat().st_size,
        )

    def _remove_temporary_file(self, temporary_path: PurePosixPath) -> None:
        # Preserve the integrity failure; a later upload reuses and overwrites this temp path.
        with suppress(KindleSshError):
            self._run_ssh_checked(f"rm -f {shlex.quote(str(temporary_path))}")

    def _remote_sha256(self, remote_path: PurePosixPath) -> str:
        result = self._run_ssh_checked(f"sha256sum {shlex.quote(str(remote_path))}")
        fields = result.stdout.split()
        return fields[0] if fields else ""

    @staticmethod
    def _local_sha256(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _remote_path(self, relative_path: str | PurePosixPath) -> PurePosixPath:
        raw_path = str(relative_path)
        if "\x00" in raw_path or "\n" in raw_path or "\r" in raw_path:
            raise ValueError("Remote path contains unsupported characters")
        relative = PurePosixPath(raw_path)
        if relative.is_absolute() or relative == PurePosixPath(".") or ".." in relative.parts:
            raise ValueError("Remote path must stay beneath the Kindrop destination root")
        return self.config.destination_root / relative

    def _storage_path(self, path: str | PurePosixPath) -> PurePosixPath:
        raw_path = str(path)
        if "\x00" in raw_path or "\n" in raw_path or "\r" in raw_path:
            raise ValueError("Kindle storage path contains unsupported characters")
        target = PurePosixPath(raw_path)
        if not target.is_absolute() or ".." in target.parts:
            raise ValueError("Kindle storage path must be absolute and cannot contain traversal")
        try:
            target.relative_to(self.config.storage_root)
        except ValueError as error:
            raise ValueError(
                f"Kindle storage path must stay beneath {self.config.storage_root}"
            ) from error
        return target

    def _mutable_storage_path(self, path: str | PurePosixPath) -> PurePosixPath:
        target = self._storage_path(path)
        if target == self.config.storage_root:
            raise ValueError("The Kindle storage root cannot be changed or deleted")
        return target

    @staticmethod
    def _file_name(name: str) -> str:
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\x00" in name
            or "\n" in name
            or "\r" in name
        ):
            raise ValueError("The new name must be a single valid file name")
        return name

    def _storage_guard(self, variable: str) -> str:
        root = shlex.quote(str(self.config.storage_root))
        return (
            f"resolved=$(readlink -f \"${variable}\") || {{ "
            f"printf 'NOT_FOUND\\n' >&2; exit {_STORAGE_NOT_FOUND_EXIT}; }}; "
            f"case \"$resolved\" in {root}|{root}/*) ;; *) "
            f"printf 'OUTSIDE_STORAGE\\n' >&2; exit {_STORAGE_BOUNDARY_EXIT} ;; esac"
        )

    def _run_storage_checked(
        self,
        remote_command: str,
        *,
        operation: str,
        collision_path: str | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._run(
                self._ssh_command(remote_command),
                timeout=self.config.command_timeout_seconds,
                input_text=input_text,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise KindleSshError(f"{operation} failed: {error}") from error
        detail = (result.stderr or result.stdout or f"{operation} failed").strip()
        if result.returncode == _STORAGE_COLLISION_EXIT and collision_path:
            raise KindleCollisionError(collision_path)
        if result.returncode == _STORAGE_NOT_FOUND_EXIT:
            raise KindleStorageItemNotFoundError(detail or "Kindle item not found")
        if result.returncode == _STORAGE_BOUNDARY_EXIT:
            raise ValueError("The resolved path leaves Kindle user storage")
        if result.returncode != 0:
            raise KindleSshError(detail)
        return result

    def _run_ssh_checked(self, remote_command: str) -> subprocess.CompletedProcess[str]:
        return self._run_command_checked(
            self._ssh_command(remote_command),
            timeout=self.config.command_timeout_seconds,
            operation="Kindle SSH command",
        )

    def _run_command_checked(
        self,
        command: Sequence[str],
        *,
        timeout: int,
        operation: str,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._run(command, timeout=timeout, input_text=input_text)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise KindleSshError(f"{operation} failed: {error}") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"{operation} failed").strip()
            raise KindleSshError(detail)
        return result

    def _run(
        self, command: Sequence[str], *, timeout: int, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        if input_text is None:
            return self._runner(command, timeout=timeout)
        return self._runner(command, timeout=timeout, input=input_text)

    def _ssh_command(self, remote_command: str) -> list[str]:
        return [
            "ssh",
            "-p",
            str(self.config.port),
            "-i",
            str(self.config.key_path),
            *self._security_options(),
            f"-oConnectTimeout={self.config.connect_timeout_seconds}",
            f"{self.config.user}@{self.config.host}",
            remote_command,
        ]

    def _sftp_command(self) -> list[str]:
        return [
            "sftp",
            "-b",
            "-",
            "-P",
            str(self.config.port),
            "-i",
            str(self.config.key_path),
            *self._security_options(),
            f"-oConnectTimeout={self.config.connect_timeout_seconds}",
            f"{self.config.user}@{self.config.host}",
        ]

    def _sftp_transfer_timeout(self, local_path: Path) -> int:
        """Allow slow Kindle Wi-Fi transfers without leaving them unbounded."""
        estimated_seconds = (
            _SFTP_TRANSFER_SETUP_SECONDS
            + (local_path.stat().st_size + _SFTP_MIN_TRANSFER_BYTES_PER_SECOND - 1)
            // _SFTP_MIN_TRANSFER_BYTES_PER_SECOND
        )
        return min(
            _SFTP_MAX_TRANSFER_TIMEOUT_SECONDS,
            max(self.config.command_timeout_seconds, estimated_seconds),
        )

    @staticmethod
    def _sftp_quote(value: str) -> str:
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("SFTP path contains unsupported characters")
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _security_options(self) -> list[str]:
        return [
            "-oBatchMode=yes",
            "-oIdentitiesOnly=yes",
            "-oPasswordAuthentication=no",
            "-oKbdInteractiveAuthentication=no",
            "-oStrictHostKeyChecking=yes",
            f"-oUserKnownHostsFile={self.config.known_hosts_path}",
        ]
