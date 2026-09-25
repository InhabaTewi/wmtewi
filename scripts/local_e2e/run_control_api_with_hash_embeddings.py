import uvicorn
from fastapi import Depends
from sqlalchemy.orm import Session

from apps.control_api import dependencies
from apps.control_api.main import app, get_knowledge_service
from packages.knowledge.embedding import HashEmbeddingProvider
from packages.knowledge.service import KnowledgeService


async def get_local_e2e_knowledge_service(
    session: Session = Depends(dependencies.get_session),
):
    yield KnowledgeService(session, HashEmbeddingProvider())


app.dependency_overrides[get_knowledge_service] = get_local_e2e_knowledge_service


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18080)