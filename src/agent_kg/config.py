"""Domain configuration — single entry point for all domain-specific settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClusteringMethod(str, Enum):
    """Available clustering strategies."""

    AGGLOMERATIVE = "agglomerative"
    HDBSCAN = "hdbscan"


class Language(str, Enum):
    """Supported prompt languages."""

    EN = "en"
    FR = "fr"


@dataclass(frozen=True)
class SeedType:
    """A single anchor type in the seed ontology."""

    label: str
    definition: str
    examples: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SeedOntology:
    """Optional seed ontology that grounds emergent types.

    Providing seed types improves reproducibility across runs and
    prevents semantic drift.  The pipeline aligns discovered types
    to these anchors via embedding similarity.  Novel types that
    don't match any seed are flagged as candidate extensions.
    """

    entity_types: list[SeedType] = field(default_factory=list)
    relation_types: list[SeedType] = field(default_factory=list)
    alignment_threshold: float = 0.80


@dataclass(frozen=True)
class RoleConfig:
    """Configuration for a single semantic role (Frame Semantics).

    Each role maps to a linguistic case role (agent, theme, etc.)
    with a domain-specific description and guiding question.
    """

    name: str
    question: str
    description: str
    examples_include: list[str] = field(default_factory=list)
    examples_exclude: list[str] = field(default_factory=list)


# ── Default roles (Fillmore case grammar) ──────────────────────────
DEFAULT_ROLES: dict[str, RoleConfig] = {
    "agent": RoleConfig(
        name="agent",
        question="Who initiates, controls, or perceives the action?",
        description="Subject of the relation — the responsible actor (human, organisation, system).",
    ),
    "theme": RoleConfig(
        name="theme",
        question="What is involved, affected, or modified?",
        description="Object of the relation — the entity being acted upon.",
    ),
    "trigger": RoleConfig(
        name="trigger",
        question="What triggers the action without intention?",
        description="Event, signal, or condition that activates the relation.",
    ),
    "purpose": RoleConfig(
        name="purpose",
        question="For what objective does the action take place?",
        description="Intention pursued or strategic goal.",
    ),
    "reason": RoleConfig(
        name="reason",
        question="What cause or justification explains the action?",
        description="Motive, explanation, norm, or constraint.",
    ),
    "instrument": RoleConfig(
        name="instrument",
        question="What means is used?",
        description="Tool, software, material, or procedure.",
    ),
    "beneficiary": RoleConfig(
        name="beneficiary",
        question="Who benefits from the action?",
        description="Client, user, or entity that profits.",
    ),
    "context": RoleConfig(
        name="context",
        question="In what framework does the action take place?",
        description="Legal, contractual, organisational, or economic environment.",
    ),
    "origin": RoleConfig(
        name="origin",
        question="Where does it come from?",
        description="Source or provenance of an action or movement.",
    ),
    "destination": RoleConfig(
        name="destination",
        question="Where or to whom does it go?",
        description="Arrival point, target of dispatch.",
    ),
    "time": RoleConfig(
        name="time",
        question="When does the action take place?",
        description="Date, time, interval.",
    ),
    "location": RoleConfig(
        name="location",
        question="Where does the action take place?",
        description="Physical or logical execution location.",
    ),
}


@dataclass(frozen=True)
class ValidationRule:
    """A declarative SHACL-like constraint for graph validation.

    Expressed as a Python-evaluable predicate string or a callable tag
    that the Validator agent resolves at runtime.
    """

    name: str
    description: str
    severity: str = "error"  # "error" | "warning"
    # Predicate expressed as a string for serialisation, or a callable name
    predicate: str = ""


# ── Default validation rules ────────────────────────────────────────
DEFAULT_VALIDATION_RULES: list[ValidationRule] = [
    ValidationRule(
        name="has_agent_and_theme",
        description="Every relation must have at least one agent and one theme.",
        severity="error",
        predicate="len(relation.roles.agents) >= 1 and len(relation.roles.themes) >= 1",
    ),
    ValidationRule(
        name="no_generic_entity_labels",
        description="Entity labels must not be generic role names.",
        severity="error",
        predicate="entity.label.lower() not in GENERIC_LABELS",
    ),
    ValidationRule(
        name="no_orphan_entities",
        description="Every entity must be connected to at least one relation.",
        severity="warning",
    ),
]


@dataclass
class DomainConfig:
    """Complete domain configuration — the single entry point for customisation.

    Attributes:
        domain_name: Human‑readable name of the domain.
        language: Language used for LLM prompts.
        domain_context: Free‑text paragraph injected into prompts to orient the LLM
            towards domain‑relevant extractions.
        seed_ontology: Optional anchor types for ontology grounding.
        roles: Semantic role definitions (Frame Semantics).
            Defaults to the 12 universal case grammar roles.
        generic_entity_blocklist: Labels that are too generic for entities.
        clustering_method: Which clustering strategy to use.
        clustering_params: Extra kwargs forwarded to the clustering strategy.
        embedding_model: OpenAI embedding model name.
        extraction_model: LLM model for entity/relation extraction.
        reasoning_model: LLM model for ontology negotiation (reasoning‑class).
        validation_model: LLM model for the validator agent's neural layer.
        validation_rules: Declarative graph constraints.
        ontology_staleness_threshold: Number of new documents before
            re‑triggering ontology negotiation.
    """

    # Domain identity
    domain_name: str = "General"
    language: Language = Language.EN

    # Domain framing
    domain_context: str = ""

    # Ontology
    seed_ontology: SeedOntology | None = None

    # Roles
    roles: dict[str, RoleConfig] = field(default_factory=lambda: dict(DEFAULT_ROLES))

    # Entity constraints
    generic_entity_blocklist: list[str] = field(
        default_factory=lambda: [
            "agent", "theme", "trigger", "purpose", "reason",
            "instrument", "beneficiary", "context", "origin",
            "destination", "co_agent", "location", "time",
            "person", "organisation", "place", "object",
            "concept", "event", "document", "information",
            "data", "file",
        ]
    )

    # Clustering
    clustering_method: ClusteringMethod = ClusteringMethod.AGGLOMERATIVE
    clustering_params: dict[str, Any] = field(default_factory=dict)

    # Models
    embedding_model: str = "text-embedding-3-small"
    extraction_model: str = "gpt-4o"
    reasoning_model: str = "o3"
    validation_model: str = "gpt-4o-mini"

    # Validation
    validation_rules: list[ValidationRule] = field(
        default_factory=lambda: list(DEFAULT_VALIDATION_RULES)
    )

    # Pipeline behaviour
    ontology_staleness_threshold: int = 50

    # Entity resolution
    entity_resolution_enabled: bool = True
    entity_resolution_similarity_threshold: float = 0.15
    entity_resolution_llm_arbitration: bool = True

    # Quality control
    qc_enabled: bool = True
