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

DEFAULT_KINDROP_ROOT = PurePosixPath("/mnt/us/documents/KOReader/Kindrop")
DEFAULT_KINDLE_STORAGE_ROOT = PurePosixPath("/mnt/us")
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]*$", re.IGNORECASE)


CommandRunner = Callable[
    [Sequence[str]],
    subprocess.CompletedProcess[str],
]


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
class KindleDeliveryReceipt:
    remote_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class KindleHostKey:
    fingerprint: str
    known_hosts_line: str


class KindleSshError(RuntimeError):
    pass


class KindleCollisionError(KindleSshError):
    def __init__(self, remote_path: str) -> None:
        self.remote_path = remote_path
        super().__init__(f"A file not managed by Kindrop already exists at {remote_path}")


class KindleIntegrityError(KindleSshError):
    pass


def _subprocess_runner(
    command: Sequence[str], *, timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class KindleSshTransport:
    def __init__(
        self,
        config: KindleSshConfig,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = _subprocess_runner,
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
    ) -> KindleDeliveryReceipt:
        if not local_path.is_file():
            raise ValueError(f"Delivery source does not exist: {local_path}")
        remote_path = self._remote_path(relative_path)
        self._run_ssh_checked(f"mkdir -p {shlex.quote(str(remote_path.parent))}")
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
            self._scp_command(local_path, temporary_path),
            timeout=self.config.command_timeout_seconds,
            operation="Kindle SCP upload",
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
        return KindleDeliveryReceipt(
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

    def _run_ssh_checked(self, remote_command: str) -> subprocess.CompletedProcess[str]:
        return self._run_command_checked(
            self._ssh_command(remote_command),
            timeout=self.config.command_timeout_seconds,
            operation="Kindle SSH command",
        )

    def _run_command_checked(
        self, command: Sequence[str], *, timeout: int, operation: str
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._run(command, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise KindleSshError(f"{operation} failed: {error}") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"{operation} failed").strip()
            raise KindleSshError(detail)
        return result

    def _run(
        self, command: Sequence[str], *, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        return self._runner(command, timeout=timeout)

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

    def _scp_command(self, local_path: Path, remote_path: PurePosixPath) -> list[str]:
        return [
            "scp",
            "-P",
            str(self.config.port),
            "-i",
            str(self.config.key_path),
            *self._security_options(),
            f"-oConnectTimeout={self.config.connect_timeout_seconds}",
            "--",
            str(local_path),
            f"{self.config.user}@{self.config.host}:{shlex.quote(str(remote_path))}",
        ]

    def _security_options(self) -> list[str]:
        return [
            "-oBatchMode=yes",
            "-oIdentitiesOnly=yes",
            "-oPasswordAuthentication=no",
            "-oKbdInteractiveAuthentication=no",
            "-oStrictHostKeyChecking=yes",
            f"-oUserKnownHostsFile={self.config.known_hosts_path}",
        ]
