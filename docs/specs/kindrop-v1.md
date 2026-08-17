# Kindrop v1 product specification

## Goal

Provide a free, personal, on-demand localhost application that converts new CBR/CBZ revisions from one recursively scanned `My Drive` folder into Kindle EPUB documents and sends them through the user's Gmail account.

## Locked boundaries

- One user, one Google account, one Drive source folder, one Kindle profile, and one Kindle destination.
- Manual recursive scans; sources in Drive are always read-only.
- CBR and CBZ input only; EPUB Send to Kindle output only.
- Review is mandatory before a batch starts.
- A Drive revision is identified by file ID and checksum, or by file ID, size, and modification time when no checksum exists.
- `ComicInfo.xml` supplies metadata when present; the filename is the fallback and the user may override the title.
- Conversion preset snapshots belong to the batch/job history.
- Processing is sequential. Gmail sends are at least one minute apart and target artifacts are at most 20 MiB.
- Amazon mail can prove a rejection or request verification, but silence is only `sent_unconfirmed`.
- Ambiguous sends are never retried automatically.

## User journeys

The Settings page guides OAuth client upload, Google connection, My Drive folder selection, Kindle destination/profile setup, and cache clearing. The Desk starts scans and shows progress. Review selects and edits candidates before creating a batch. History shows conversion state, every EPUB part, Amazon state, and explicit recovery actions.

## Operational states

Jobs use `queued`, `downloading`, `converting`, `sending`, `sent`, `failed`, and `cancelled`. Deliveries use `pending`, `sent_unconfirmed`, `verification_required`, `verified`, `rejected`, `unknown`, and `action_required`.

## Acceptance focus

Drive recursion/pagination and revision idempotency, safe archive metadata reads, deterministic KCC settings and 20 MiB validation, correct MIME mail and send cadence, documented Amazon error classification, strict verification-link validation, restart recovery, the complete compiled SPA workflow, and an ARM64 Docker Compose smoke test.
