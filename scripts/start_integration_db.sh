#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="inaba-m01-postgres-test"
IMAGE="localhost/pg17:latest"
HOST_PORT="55432"
DATABASE="inaba_m01_test"
DATABASE_USER="inaba_test"
DATABASE_PASSWORD="inaba_test_password"

if ! podman image exists "$IMAGE"; then
    echo "Required local image is unavailable: $IMAGE" >&2
    exit 1
fi

if podman container exists "$CONTAINER_NAME"; then
    if [[ "$(podman inspect --format '{{.State.Running}}' "$CONTAINER_NAME")" == "true" ]]; then
        echo "$CONTAINER_NAME is already running"
        exit 0
    fi
    echo "$CONTAINER_NAME exists but is not running; run scripts/stop_integration_db.sh first" >&2
    exit 1
fi

if ss -lnt | grep -qE ":${HOST_PORT}\b"; then
    echo "Port ${HOST_PORT} is already in use; refusing to start integration database" >&2
    exit 1
fi

podman run --rm -d \
    --name "$CONTAINER_NAME" \
    -e "POSTGRES_USER=$DATABASE_USER" \
    -e "POSTGRES_PASSWORD=$DATABASE_PASSWORD" \
    -e "POSTGRES_DB=$DATABASE" \
    -p "127.0.0.1:${HOST_PORT}:5432" \
    "$IMAGE"

for _ in {1..1000}; do
    if podman exec "$CONTAINER_NAME" pg_isready -U "$DATABASE_USER" -d "$DATABASE" >/dev/null 2>&1; then
        echo "$CONTAINER_NAME is ready on 127.0.0.1:${HOST_PORT}"
        exit 0
    fi
done

echo "$CONTAINER_NAME did not become ready" >&2
podman logs "$CONTAINER_NAME" >&2
exit 1
