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


def check_source_non_empty(relation: Relation) -> list[Violation]:
    """Relations must have at least one non-empty source quote."""
    if not relation.source.quotes or not any(q.strip() for q in relation.source.quotes):
        return [Violation(
            rule_name="source_non_empty",
            severity="warning",
            message=f"Relation '{relation.generic}' has no source quotes.",
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

def check_quotes_are_verbatim(
    relation: Relation,
    document_text: str,
    chunk_text: str | None = None,
) -> list[Violation]:
    """Each source quote must be an exact substring of its source chunk (or document).

    When *chunk_text* is provided (i.e. the relation has a ``chunk_id`` and the
    chunk text is known), the quote is validated against the **chunk** — a tighter
    scope that also confirms the chunk assignment is correct.

    Falls back to full document text when chunk text is unavailable.

    Note: in the main pipeline path, verbatimness is enforced during relation
    extraction via Pydantic validation context (so the LLM is retried
    automatically). This symbolic rule is a fail-closed backstop.
    """
    reference_text = chunk_text if chunk_text else document_text
    scope = "chunk" if chunk_text else "document"

    if not reference_text:
        return []  # can't verify without source text

    violations: list[Violation] = []
    for quote in relation.source.quotes:
        if quote not in reference_text:
            violations.append(Violation(
                rule_name="quote_not_verbatim",
                severity="error",
                message=(
                    f"Relation '{relation.generic}': source quote is not "
                    f"an exact substring of the source {scope}: "
                    f"'{quote[:80]}...'"
                ),
                subject_type="relation",
                context={
                    "document_id": relation.source.document_id,
                    "chunk_id": relation.source.chunk_id,
                    "scope": scope,
                    "quote_preview": quote[:120],
                },
            ))
    return violations


def run_symbolic_validation(
    relations: list[Relation],
    blocklist: list[str],
    confidence_threshold: float = 0.3,
    doc_texts: dict[str, str] | None = None,
    chunk_texts: dict[str, str] | None = None,
) -> list[Violation]:
    """Run all symbolic validation rules on a list of relations.

    Args:
        chunk_texts: Optional mapping of ``chunk_id → chunk text``.
            When available, quote-verbatim checks run against the
            chunk text (tighter scope) instead of the full document.

    Returns:
        Flat list of all violations found.
    """
    violations: list[Violation] = []

    for relation in relations:
        violations.extend(check_has_agent_and_theme(relation))
        violations.extend(check_source_non_empty(relation))
        violations.extend(check_confidence_threshold(relation, confidence_threshold))
        violations.extend(check_no_duplicate_entities_in_relation(relation))

        # Quote verbatim check — prefer chunk text, fall back to doc text
        chunk_id = relation.source.chunk_id
        c_text = chunk_texts.get(chunk_id) if chunk_texts and chunk_id else None
        doc_text = doc_texts.get(relation.source.document_id, "") if doc_texts else ""
        if c_text or doc_text:
            violations.extend(
                check_quotes_are_verbatim(relation, doc_text, chunk_text=c_text)
            )

        for entity in relation.roles.all_entities():
            violations.extend(check_no_generic_entity_labels(entity, blocklist))

    if violations:
        errors = sum(1 for v in violations if v.severity == "error")
        warnings = sum(1 for v in violations if v.severity == "warning")
        logger.info("Symbolic validation: %d errors, %d warnings.", errors, warnings)

    return violations
