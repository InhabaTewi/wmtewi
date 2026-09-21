#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="inaba-m01-postgres-test"

if ! podman container exists "$CONTAINER_NAME"; then
    echo "$CONTAINER_NAME does not exist"
    exit 0
fi

podman stop "$CONTAINER_NAME"
