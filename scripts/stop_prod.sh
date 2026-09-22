#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/deploy/docker-compose.prod.yml"
COMPOSE_BIN="${INABA_COMPOSE_BIN:-podman-compose}"

export INABA_ENV_FILE="${INABA_ENV_FILE:-/etc/inaba/inaba.env}"
$COMPOSE_BIN -f "$COMPOSE_FILE" down
