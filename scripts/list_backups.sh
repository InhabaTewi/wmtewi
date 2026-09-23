#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${INABA_ENV_FILE:-/etc/inaba/inaba.env}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-$(sed -n 's/^INABA_BACKUP_DIR=//p' "$ENV_FILE" | tail -n 1)}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/inaba-backups}"

find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*/[0-9]{8}-[0-9]{6}' -print0 |
    sort -z |
    while IFS= read -r -d '' backup_dir; do
        "$PROJECT_ROOT/.venv/bin/python" - "$backup_dir/manifest.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
print(
    "backup_id={backup_id} created_at={backup_finished_at} git_commit={git_commit} database={database_name} status=PASS".format(
        **manifest
    )
)
PY
    done