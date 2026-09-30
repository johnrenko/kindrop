# 0005 — Explicit private reverse proxy access

Kindrop runs on a home server and is accessed by its single user through Tailscale
Serve. The localhost-only Host and Origin checks rejected this deployment.

Keep the published Docker port on loopback and the default base URL local. Permit
one additional hostname derived from the explicitly configured KINDROP_APP_BASE_URL.
For remote mutation requests, accept only the configured HTTPS origin including
its port; retain local access and reject other hosts and origins. OAuth uses the
same configured base URL. Private HTTPS deployments require a Web OAuth client
with its exact callback registered, rather than the local Desktop client.

Tailscale Serve supplies transport encryption and tailnet access control. This
adds no application login or public/LAN listener and requires a trusted tailnet.
Do not use this deployment with a public proxy or Tailscale Funnel.
