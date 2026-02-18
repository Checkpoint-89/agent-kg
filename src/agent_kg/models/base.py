"""Core data models for entities, relations, and semantic roles.

These models serve a dual purpose:
1. **LLM extraction schema** — passed to ``instructor`` as the response model
   for structured output.  Fields marked ``SkipJsonSchema`` are excluded from
   the schema sent to the LLM (computed at validation time).
2. **Internal data representation** — carried through the pipeline from
   extraction to graph construction.

The 12 semantic roles follow Fillmore's Frame Semantics / case grammar.
Role subclasses are deliberately kept (not collapsed into a single generic
``RoleEntity``) because:
- Each subclass carries a ``Literal`` role tag → the LLM sees the role
  constraint in the JSON schema, producing better extractions.
- Each subclass has a distinct docstring → the LLM receives role-specific
  guidance without prompt engineering.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from agent_kg.utils.sanitize import sanitize_for_identifier


# =====================================================================
# Relation type
# =====================================================================

class RelationType(BaseModel):
    """Describes *what kind* of relation this is.

    The ``(verb, target_category)`` pair uniquely identifies the relation
    class. The ``axis`` classifies it along one of three fundamental
    dimensions.
    """

    model_config = ConfigDict(extra="forbid")

    axis: Literal["ONTOLOGICAL", "DYNAMIC", "STRUCTURAL"] = Field(
        ...,
        description=(
            "Classification axis.  ONTOLOGICAL = stable/inherent properties; "
            "DYNAMIC = actions/processes/events; STRUCTURAL = organisational links."
        ),
    )
    verb: str = Field(
        ...,
        description="Infinitive verb phrase describing the interaction or state. No subject or object.",
    )
    target_category: str = Field(
        ...,
        description=(
            "Generic category of the object/target of the verb. "
            "Disambiguates relations sharing the same verb."
        ),
    )
    definition: str = Field(
        ...,
        description=(
            "Domain-independent definition of the relation class. "
            "Must be generic enough for embedding-based clustering."
        ),
    )

    # Computed — excluded from LLM schema
    label: SkipJsonSchema[str | None] = Field(
        default=None,
        description="Canonical identifier derived from verb + target_category.",
    )

    # ── Validators ──────────────────────────────────────────────────
    @field_validator("verb", mode="after")
    @classmethod
    def _normalise_verb(cls, v: str) -> str:
        return sanitize_for_identifier(v.strip(), style="upper")

    @model_validator(mode="after")
    def _compute_label(self) -> RelationType:
        if self.verb:
            raw = f"{self.verb}_{self.target_category.strip().replace(' ', '_')}"
            self.label = sanitize_for_identifier(raw, style="upper")
        return self


# =====================================================================
# Entity type (lightweight reference used in ontology models)
# =====================================================================

class EntityType(BaseModel):
    """A named entity class with its definition.

    Symmetrical with ``RelationType`` — normalised label and computed
    embedding text for clustering.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(
        ...,
        description="Entity class name (e.g. 'Person', 'Organisation').",
    )
    definition: str = Field(
        ...,
        description=(
            "Domain-independent definition of the entity class. "
            "Must be generic enough for embedding-based clustering."
        ),
    )

    # Computed — excluded from LLM schema
    to_embed: SkipJsonSchema[str | None] = Field(
        default=None,
        description="Embedding text for clustering.",
    )

    # ── Validators ──────────────────────────────────────────────────
    @field_validator("label", mode="after")
    @classmethod
    def _normalise_label(cls, v: str) -> str:
        return v.strip().title()

    @model_validator(mode="after")
    def _compute_fields(self) -> EntityType:
        self.to_embed = f"Entity type: {self.label}. Definition: {self.definition}"
        return self


# =====================================================================
# Property (key-value attribute)
# =====================================================================

class Property(BaseModel):
    """A single key-value attribute attached to an entity or relation."""

    key: str = Field(..., description="Attribute name.")
    value: str = Field(..., description="Attribute value.")

    @field_validator("key", mode="after")
    @classmethod
    def _snake_case(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "_")

    @field_validator("value", mode="after")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()

    def __str__(self) -> str:
        return f"{self.key}: {self.value}"


# =====================================================================
# Base entity
# =====================================================================

