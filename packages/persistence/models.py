import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

from packages.persistence.config import DEFAULT_EMBEDDING_DIMENSION


class Base(DeclarativeBase):
    pass


class TimestampedModel:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PersonaVersion(TimestampedModel, Base):
    __tablename__ = "persona_versions"
    __table_args__ = (UniqueConstraint("persona_id", "version", name="uq_persona_versions_identity"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    persona_id: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(256))
    system_prompt: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class SessionRecord(TimestampedModel, Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[str] = mapped_column(String(256), index=True)
    persona_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("persona_versions.id"))


class Message(TimestampedModel, Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(index=True)


class MemoryAtomRecord(TimestampedModel, Base):
    __tablename__ = "memory_atoms"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[str | None] = mapped_column(String(256), index=True)
    object_id: Mapped[str | None] = mapped_column(String(256))
    persona_id: Mapped[str | None] = mapped_column(String(128), index=True)
    session_id: Mapped[str | None] = mapped_column(String(256), index=True)
    content: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(256), index=True)
    importance: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    source_event_id: Mapped[uuid.UUID | None]
    source_runtime_mode: Mapped[str] = mapped_column(String(16))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("memory_atoms.id"))


class MemoryLink(Base):
    __tablename__ = "memory_links"
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_atoms.id"), primary_key=True)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("memory_atoms.id"), primary_key=True)
    relation: Mapped[str] = mapped_column(String(64), primary_key=True)


class KnowledgeDocument(TimestampedModel, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("source_uri", "version", name="uq_knowledge_document_version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_uri: Mapped[str] = mapped_column(String(1024), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    content: Mapped[str] = mapped_column(Text)


class KnowledgeChunk(TimestampedModel, Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)


class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_chunks.id"), unique=True)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(DEFAULT_EMBEDDING_DIMENSION).with_variant(JSON, "sqlite")
    )
    embedding_model: Mapped[str] = mapped_column(String(256))


class BehaviorExample(TimestampedModel, Base):
    __tablename__ = "behavior_examples"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    persona_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("persona_versions.id"))
    input_text: Mapped[str] = mapped_column(Text)
    output_text: Mapped[str] = mapped_column(Text)


class InteractionTrace(TimestampedModel, Base):
    __tablename__ = "interaction_traces"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class Feedback(TimestampedModel, Base):
    __tablename__ = "feedback"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interaction_traces.id"), index=True)
    rating: Mapped[int | None] = mapped_column(Integer)
    corrected_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class TrainingCandidate(TimestampedModel, Base):
    __tablename__ = "training_candidates"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("interaction_traces.id"), unique=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Text)


class DatasetSnapshot(TimestampedModel, Base):
    __tablename__ = "dataset_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[str] = mapped_column(String(256), unique=True)
    dvc_rev: Mapped[str] = mapped_column(String(256))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)


class TrainingJob(TimestampedModel, Base):
    __tablename__ = "training_jobs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class TrainingRun(TimestampedModel, Base):
    __tablename__ = "training_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("training_jobs.id"))
    status: Mapped[str] = mapped_column(String(32))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON)


class ModelVersion(TimestampedModel, Base):
    __tablename__ = "model_versions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model_id: Mapped[str] = mapped_column(String(256), index=True)
    version: Mapped[str] = mapped_column(String(256))
    alias: Mapped[str | None] = mapped_column(String(32), index=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class WorkerNode(TimestampedModel, Base):
    __tablename__ = "worker_nodes"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    worker_id: Mapped[str] = mapped_column(String(128), unique=True)
    node_name: Mapped[str] = mapped_column(String(256), unique=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="REGISTERING")
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gpu_name: Mapped[str | None] = mapped_column(String(256))
    gpu_count: Mapped[int | None] = mapped_column(Integer)
    vram_total_mb: Mapped[int | None] = mapped_column(Integer)
    vram_used_mb: Mapped[int | None] = mapped_column(Integer)
    vram_free_mb: Mapped[int | None] = mapped_column(Integer)
    loaded_model: Mapped[str | None] = mapped_column(String(256))
    model_version: Mapped[str | None] = mapped_column(String(256))
    model_alias: Mapped[str | None] = mapped_column(String(128))
