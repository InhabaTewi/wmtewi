from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.memory.service import MemoryService
from packages.persistence.models import Message
from packages.persona.repository import PersonaRepository
from packages.schemas.chat import AgentContext, BehaviorExample, ChatEvent, KnowledgeChunk

SAFETY_TOOL_POLICY = "Follow safety policy. Use tools only when explicitly authorized."


class ContextBuilder:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.personas = PersonaRepository(session)
        self.memory = MemoryService(session)

    def build(
        self,
        event: ChatEvent,
        runtime_mode: str,
        persona_id: str = "inaba",
        knowledge_chunks: list[KnowledgeChunk] | None = None,
        behavior_examples: list[BehaviorExample] | None = None,
    ) -> AgentContext:
        persona = self.personas.get_active(persona_id)
        if persona is None:
            raise LookupError("No active Inaba persona")
        messages = list(
            reversed(
                self.session.scalars(
                    select(Message)
                    .where(Message.session_id == event.session_id)
                    .order_by(Message.created_at.desc())
                    .limit(12)
                ).all()
            )
        )
        return AgentContext(
            persona_version=persona.version,
            persona_system_prompt=persona.system_prompt,
            recent_messages=[{"role": message.role, "content": message.content} for message in messages],
            memories=self.memory.by_context(
                persona_id=persona_id,
                subject_id=event.user_id,
                session_id=event.session_id,
            ),
            knowledge_chunks=knowledge_chunks or [],
            behavior_examples=behavior_examples or [],
            runtime_mode=runtime_mode,
        )

    @staticmethod
    def to_messages(context: AgentContext, event: ChatEvent) -> list[dict[str, str]]:
        messages = [
            {
                "role": "system",
                "content": f"Persona version: {context.persona_version}\n{context.persona_system_prompt}",
            },
            {"role": "system", "content": SAFETY_TOOL_POLICY},
        ]
        messages.extend({"role": "system", "content": f"Memory: {memory.content}"} for memory in context.memories)
        messages.extend({"role": "system", "content": f"Knowledge: {chunk.content}"} for chunk in context.knowledge_chunks)
        if context.runtime_mode == "api":
            messages.extend(
                {"role": "system", "content": f"Example input: {example.input_text}\nExample output: {example.output_text}"}
                for example in context.behavior_examples
            )
        messages.extend(context.recent_messages)
        messages.append({"role": "user", "content": event.text})
        return messages