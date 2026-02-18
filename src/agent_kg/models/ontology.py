"""Ontology models — versioned schema.

The ontology is a *living artifact* that evolves as new documents are
processed.  It is represented as a versioned ``OntologySchema`` that
tracks both seed-anchored types and emergent extensions.

Type governance (accept / merge / reject) is handled by the Arbiter
agent (see ``arbiter_agent.py``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


# =====================================================================
# Versioned ontology schema
# =====================================================================

class OntologyType(BaseModel):
    """A single type (entity or relation) in the ontology."""

    label: str = Field(..., description="Canonical label.")
    definition: str = Field(..., description="Definition.")
    is_seed: bool = Field(
        default=False,
        description="True if this type comes from the seed ontology.",
    )
    cluster_name: str | None = Field(
        default=None,
        description="Name of the cluster this type was synthesised from.",
    )


class OntologySchema(BaseModel):
    """A versioned snapshot of the discovered ontology.

    Each negotiation round produces a new version.  The ``version``
    counter is monotonically increasing.  ``parent_version`` points
    to the version this was derived from (``None`` for the initial).
    """

    version: int = Field(default=1, description="Monotonically increasing version number.")
    parent_version: int | None = Field(
        default=None,
        description="Version this was derived from.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of creation.",
    )

    entity_types: list[OntologyType] = Field(default_factory=list)
    relation_types: list[OntologyType] = Field(default_factory=list)

    # Bookkeeping
    documents_since_last_negotiation: int = Field(
        default=0,
        description="Number of documents processed since this version was created.",
    )

    def is_stale(self, threshold: int) -> bool:
        """Check if the ontology should be re-negotiated."""
        return self.documents_since_last_negotiation >= threshold

    def type_labels(self) -> set[str]:
        """All type labels (entity + relation) in this schema."""
        return {t.label for t in self.entity_types} | {t.label for t in self.relation_types}
