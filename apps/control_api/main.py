from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from sqlalchemy.orm import Session

from apps.control_api.dependencies import get_knowledge_service, get_provider_router, get_session
from packages.chat.service import ChatService
from packages.knowledge.service import KnowledgeNotFoundError, KnowledgeService
from packages.memory.service import MemoryNotFoundError, MemoryService
from packages.persona.repository import PersonaRepository
from packages.providers import ProviderRouter, ProviderUnavailableError
from packages.schemas.chat import ChatRequest, ChatResult
from packages.schemas.knowledge import KnowledgeDocumentCreate, KnowledgeDocumentRead, KnowledgeSearchResponse
from packages.schemas.memory import (
    MemoryAtom,
    MemoryCandidateCreate,
    MemorySearchRequest,
    MemorySupersedeRequest,
)
from packages.schemas.persona import PersonaVersionRead

app = FastAPI(title="Inaba AI Control API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/personas/{persona_id}/active", response_model=PersonaVersionRead)
def get_active_persona(persona_id: str, session: Session = Depends(get_session)) -> PersonaVersionRead:
    persona = PersonaRepository(session).get_active(persona_id)
    if persona is None:
        raise HTTPException(status_code=404, detail="Active persona not found")
    return PersonaVersionRead.model_validate(persona)


@app.post("/api/memory/search", response_model=list[MemoryAtom])
def search_memory(request: MemorySearchRequest, session: Session = Depends(get_session)) -> list[MemoryAtom]:
    return MemoryService(session).search(request)


@app.post("/api/memory/candidates", response_model=MemoryAtom, status_code=201)
def create_memory_candidate(
    candidate: MemoryCandidateCreate, session: Session = Depends(get_session)
) -> MemoryAtom:
    memory = MemoryService(session).create_candidate(candidate)
    session.commit()
    return memory


@app.post("/api/memory/{memory_id}/confirm", response_model=MemoryAtom)
def confirm_memory(memory_id: str, session: Session = Depends(get_session)) -> MemoryAtom:
    try:
        memory = MemoryService(session).confirm(UUID(memory_id))
    except (MemoryNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Memory not found") from None
    session.commit()
    return memory


@app.post("/api/memory/{memory_id}/supersede", response_model=MemoryAtom, status_code=201)
def supersede_memory(
    memory_id: str,
    replacement: MemorySupersedeRequest,
    session: Session = Depends(get_session),
) -> MemoryAtom:
    try:
        memory = MemoryService(session).supersede(UUID(memory_id), replacement)
    except (MemoryNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="Memory not found") from None
    session.commit()
    return memory


@app.get("/api/memory/subject/{subject_id}", response_model=list[MemoryAtom])
def get_subject_memory(subject_id: str, session: Session = Depends(get_session)) -> list[MemoryAtom]:
    return MemoryService(session).by_subject(subject_id)


@app.post("/api/chat", response_model=ChatResult)
async def chat(
    request: ChatRequest,
    session: Session = Depends(get_session),
    router: ProviderRouter = Depends(get_provider_router),
    knowledge_service: KnowledgeService = Depends(get_knowledge_service),
) -> ChatResult:
    try:
        return await ChatService(session, router, knowledge_service).chat(request)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProviderUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/knowledge/documents", response_model=KnowledgeDocumentRead, status_code=201)
async def ingest_knowledge_document(
    request: KnowledgeDocumentCreate, knowledge: KnowledgeService = Depends(get_knowledge_service)
) -> KnowledgeDocumentRead:
    document = await knowledge.aingest(request)
    knowledge.session.commit()
    return document


@app.post("/api/knowledge/reindex/{document_id}", response_model=KnowledgeDocumentRead)
async def reindex_knowledge_document(
    document_id: UUID, knowledge: KnowledgeService = Depends(get_knowledge_service)
) -> KnowledgeDocumentRead:
    try:
        document = await knowledge.areindex(document_id)
    except KnowledgeNotFoundError:
        raise HTTPException(status_code=404, detail="Knowledge document not found") from None
    knowledge.session.commit()
    return document


@app.get("/api/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    q: str, limit: int = Query(default=5, ge=1, le=20), knowledge: KnowledgeService = Depends(get_knowledge_service)
) -> KnowledgeSearchResponse:
    return KnowledgeSearchResponse(chunks=await knowledge.asearch(q, limit))


@app.delete("/api/knowledge/documents/{document_id}", status_code=204)
def delete_knowledge_document(
    document_id: UUID, knowledge: KnowledgeService = Depends(get_knowledge_service)
) -> Response:
    try:
        knowledge.delete(document_id)
    except KnowledgeNotFoundError:
        raise HTTPException(status_code=404, detail="Knowledge document not found") from None
    knowledge.session.commit()
    return Response(status_code=204)
