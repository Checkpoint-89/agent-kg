"""Prompt templates for extraction, role filling, sub-clustering, and synthesis.

All templates are language-configurable via ``DomainConfig``.
They use Python string formatting (``{variable}``) for injection.
"""

from __future__ import annotations


# =====================================================================
# Relation extraction prompt (decomposed — no roles)
# =====================================================================

RELATION_EXTRACTION_SYSTEM = """\
You are an expert knowledge extraction agent for the **{domain_name}** domain.

## Objective
Extract **relations** from the provided document.
- A **relation** is an atomic interaction, event, state change, or assertion relevant to the domain.
- At this stage, extract only the relation itself — **do NOT assign entities or roles**.
  Roles are assigned in a later step.

## Domain context
{domain_context}

## Classification axes
Classify each relation along exactly one axis:
1. **ONTOLOGICAL** (static) — stable, inherent, or durable properties.
   Criterion: the relation holds regardless of a specific moment in time.
2. **DYNAMIC** (change) — actions, processes, transitions, or events with a beginning and end.
3. **STRUCTURAL** (organisation) — organisational, hierarchical, or dependency links.

## Constraints
- Descriptions must be self-contained — understandable without the source document.
- Verb phrases must be in infinitive form, without subject or object.
- Each source quote must be an **exact, verbatim copy-paste** from the source
  document — do NOT paraphrase, truncate, or summarise. Include at least one full
  sentence (minimum ~40 characters). If the relation is evidenced in multiple
  passages, provide multiple quotes.

{ontology_section}

{graph_context}
"""

RELATION_EXTRACTION_USER = """\
Extract all relevant relations (without roles) from the following document.

## Document
{document_text}
"""


# =====================================================================
# Role extraction prompt (per relation)
# =====================================================================

ROLE_EXTRACTION_SYSTEM = """\
You are an expert entity and role extractor for the **{domain_name}** domain.

## Objective
Given a relation description and its source document, assign entities to
the appropriate semantic roles.

## Domain context
{domain_context}

## Semantic roles (Frame Semantics)
{role_descriptions}

## Constraints
- Entity labels must be domain-specific, NOT generic role names.
  Forbidden labels: {blocklist}
- Entity names must match the source text exactly.
- Definitions must be domain-independent and self-contained.
- When you encounter an entity whose type is NOT in the known entity types
  list below, add it to `candidate_entity_types` so it can be reviewed.

{entity_type_section}

{graph_context}
"""

ROLE_EXTRACTION_USER = """\
Assign entities to semantic roles for the following relation.

## Relation
- **Type**: {relation_type}
- **Definition**: {relation_definition}
- **Description**: {relation_description}
- **Quotes**: {quote}

## Source document
{document_text}
"""


# =====================================================================
# Helper: format role descriptions from DomainConfig
# =====================================================================

def format_role_descriptions(roles: dict) -> str:
    """Format role configs into a prompt section."""
    lines = []
    for name, cfg in roles.items():
        line = f"- **{name}**: {cfg.description} (Question: {cfg.question})"
        if getattr(cfg, "examples_include", None):
            examples = "; ".join(cfg.examples_include[:3])
            line += f" (Include: {examples})"
        if getattr(cfg, "examples_exclude", None):
            examples = "; ".join(cfg.examples_exclude[:3])
            line += f" (Exclude: {examples})"
        lines.append(line)
    return "\n".join(lines)


def format_seed_ontology_section(seed) -> str:
    """Format seed ontology (if any) into a prompt section."""
    if seed is None:
        return ""

    lines = ["## Seed ontology (anchor types — align to these when possible)"]

    if seed.entity_types:
        lines.append("\n### Seed entity types:")
        for t in seed.entity_types:
            lines.append(f"- **{t.label}**: {t.definition}")

    if seed.relation_types:
        lines.append("\n### Seed relation types:")
        for t in seed.relation_types:
            lines.append(f"- **{t.label}**: {t.definition}")

    lines.append(
        f"\nAlignment threshold: {seed.alignment_threshold:.0%} cosine similarity. "
        "If a discovered type exceeds this threshold against a seed type, prefer alignment."
    )
    return "\n".join(lines)
