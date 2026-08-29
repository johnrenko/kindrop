#!/bin/sh
set -eu

mkdir -p "${TMPDIR:-/tmp}"
alembic upgrade head
exec "$@"
