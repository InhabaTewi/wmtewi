# Inaba AI

Cloud control plane and local GPU worker for the Inaba AI robot.

## Development

```powershell
python -m pip install -e ".[dev,postgres]"
Copy-Item .env.example .env
alembic upgrade head
python -m uvicorn apps.control_api.main:app --reload
pytest
```

The default database URL targets local PostgreSQL. Tests use SQLite and do not require a running database.

Set `EXTERNAL_LLM_BASE_URL`, `EXTERNAL_LLM_API_KEY`, and `EXTERNAL_LLM_MODEL` to use an OpenAI-compatible API. The same base URL/key provide the default embedding endpoint; set `EMBEDDING_MODEL` when the provider uses a different embedding model.

## Implemented APIs

- `GET /api/personas/{persona_id}/active`
- `POST /api/memory/search`, `POST /api/memory/candidates`, `POST /api/memory/{id}/confirm`, `POST /api/memory/{id}/supersede`
- `POST /api/chat`
- `POST /api/knowledge/documents`, `POST /api/knowledge/reindex/{id}`, `GET /api/knowledge/search`, `DELETE /api/knowledge/documents/{id}`
