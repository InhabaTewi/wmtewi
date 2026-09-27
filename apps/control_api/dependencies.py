import asyncio
import logging
import secrets
from collections.abc import AsyncGenerator, Awaitable, Generator
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import uuid4

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
ReadinessFailureKind = Literal["none", "degraded", "timeout", "unavailable", "exception"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReadinessComponent:
    state: str
    elapsed_ms: int
    failure_kind: ReadinessFailureKind
    exception_type: str | None = None


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
            claim_timeout_seconds=settings.local_job_claim_timeout_seconds,
            inference_timeout_seconds=settings.local_inference_timeout_seconds,
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
            claim_timeout_seconds=settings.local_job_claim_timeout_seconds,
            inference_timeout_seconds=settings.local_inference_timeout_seconds,
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


def _component_state(value: bool | ReadinessState) -> str:
    return "ok" if value is True else "unavailable" if value is False else value


async def _measure_readiness_component(
    name: str,
    check: Awaitable[bool | ReadinessState],
    components: dict[str, ReadinessComponent],
) -> bool | ReadinessState:
    started = perf_counter()
    try:
        value = await check
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        components[name] = ReadinessComponent(
            state="unavailable",
            elapsed_ms=round((perf_counter() - started) * 1000),
            failure_kind="exception",
            exception_type=type(exc).__name__,
        )
        raise

    state = _component_state(value)
    components[name] = ReadinessComponent(
        state=state,
        elapsed_ms=round((perf_counter() - started) * 1000),
        failure_kind="none" if state in {"ok", "healthy"} else "degraded" if state == "degraded" else "unavailable",
    )
    return value


def _unavailable_report(check_id: str) -> dict[str, str]:
    return {
        "status": "not_ready",
        "database": "unavailable",
        "persona": "unavailable",
        "llm": "unavailable",
        "embedding": "unavailable",
        "check_id": check_id,
    }


def _log_readiness_failure(
    check_id: str,
    report: dict[str, str],
    components: dict[str, ReadinessComponent],
    total_ms: int,
    aggregate_failure_kind: ReadinessFailureKind | None = None,
) -> None:
    failed = [name for name, component in components.items() if component.state == "unavailable"]
    failure_components = failed or [report.get("failure_component", "unknown")]
    failure_kind = aggregate_failure_kind or next(
        (components[name].failure_kind for name in failed),
        "unavailable",
    )
    logger.warning(
        "event=readiness_unavailable check_id=%s failure_components=%s failure_kind=%s "
        "database_state=%s persona_state=%s llm_state=%s embedding_state=%s "
        "database_ms=%s persona_ms=%s llm_ms=%s embedding_ms=%s total_ms=%s exception_types=%s",
        check_id,
        ",".join(failure_components),
        failure_kind,
        components.get("database", ReadinessComponent(report["database"], 0, "timeout")).state,
        components.get("persona", ReadinessComponent(report["persona"], 0, "timeout")).state,
        components.get("llm", ReadinessComponent(report["llm"], 0, "timeout")).state,
        components.get("embedding", ReadinessComponent(report["embedding"], 0, "timeout")).state,
        components.get("database", ReadinessComponent("unavailable", 0, "timeout")).elapsed_ms,
        components.get("persona", ReadinessComponent("unavailable", 0, "timeout")).elapsed_ms,
        components.get("llm", ReadinessComponent("unavailable", 0, "timeout")).elapsed_ms,
        components.get("embedding", ReadinessComponent("unavailable", 0, "timeout")).elapsed_ms,
        total_ms,
        ",".join(
            f"{name}:{component.exception_type}"
            for name, component in components.items()
            if component.exception_type is not None
        )
        or "none",
    )


async def readiness_report() -> dict[str, str]:
    check_id = str(uuid4())
    started = perf_counter()
    components: dict[str, ReadinessComponent] = {}
    try:
        database, persona, llm, embedding = await asyncio.wait_for(
            asyncio.gather(
                _measure_readiness_component("database", asyncio.to_thread(check_database_readiness), components),
                _measure_readiness_component("persona", asyncio.to_thread(check_persona_readiness), components),
                _measure_readiness_component("llm", check_llm_readiness(), components),
                _measure_readiness_component("embedding", check_embedding_readiness(), components),
            ),
            timeout=settings.readiness_timeout,
        )
    except asyncio.TimeoutError:
        report = _unavailable_report(check_id)
        report["failure_component"] = "unknown"
        _log_readiness_failure(check_id, report, components, round((perf_counter() - started) * 1000), "timeout")
        return report
    except Exception:
        report = _unavailable_report(check_id)
        failed = [name for name, component in components.items() if component.state == "unavailable"]
        report["failure_component"] = failed[0] if failed else "unknown"
        _log_readiness_failure(check_id, report, components, round((perf_counter() - started) * 1000), "exception")
        return report

    report = {
        "database": "ok" if database else "unavailable",
        "persona": "ok" if persona else "unavailable",
        "llm": llm,
        "embedding": embedding,
        "check_id": check_id,
    }
    if not database or not persona or "unavailable" in {llm, embedding}:
        failed = [name for name in ("database", "persona", "llm", "embedding") if report[name] == "unavailable"]
        result = {"status": "not_ready", **report, "failure_component": failed[0]}
        _log_readiness_failure(check_id, result, components, round((perf_counter() - started) * 1000))
        return result
    if "degraded" in {llm, embedding}:
        return {"status": "degraded", **report}
    return {"status": "ready", **report}
