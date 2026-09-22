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
    assert 'PORT="${INABA_CORE_PORT:-$PORT}"' in script


def test_lifecycle_scripts_load_deployment_overrides_from_env_file() -> None:
    for filename in ("deploy_prod.sh", "status_prod.sh", "stop_prod.sh"):
        script = (PROJECT_ROOT / "scripts" / filename).read_text()
        assert "INABA_POSTGRES_DATA_DIR" in script
        assert "INABA_CORE_PORT" in script