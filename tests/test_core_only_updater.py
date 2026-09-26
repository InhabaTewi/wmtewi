import os
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPDATER = PROJECT_ROOT / "scripts" / "update_core_only.sh"
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


@pytest.fixture
def updater_runtime(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    env_file = tmp_path / "inaba.env"
    knowledge = tmp_path / "knowledge"
    backups = tmp_path / "backups"
    env_file.write_text("APP_ENV=production\n")
    knowledge.mkdir()
    backups.mkdir()
    (bin_dir / "podman").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' \"$*\" >> \"$UPDATER_LOG\"\n"
        "if [[ \"$1 $2\" == 'container exists' ]]; then exit 0; fi\n"
        "if [[ \"$1 $2\" == 'image exists' ]]; then [[ -z \"${FAKE_FAIL_IMAGE:-}\" ]]; exit; fi\n"
        "if [[ \"$1 $2\" == 'network exists' ]]; then [[ -z \"${FAKE_FAIL_NETWORK:-}\" ]]; exit; fi\n"
        "if [[ \"$1\" == port ]]; then printf '%s\\n' \"${FAKE_PORT_OUTPUT:-127.0.0.1:1515}\"; exit \"${FAKE_PORT_EXIT_CODE:-0}\"; fi\n"
        "if [[ \"$1\" != inspect ]]; then exit 0; fi\n"
        "format=\"$3\"\n"
        "if [[ \"$format\" == *'.HostIp'* || \"$format\" == *'.HostIP'* || \"$format\" == *'.NetworkSettings.Ports'* ]]; then echo 'unsupported legacy nested template' >&2; exit 99; fi\n"
        "if [[ \"$format\" == '{{.Id}}' && \"$4\" == inaba-core-standby ]]; then printf 'core-standby-id\\n'; exit 0; fi\n"
        "if [[ \"$format\" == '{{.Id}}' && \"$4\" == inaba-core-active ]]; then printf 'core-active-id\\n'; exit 0; fi\n"
        "phase=before; [[ -f \"$UPDATER_PHASE\" ]] && phase=after\n"
        "value() { local name=\"$1\" fallback=\"$2\"; if [[ \"$phase\" == after && -n \"${!name:-}\" ]]; then printf '%s\\n' \"${!name}\"; else printf '%s\\n' \"$fallback\"; fi; }\n"
        "case \"$format\" in\n"
        "  '{{.Id}}') value FAKE_POSTGRES_ID_AFTER pg-id ;;\n"
        "  '{{.State.Pid}}') value FAKE_POSTGRES_PID_AFTER 42 ;;\n"
        "  '{{.State.StartedAt}}') value FAKE_POSTGRES_STARTED_AT_AFTER 2026-09-27T00:00:00Z ;;\n"
        "  '{{.Image}}') value FAKE_POSTGRES_IMAGE_AFTER sha256:pg ;;\n"
        "  *) value FAKE_POSTGRES_MOUNTS_AFTER 'bind|/data/postgres|/var/lib/postgresql/data'; touch \"$UPDATER_PHASE\" ;;\n"
        "esac\n"
    )
    (bin_dir / "curl").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"curl $*\" >> \"$UPDATER_LOG\"\nexit 0\n"
    )
    for executable in bin_dir.iterdir():
        executable.chmod(0o755)
    environment = os.environ | {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "UPDATER_LOG": str(log),
        "UPDATER_PHASE": str(tmp_path / "postgres-phase"),
    }
    arguments = [
        "--target-container", "inaba-core-standby",
        "--protected-container", "inaba-core-active",
        "--host-port", "1515",
        "--image", "localhost/inaba-core:6526779",
        "--provider-mode", "prefer_local_with_cloud_fallback",
        "--postgres-container", "inaba-postgres",
        "--env-file", str(env_file),
        "--network", "inaba_internal",
        "--knowledge-data-dir", str(knowledge),
        "--backup-dir", str(backups),
    ]
    return environment, arguments, log


def run_updater(environment, arguments):
    if not GIT_BASH.exists():
        pytest.fail("Git Bash is required for updater contract tests")
    return subprocess.run([GIT_BASH, str(UPDATER), *arguments], text=True, capture_output=True, env=environment)


def test_no_mode_refuses_before_any_runtime_command(updater_runtime) -> None:
    environment, arguments, log = updater_runtime
    result = run_updater(environment, arguments)
    assert result.returncode != 0
    assert "explicit --dry-run or --apply is required" in result.stderr
    assert not log.exists()


def test_dry_run_validates_without_mutation(updater_runtime) -> None:
    environment, arguments, log = updater_runtime
    result = run_updater(environment, ["--dry-run", *arguments])
    assert result.returncode == 0, result.stderr
    commands = log.read_text()
    assert "image exists localhost/inaba-core:6526779" in commands
    assert "network exists inaba_internal" in commands
    assert "port inaba-core-standby 8000/tcp" in commands
    assert "stop " not in commands
    assert "rm " not in commands
    assert "run " not in commands
    assert "target_container=inaba-core-standby" in result.stdout
    assert "migration=none" in result.stdout
    assert "nginx=none" in result.stdout


