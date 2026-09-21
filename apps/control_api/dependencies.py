from collections.abc import AsyncGenerator, Generator

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from packages.persistence.config import settings
from packages.knowledge.embedding import ExternalOpenAIEmbeddingProvider
from packages.knowledge.service import KnowledgeService
from packages.providers import ExternalOpenAIProvider, ProviderRouter


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


def create_provider_router() -> ProviderRouter:
    return ProviderRouter(
        external=ExternalOpenAIProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model_id=settings.llm_model,
            connect_timeout=settings.llm_connect_timeout,
            read_timeout=settings.llm_read_timeout,
            max_retries=settings.llm_max_retries,
        )
    )


async def get_provider_router() -> AsyncGenerator[ProviderRouter, None]:
    router = create_provider_router()
    try:
        yield router
    finally:
        await router.external.aclose()


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