class Entity(BaseModel):
    """An entity extracted from a document.

    ``label`` is the *class*, ``name`` is the *instance*. The ``definition`` field describes the
    class in domain-independent terms for embedding and clustering.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., description="Entity class.")
    name: str = Field(
        ...,
        description="Exact entity name as it appears in the source text.",
    )
    definition: str = Field(
        ...,
        description=(
            "Domain-independent definition of the entity class. "
            "Must be generic enough for embedding-based clustering."
        ),
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="LLM self-assessed extraction confidence.",
    )

    # Computed — excluded from LLM schema
    entity_type: SkipJsonSchema[EntityType | None] = Field(default=None)
    metadata: SkipJsonSchema[dict[str, Any] | None] = Field(default=None)
    to_embed: SkipJsonSchema[str | None] = Field(default=None)

    # ── Validators ──────────────────────────────────────────────────
    @field_validator("label", "name", mode="after")
    @classmethod
    def _title_case(cls, v: str) -> str:
        return v.strip().title()

    @model_validator(mode="after")
    def _compute_fields(self) -> Entity:
        self.to_embed = f"Entity class: {self.label}. Definition: {self.definition}"
        self.entity_type = EntityType(label=self.label, definition=self.definition)
        return self

    def check_not_generic(self, blocklist: list[str]) -> None:
        """Raise ``ValueError`` if the label is in the blocklist.

        Called explicitly by the extraction agent rather than in a
        validator, so the blocklist can come from ``DomainConfig``.
        """
        if self.label.lower() in [b.lower() for b in blocklist]:
            raise ValueError(
                f"Label '{self.label}' is too generic. "
                f"Forbidden: {', '.join(blocklist[:10])}…"
            )


# =====================================================================
# Role-typed entity subclasses (Frame Semantics)
# =====================================================================

class AgentEntity(Entity):
    """Entity that initiates, controls, or perceives the action."""

    role: Literal["agent"] = Field(
        "agent",
        description="Semantic role: the actor who initiates or controls the action.",
    )


class ThemeEntity(Entity):
    """Entity that is involved, affected, or modified."""

    role: Literal["theme"] = Field(
        "theme",
        description="Semantic role: the entity acted upon or affected.",
    )


class CircumstanceEntity(Entity):
    """Entity playing a circumstantial role (trigger, purpose, reason, instrument, beneficiary)."""

    role: Literal["trigger", "purpose", "reason", "instrument", "beneficiary"] = Field(
        ...,
        description="Semantic role: circumstantial participant in the relation.",
    )


class ContextEntity(Entity):
    """Entity representing the framework in which the action takes place."""

    role: Literal["context"] = Field(
        "context",
        description="Semantic role: legal, contractual, or organisational environment.",
    )


class OriginDestinationEntity(Entity):
    """Entity playing an origin or destination role."""

    role: Literal["origin", "destination"] = Field(
        ...,
        description="Semantic role: source or target of movement / dispatch.",
    )


class TimeLocationEntity(Entity):
    """Entity anchoring the action in time or space."""

    role: Literal["time", "location"] = Field(
        ...,
        description="Semantic role: temporal or spatial anchor.",
    )


# =====================================================================
# Roles container
# =====================================================================

class Roles(BaseModel):
    """All semantic roles populated for a single relation.

    When entities don't match any known entity type, the LLM should
    add the novel types to ``candidate_entity_types`` for Arbiter review.
    """

    agents: list[AgentEntity] = Field(
        ...,
        min_length=1,
        description="Entities playing the agent role.",
    )
    themes: list[ThemeEntity] = Field(
        ...,
        min_length=1,
        description="Entities playing the theme role.",
    )
    circumstances: list[CircumstanceEntity] = Field(
        default_factory=list,
        description="Entities playing circumstantial roles.",
    )
    context: list[ContextEntity] = Field(
        default_factory=list,
        description="Entities playing the context role.",
    )
    origin_destinations: list[OriginDestinationEntity] = Field(
        default_factory=list,
        description="Entities playing origin/destination roles.",
    )
    time_locations: list[TimeLocationEntity] = Field(
        default_factory=list,
        description="Entities playing time/location roles.",
    )
    candidate_entity_types: list[EntityType] = Field(
        default_factory=list,
        description=(
            "Novel entity types not found among known types. "
            "Only add types genuinely absent from the known list."
        ),
    )

    def all_entities(self) -> list[Entity]:
        """Flatten all role lists into a single entity list."""
        return [
            *self.agents,
            *self.themes,
            *self.circumstances,
            *self.context,
            *self.origin_destinations,
            *self.time_locations,
        ]


# =====================================================================
# Relation
# =====================================================================

class Provenance(BaseModel):
    """Links a relation back to its source document."""

    document_id: str = Field(..., description="Source document identifier.")
    quote: str = Field(
        ...,
        description="Verbatim excerpt from the source that best illustrates the relation.",
    )


class RawRelation(BaseModel):
    """A relation without semantic roles — output of the Relation extraction step.

    Roles are filled in a subsequent step by the Role extraction.
    Call ``with_roles()`` to produce a full ``Relation``.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        ...,
        description=(
            "Describe the relation in its context. Be precise and exhaustive. "
            "The description must be self-contained — understandable without "
            "the source document."
        ),
    )
    relation_type: RelationType = Field(..., description="Type of the relation.")
    provenance: Provenance = Field(..., description="Source document and supporting quote.")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="LLM self-assessed extraction confidence.",
    )

    # Computed — excluded from LLM schema
    to_embed: SkipJsonSchema[str | None] = Field(default=None)

    # ── Validators ──────────────────────────────────────────────────
    @model_validator(mode="after")
    def _compute_fields(self) -> RawRelation:
        label = self.relation_type.label or self.relation_type.verb
        self.to_embed = (
            f"Relation: {label}. "
            f"Definition: {self.relation_type.definition}. "
            f"{self.description}"
        )
        return self

    def with_roles(self, roles: Roles) -> Relation:
        """Combine with extracted roles to produce a full ``Relation``."""
        return Relation(
            description=self.description,
            relation_type=self.relation_type,
            roles=roles,
            provenance=self.provenance,
            confidence=self.confidence,
        )


