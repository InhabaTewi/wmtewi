#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/deploy/docker-compose.prod.yml"
ENV_FILE="${INABA_ENV_FILE:-/etc/inaba/inaba.env}"
COMPOSE_BIN="${INABA_COMPOSE_BIN:-}"
PORT="${INABA_CORE_PORT:-8000}"

if [[ -z "$COMPOSE_BIN" ]]; then
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        COMPOSE_BIN="docker compose"
    elif command -v podman-compose >/dev/null 2>&1; then
        COMPOSE_BIN="podman-compose"
    else
        echo "docker compose or podman-compose is required" >&2
        exit 1
    fi
fi

if [[ ! -r "$ENV_FILE" ]]; then
    echo "Production env file is missing or unreadable: $ENV_FILE" >&2
    exit 1
fi

required=(APP_ENV DATABASE_URL POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB LLM_BASE_URL LLM_API_KEY EMBEDDING_BASE_URL EMBEDDING_API_KEY SERVICE_TOKEN ALLOWED_HOSTS)
for key in "${required[@]}"; do
    if ! grep -qE "^${key}=.+" "$ENV_FILE"; then
        echo "Production env file is missing required value: $key" >&2
        exit 1
    fi
done

if ! grep -q '^APP_ENV=production$' "$ENV_FILE"; then
    echo "APP_ENV must be production in $ENV_FILE" >&2
    exit 1
fi

for key in POSTGRES_IMAGE PYTHON_BASE_IMAGE INABA_POSTGRES_DATA_DIR INABA_KNOWLEDGE_DATA_DIR INABA_BACKUP_DIR INABA_CORE_PORT INABA_CORE_IMAGE; do
    value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
    if [[ -n "$value" ]]; then
        export "$key=$value"
    fi
done
PORT="${INABA_CORE_PORT:-$PORT}"

for directory in "${INABA_POSTGRES_DATA_DIR:-/opt/inaba-data/postgres}" "${INABA_KNOWLEDGE_DATA_DIR:-/opt/inaba-data/knowledge}" "${INABA_BACKUP_DIR:-/opt/inaba-backups}"; do
    mkdir -p "$directory"
done
mkdir -p "${INABA_KNOWLEDGE_DATA_DIR:-/opt/inaba-data/knowledge}/sources" "${INABA_KNOWLEDGE_DATA_DIR:-/opt/inaba-data/knowledge}/manifests"

export INABA_ENV_FILE="$ENV_FILE"
export INABA_CORE_PORT="${INABA_CORE_PORT:-$PORT}"
if [[ -z "${INABA_CORE_IMAGE:-}" ]]; then
    $COMPOSE_BIN -f "$COMPOSE_FILE" build core
else
    echo "Using prebuilt Core image: $INABA_CORE_IMAGE"
fi
$COMPOSE_BIN -f "$COMPOSE_FILE" up -d postgres

for _ in {1..60}; do
    if $COMPOSE_BIN -f "$COMPOSE_FILE" exec -T postgres pg_isready -U "$(grep '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2-)" -d "$(grep '^POSTGRES_DB=' "$ENV_FILE" | cut -d= -f2-)" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if ! $COMPOSE_BIN -f "$COMPOSE_FILE" exec -T postgres pg_isready -U "$(grep '^POSTGRES_USER=' "$ENV_FILE" | cut -d= -f2-)" -d "$(grep '^POSTGRES_DB=' "$ENV_FILE" | cut -d= -f2-)" >/dev/null 2>&1; then
    echo "PostgreSQL did not become ready" >&2
    exit 1
fi

$COMPOSE_BIN -f "$COMPOSE_FILE" run --rm --no-deps core python -m alembic upgrade head
$COMPOSE_BIN -f "$COMPOSE_FILE" up -d core

for _ in {1..60}; do
    if curl --fail --silent --show-error "http://127.0.0.1:${PORT}/health/live" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl --fail --silent --show-error "http://127.0.0.1:${PORT}/health/live" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:${PORT}/health/ready" >/dev/null
echo "Inaba production stack is ready on 127.0.0.1:${PORT}"
