#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/deploy/docker-compose.prod.yml"
COMPOSE_BIN="${INABA_COMPOSE_BIN:-podman-compose}"
PORT="${INABA_CORE_PORT:-8000}"

export INABA_ENV_FILE="${INABA_ENV_FILE:-/etc/inaba/inaba.env}"
$COMPOSE_BIN -f "$COMPOSE_FILE" ps
curl --silent --show-error "http://127.0.0.1:${PORT}/health/live" || true
echo
curl --silent --show-error "http://127.0.0.1:${PORT}/health/ready" || true
echo
$COMPOSE_BIN -f "$COMPOSE_FILE" exec -T core python -m alembic current || true