class Relation(BaseModel):
    """A relation extracted from a document.

    Relations are reified as first-class objects (not mere edges)
    because they carry their own properties, provenance, and roles.
    """

    model_config = ConfigDict(extra="forbid")

    # Core
    description: str = Field(
        ...,
        description=(
            "Describe the relation in its context. Be precise and exhaustive. "
            "The description must be self-contained — understandable without "
            "the source document."
        ),
    )
    relation_type: RelationType = Field(..., description="Type of the relation.")
    roles: Roles = Field(..., description="Semantic roles and their entity assignments.")
    provenance: Provenance = Field(..., description="Source document and supporting quote.")
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="LLM self-assessed extraction confidence.",
    )

    # Computed — excluded from LLM schema
    labels: SkipJsonSchema[list[str] | None] = Field(default=None)
    generic: SkipJsonSchema[str | None] = Field(default=None)
    specific: SkipJsonSchema[str | None] = Field(default=None)
    to_embed: SkipJsonSchema[str | None] = Field(default=None)
    metadata: SkipJsonSchema[dict[str, Any] | None] = Field(default=None)

    # ── Validators ──────────────────────────────────────────────────
    @model_validator(mode="after")
    def _compute_fields(self) -> Relation:
        subject_label = self.roles.agents[0].label
        obj_label = self.roles.themes[0].label

        self.labels = [self.relation_type.verb, self.relation_type.label or ""]
        self.generic = f"{subject_label} {self.relation_type.label} {obj_label}"
        self.specific = (
            f"{subject_label} ({self.roles.agents[0].name}) "
            f"{self.relation_type.label} "
            f"{obj_label} ({self.roles.themes[0].name})"
        )
        self.to_embed = (
            f"Generic: {self.generic}. "
            f"Definition: {self.relation_type.definition}"
        )
        return self

    def update_metadata(self, extra: dict[str, Any]) -> None:
        if self.metadata is None:
            self.metadata = {}
        self.metadata.update(extra)


# =====================================================================
# Document-level extraction result
# =====================================================================

class DocumentRawRelations(BaseModel):
    """Top-level raw extraction result for a document.

    Response model for the Relation extraction step (no roles).
    """

    model_config = ConfigDict(extra="forbid")

    relations: list[RawRelation] = Field(
        ...,
        description="Relations extracted from the document (without semantic roles).",
    )


class CandidateType(BaseModel):
    """A novel type discovered during extraction, pending Arbiter review."""

    kind: Literal["relation", "entity"] = Field(
        ..., description="Whether this is a relation type or entity type.",
    )
    label: str = Field(..., description="Type label.")
    definition: str = Field(..., description="Type definition.")
    source_description: str = Field(
        default="",
        description="Relation/entity context that triggered this candidate.",
    )


# =====================================================================
# Entity resolution models
# =====================================================================


class MergeDecision(BaseModel):
    """LLM-arbitrated merge decision for a cluster of entity mentions.

    Used as ``instructor`` response model during entity resolution
    Stage 3 (LLM arbitration).
    """

    should_merge: bool = Field(
        ...,
        description="True if all mentions in the cluster refer to the same real-world entity.",
    )
    canonical_name: str = Field(
        ...,
        description="The preferred surface form to use as the canonical name.",
    )
    canonical_label: str = Field(
        ...,
        description="The preferred entity class label.",
    )
    canonical_definition: str = Field(
        ...,
        description="Unified definition for the merged entity class.",
    )
    reasoning: str = Field(
        ...,
        description="Explanation of why these mentions should or should not be merged.",
    )


class ResolutionEntry(BaseModel):
    """One merge operation in the resolution report."""

    canonical_name: str
    canonical_label: str
    aliases: list[str] = Field(default_factory=list)
    mention_count: int = 0
    method: str = Field(
        ...,
        description="Resolution method: 'exact', 'embedding', or 'llm'.",
    )


class ResolutionReport(BaseModel):
    """Summary of entity resolution results."""

    total_mentions: int = 0
    unique_before: int = 0
    unique_after: int = 0
    merges: list[ResolutionEntry] = Field(default_factory=list)
