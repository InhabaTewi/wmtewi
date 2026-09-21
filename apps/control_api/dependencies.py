from collections.abc import Generator

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from packages.persistence.config import settings
from packages.knowledge.embedding import ExternalOpenAIEmbeddingProvider
from packages.knowledge.service import KnowledgeService
from packages.providers import ExternalOpenAIProvider, ProviderRouter

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def get_provider_router() -> ProviderRouter:
    return ProviderRouter(
        external=ExternalOpenAIProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model_id=settings.llm_model,
            connect_timeout=settings.llm_connect_timeout,
            read_timeout=settings.llm_read_timeout,
        )
    )


def get_knowledge_service(session: Session = Depends(get_session)) -> KnowledgeService:
    return KnowledgeService(
        session,
        ExternalOpenAIEmbeddingProvider(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model_id=settings.embedding_model,
            dimension=settings.embedding_dimension,
        ),
    )
