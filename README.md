# Kindrop

Kindrop is a personal, local-only web app that turns new CBR/CBZ revisions in one Google Drive folder into Kindle-ready EPUB files. It uses Kindle Comic Converter (KCC), sends one EPUB per Gmail message, and watches Amazon replies for verification requests or documented rejection codes.

Kindrop never modifies the source files in Drive. The application listens only on `127.0.0.1:8787` and has no application login because it is designed for a single user on one trusted computer.

## Start with Docker Compose

Requirements: Docker Desktop with Compose, OpenSSL, a Google Cloud OAuth client, and an email address already listed in Amazon's **Approved Personal Document Email List**.

```sh
./scripts/bootstrap.sh
docker compose up --build
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787), then complete the setup sections in order:

1. Upload the Google OAuth desktop-client JSON and connect Google.
2. Browse `My Drive` and select a source folder.
3. Enter the Send to Kindle address and select the KCC device profile.
4. Scan, review the candidates, then create a conversion batch.

For the Google project steps, see [docs/google-cloud-setup.md](docs/google-cloud-setup.md).

## Runtime behavior

- `web` serves the compiled React app, the FastAPI API, OAuth callbacks, and server-sent events.
- `worker` performs Drive scans, downloads, sequential KCC conversions, Gmail sending, and Amazon-mail reconciliation.
- SQLite runs in WAL mode in the `kindrop-data` volume.
- Temporary archives and EPUB files live in `kindrop-cache`; inspected sources expire after 24 hours.
- OAuth material is encrypted with `secrets/kindrop.key`, mounted read-only. Back up this file together with the data volume. Losing it makes stored Google credentials unreadable.
- Every Gmail send start is separated by at least 60 seconds. Confirmed throttling is retried up to three times. An uncertain response becomes **Unknown** and requires an explicit resend decision.
- A sent item remains **Sent — unconfirmed** unless Amazon asks for verification or reports a rejection. Kindrop does not claim positive Kindle delivery.

Stop the services with `docker compose down`. Add `-v` only if you intentionally want to delete Kindrop's database and cache volumes.

## Local development

Backend:

```sh
cd backend
uv sync
UV_CACHE_DIR=/tmp/kindrop-uv-cache uv run pytest
uv run ruff check .
```

Frontend:

```sh
cd frontend
npm install
npm test
npm run typecheck
npm run lint
npm run build
# With the Docker stack running:
npm run e2e
```

The frontend development server proxies `/api` to `127.0.0.1:8787`.

## Safety boundaries

Kindrop accepts `My Drive` folders only, reads CBR/CBZ archives without writing back to Drive, validates `ComicInfo.xml` without extracting arbitrary archive paths, rejects artifacts above 20 MiB, and follows verification links only over HTTPS on approved Amazon domains without an off-domain redirect.

The first release is intentionally limited to one Google account, one Kindle destination, manual scans, and a single sequential worker. See [CONTEXT.md](CONTEXT.md) and the [architecture decisions](docs/adr/) for the product boundaries.
