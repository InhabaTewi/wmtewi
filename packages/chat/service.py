from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from sqlalchemy.orm import Session

from packages.context_builder.builder import ContextBuilder
from packages.knowledge.service import KnowledgeService
from packages.memory.service import MemoryService
from packages.persistence.models import InteractionTrace, Message, SessionRecord
from packages.providers.router import ProviderRouter
from packages.schemas.chat import AgentResponse, ChatEvent, ChatRequest, ChatResult
from packages.schemas.memory import MemoryCandidateCreate


class ChatService:
    def __init__(
        self,
        session: Session,
        router: ProviderRouter,
        knowledge_service: KnowledgeService | None = None,
    ) -> None:
        self.session = session
        self.router = router
        self.knowledge_service = knowledge_service

    async def chat(self, request: ChatRequest) -> ChatResult:
        trace_id = uuid4()
        event = ChatEvent(
            event_id=uuid4(),
            trace_id=trace_id,
            channel=request.channel,
            session_id=request.session_id,
            user_id=request.user_id,
            text=request.text,
            timestamp=datetime.now(UTC),
            metadata=request.metadata,
        )
        route = await self.router.select()
        knowledge_chunks = await self.knowledge_service.asearch(event.text) if self.knowledge_service else []
        context = ContextBuilder(self.session).build(
            event,
            route.runtime_mode,
            request.persona_id,
            knowledge_chunks,
        )
        started_at = perf_counter()
        response, route = await self.router.generate(
            ContextBuilder.to_messages(context, event), AgentResponse, str(trace_id)
        )
        latency_ms = round((perf_counter() - started_at) * 1000)
        self._persist(event, context, response, route, latency_ms, request.persona_id)
        self.session.commit()
        return ChatResult(
            trace_id=trace_id,
            runtime_mode=route.runtime_mode,
            provider=route.provider.name,
            response=response,
        )

    def _persist(self, event, context, response, route, latency_ms: int, persona_id: str) -> None:
        session_record = self.session.get(SessionRecord, event.session_id)
        if session_record is None:
            session_record = SessionRecord(
                id=event.session_id,
                channel=event.channel,
                user_id=event.user_id,
            )
            self.session.add(session_record)
            self.session.flush()
        self.session.add_all(
            [
                Message(session_id=event.session_id, role="user", content=event.text, trace_id=event.trace_id),
                Message(session_id=event.session_id, role="assistant", content=response.speech, trace_id=event.trace_id),
            ]
        )
        memory_service = MemoryService(self.session)
        for candidate in response.memory_candidates:
            memory_service.create_candidate(
                MemoryCandidateCreate(
                    **(
                        candidate.model_dump()
                        | {
                            "source_event_id": event.event_id,
                            "source_runtime_mode": route.runtime_mode,
                            "persona_id": persona_id,
                            "session_id": event.session_id,
                        }
                    ),
                )
            )
        self.session.add(
            InteractionTrace(
                id=event.trace_id,
                event_id=event.event_id,
                payload={
                    "trace_id": str(event.trace_id),
                    "event_id": str(event.event_id),
                    "persona_version": context.persona_version,
                    "memory_ids": [str(memory.id) for memory in context.memories],
                    "knowledge_chunk_ids": [str(chunk.id) for chunk in context.knowledge_chunks],
                    "behavior_example_ids": [str(example.id) for example in context.behavior_examples],
                    "runtime_mode": route.runtime_mode,
                    "provider": route.provider.name,
                    "model_id": route.provider.model_id,
                    "response": response.model_dump(mode="json"),
                    "latency_ms": latency_ms,
                },
            )
        )