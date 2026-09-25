"""SQLAlchemy ORM mirror of `schema.sql`.

The vector-heavy queries (retrieval, episodic recall) will stay on raw
asyncpg — the SQL is doing exact-form pgvector work and ORMs get in the way
of that. But CRUD on `semantic_profiles`, `concepts`, `weak_topics`, `pyqs`
is more pleasant through the ORM, so this file gives new code a typed
alternative.

The ORM and raw-SQL layers share the underlying tables — care is taken to
keep the column list identical to `schema.sql`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for RAGNOUS tables."""


class User(Base):
    __tablename__ = "users"

    id: Any = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Any = Column(Text, unique=True, nullable=False)
    hashed_password: Any = Column(Text, nullable=False)
    role: Any = Column(Text, nullable=False, default="student")
    created_at: Any = Column(DateTime(timezone=True), default=datetime.utcnow)


class SemanticProfile(Base):
    __tablename__ = "semantic_profiles"

    user_id: Any = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    learning_style: Any = Column(Text)
    language_preference: Any = Column(Text, default="hinglish")
    weak_subjects: Any = Column(JSONB, default=list)
    updated_at: Any = Column(DateTime(timezone=True), default=datetime.utcnow)


class Concept(Base):
    __tablename__ = "concepts"

    id: Any = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Any = Column(Text, nullable=False)
    subject: Any = Column(Text, nullable=False)


class ConceptEdge(Base):
    __tablename__ = "concept_edges"

    parent_id: Any = Column(UUID(as_uuid=True), ForeignKey("concepts.id"), primary_key=True)
    child_id: Any = Column(UUID(as_uuid=True), ForeignKey("concepts.id"), primary_key=True)
    relation: Any = Column(Text, default="prerequisite")


class WeakTopic(Base):
    __tablename__ = "weak_topics"

    user_id: Any = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    concept_id: Any = Column(UUID(as_uuid=True), ForeignKey("concepts.id"), primary_key=True)
    confidence_score: Any = Column(Float, default=0.0)
    last_reviewed: Any = Column(DateTime(timezone=True))
    next_review: Any = Column(DateTime(timezone=True))


class PYQ(Base):
    __tablename__ = "pyqs"

    id: Any = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subject: Any = Column(Text)
    chapter: Any = Column(Text)
    year: Any = Column(Integer)
    question: Any = Column(Text)
    marks: Any = Column(Integer)
    rubric: Any = Column(Text)


class TopicVideo(Base):
    __tablename__ = "topic_videos"

    topic_id: Any = Column(UUID(as_uuid=True), ForeignKey("concepts.id"), primary_key=True)
    youtube_url: Any = Column(Text, nullable=False, primary_key=True)
    title: Any = Column(Text)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Any = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query: Any = Column(Text)
    retrieval_precision: Any = Column(Float)
    retrieval_recall: Any = Column(Float)
    citation_accuracy: Any = Column(Float)
    hallucination_flag: Any = Column(Boolean)
    latency_ms: Any = Column(Integer)
    tokens_used: Any = Column(Integer)
    cost_usd: Any = Column(Numeric)
    created_at: Any = Column(DateTime(timezone=True), default=datetime.utcnow)


__all__ = [
    "Base",
    "Concept",
    "ConceptEdge",
    "EvalRun",
    "PYQ",
    "SemanticProfile",
    "TopicVideo",
    "User",
    "WeakTopic",
]
