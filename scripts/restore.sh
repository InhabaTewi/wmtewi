#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 --backup BACKUP_DIRECTORY --target test [--database DATABASE]" >&2
}

backup_dir=""
target=""
target_db="${INABA_RESTORE_TEST_DB:-inaba_restore_test}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --backup) backup_dir="$2"; shift 2 ;;
        --target) target="$2"; shift 2 ;;
        --database) target_db="$2"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done
if [[ "$target" != "test" || -z "$backup_dir" ]]; then
    usage
    exit 2
fi
if [[ ! "$target_db" =~ ^[A-Za-z_][A-Za-z0-9_]*$ || "$target_db" == "inaba" ]]; then
    echo "Restore target database must be an isolated non-production database name" >&2
    exit 1
fi

ENV_FILE="${INABA_ENV_FILE:-/etc/inaba/inaba.env}"
POSTGRES_CONTAINER="${INABA_POSTGRES_CONTAINER:-inaba-postgres}"
POSTGRES_USER="$(sed -n 's/^POSTGRES_USER=//p' "$ENV_FILE" | tail -n 1)"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="$(realpath "$backup_dir")"

"$PROJECT_ROOT/scripts/verify_backup.sh" "$backup_dir"
podman inspect "$POSTGRES_CONTAINER" >/dev/null
podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$target_db' AND pid <> pg_backend_pid();" \
    -c "DROP DATABASE IF EXISTS $target_db;" \
    -c "CREATE DATABASE $target_db;" >/dev/null
podman exec -i "$POSTGRES_CONTAINER" pg_restore -U "$POSTGRES_USER" -d "$target_db" --exit-on-error --no-owner --no-privileges < "$backup_dir/postgres/database.dump"

revision="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$target_db" -Atqc 'SELECT version_num FROM alembic_version')"
vector_extension="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$target_db" -Atqc "SELECT extname FROM pg_extension WHERE extname = 'vector'")"
vector_type="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$target_db" -Atqc "SELECT atttypid::regtype::text FROM pg_attribute WHERE attrelid = 'knowledge_embeddings'::regclass AND attname = 'embedding' AND NOT attisdropped")"
hnsw_index="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$target_db" -Atqc "SELECT indexname FROM pg_indexes WHERE tablename = 'knowledge_embeddings' AND indexdef ILIKE '%hnsw%' AND indexdef ILIKE '%vector_cosine_ops%' LIMIT 1")"
persona="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$target_db" -Atqc "SELECT version FROM persona_versions WHERE persona_id = 'inaba' AND is_active LIMIT 1")"
memory_count="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$target_db" -Atqc 'SELECT count(*) FROM memory_atoms')"
knowledge_count="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$target_db" -Atqc 'SELECT count(*) FROM knowledge_documents')"
vector_retrieval="$(podman exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d "$target_db" -Atqc "SELECT count(*) FROM (SELECT embedding <=> (SELECT embedding FROM knowledge_embeddings LIMIT 1) FROM knowledge_embeddings LIMIT 1) AS restored_vector_query")"

if [[ -z "$revision" || "$vector_extension" != "vector" || "$vector_type" != "vector" || -z "$hnsw_index" || -z "$persona" || "$vector_retrieval" != "1" ]]; then
    echo "Restore verification failed" >&2
    exit 1
fi
printf 'restore_target=%s status=PASS alembic_revision=%s persona=valid memory_count=%s knowledge_count=%s vector_retrieval=valid\n' \
    "$target_db" "$revision" "$memory_count" "$knowledge_count"