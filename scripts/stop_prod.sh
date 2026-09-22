#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/deploy/docker-compose.prod.yml"
COMPOSE_BIN="${INABA_COMPOSE_BIN:-podman-compose}"
ENV_FILE="${INABA_ENV_FILE:-/etc/inaba/inaba.env}"

if [[ -r "$ENV_FILE" ]]; then
	for key in POSTGRES_IMAGE PYTHON_BASE_IMAGE INABA_POSTGRES_DATA_DIR INABA_KNOWLEDGE_DATA_DIR INABA_BACKUP_DIR INABA_CORE_PORT INABA_CORE_IMAGE; do
		value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
		if [[ -n "$value" ]]; then
			export "$key=$value"
		fi
	done
fi

export INABA_ENV_FILE="$ENV_FILE"
$COMPOSE_BIN -f "$COMPOSE_FILE" down
