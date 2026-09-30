# Kindrop

Kindrop is a personal, local-only web app that prepares CBR/CBZ/PDF/EPUB files from one Google Drive folder and copies them directly into KOReader over the Kindle's SSH server. KCC optimization targets Paperwhite 1/2 (`KPW`); Gmail remains an explicit manual fallback.

Kindrop never modifies the source files in Drive. The application listens only on `127.0.0.1:8787` and has no application login because it is designed for a single user. It can also be accessed through a trusted private reverse proxy by setting `KINDROP_APP_BASE_URL` in `.env` to its HTTPS URL. Only localhost and that configured hostname are accepted; remote mutation origins must match its scheme, hostname and port.

## Start with Docker Compose

Requirements: Docker Desktop with Compose, OpenSSL, a Google Cloud OAuth client, KOReader SSH configured for key-only login, and optionally an email address listed in Amazon's **Approved Personal Document Email List**.

```sh
./scripts/bootstrap.sh
cp -n .env.example .env
# Edit .env and set KINDROP_SSH_KEY_FILE to the existing Kindle private key.
docker compose up --build
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787), then complete the setup sections in order:

1. Upload the Google OAuth desktop-client JSON and connect Google.
2. Browse `My Drive` and select a source folder.
3. Reserve the Kindle address in the router, save the SSH destination, inspect the host fingerprint, compare it on a trusted connection, then explicitly trust it.
4. Optionally enter the Send to Kindle fallback address.
5. Review the automatically scanned candidates, then choose **Optimize & send**.

For the Google project steps, see [docs/google-cloud-setup.md](docs/google-cloud-setup.md).

## Runtime behavior

- `web` serves the compiled React app, the FastAPI API, OAuth callbacks, and server-sent events.
- `worker` performs Drive scans, downloads, sequential KCC conversions, verified SSH copies, and optional Gmail reconciliation.
- SQLite runs in WAL mode in the `kindrop-data` volume.
- Pending source and output files live in `kindrop-cache`; verified SSH artifacts are deleted immediately.
- OAuth material is encrypted with `secrets/kindrop.key`, mounted read-only. Back up this file together with the data volume. Losing it makes stored Google credentials unreadable.
- Every Gmail send start is separated by at least 60 seconds. Confirmed throttling is retried up to three times. An uncertain response becomes a transient **Unknown**: Kindrop checks Gmail's Sent folder and resends automatically if the message never left (three sends at most, Kindle-side duplicates accepted). A delivery that can never be confirmed becomes **Failed** with the reason shown.
- A verified SSH copy becomes **Copied to Kindle** after remote SHA-256 validation and atomic publication.
- A terminal Conversion Job can be retried directly from History with the same settings, or retried with corrected settings from its `…` menu. Kindrop creates a new job and warns before sending another Kindle copy; it cannot remove the copy already sent.

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

Kindrop accepts `My Drive` folders only, reads CBR/CBZ/PDF/EPUB files without writing back to Drive, and can write only below `/mnt/us/documents/KOReader/Kindrop`. Untracked collisions block, a batch must preserve 100 MiB of free space, and only the manual Gmail fallback applies the 20 MiB artifact limit.

The first release is intentionally limited to one Google account, one Kindle destination, and a single sequential worker. See [CONTEXT.md](CONTEXT.md) and the [architecture decisions](docs/adr/) for the product boundaries.

## Private Tailscale access

Set `KINDROP_APP_BASE_URL=https://homeserver.tail4fc390.ts.net:8445` in `.env`,
then run `sudo tailscale serve --bg --https=8445 http://127.0.0.1:8787` and
`docker compose up -d --build --wait`. Keep the Docker port bound to localhost
and use Serve only within the trusted tailnet. Do not enable Funnel.

For Google OAuth over HTTPS, use a Web application client and register the exact
`${KINDROP_APP_BASE_URL}/api/oauth/callback` redirect URI; see
[Google Cloud setup](docs/google-cloud-setup.md). Restart the Google connection
flow after changing the base URL; an earlier flow uses its original redirect URI.