@pytest.mark.parametrize(
    "port_output, expected_success",
    [
        ("127.0.0.1:1515", True),
        ("8000/tcp -> 127.0.0.1:1515", True),
        ("127.0.0.1:1516", False),
        ("0.0.0.0:1515", False),
        (":1515", False),
        ("127.0.0.1:1515\n0.0.0.0:1515", False),
        ("not-a-port-mapping", False),
    ],
)
def test_podman_port_output_is_strictly_validated(updater_runtime, port_output, expected_success) -> None:
    environment, arguments, _ = updater_runtime
    environment["FAKE_PORT_OUTPUT"] = port_output

    result = run_updater(environment, ["--dry-run", *arguments])

    assert (result.returncode == 0) is expected_success
    if not expected_success:
        assert "PORT_BINDING_VALIDATION_FAILED" in result.stderr


def test_podman_port_failure_fails_closed(updater_runtime) -> None:
    environment, arguments, _ = updater_runtime
    environment["FAKE_PORT_EXIT_CODE"] = "1"

    result = run_updater(environment, ["--dry-run", *arguments])

    assert result.returncode != 0
    assert "PORT_BINDING_VALIDATION_FAILED" in result.stderr


@pytest.mark.parametrize("argument, value", [("--host-port", "80"), ("--provider-mode", "unknown")])
def test_invalid_explicit_value_refuses_before_mutation(updater_runtime, argument, value) -> None:
    environment, arguments, log = updater_runtime
    index = arguments.index(argument)
    arguments[index + 1] = value
    result = run_updater(environment, ["--dry-run", *arguments])
    assert result.returncode != 0
    assert "DEPLOYMENT_GUARD_FAILED" in result.stderr
    assert not log.exists()


def test_protected_target_collision_is_rejected_before_mutation(updater_runtime) -> None:
    environment, arguments, log = updater_runtime
    index = arguments.index("--protected-container")
    arguments[index + 1] = "inaba-core-standby"
    result = run_updater(environment, ["--dry-run", *arguments])
    assert result.returncode != 0
    assert "must differ" in result.stderr
    assert not log.exists()


def test_non_podman_runtime_is_rejected_before_mutation(updater_runtime) -> None:
    environment, arguments, log = updater_runtime
    result = run_updater(environment, ["--dry-run", "--runtime", "docker", *arguments])
    assert result.returncode != 0
    assert "runtime must be podman" in result.stderr
    assert not log.exists()


@pytest.mark.parametrize(
    "failure, expected",
    [
        ("missing_env", "env file is missing or unreadable"),
        ("missing_image", "image does not exist"),
        ("missing_network", "network does not exist"),
    ],
)
def test_required_preflight_dependency_failure_never_mutates(updater_runtime, failure, expected) -> None:
    environment, arguments, log = updater_runtime
    if failure == "missing_env":
        env_path = Path(arguments[arguments.index("--env-file") + 1])
        env_path.unlink()
    elif failure == "missing_image":
        environment["FAKE_FAIL_IMAGE"] = "1"
    else:
        environment["FAKE_FAIL_NETWORK"] = "1"

    result = run_updater(environment, ["--dry-run", *arguments])

    assert result.returncode != 0
    assert expected in result.stderr
    if log.exists():
        commands = log.read_text()
        assert "stop " not in commands
        assert "rm " not in commands
        assert "run " not in commands


def test_apply_mutates_only_explicit_target_and_never_postgres_or_protected(updater_runtime) -> None:
    environment, arguments, log = updater_runtime
    result = run_updater(environment, ["--apply", *arguments])
    assert result.returncode == 0, result.stderr
    commands = log.read_text().splitlines()
    mutations = [command for command in commands if command.startswith(("stop ", "rm ", "run "))]
    assert mutations[0] == "stop inaba-core-standby"
    assert mutations[1] == "rm inaba-core-standby"
    assert len(mutations) == 3
    assert all("inaba-postgres" not in command for command in mutations)
    assert all("inaba-core-active" not in command for command in mutations)
    assert "--env LLM_PROVIDER_MODE=prefer_local_with_cloud_fallback" in mutations[2]
    assert "--publish 127.0.0.1:1515:8000" in mutations[2]


@pytest.mark.parametrize(
    "changed_variable",
    [
        "FAKE_POSTGRES_ID_AFTER",
        "FAKE_POSTGRES_PID_AFTER",
        "FAKE_POSTGRES_STARTED_AT_AFTER",
        "FAKE_POSTGRES_IMAGE_AFTER",
        "FAKE_POSTGRES_MOUNTS_AFTER",
    ],
)
def test_postgres_identity_change_fails_closed(updater_runtime, changed_variable) -> None:
    environment, arguments, _ = updater_runtime
    environment[changed_variable] = "changed"
    result = run_updater(environment, ["--apply", *arguments])
    assert result.returncode != 0
    assert "DEPLOYMENT_GUARD_FAILED" in result.stderr
    assert "PostgreSQL container identity changed" in result.stderr