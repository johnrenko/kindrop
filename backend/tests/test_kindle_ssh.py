import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

import pytest

from kindrop.kindle_ssh import (
    KindleCollisionError,
    KindleIntegrityError,
    KindleSshConfig,
    KindleSshTransport,
)


class FakeCommandRunner:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
        self.results = results
        self.commands: list[list[str]] = []
        self.inputs: list[str | None] = []

    def __call__(
        self, command: Sequence[str], *, timeout: int, **kwargs
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        self.inputs.append(kwargs.get("input"))
        return self.results.pop(0)


def completed(*, stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def config(tmp_path: Path) -> KindleSshConfig:
    return KindleSshConfig(
        host="192.168.1.53",
        port=2222,
        user="root",
        key_path=tmp_path / "id_kindrop",
        known_hosts_path=tmp_path / "known_hosts",
    )


def test_destination_must_stay_inside_the_kindrop_owned_subtree(tmp_path: Path):
    with pytest.raises(ValueError, match="beneath"):
        KindleSshConfig(
            host="192.168.1.53",
            key_path=tmp_path / "key",
            known_hosts_path=tmp_path / "known_hosts",
            destination_root=PurePosixPath("/etc"),
        )
    with pytest.raises(ValueError, match="traversal"):
        KindleSshConfig(
            host="192.168.1.53",
            key_path=tmp_path / "key",
            known_hosts_path=tmp_path / "known_hosts",
            destination_root=PurePosixPath(
                "/mnt/us/documents/KOReader/Kindrop/../outside"
            ),
        )


def test_probe_reports_remote_free_space_using_pinned_key_only_authentication(tmp_path: Path):
    runner = FakeCommandRunner(
        [
            completed(
                stdout=(
                    "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                    "/dev/loop/0 1382560 652448 730112 48% /mnt/us\n"
                )
            )
        ]
    )
    transport = KindleSshTransport(config(tmp_path), runner=runner)

    probe = transport.probe()

    assert probe.reachable is True
    assert probe.free_bytes == 730112 * 1024
    command = runner.commands[0]
    assert command[0] == "ssh"
    assert "-oBatchMode=yes" in command
    assert "-oIdentitiesOnly=yes" in command
    assert "-oPasswordAuthentication=no" in command
    assert "-oKbdInteractiveAuthentication=no" in command
    assert "-oStrictHostKeyChecking=yes" in command
    assert f"-oUserKnownHostsFile={tmp_path / 'known_hosts'}" in command
    assert str(tmp_path / "id_kindrop") in command
    assert "root@192.168.1.53" in command


def test_probe_reports_unreachable_without_fabricating_free_space(tmp_path: Path):
    runner = FakeCommandRunner([completed(returncode=255, stderr="Connection timed out")])

    probe = KindleSshTransport(config(tmp_path), runner=runner).probe()

    assert probe.reachable is False
    assert probe.free_bytes is None


def test_delivery_rejects_an_untracked_remote_collision(tmp_path: Path):
    source = tmp_path / "book.cbz"
    source.write_bytes(b"comic")
    runner = FakeCommandRunner(
        [completed(), completed(stdout="EXISTS\n")]
    )
    transport = KindleSshTransport(config(tmp_path), runner=runner)

    with pytest.raises(KindleCollisionError) as error:
        transport.deliver(source, "One Piece/001 - Romance Dawn.cbz")

    assert error.value.remote_path == (
        "/mnt/us/documents/KOReader/Kindrop/One Piece/001 - Romance Dawn.cbz"
    )
    assert len(runner.commands) == 2
    assert runner.commands[0][0] == "ssh"
    assert "mkdir -p '/mnt/us/documents/KOReader/Kindrop/One Piece'" in runner.commands[0][-1]


def test_delivery_verifies_sha256_before_atomically_publishing_file(tmp_path: Path):
    source = tmp_path / "book.cbz"
    source.write_bytes(b"comic")
    expected_sha256 = "7e002e1ba02e628e7422bd3017520effa8cc1638662efa78a78b6af1548eb575"
    runner = FakeCommandRunner(
        [
            completed(),
            completed(stdout="MISSING\n"),
            completed(),
            completed(stdout=f"{expected_sha256}  temporary-file\n"),
            completed(stdout="PUBLISHED\n"),
        ]
    )
    transport = KindleSshTransport(config(tmp_path), runner=runner)

    receipt = transport.deliver(source, "One Piece/001 - Romance Dawn.cbz")

    assert receipt.remote_path == (
        "/mnt/us/documents/KOReader/Kindrop/One Piece/001 - Romance Dawn.cbz"
    )
    assert receipt.sha256 == expected_sha256
    assert receipt.size_bytes == 5
    sftp_command = runner.commands[2]
    assert sftp_command[0] == "sftp"
    assert "-b" in sftp_command
    assert "-oStrictHostKeyChecking=yes" in sftp_command
    assert f"-oUserKnownHostsFile={tmp_path / 'known_hosts'}" in sftp_command
    assert runner.inputs[2] == (
        f'put "{source}" '
        '"/mnt/us/documents/KOReader/Kindrop/One Piece/'
        '.001 - Romance Dawn.cbz.7e002e1ba02e.kindrop-part"\nquit\n'
    )
    assert not any(command[0] == "scp" for command in runner.commands)
    assert runner.commands[3][0] == "ssh"
    assert "sha256sum" in runner.commands[3][-1]
    assert runner.commands[4][0] == "ssh"
    assert "mv -f" in runner.commands[4][-1]


def test_delivery_can_explicitly_replace_a_tracked_remote_file(tmp_path: Path):
    source = tmp_path / "book.cbz"
    source.write_bytes(b"comic")
    expected_sha256 = "7e002e1ba02e628e7422bd3017520effa8cc1638662efa78a78b6af1548eb575"
    runner = FakeCommandRunner(
        [
            completed(),
            completed(stdout="EXISTS\n"),
            completed(),
            completed(stdout=f"{expected_sha256}  temporary-file\n"),
            completed(),
        ]
    )

    receipt = KindleSshTransport(config(tmp_path), runner=runner).deliver(
        source, "One Piece/001.cbz", replace=True
    )

    assert receipt.sha256 == expected_sha256
    assert "mv -f" in runner.commands[-1][-1]


def test_delivery_does_not_overwrite_a_file_created_during_upload(tmp_path: Path):
    source = tmp_path / "book.cbz"
    source.write_bytes(b"comic")
    expected_sha256 = "7e002e1ba02e628e7422bd3017520effa8cc1638662efa78a78b6af1548eb575"
    runner = FakeCommandRunner(
        [
            completed(),
            completed(stdout="MISSING\n"),
            completed(),
            completed(stdout=f"{expected_sha256}  temporary-file\n"),
            completed(stdout="EXISTS\n"),
            completed(),
        ]
    )

    with pytest.raises(KindleCollisionError):
        KindleSshTransport(config(tmp_path), runner=runner).deliver(
            source, "One Piece/001.cbz"
        )

    assert "rm -f" in runner.commands[-1][-1]


def test_delivery_removes_temporary_file_when_remote_hash_does_not_match(tmp_path: Path):
    source = tmp_path / "book.cbz"
    source.write_bytes(b"comic")
    runner = FakeCommandRunner(
        [
            completed(),
            completed(stdout="MISSING\n"),
            completed(),
            completed(stdout=f"{'0' * 64}  temporary-file\n"),
            completed(),
        ]
    )

    with pytest.raises(KindleIntegrityError):
        KindleSshTransport(config(tmp_path), runner=runner).deliver(
            source, "One Piece/001.cbz"
        )

    assert len(runner.commands) == 5
    assert "rm -f" in runner.commands[-1][-1]
    assert not any("mv -f" in command[-1] for command in runner.commands if command[0] == "ssh")


def test_delivery_rejects_paths_that_escape_the_kindrop_root(tmp_path: Path):
    source = tmp_path / "book.cbz"
    source.write_bytes(b"comic")
    runner = FakeCommandRunner([])

    with pytest.raises(ValueError, match="beneath"):
        KindleSshTransport(config(tmp_path), runner=runner).deliver(
            source, "../documents/private.cbz"
        )

    assert runner.commands == []


def test_remote_paths_are_shell_quoted_as_single_arguments(tmp_path: Path):
    source = tmp_path / "book.cbz"
    source.write_bytes(b"comic")
    runner = FakeCommandRunner([completed(), completed(stdout="EXISTS\n")])

    with pytest.raises(KindleCollisionError):
        KindleSshTransport(config(tmp_path), runner=runner).deliver(
            source, "Series $(touch hacked)/Book's.cbz"
        )

    assert runner.commands[0][-1] == (
        "mkdir -p '/mnt/us/documents/KOReader/Kindrop/Series $(touch hacked)'"
    )
    assert "Book'\"'\"'s.cbz" in runner.commands[1][-1]


def test_host_key_is_inspected_without_implicitly_trusting_it(tmp_path: Path):
    host_line = (
        "[192.168.1.53]:2222 ssh-ed25519 "
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
    )
    runner = FakeCommandRunner([completed(stdout=host_line)])
    transport = KindleSshTransport(config(tmp_path), runner=runner)

    host_key = transport.inspect_host_key()

    assert host_key.fingerprint == "SHA256:Zmh6rfhivXdsj8GLjp+OIAiXFIVu4jOzkCpZHQ1fKSU"
    assert host_key.known_hosts_line == host_line.strip()
    assert runner.commands == [
        ["ssh-keyscan", "-T", "10", "-p", "2222", "192.168.1.53"]
    ]
    assert not (tmp_path / "known_hosts").exists()


def test_host_key_is_pinned_only_after_matching_explicit_fingerprint(tmp_path: Path):
    host_line = (
        "[192.168.1.53]:2222 ssh-ed25519 "
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
    )
    runner = FakeCommandRunner([completed(stdout=host_line)])
    transport = KindleSshTransport(config(tmp_path), runner=runner)

    host_key = transport.trust_host_key(
        "SHA256:Zmh6rfhivXdsj8GLjp+OIAiXFIVu4jOzkCpZHQ1fKSU"
    )

    known_hosts = tmp_path / "known_hosts"
    assert host_key.known_hosts_line == host_line.strip()
    assert known_hosts.read_text() == host_line
    assert stat.S_IMODE(known_hosts.stat().st_mode) == 0o600
    assert transport.pinned_host_key() == host_key


def test_host_key_fingerprint_mismatch_leaves_existing_pin_unchanged(tmp_path: Path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("original pin\n")
    host_line = (
        "[192.168.1.53]:2222 ssh-ed25519 "
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\n"
    )
    runner = FakeCommandRunner([completed(stdout=host_line)])

    with pytest.raises(ValueError, match="fingerprint"):
        KindleSshTransport(config(tmp_path), runner=runner).trust_host_key("SHA256:other")

    assert known_hosts.read_text() == "original pin\n"
