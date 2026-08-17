#!/bin/sh
set -eu

umask 077
mkdir -p secrets
if [ ! -f secrets/kindrop.key ]; then
  openssl rand -base64 32 | tr '/+' '_-' > secrets/kindrop.key
  chmod 600 secrets/kindrop.key
  echo "Created secrets/kindrop.key"
else
  chmod 600 secrets/kindrop.key
  echo "Kept existing secrets/kindrop.key"
fi

echo "Run: docker compose up --build"
