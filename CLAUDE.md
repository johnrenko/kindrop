# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What Kindrop is

A personal, local-only web app that converts new CBR/CBZ revisions from one Google Drive folder into Kindle-ready EPUBs (via Kindle Comic Converter), emails one EPUB per Gmail message to a Send-to-Kindle address, and reconciles Amazon's reply emails. Single user, no login, binds to `127.0.0.1:8787` only. It never writes back to Drive.

`CONTEXT.md` defines the ubiquitous language (Source Folder, Scan, Candidate, Conversion Batch/Job/Preset, Artifact, Delivery, Kindle Destination) **including terms to avoid** — use these exact terms in code, UI, and docs. Product requirements live in `docs/specs/kindrop-v1.md` (no external issue tracker); design boundaries are in `docs/adr/`.

## Commands

Backend (from `backend/`, uses uv):

```sh
uv sync                                        # install deps
UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest       # all tests
uv run pytest tests/test_api.py -k test_name   # single test
uv run ruff check .                            # lint (line-length 100, py312)
```

Frontend (from `frontend/`):

```sh
npm install
npm test              # vitest run
npx vitest run src/test/file.test.tsx   # single test file
npm run typecheck     # tsc -b
npm run lint          # eslint
npm run build
npm run e2e           # Playwright — requires the Docker stack running on 8787
npm run dev           # dev server, proxies /api to 127.0.0.1:8787
```

Full stack:

```sh
./scripts/bootstrap.sh        # first-time setup (generates secrets/kindrop.key)
docker compose up --build     # web + worker
```

Migrations: Alembic in `backend/migrations/versions/`, run automatically by the web entrypoint. Write migrations idempotently — a fresh database must be able to bootstrap through the whole chain (see migration 0002's history).

## Architecture

One Docker image, two containers sharing a SQLite database (WAL mode) in the `kindrop-data` volume:

- **web** — FastAPI app. `kindrop/main.py` → `api.create_app()` (`api.py` holds all routes: REST API, OAuth callbacks, SSE, and serving the built React app).
- **worker** — `kindrop/worker.py`, a single sequential polling loop that claims pending Scans/Jobs/Deliveries from the database. There is no message queue; the DB is the coordination layer.

Backend pipeline (entities in `models.py`, one table each):
`Scan` → `Revision` (dedup by fingerprint, see `domain.revision_fingerprint`) → `Candidate` → `Batch` → `Job` → `Artifact` (EPUB parts) → `Delivery` → `DeliveryAttempt`. An `Event` table feeds server-sent events to the UI.

Key backend modules:

- `services.py` — the core logic: `ScanProcessor` and `JobProcessor`. They depend on `Protocol` gateways (`DriveGateway`, `KccGateway`, `GmailGateway`), which tests replace with fakes (see `tests/test_workflows.py`). Keep new external dependencies behind the same pattern.
- `google.py` — real Drive/Gmail gateways; `oauth.py` + `crypto.SecretStore` encrypt OAuth tokens with `secrets/kindrop.key`.
- `kcc.py` builds the KCC command line from a `ConversionPreset`; `runners.KccRunner` executes it as a subprocess (the Docker image is based on the KCC image, with `unrar` added — some CBRs use RAR methods p7zip cannot read).
- `mail_monitor.py` / `amazon_mail.py` — poll Gmail for Amazon replies, classify rejections, follow verification links only over HTTPS on approved Amazon domains.
- `worker.DeliveryRateLimiter` — Gmail sends are spaced ≥60 s apart; an ambiguous send response becomes **Unknown** and requires an explicit user resend decision. Never claim positive Kindle delivery: sent items stay "Sent — unconfirmed".

Frontend: React 19 + TanStack Router (`router.tsx`) + TanStack Query (`query.ts`). Pages in `src/pages/` (Dashboard, Review, Jobs, Settings), thin API client in `src/api.ts`, live updates via SSE.

## Safety boundaries (product invariants)

- Read-only toward Drive; `My Drive` folders only.
- Validate `ComicInfo.xml` without extracting arbitrary archive paths; reject artifacts above 20 MiB (Gmail attachment limit).
- Local single-user: do not add LAN exposure, multi-account, or auth without an ADR.

## Operational cautions

- Never run `docker compose down -v` to "start clean" — it destroys the Google OAuth tokens and settings. Clearing history in the UI plus targeted SQL is enough.
- The `kindrop:local` image can lag behind the code: after backend changes, rebuild with `docker compose up --build -d` before testing against the running stack.
- `sent`/`failed` revisions are never re-offered by a Scan (fingerprint dedup), and "Clear history" keeps those fingerprints; delete the `revisions` rows in SQL to re-offer files.
