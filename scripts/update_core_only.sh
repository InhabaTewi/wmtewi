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

for key in APP_ENV DATABASE_URL POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB LLM_BASE_URL LLM_API_KEY EMBEDDING_BASE_URL EMBEDDING_API_KEY SERVICE_TOKEN ALLOWED_HOSTS; do
    if ! grep -qE "^${key}=.+" "$ENV_FILE"; then
        echo "Production env file is missing required value: $key" >&2
        exit 1
    fi
done

if ! grep -q '^APP_ENV=production$' "$ENV_FILE"; then
    echo "APP_ENV must be production in $ENV_FILE" >&2
    exit 1
fi

for key in POSTGRES_IMAGE PYTHON_BASE_IMAGE INABA_KNOWLEDGE_DATA_DIR INABA_CORE_PORT INABA_CORE_IMAGE; do
    value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true)"
    if [[ -n "$value" ]]; then
        export "$key=$value"
    fi
done
PORT="${INABA_CORE_PORT:-$PORT}"
export INABA_ENV_FILE="$ENV_FILE"
export INABA_CORE_PORT="$PORT"

RUNTIME_BIN="${COMPOSE_BIN%% *}"
postgres_container="$($COMPOSE_BIN -f "$COMPOSE_FILE" ps -q postgres)"
if [[ -z "$postgres_container" ]]; then
    echo "PostgreSQL must already be running; core-only update will not start it" >&2
    exit 1
fi
postgres_before="$($RUNTIME_BIN inspect --format '{{.Id}} {{.State.StartedAt}}' "$postgres_container")"

if [[ -z "${INABA_CORE_IMAGE:-}" ]]; then
    $COMPOSE_BIN -f "$COMPOSE_FILE" build core
else
    echo "Using prebuilt Core image: $INABA_CORE_IMAGE"
fi

$COMPOSE_BIN -f "$COMPOSE_FILE" run --rm --no-deps core python -m alembic upgrade head
$COMPOSE_BIN -f "$COMPOSE_FILE" up -d --no-deps --force-recreate core

for _ in {1..60}; do
    if curl --fail --silent --show-error "http://127.0.0.1:${PORT}/health/live" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl --fail --silent --show-error "http://127.0.0.1:${PORT}/health/live" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:${PORT}/health/ready" >/dev/null

postgres_after_container="$($COMPOSE_BIN -f "$COMPOSE_FILE" ps -q postgres)"
postgres_after="$($RUNTIME_BIN inspect --format '{{.Id}} {{.State.StartedAt}}' "$postgres_after_container")"
if [[ "$postgres_before" != "$postgres_after" ]]; then
    echo "ALERT: PostgreSQL container identity changed during core-only update; investigate immediately" >&2
    exit 1
fi

echo "Core updated on 127.0.0.1:${PORT}; PostgreSQL container identity unchanged"