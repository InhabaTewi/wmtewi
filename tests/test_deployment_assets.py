import subprocess
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_production_compose_keeps_postgres_internal_and_core_localhost_only() -> None:
    compose = yaml.safe_load((PROJECT_ROOT / "deploy/docker-compose.prod.yml").read_text())
    postgres = compose["services"]["postgres"]
    core = compose["services"]["core"]

    assert "ports" not in postgres
    assert postgres["expose"] == ["5432"]
    assert core["ports"] == ["127.0.0.1:${INABA_CORE_PORT:-8000}:8000"]
    assert postgres["restart"] == core["restart"] == "unless-stopped"
    assert postgres["networks"] == core["networks"] == ["inaba_internal"]
    assert "healthcheck" in postgres and "healthcheck" in core
    assert postgres["logging"]["options"] == {"max-size": "10m", "max-file": "3"}
    assert core["logging"]["options"] == {"max-size": "10m", "max-file": "3"}


def test_core_dockerfile_is_nonroot_and_excludes_development_assets() -> None:
    dockerfile = (PROJECT_ROOT / "deploy/Dockerfile.core").read_text()
    ignored = (PROJECT_ROOT / ".dockerignore").read_text()

    assert "ARG PYTHON_BASE_IMAGE=python:3.12-slim" in dockerfile
    assert "FROM ${PYTHON_BASE_IMAGE}" in dockerfile
    assert "pip install --no-cache-dir ." in dockerfile
    assert "USER inaba" in dockerfile
    assert "--reload" not in dockerfile
    for entry in (".git", ".venv", ".env", "*.tar", "tmp", "tests"):
        assert entry in ignored


def test_production_scripts_are_shell_valid_and_never_prune_or_remove_volumes() -> None:
    scripts = [
        PROJECT_ROOT / "scripts/deploy_prod.sh",
        PROJECT_ROOT / "scripts/stop_prod.sh",
        PROJECT_ROOT / "scripts/status_prod.sh",
        PROJECT_ROOT / "scripts/upgrade_prod.sh",
    ]
    for script in scripts:
        subprocess.run(["bash", "-n", script], check=True)
        content = script.read_text()
        assert "prune" not in content
        assert "down -v" not in content


def test_deploy_script_runs_migration_before_core_start() -> None:
    script = (PROJECT_ROOT / "scripts/deploy_prod.sh").read_text()

    assert "python -m alembic upgrade head" in script
    assert script.index("python -m alembic upgrade head") < script.index("up -d core")
    assert "/health/live" in script
    assert "/health/ready" in script
    assert "PYTHON_BASE_IMAGE" in script
    assert 'if [[ -z "${INABA_CORE_IMAGE:-}" ]]; then' in script
    assert 'echo "Using prebuilt Core image: $INABA_CORE_IMAGE"' in script
    assert 'PORT="${INABA_CORE_PORT:-$PORT}"' in script


def test_lifecycle_scripts_load_deployment_overrides_from_env_file() -> None:
    for filename in ("deploy_prod.sh", "status_prod.sh", "stop_prod.sh"):
        script = (PROJECT_ROOT / "scripts" / filename).read_text()
        assert "INABA_POSTGRES_DATA_DIR" in script
        assert "INABA_CORE_PORT" in script


def test_http_ingress_template_is_loopback_only_and_strips_tewi_prefix() -> None:
    template = (PROJECT_ROOT / "deploy/nginx/tewi-location.conf.template").read_text()

    assert "location = /tewi" in template
    assert "location ^~ /tewi/" in template
    assert "proxy_pass http://127.0.0.1:1515/;" in template
    assert "proxy_set_header Authorization $http_authorization;" in template
    assert "proxy_set_header Authorization \"\";" not in template
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in template
    assert "client_max_body_size 20m;" in template
    for directive in ("proxy_connect_timeout 10s;", "proxy_send_timeout 120s;", "proxy_read_timeout 120s;"):
        assert directive in template
    for header in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"):
        assert header in template
    assert "5432" not in template
    assert "listen 443" not in template
    assert "ssl_" not in template


def test_backup_scripts_use_logical_dump_and_isolated_restore() -> None:
    backup = (PROJECT_ROOT / "scripts/backup.sh").read_text()
    restore = (PROJECT_ROOT / "scripts/restore.sh").read_text()
    verify = (PROJECT_ROOT / "scripts/verify_backup.sh").read_text()

    assert "set -euo pipefail" in backup
    assert "pg_dump" in backup
    assert "-Fc" in backup
    assert "pg_restore" not in backup
    assert "database.dump" in backup
    assert "sources.tar.gz" in backup
    assert "checksums.sha256" in backup
    assert "POSTGRES_PASSWORD" not in backup
    assert "SERVICE_TOKEN" not in backup
    assert "rm -rf \"$BACKUP_ROOT\"/*" not in backup
    assert "--target test" in restore
    assert "pg_restore" in restore
    assert "inaba_restore_test" in restore
    assert '"$target_db" == "inaba"' in restore
    assert "vector_cosine_ops" in restore
    assert "sha256sum --check checksums.sha256" in verify
    assert 'PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in (
        PROJECT_ROOT / "scripts/list_backups.sh"
    ).read_text()


def test_backup_timer_template_uses_protected_environment_file() -> None:
    service = (PROJECT_ROOT / "deploy/systemd/inaba-backup.service").read_text()
    timer = (PROJECT_ROOT / "deploy/systemd/inaba-backup.timer").read_text()

    assert "EnvironmentFile=/etc/inaba/inaba.env" in service
    assert "backup.sh" in service
    assert "OnCalendar=" in timer
    assert "POSTGRES_PASSWORD" not in service