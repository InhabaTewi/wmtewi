# M01 Cloud Deployment

M01-7 deploys the Cloud Core as `inaba-core` and `inaba-postgres` on a private `inaba_internal` container network. It does not add TLS, a GPU worker, or public PostgreSQL. For M01-8 HTTP ingress, Core binds to `127.0.0.1:1515` and existing host Nginx publishes `http://wmtewi.space/tewi`; see [M01_HTTP_INGRESS.md](M01_HTTP_INGRESS.md).

## Prerequisites

Install Podman with `podman-compose` or Docker with `docker compose`, Git, and curl. Clone the repository, then create the host directories:

```bash
git clone https://github.com/InhabaTewi/wmtewi.git
cd wmtewi
sudo install -d -m 0750 /etc/inaba
sudo install -d -m 0750 /opt/inaba-data/postgres
sudo install -d -m 0750 /opt/inaba-data/knowledge/sources
sudo install -d -m 0750 /opt/inaba-data/knowledge/manifests
sudo install -d -m 0750 /opt/inaba-backups
```

The PostgreSQL bind mount persists at `/opt/inaba-data/postgres`; raw Knowledge reconstruction assets persist at `/opt/inaba-data/knowledge`; the backup mount is reserved at `/opt/inaba-backups` for M01-9. Ensure the container runtime can read/write these directories. The Core image runs as UID/GID `10001`; configure ownership or ACLs to permit its access to the two Core data mounts.

## Environment

Create `/etc/inaba/inaba.env` with permissions suitable for secrets, using `.env.example` as the template. Do not commit this file.

```bash
sudo cp .env.example /etc/inaba/inaba.env
sudo chmod 0600 /etc/inaba/inaba.env
sudoedit /etc/inaba/inaba.env
```

Set `APP_ENV=production`, a non-development `DATABASE_URL` using host `postgres`, strong `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`, cloud LLM/Embedding values, `SERVICE_TOKEN`, and explicit `ALLOWED_HOSTS`. Use `CORS_ORIGINS=` when there is no browser frontend. Example database URL format:

```text
DATABASE_URL=postgresql+psycopg://inaba_app:replace-with-db-password@postgres:5432/inaba
```

The deployment script validates configuration before starting. Secrets remain only in `/etc/inaba/inaba.env`; never pass them as a Git-tracked compose value.

## Deploy and Verify

```bash
sudo INABA_ENV_FILE=/etc/inaba/inaba.env ./scripts/deploy_prod.sh
sudo INABA_ENV_FILE=/etc/inaba/inaba.env ./scripts/status_prod.sh
curl http://127.0.0.1:1515/health/live
curl http://127.0.0.1:1515/health/ready
```

`deploy_prod.sh` builds the Core image, starts PostgreSQL, waits for `pg_isready`, runs `python -m alembic upgrade head` using the Core image, starts Core only after migration success, and then requires live and ready health checks. It is idempotent: it does not remove volumes, reset data, import data, or alter secrets. Migration failure leaves Core stopped and exits nonzero.

Container health uses `/health/live`; deployment success requires `/health/ready`. Readiness requires configured cloud providers to be reachable. Without real provider credentials, Core may be live but intentionally not ready.

## Initial Data

Migrations do not import user data. After readiness succeeds, import content using the M01-6 CLI inside the Core container:

```bash
podman exec -it inaba-core python -m apps.control_api.cli persona import --file configs/persona/inaba.yaml --activate
podman exec -it inaba-core python -m apps.control_api.cli knowledge import --path /data/knowledge/sources
podman exec -it inaba-core python -m apps.control_api.cli memory import --file /data/knowledge/memory.jsonl
```

Follow [M01_DATA_MIGRATION.md](M01_DATA_MIGRATION.md) for data semantics. Upload Persona YAML, raw Markdown/text Knowledge source files, and Memory JSONL only. Do not copy PostgreSQL volumes, pgvector rows, API keys, `SERVICE_TOKEN`, conversations, or traces.

## Operations

```bash
sudo INABA_ENV_FILE=/etc/inaba/inaba.env ./scripts/stop_prod.sh
sudo INABA_ENV_FILE=/etc/inaba/inaba.env ./scripts/upgrade_prod.sh
```

`stop_prod.sh` stops the stack without `-v`; data remains in host directories. `upgrade_prod.sh` uses the deploy flow: build, PostgreSQL health, migration, Core restart, live, and readiness. If an upgrade fails, inspect `podman-compose -f deploy/docker-compose.prod.yml logs`; restore a known-good Git revision/image and rerun the deploy script. M01-7 does not implement automatic rollback.

## Offline pgvector Image

The standard image is `pgvector/pgvector:pg17`. On an offline server, import a vetted image archive outside the repository and override the image for the deploy command:

```bash
podman load -i pgvector-pg17.tar
sudo POSTGRES_IMAGE=localhost/pg17:latest INABA_ENV_FILE=/etc/inaba/inaba.env ./scripts/deploy_prod.sh
```

Never commit image tar files. `localhost/pg17:latest` is an offline override, not the production default.

## Offline Python Base Image

The default Core build uses `python:3.12-slim`. For an offline server, save the base image on a connected machine, transfer the archive outside Git, then load and tag it locally:

```bash
docker pull python:3.12-slim
docker save python:3.12-slim -o python-3.12-slim.tar

podman load -i python-3.12-slim.tar
podman tag <loaded-image> localhost/python:3.12-slim
sudo PYTHON_BASE_IMAGE=localhost/python:3.12-slim \
	INABA_ENV_FILE=/etc/inaba/inaba.env ./scripts/deploy_prod.sh
```

`PYTHON_BASE_IMAGE` is a build override only. It defaults to the official image in normal connected environments and must never be replaced by a local-only image name in the committed Dockerfile.

## Offline Prebuilt Core Image

When a Core image has been built on a connected machine, load its archive on the production host and set `INABA_CORE_IMAGE` in `/etc/inaba/inaba.env` to the loaded image reference:

```bash
podman load -i inaba-core-m01.tar
# Set INABA_CORE_IMAGE to the repository:tag reported by podman load.
sudoedit /etc/inaba/inaba.env
sudo INABA_ENV_FILE=/etc/inaba/inaba.env ./scripts/deploy_prod.sh
```

With `INABA_CORE_IMAGE` set, `deploy_prod.sh` uses that existing image and skips `build core`. Without it, the script preserves the normal connected-environment build flow.

## Troubleshooting

- If port 8000 is occupied, deploy with `INABA_CORE_PORT=18000` and use that port for health/status commands.
- If PostgreSQL fails, inspect `inaba-postgres` logs and its host data-directory permissions; do not delete the directory to retry.
- If migrations fail, Core intentionally remains unavailable; correct the migration/configuration error before rerunning deployment.
- A ready failure from LLM or Embedding is expected until real cloud provider credentials/endpoints are configured. No fake secret or placeholder makes the service ready.
