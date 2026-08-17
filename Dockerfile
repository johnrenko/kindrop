FROM node:22-alpine AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/ciromattia/kcc:v11.0.1@sha256:7c7879486d384983b3c67c048481b9830fef01c09efdea86e948db21f6c429da

USER root
RUN sed -i 's/ main/ main non-free/g' /etc/apt/sources.list \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libarchive-tools unrar \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 kindrop \
    && useradd --system --uid 10001 --gid kindrop --home-dir /app kindrop \
    && mkdir -p /app/backend /app/frontend /data /cache \
    && chown -R kindrop:kindrop /app /data /cache

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/backend
COPY --chown=kindrop:kindrop backend/ ./
RUN pip install --no-cache-dir .
COPY --from=frontend-builder --chown=kindrop:kindrop /build/frontend/dist /app/frontend
COPY --chown=kindrop:kindrop docker/web-entrypoint.sh /usr/local/bin/kindrop-web
RUN chmod 755 /usr/local/bin/kindrop-web

USER kindrop
EXPOSE 8787
ENTRYPOINT []
CMD ["uvicorn", "kindrop.main:app", "--host", "0.0.0.0", "--port", "8787"]
