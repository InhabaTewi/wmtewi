# M01 Production Backup and Restore

## Scope

M01-9 creates logical PostgreSQL backups with `pg_dump -Fc`; it never copies a live PostgreSQL data directory or volume. A backup also contains the raw Knowledge `sources/` and `manifests/` archive, the active Inaba Persona YAML, a non-sensitive configuration summary, a manifest, and SHA-256 checksums.

The backup root defaults to `/opt/inaba-backups`. Each completed backup has the form `YYYYMMDD-HHMMSS/` and includes `postgres/database.dump`, `knowledge/sources.tar.gz`, `persona/inaba.yaml`, `manifest.json`, and `checksums.sha256`. Secrets are never copied: API keys, `SERVICE_TOKEN`, and `POSTGRES_PASSWORD` appear only as `configured: true` metadata.

## Backup and Verification

```bash
sudo INABA_ENV_FILE=/etc/inaba/inaba.env ./scripts/backup.sh
./scripts/list_backups.sh
./scripts/verify_backup.sh /opt/inaba-backups/YYYYMMDD-HHMMSS
```

`pg_dump` uses a consistent database snapshot and does not stop Core or PostgreSQL. The script checks PostgreSQL availability and disk capacity before writing, builds in a private `.in-progress` directory, then atomically publishes the completed backup. A failed run is marked `.failed` and is never reported as successful.

`BACKUP_RETENTION_COUNT` defaults to `7`; set it to `0` to disable automated retention. Retention only removes completed directories matching the Inaba timestamp format directly beneath the configured backup root.

## Isolated Restore Test

Never restore over production. Restore is restricted to an explicit `--target test` database, which defaults to `inaba_restore_test` inside the production PostgreSQL container:

```bash
sudo INABA_ENV_FILE=/etc/inaba/inaba.env \
  ./scripts/restore.sh --backup /opt/inaba-backups/YYYYMMDD-HHMMSS --target test
```

The restore script verifies checksums, recreates only the isolated target database, runs `pg_restore`, then validates the Alembic revision, pgvector extension, `vector(1536)` column, HNSW cosine index, active Inaba Persona, row counts, and a database-side vector-distance query. It does not call Qwen or regenerate embeddings.

For an older backup, restore it into the isolated database first, inspect it, then run the standard application migration flow against that isolated target before planning any production recovery. A production overwrite is intentionally unsupported by this script and requires a separate reviewed disaster-recovery procedure.

## Disaster Recovery and Secrets

To recover data, provision a compatible PostgreSQL + pgvector target, verify the backup, restore into an isolated database, validate it, and promote only through a separately reviewed maintenance procedure. Raw Knowledge sources are retained so reindexing remains possible, but a normal database restore already includes stored vectors.

Secrets are deliberately excluded from ordinary backups. Recover `POSTGRES_PASSWORD`, `SERVICE_TOKEN`, and Qwen API keys from an encrypted secret store or password manager, never from a plain backup archive.

## Timer Template

Install the repository templates only after a successful manual restore test:

```bash
sudo cp deploy/systemd/inaba-backup.service /etc/systemd/system/
sudo cp deploy/systemd/inaba-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inaba-backup.timer
systemctl list-timers inaba-backup.timer
```

The timer is not enabled automatically. It reads `/etc/inaba/inaba.env` through the service `EnvironmentFile` and does not embed secrets.