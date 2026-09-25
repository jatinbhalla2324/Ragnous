"""Typed shapes for the concept dependency graph.

Not a graph *engine* — Postgres owns the storage, and `concepts` /
`concept_edges` are already declared in `schema.sql`. This file gives the
rest of the codebase a Python type to hand around when it's easier than
juggling `dict` rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal, Optional
from uuid import UUID


Relation = Literal["prerequisite", "leads_to", "related"]


@dataclass(frozen=True)
class Concept:
    id: UUID
    name: str
    subject: str


@dataclass(frozen=True)
class ConceptEdge:
    parent_id: UUID
    child_id: UUID
    relation: Relation = "prerequisite"


@dataclass
class ConceptPath:
    """A prerequisite chain a student should learn in order."""
    subject: str
    start: Concept
    end: Concept
    steps: List[Concept] = field(default_factory=list)


@dataclass
class WeakConcept:
    """`weak_topics` row joined with the concept metadata."""
    concept: Concept
    confidence_score: float
    last_reviewed: Optional[str]
    next_review: Optional[str]
