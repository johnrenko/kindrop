# KOReader OPDS Catalog — Design

**Date:** 2026-08-25
**Status:** Approved

## Context

The user installed KOReader on their Kindle. KOReader reads CBZ and PDF natively, which
removes the need for Kindle-specific formatting (KCC conversion to EPUB) and email
delivery. KOReader cannot talk to Google Drive directly; its supported remote sources are
OPDS catalogs, WebDAV, FTP, and Dropbox.

**Decision:** Kindrop mirrors the full Source Folder locally and exposes it as an OPDS
catalog on the LAN. The existing KCC → EPUB → Gmail pipeline is kept as an optional
alternate mode, untouched.

Constraints that shaped the design:

- Kindle storage is limited: files are downloaded on demand from KOReader and deleted
  after reading. The catalog must make it easy to see what was already fetched.
- Mac disk space is acceptable for a full local mirror (user's explicit choice over
  on-demand proxying from Drive).
- Kindrop stays read-only toward Drive; Drive remains the source of truth.

## Vocabulary (additions to CONTEXT.md)

- **Catalog** — the OPDS catalog exposed on the LAN that KOReader browses.
  _Avoid:_ server, share.
- **Library File** — the local, KOReader-ready copy of a Drive File Revision: CBZ and PDF
  kept as-is, CBR repackaged into CBZ (plain re-zip, no image processing).
  _Avoid:_ cache, download.
- **Mirroring** — the automatic materialization of Library Files after a Scan.

## Architecture

### Discovery (unchanged)

The Scan pipeline keeps discovering Drive File Revisions with fingerprint dedup. After a
Scan, every new Revision automatically gets a `pending` Library File — no Review step for
the Catalog. Review remains the entry point of the optional email mode only.

### Mirroring (worker)

The single sequential worker loop gains one claim type: claim `pending` Library Files,
download the source from Drive through the existing `DriveGateway` protocol, repackage
CBR → CBZ when needed, write the result under `/cache/library/`, mark `ready`. Failures
follow the existing transient-retry pattern; a file that cannot be repackaged is marked
`failed` with its error and never appears in the Catalog.

### Catalog service (new compose service)

A third compose service, `catalog`, runs from the same image and serves a small dedicated
FastAPI app (`kindrop/opds.py`):

- Exposed to the LAN on port **8788** (the only port mapped beyond localhost).
- **HTTP Basic Auth is mandatory** on every route; credentials are generated at bootstrap
  and stored with the existing `SecretStore`.
- Near read-only: it reads the shared SQLite database and streams files from the cache
  volume. Its only writes are the download-tracking fields (`last_downloaded_at`,
  `download_count`) and their `Event` rows. It never calls Google APIs and never touches
  OAuth tokens.
- The web UI and API remain strictly on `127.0.0.1:8787` (ADR 0001 unchanged for them).

This is recorded as **ADR 0004 — Expose the OPDS Catalog on the LAN**, amending ADR 0001:
only the catalog service leaves localhost, authenticated, serving files without any
write access beyond download tracking.

## Data model

New table `library_files` (Alembic migration, written idempotently per the migration
policy):

| column               | notes                                             |
| -------------------- | ------------------------------------------------- |
| `id`                 | PK                                                |
| `revision_id`        | FK → revisions, unique                            |
| `path`               | relative path under `/cache/library/`             |
| `format`             | `cbz` or `pdf`                                    |
| `size`               | bytes of the Library File                         |
| `status`             | `pending` / `mirroring` / `ready` / `failed` / `removed` |
| `error`              | nullable, last failure message                    |
| `mirrored_at`        | nullable                                          |
| `last_downloaded_at` | nullable, set on each OPDS acquisition            |
| `download_count`     | default 0                                         |

`Event` rows feed the SSE stream as elsewhere (library file ready, mirroring failed).

## OPDS catalog

- OPDS 1.2 (Atom) — the flavor KOReader supports best.
- Root navigation feed: **Latest additions** (most recent `ready` files), **By series**
  (grouped from the existing filename parsing, e.g. `Naruto, Ch. NNN`), **All**.
- Acquisition entries carry title, file size, mime type (`application/vnd.comicbook+zip`
  for CBZ, `application/pdf` for PDF) and an authenticated download link
  `/opds/download/{id}`.
- Entries already downloaded at least once are prefixed with a ✓ marker in their title so
  the user can manage Kindle space from KOReader.

## Reconciliation

A Scan also reconciles the mirror: Library Files whose Revision disappeared from Drive
are marked `removed` and their local file is deleted. Fingerprint dedup semantics of the
email mode (`sent`/`failed` never re-offered) are unchanged.

## UI

- **Dashboard**: mirror status — file count, total size, mirroring in progress.
- **Settings**: new Catalog section — enable/disable, Basic Auth credentials, and the
  exact URL to type into KOReader (`http://<mac-ip>:8788/opds`).
- Review, Jobs, and Deliveries pages stay as-is for the optional email mode.

## Testing

- Reuse the fake `DriveGateway` from `tests/test_workflows.py` for the mirroring flow.
- Unit tests for CBR → CBZ repackaging (including a corrupt archive → `failed`).
- OPDS feed tests: valid Atom XML, correct navigation/acquisition structure, `401`
  without credentials, download increments `download_count`.
- Workflow test: scan → mirror → catalog exposure end to end.

## Out of scope

- Any change to the KCC/email pipeline beyond leaving it optional.
- Read-state sync with KOReader (only download tracking is kept).
- Serving the catalog beyond the LAN, HTTPS, or multi-user auth.
- Deleting or writing anything on Drive.
