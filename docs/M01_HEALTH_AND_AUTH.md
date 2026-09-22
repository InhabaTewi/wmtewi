# M01 Health and Service Authentication

## Health Endpoints

`GET /health` remains the compatibility liveness endpoint. `GET /health/live` is the canonical liveness endpoint. Both are anonymous, return HTTP 200 with `{"status":"ok","service":"inaba-core"}`, and do not access the database, providers, or external network.

`GET /health/ready` is anonymous and checks PostgreSQL connectivity with `SELECT 1`, the `vector` extension, the Alembic revision at head, the active `inaba` Persona, and the LLM and Embedding provider health endpoints. It never applies migrations, creates embeddings, or calls LLM generation.

The ready response reports only normalized component states: `database`, `persona`, `llm`, and `embedding`. `ready` returns HTTP 200, `degraded` returns HTTP 200 when all required dependencies remain usable, and `not_ready` returns HTTP 503. Response bodies never contain connection URLs, provider keys, or raw exception details. The aggregate check is bounded by `READINESS_TIMEOUT` (default `5` seconds).

## Service Authentication

Every non-health HTTP route requires `Authorization: Bearer <SERVICE_TOKEN>`. The token is compared with `secrets.compare_digest`; missing, malformed, unsupported-scheme, and incorrect credentials all receive the same HTTP 401 response: `{"detail":"Unauthorized"}`. The middleware never logs the Authorization header or token.

Health paths are the only anonymous allowlist: `/health`, `/health/live`, and `/health/ready`. This is service-to-service authentication, not user authentication.

```bash
curl -H 'Authorization: Bearer example-token' \
  http://localhost:8000/api/knowledge/search?q=example

curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```