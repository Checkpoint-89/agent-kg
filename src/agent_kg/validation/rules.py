"""Validation rules — SHACL-like constraints expressed as Python predicates.

The symbolic layer checks graph consistency without LLM calls.
Each rule returns a list of ``Violation`` objects that the neural
layer (ValidatorAgent) can then resolve.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agent_kg.models.base import Entity, Relation

logger = logging.getLogger(__name__)


@dataclass
class Violation:
    """A single constraint violation."""

    rule_name: str
    severity: str  # "error" | "warning"
    message: str
    subject_type: str  # "relation" | "entity"
    subject_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)


def check_has_agent_and_theme(relation: Relation) -> list[Violation]:
    """Every relation must have ≥1 agent and ≥1 theme."""
    violations = []
    if len(relation.roles.agents) < 1:
        violations.append(Violation(
            rule_name="has_agent_and_theme",
            severity="error",
            message=f"Relation '{relation.generic}' has no agent role.",
            subject_type="relation",
        ))
    if len(relation.roles.themes) < 1:
        violations.append(Violation(
            rule_name="has_agent_and_theme",
            severity="error",
            message=f"Relation '{relation.generic}' has no theme role.",
            subject_type="relation",
        ))
    return violations


def check_no_generic_entity_labels(
    entity: Entity,
    blocklist: list[str],
) -> list[Violation]:
    """Entity labels must not be generic role names."""
    if entity.label.lower() in [b.lower() for b in blocklist]:
        return [Violation(
            rule_name="no_generic_entity_labels",
            severity="error",
            message=f"Entity label '{entity.label}' is too generic.",
            subject_type="entity",
            context={"label": entity.label},
        )]
    return []


def check_provenance_non_empty(relation: Relation) -> list[Violation]:
    """Relations must have a non-empty provenance quote."""
    if not relation.provenance.quote.strip():
        return [Violation(
            rule_name="provenance_non_empty",
            severity="warning",
            message=f"Relation '{relation.generic}' has an empty provenance quote.",
            subject_type="relation",
        )]
    return []


def check_confidence_threshold(
    relation: Relation,
    threshold: float = 0.3,
) -> list[Violation]:
    """Flag relations with very low confidence."""
    if relation.confidence < threshold:
        return [Violation(
            rule_name="low_confidence",
            severity="warning",
            message=(
                f"Relation '{relation.generic}' has low confidence "
                f"({relation.confidence:.2f} < {threshold:.2f})."
            ),
            subject_type="relation",
            context={"confidence": relation.confidence},
        )]
    return []


def check_no_duplicate_entities_in_relation(relation: Relation) -> list[Violation]:
    """An entity should not appear in multiple roles with the same name+label."""
    seen: set[tuple[str, str]] = set()
    violations = []
    for entity in relation.roles.all_entities():
        key = (entity.label.lower(), entity.name.lower())
        if key in seen:
            violations.append(Violation(
                rule_name="duplicate_entity_in_relation",
                severity="warning",
                message=(
                    f"Entity '{entity.name}' ({entity.label}) appears "
                    f"multiple times in relation '{relation.generic}'."
                ),
                subject_type="entity",
                context={"label": entity.label, "name": entity.name},
            ))
        seen.add(key)
    return violations


# ── Master runner ───────────────────────────────────────────────────

def run_symbolic_validation(
    relations: list[Relation],
    blocklist: list[str],
    confidence_threshold: float = 0.3,
) -> list[Violation]:
    """Run all symbolic validation rules on a list of relations.

    Returns:
        Flat list of all violations found.
    """
    violations: list[Violation] = []

    for relation in relations:
        violations.extend(check_has_agent_and_theme(relation))
        violations.extend(check_provenance_non_empty(relation))
        violations.extend(check_confidence_threshold(relation, confidence_threshold))
        violations.extend(check_no_duplicate_entities_in_relation(relation))

        for entity in relation.roles.all_entities():
            violations.extend(check_no_generic_entity_labels(entity, blocklist))

    if violations:
        errors = sum(1 for v in violations if v.severity == "error")
        warnings = sum(1 for v in violations if v.severity == "warning")
        logger.info("Symbolic validation: %d errors, %d warnings.", errors, warnings)

    return violations
