#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 BACKUP_DIRECTORY" >&2
    exit 2
fi

backup_dir="$(realpath "$1")"
for required in manifest.json checksums.sha256 postgres/database.dump knowledge/sources.tar.gz persona/inaba.yaml; do
    if [[ ! -f "$backup_dir/$required" ]]; then
        echo "Backup verification failed: missing $required" >&2
        exit 1
    fi
done
(
    cd "$backup_dir"
    sha256sum --check checksums.sha256
)
echo "backup verification: PASS"