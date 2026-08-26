# Kindrop v1 product specification

## Goal

Provide a free, personal, on-demand localhost application that prepares new CBR/CBZ/PDF/EPUB revisions from one recursively scanned `My Drive` folder and copies them directly to KOReader over the Kindle's local SSH server.

## Locked boundaries

- One user, one Google account, one Drive source folder, one Kindle profile, and one Kindle destination.
- Opening the Desk starts a recursive scan; manual Refresh remains available and Drive is read-only.
- CBR, CBZ, PDF, and EPUB inputs. Optimized comics become one CBZ; PDF and EPUB may pass through.
- Review is mandatory before a batch starts.
- A Drive revision is identified by file ID and checksum, or by file ID, size, and modification time when no checksum exists.
- `ComicInfo.xml` supplies metadata when present; the filename is the fallback and the user may override the title.
- Conversion preset snapshots belong to the batch/job history.
- PDF optimization defaults on and can be disabled per document; EPUB always defaults to passthrough.
- KCC uses the Paperwhite 1/2 `KPW` profile and creates one optimized CBZ without arbitrary splitting.
- SSH to `root@192.168.1.53:2222` is primary, configurable, LAN-only, key-authenticated, and host-key pinned.
- Reserve `192.168.1.53` for the Kindle in the router's DHCP settings so the endpoint stays stable.
- Kindrop owns only `/mnt/us/documents/KOReader/Kindrop`, reserves 100 MiB, and never deletes books automatically.
- A batch is all-or-nothing for capacity. Uploads use a temporary name, remote SHA-256 verification, and atomic rename.
- Gmail is an explicit manual fallback only. Its existing split-EPUB, cadence, Amazon reconciliation, and resend rules remain unchanged.

## User journeys

The Settings page guides OAuth, Source Folder selection, SSH connection testing, pinned-host recovery, optional Kindle email, and cache clearing. The Desk scans automatically and supports Refresh. Review selects all new Candidates by default and offers metadata edits, per-document optimization, and one **Optimize & send** action. History shows conversion and SSH delivery state, retains pending work across restarts, and offers the explicit **Send by email instead** fallback. A terminal Job can be retried as a separate history entry.

## Operational states

Jobs use `queued`, `downloading`, `converting`, `sending`, `ready_to_deliver`, `waiting_for_kindle`, `waiting_for_space`, `copied_to_kindle`, `sent`, `failed`, and `cancelled`. SSH Deliveries use `pending`, `waiting_for_kindle`, `waiting_for_space`, `copied_to_kindle`, `failed`, `action_required`, and `cancelled`. Gmail Deliveries additionally use `sent_unconfirmed`, `verification_required`, `verified`, `rejected`, and transient `unknown`.

## Acceptance focus

Drive recursion/pagination and revision idempotency, safe metadata reads, deterministic KPW optimization, EPUB passthrough, whole-batch capacity checks, owned-path collision safety, atomic verified SSH copies, pinned host keys, restart recovery, explicit Gmail fallback, the compiled SPA workflow, and an ARM64 Docker Compose smoke test.
