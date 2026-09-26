import asyncio
import secrets
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Depends
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from packages.persistence.config import settings
from packages.knowledge.embedding import ExternalOpenAIEmbeddingProvider
from packages.knowledge.service import KnowledgeService
from packages.persona.repository import PersonaRepository
from packages.providers import ExternalOpenAIProvider, FailoverPolicy, LocalWorkerProvider, ProviderRouter
from packages.providers.local_cloud_fallback import PreferLocalWithCloudFallbackProvider


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ReadinessState = Literal["healthy", "degraded", "unavailable"]


def database_engine_options(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {}
    return {
        "pool_pre_ping": True,
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_recycle": settings.database_pool_recycle,
        "connect_args": {"connect_timeout": settings.database_connect_timeout},
    }


def create_database_engine(database_url: str | None = None):
    url = database_url or settings.database_url
    if url.startswith("sqlite"):
        return create_engine(url)
    return create_engine(url, **database_engine_options(url))


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def is_valid_service_authorization(authorization: str | None) -> bool:
    expected = settings.service_token.get_secret_value() if settings.service_token is not None else None
    if not expected or authorization is None:
        return False
    scheme, separator, provided = authorization.partition(" ")
    return separator == " " and scheme == "Bearer" and bool(provided) and secrets.compare_digest(provided, expected)


def create_provider_router() -> ProviderRouter:
    if settings.llm_provider_mode == "local_worker":
        local = LocalWorkerProvider(
            SessionLocal,
            request_timeout_seconds=settings.local_worker_request_timeout_seconds,
            lease_seconds=settings.local_worker_job_lease_seconds,
            ttl_seconds=settings.local_worker_job_ttl_seconds,
            result_poll_interval_seconds=settings.local_worker_result_poll_interval_seconds,
        )
        return ProviderRouter(external=None, local=local, mode="local_worker")

    external = ExternalOpenAIProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model_id=settings.llm_model,
            connect_timeout=settings.llm_connect_timeout,
            read_timeout=settings.llm_read_timeout,
            max_retries=settings.llm_max_retries,
        )
    if settings.llm_provider_mode == "prefer_local_with_cloud_fallback":
        local = LocalWorkerProvider(
            SessionLocal,
            request_timeout_seconds=settings.local_worker_request_timeout_seconds,
            lease_seconds=settings.local_worker_job_lease_seconds,
            ttl_seconds=settings.local_worker_job_ttl_seconds,
            result_poll_interval_seconds=settings.local_worker_result_poll_interval_seconds,
        )
        return ProviderRouter(external=external, local=local, mode="prefer_local_with_cloud_fallback")
    return ProviderRouter(external=external, mode="cloud")


async def get_provider_router() -> AsyncGenerator[ProviderRouter, None]:
    router = create_provider_router()
    try:
        yield router
    finally:
        if router.external is not None:
            await router.external.aclose()
        if router.local is not None:
            await router.local.aclose()


def create_knowledge_service(session: Session) -> KnowledgeService:
    return KnowledgeService(
        session,
        ExternalOpenAIEmbeddingProvider(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model_id=settings.embedding_model,
            dimension=settings.embedding_dimension,
            connect_timeout=settings.embedding_connect_timeout,
            read_timeout=settings.embedding_read_timeout,
            max_retries=settings.embedding_max_retries,
        ),
    )


async def get_knowledge_service(
    session: Session = Depends(get_session),
) -> AsyncGenerator[KnowledgeService, None]:
    knowledge = create_knowledge_service(session)
    try:
        yield knowledge
    finally:
        await knowledge.embedding_provider.aclose()


def _expected_alembic_revision() -> str:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()


def check_database_readiness() -> bool:
    try:
        with engine.connect() as connection:
            if connection.scalar(text("SELECT 1")) != 1:
                return False
            if connection.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")) != 1:
                return False
            return connection.scalar(text("SELECT version_num FROM alembic_version")) == _expected_alembic_revision()
    except Exception:
        return False


def check_persona_readiness() -> bool:
    try:
        with SessionLocal() as session:
            return PersonaRepository(session).get_active("inaba") is not None
    except Exception:
        return False


async def check_llm_readiness() -> ReadinessState:
    router = create_provider_router()
    try:
        if router.mode == "prefer_local_with_cloud_fallback":
            if router.local is None or router.external is None:
                return "unavailable"
            return (await PreferLocalWithCloudFallbackProvider(router.local, router.external, FailoverPolicy()).health_status()).state
        provider = router.local if router.mode == "local_worker" else router.external
        return "healthy" if provider is not None and await provider.health() else "unavailable"
    except Exception:
        return "unavailable"
    finally:
        if router.external is not None:
            await router.external.aclose()
        if router.local is not None:
            await router.local.aclose()


async def check_embedding_readiness() -> ReadinessState:
    knowledge = create_knowledge_service(SessionLocal())
    try:
        return (await knowledge.embedding_provider.health_status()).state
    except Exception:
        return "unavailable"
    finally:
        knowledge.session.close()
        await knowledge.embedding_provider.aclose()


async def readiness_report() -> dict[str, str]:
    try:
        database, persona, llm, embedding = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(check_database_readiness),
                asyncio.to_thread(check_persona_readiness),
                check_llm_readiness(),
                check_embedding_readiness(),
            ),
            timeout=settings.readiness_timeout,
        )
    except (asyncio.TimeoutError, Exception):
        return {
            "status": "not_ready",
            "database": "unavailable",
            "persona": "unavailable",
            "llm": "unavailable",
            "embedding": "unavailable",
        }

    report = {
        "database": "ok" if database else "unavailable",
        "persona": "ok" if persona else "unavailable",
        "llm": llm,
        "embedding": embedding,
    }
    if not database or not persona or "unavailable" in {llm, embedding}:
        return {"status": "not_ready", **report}
    if "degraded" in {llm, embedding}:
        return {"status": "degraded", **report}
    return {"status": "ready", **report}
