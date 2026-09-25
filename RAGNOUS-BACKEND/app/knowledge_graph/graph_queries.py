"""Query helpers over `concepts` + `concept_edges` + `weak_topics`.

Three public functions:

- `find_concept(name)`  — case-insensitive lookup, returns `Concept | None`.
- `prerequisites_of(concept_id)` — breadth-first walk up the `prerequisite`
  edges, so the tutor can say "before we tackle X you need Y and Z".
- `stale_weak_concepts(user_id, limit)` — the "next review is due" list,
  used by the Revision workflow (once we bring the FE surface back).

Uses raw asyncpg through `db.session.get_conn` — the queries are small and
the ORM's extra layer would be a distraction.
"""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from app.db.session import get_conn
from app.knowledge_graph.graph_schema import Concept, WeakConcept


async def find_concept(name: str) -> Optional[Concept]:
    async with get_conn() as conn:
        if conn is None:
            return None
        row = await conn.fetchrow(
            "SELECT id, name, subject FROM concepts WHERE lower(name) = lower($1) LIMIT 1",
            name,
        )
    if not row:
        return None
    return Concept(id=row["id"], name=row["name"], subject=row["subject"])


async def prerequisites_of(concept_id: UUID, max_depth: int = 3) -> List[Concept]:
    """BFS the parents of `concept_id` up to `max_depth` levels.

    Cycles in the edge table (however implausible) are handled by tracking
    visited ids — otherwise a cycle would loop forever on this query.
    """
    async with get_conn() as conn:
        if conn is None:
            return []

        visited: set[UUID] = set()
        frontier: list[UUID] = [concept_id]
        results: list[Concept] = []

        for _ in range(max_depth):
            if not frontier:
                break
            rows = await conn.fetch(
                """
                SELECT c.id, c.name, c.subject
                FROM concept_edges e
                JOIN concepts c ON c.id = e.parent_id
                WHERE e.child_id = ANY($1::uuid[])
                  AND e.relation = 'prerequisite'
                """,
                frontier,
            )
            next_frontier: list[UUID] = []
            for r in rows:
                if r["id"] in visited:
                    continue
                visited.add(r["id"])
                results.append(Concept(id=r["id"], name=r["name"], subject=r["subject"]))
                next_frontier.append(r["id"])
            frontier = next_frontier

        return results


async def stale_weak_concepts(user_id: str, limit: int = 10) -> List[WeakConcept]:
    """Concepts whose `next_review` is in the past or NULL, oldest first."""
    async with get_conn() as conn:
        if conn is None:
            return []
        rows = await conn.fetch(
            """
            SELECT c.id, c.name, c.subject,
                   wt.confidence_score, wt.last_reviewed, wt.next_review
            FROM weak_topics wt
            JOIN concepts c ON c.id = wt.concept_id
            WHERE wt.user_id = $1::uuid
              AND (wt.next_review IS NULL OR wt.next_review <= now())
            ORDER BY wt.next_review NULLS FIRST
            LIMIT $2
            """,
            user_id, limit,
        )
    return [
        WeakConcept(
            concept=Concept(id=r["id"], name=r["name"], subject=r["subject"]),
            confidence_score=float(r["confidence_score"] or 0.0),
            last_reviewed=r["last_reviewed"].isoformat() if r["last_reviewed"] else None,
            next_review=r["next_review"].isoformat() if r["next_review"] else None,
        )
        for r in rows
    ]
