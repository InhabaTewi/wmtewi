#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${INABA_ENV_FILE:-/etc/inaba/inaba.env}"
POSTGRES_CONTAINER="${INABA_POSTGRES_CONTAINER:-inaba-postgres}"
CORE_CONTAINER="${INABA_CORE_CONTAINER:-inaba-core}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

env_value() {
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1
}

if [[ ! -r "$ENV_FILE" ]]; then
    echo "Production env file is missing or unreadable: $ENV_FILE" >&2
    exit 1
fi

POSTGRES_USER="$(env_value POSTGRES_USER)"
POSTGRES_DB="$(env_value POSTGRES_DB)"
KNOWLEDGE_DIR="$(env_value INABA_KNOWLEDGE_DATA_DIR)"
BACKUP_ROOT="${BACKUP_ROOT:-$(env_value INABA_BACKUP_DIR)}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/inaba-backups}"
RETENTION_COUNT="${BACKUP_RETENTION_COUNT:-$(env_value BACKUP_RETENTION_COUNT)}"
RETENTION_COUNT="${RETENTION_COUNT:-7}"

if [[ -z "$POSTGRES_USER" || -z "$POSTGRES_DB" || ! -d "$KNOWLEDGE_DIR" ]]; then
    echo "Production backup configuration is incomplete" >&2
    exit 1
fi
if [[ ! "$RETENTION_COUNT" =~ ^[0-9]+$ ]]; then
    echo "BACKUP_RETENTION_COUNT must be a non-negative integer" >&2
    exit 1
fi

podman inspect "$POSTGRES_CONTAINER" >/dev/null
podman exec "$POSTGRES_CONTAINER" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null

database_bytes="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT pg_database_size(current_database())")"
source_bytes="$(du -sb "$KNOWLEDGE_DIR" | awk '{print $1}')"
available_bytes="$(( $(df -Pk "$BACKUP_ROOT" | awk 'NR==2 {print $4}') * 1024 ))"
required_bytes="$(( database_bytes + source_bytes + 134217728 ))"
if (( available_bytes < required_bytes )); then
    echo "Insufficient backup disk space" >&2
    exit 1
fi

backup_id="$(date -u +%Y%m%d-%H%M%S)"
work_dir="$BACKUP_ROOT/.${backup_id}.in-progress"
backup_dir="$BACKUP_ROOT/$backup_id"
mkdir -p "$BACKUP_ROOT"
chmod 750 "$BACKUP_ROOT"
mkdir -m 700 "$work_dir"

failed=1
cleanup() {
    if (( failed )); then
        mv "$work_dir" "$BACKUP_ROOT/.${backup_id}.failed" 2>/dev/null || true
    fi
}
trap cleanup EXIT

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$work_dir/postgres" "$work_dir/knowledge" "$work_dir/persona" "$work_dir/config"
umask 077
podman exec "$POSTGRES_CONTAINER" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$work_dir/postgres/database.dump"
tar -C "$KNOWLEDGE_DIR" -czf "$work_dir/knowledge/sources.tar.gz" sources manifests
cp "$PROJECT_ROOT/configs/persona/inaba.yaml" "$work_dir/persona/inaba.yaml"

git_commit="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
core_image="$(env_value INABA_CORE_IMAGE)"
core_image="${core_image:-inaba-core:latest}"
core_image_id="$(podman image inspect "$core_image" --format '{{.Id}}')"
postgres_version="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc 'SHOW server_version')"
pgvector_version="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT extversion FROM pg_extension WHERE extname = 'vector'")"
alembic_revision="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc 'SELECT version_num FROM alembic_version')"
persona_version="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT version FROM persona_versions WHERE persona_id = 'inaba' AND is_active LIMIT 1")"
knowledge_source_count="$(find "$KNOWLEDGE_DIR/sources" -type f | wc -l)"
memory_count="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc 'SELECT count(*) FROM memory_atoms')"
knowledge_document_count="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc 'SELECT count(*) FROM knowledge_documents')"
nginx_config="/etc/nginx/conf.d/aigchelper.conf"
nginx_sha256="$(sha256sum "$nginx_config" | awk '{print $1}')"

"$PROJECT_ROOT/.venv/bin/python" - "$work_dir/manifest.json" <<PY
import json
from pathlib import Path

manifest = {
    "backup_id": "${backup_id}",
    "backup_started_at": "${started_at}",
    "backup_finished_at": None,
    "hostname": "$(hostname)",
    "git_commit": "${git_commit}",
    "core_image": "${core_image}",
    "core_image_id": "${core_image_id}",
    "postgres_version": "${postgres_version}",
    "pgvector_version": "${pgvector_version}",
    "database_name": "${POSTGRES_DB}",
    "alembic_revision": "${alembic_revision}",
    "persona_id": "inaba",
    "persona_version": "${persona_version}",
    "persona_source_path": "configs/persona/inaba.yaml",
    "persona_source_sha256": "$(sha256sum "$PROJECT_ROOT/configs/persona/inaba.yaml" | awk '{print $1}')",
    "llm_provider": "$(env_value LLM_PROVIDER)",
    "llm_model": "$(env_value LLM_MODEL)",
    "embedding_provider": "$(env_value EMBEDDING_PROVIDER)",
    "embedding_model": "$(env_value EMBEDDING_MODEL)",
    "embedding_dimension": "$(env_value EMBEDDING_DIMENSION)",
    "knowledge_source_count": ${knowledge_source_count},
    "knowledge_document_count": ${knowledge_document_count},
    "memory_count": ${memory_count},
    "database_dump_filename": "postgres/database.dump",
    "knowledge_source_archive": "knowledge/sources.tar.gz",
    "nginx_config_path": "${nginx_config}",
    "nginx_config_sha256": "${nginx_sha256}",
    "secrets": {"postgres_password_configured": True, "service_token_configured": True, "llm_api_key_configured": True, "embedding_api_key_configured": True},
}
Path(__import__("sys").argv[1]).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
PY

ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"$PROJECT_ROOT/.venv/bin/python" - "$work_dir/manifest.json" "$ended_at" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
manifest = json.loads(path.read_text())
manifest["backup_finished_at"] = sys.argv[2]
path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
PY

(
    cd "$work_dir"
    sha256sum postgres/database.dump knowledge/sources.tar.gz persona/inaba.yaml manifest.json > checksums.sha256
)
chmod 600 "$work_dir/postgres/database.dump" "$work_dir/manifest.json" "$work_dir/checksums.sha256"
chmod 640 "$work_dir/knowledge/sources.tar.gz" "$work_dir/persona/inaba.yaml"
mv "$work_dir" "$backup_dir"
failed=0

if (( RETENTION_COUNT > 0 )); then
    mapfile -t backups < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*/[0-9]{8}-[0-9]{6}' -printf '%f\n' | sort -r)
    for expired in "${backups[@]:RETENTION_COUNT}"; do
        rm -rf -- "$BACKUP_ROOT/$expired"
    done
fi

printf 'backup_id=%s status=PASS database_dump_size=%s knowledge_archive_size=%s total_size=%s\n' \
    "$backup_id" \
    "$(du -h "$backup_dir/postgres/database.dump" | awk '{print $1}')" \
    "$(du -h "$backup_dir/knowledge/sources.tar.gz" | awk '{print $1}')" \
    "$(du -sh "$backup_dir" | awk '{print $1}')"