#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  update_core_only.sh --dry-run|--apply \
    --target-container NAME --protected-container NAME --host-port PORT \
    --image IMAGE --provider-mode MODE --postgres-container NAME \
    --env-file PATH --network NAME --knowledge-data-dir PATH --backup-dir PATH \
    [--runtime podman] [--health-timeout-seconds SECONDS]
EOF
}

fail() {
    echo "DEPLOYMENT_GUARD_FAILED: $*" >&2
    exit 1
}

mode=""
runtime="podman"
target_container=""
protected_container=""
host_port=""
image=""
provider_mode=""
postgres_container=""
env_file=""
network=""
knowledge_data_dir=""
backup_dir=""
health_timeout_seconds=60

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|--apply)
            [[ -z "$mode" ]] || fail "choose exactly one of --dry-run or --apply"
            mode="${1#--}"
            ;;
        --target-container|--protected-container|--host-port|--image|--provider-mode|--postgres-container|--env-file|--network|--knowledge-data-dir|--backup-dir|--runtime|--health-timeout-seconds)
            [[ $# -ge 2 ]] || fail "missing value for $1"
            case "$1" in
                --target-container) target_container="$2" ;;
                --protected-container) protected_container="$2" ;;
                --host-port) host_port="$2" ;;
                --image) image="$2" ;;
                --provider-mode) provider_mode="$2" ;;
                --postgres-container) postgres_container="$2" ;;
                --env-file) env_file="$2" ;;
                --network) network="$2" ;;
                --knowledge-data-dir) knowledge_data_dir="$2" ;;
                --backup-dir) backup_dir="$2" ;;
                --runtime) runtime="$2" ;;
                --health-timeout-seconds) health_timeout_seconds="$2" ;;
            esac
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *) fail "unknown argument: $1" ;;
    esac
    shift
done

[[ -n "$mode" ]] || fail "explicit --dry-run or --apply is required"
for required in target_container protected_container host_port image provider_mode postgres_container env_file network knowledge_data_dir backup_dir; do
    [[ -n "${!required}" ]] || fail "--${required//_/-} is required"
done
[[ "$target_container" != "$protected_container" ]] || fail "target and protected containers must differ"
[[ "$target_container" != "$postgres_container" ]] || fail "target must not name PostgreSQL"
[[ "$protected_container" != "$postgres_container" ]] || fail "protected container must not name PostgreSQL"
[[ "$runtime" == "podman" ]] || fail "runtime must be podman"
[[ "$host_port" =~ ^[0-9]+$ ]] && (( host_port >= 1024 && host_port <= 65535 )) || fail "host port must be 1024-65535"
[[ "$health_timeout_seconds" =~ ^[0-9]+$ ]] && (( health_timeout_seconds > 0 && health_timeout_seconds <= 300 )) || fail "health timeout must be 1-300 seconds"
case "$provider_mode" in
    cloud|local_worker|prefer_local_with_cloud_fallback) ;;
    *) fail "unsupported provider mode" ;;
esac
[[ -r "$env_file" ]] || fail "env file is missing or unreadable"
[[ -d "$knowledge_data_dir" ]] || fail "knowledge data directory is missing"
[[ -d "$backup_dir" ]] || fail "backup directory is missing"
command -v "$runtime" >/dev/null 2>&1 || fail "runtime command is unavailable"

container_exists() {
    "$runtime" container exists "$1"
}

postgres_snapshot() {
    local container_id pid started_at image_id mounts mounts_hash
    container_id="$("$runtime" inspect --format '{{.Id}}' "$postgres_container")"
    pid="$("$runtime" inspect --format '{{.State.Pid}}' "$postgres_container")"
    started_at="$("$runtime" inspect --format '{{.State.StartedAt}}' "$postgres_container")"
    image_id="$("$runtime" inspect --format '{{.Image}}' "$postgres_container")"
    mounts="$("$runtime" inspect --format '{{range .Mounts}}{{.Type}}|{{.Source}}|{{.Destination}}{{"\n"}}{{end}}' "$postgres_container" | LC_ALL=C sort)"
    mounts_hash="$(printf '%s' "$mounts" | sha256sum | awk '{print $1}')"
    [[ -n "$container_id" && -n "$pid" && -n "$started_at" && -n "$image_id" ]] || fail "PostgreSQL identity is incomplete"
    printf '%s|%s|%s|%s|%s' "$container_id" "$pid" "$started_at" "$image_id" "$mounts_hash"
}

container_exists "$postgres_container" || fail "PostgreSQL container does not exist"
container_exists "$target_container" || fail "target container does not exist"
container_exists "$protected_container" || fail "protected container does not exist"
target_container_id="$("$runtime" inspect --format '{{.Id}}' "$target_container")"
protected_container_id="$("$runtime" inspect --format '{{.Id}}' "$protected_container")"
[[ -n "$target_container_id" && -n "$protected_container_id" ]] || fail "Core container identity is incomplete"
[[ "$target_container_id" != "$protected_container_id" ]] || fail "target and protected container identities must differ"
"$runtime" image exists "$image" || fail "image does not exist"
"$runtime" network exists "$network" || fail "network does not exist"

target_bind="$("$runtime" inspect --format '{{range $port, $bindings := .NetworkSettings.Ports}}{{range $bindings}}{{.HostIp}}:{{.HostPort}}:{{$port}}{{"\n"}}{{end}}{{end}}' "$target_container")"
printf '%s\n' "$target_bind" | grep -Fx "127.0.0.1:${host_port}:8000/tcp" >/dev/null || fail "target does not own requested loopback port"
postgres_before="$(postgres_snapshot)"
postgres_before_hash="$(printf '%s' "$postgres_before" | sha256sum | awk '{print $1}')"

echo "mode=$mode"
echo "target_container=$target_container"
echo "protected_container=$protected_container"
echo "host_port=$host_port"
echo "image=$image"
echo "provider_mode=$provider_mode"
echo "network=$network"
echo "postgres_identity_hash=$postgres_before_hash"

[[ "$mode" == "dry-run" ]] && exit 0

assert_postgres_unchanged() {
    local postgres_after postgres_after_hash
    postgres_after="$(postgres_snapshot)"
    postgres_after_hash="$(printf '%s' "$postgres_after" | sha256sum | awk '{print $1}')"
    if [[ "$postgres_before" != "$postgres_after" ]]; then
        echo "postgres_identity_before_hash=$postgres_before_hash" >&2
        echo "postgres_identity_after_hash=$postgres_after_hash" >&2
        fail "PostgreSQL container identity changed"
    fi
}

on_apply_exit() {
    local status=$?
    trap - EXIT
    assert_postgres_unchanged
    exit "$status"
}
trap on_apply_exit EXIT

"$runtime" stop "$target_container"
"$runtime" rm "$target_container"
"$runtime" run --detach --name "$target_container" --restart unless-stopped \
    --network "$network" --env-file "$env_file" \
    --env "LLM_PROVIDER_MODE=$provider_mode" \
    --publish "127.0.0.1:${host_port}:8000" \
    --volume "${knowledge_data_dir}:/data/knowledge:rw" \
    --volume "${backup_dir}:/data/backups:rw" \
    "$image" >/dev/null

health_url="http://127.0.0.1:${host_port}"
for (( attempt = 1; attempt <= health_timeout_seconds; attempt++ )); do
    if curl --fail --silent --show-error "${health_url}/health/live" >/dev/null 2>&1 \
        && curl --fail --silent --show-error "${health_url}/health/ready" >/dev/null 2>&1; then
        echo "target_health=ready"
        exit 0
    fi
    sleep 1
done

fail "target Core did not become live and ready"
