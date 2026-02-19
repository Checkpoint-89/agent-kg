"""Relation extraction — structured output via instructor (no roles).

A plain function (not an Agent) because extraction is a deterministic
structured-output call, not a reasoning loop.  Returns ``RawRelation``
objects — relations typed but **without** semantic roles.

The decomposition purposefully separates *what happened* (this step)
from *who/what was involved* (the Role Agent step), giving each LLM
call a simpler schema and allowing prompt caching of the document
prefix.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import instructor
from openai import OpenAI

from agent_kg.agents.prompts import (
    RELATION_EXTRACTION_SYSTEM,
    RELATION_EXTRACTION_USER,
)
from agent_kg.config import DomainConfig
from agent_kg.executors.context import GraphContext
from agent_kg.models.base import DocumentRawRelations, RawRelation

if TYPE_CHECKING:
    from agent_kg.models.ontology import OntologySchema

logger = logging.getLogger(__name__)

# Maximum ontology types injected into prompts to bound context size.
_MAX_ONTOLOGY_TYPES = 50


def _build_ontology_section_from_graph_context(
    graph_context: GraphContext,
    *,
    max_types: int,
) -> str:
    """Derive an ontology-like prompt section from retrieved graph context.

    Today, `GraphContext` provides:
    - `known_entities`: [{name, label, definition}, ...]
    - `related_relations`: [{generic, verb, description}, ...]

    We fold these into lightweight "known types" hints to bias extraction toward
    existing labels/verbs, without requiring an explicit `OntologySchema`.
    """
    if graph_context.is_empty():
        return ""

    lines: list[str] = []

    # Entity types (label → definition)
    label_to_definition: dict[str, str] = {}
    for ent in graph_context.known_entities:
        label = (ent.get("label") or "").strip()
        if not label or label in label_to_definition:
            continue
        definition = (ent.get("definition") or "").strip()
        label_to_definition[label] = definition

    if label_to_definition:
        lines.append("## Known entity types (from graph context)")
        for label, definition in list(label_to_definition.items())[:max_types]:
            if definition:
                lines.append(f"- **{label}**: {definition}")
            else:
                lines.append(f"- **{label}**")

    # Relation types (verb → example generic/description)
    verb_to_examples: dict[str, list[str]] = {}
    for rel in graph_context.related_relations:
        verb = (rel.get("verb") or "").strip()
        if not verb:
            continue
        example = (rel.get("generic") or rel.get("description") or "").strip()
        verb_to_examples.setdefault(verb, [])
        if example and len(verb_to_examples[verb]) < 2 and example not in verb_to_examples[verb]:
            verb_to_examples[verb].append(example)

    if verb_to_examples:
        if lines:
            lines.append("")
        lines.append("## Known relation verbs (from graph context)")
        for verb, examples in list(verb_to_examples.items())[:max_types]:
            if examples:
                preview = examples[0][:160]
                lines.append(f"- **{verb}**: e.g. {preview}")
            else:
                lines.append(f"- **{verb}**")

    return "\n".join(lines)


def extract_raw_relations(
    document_text: str,
    document_id: str,
    client: OpenAI,
    config: DomainConfig,
    *,
    graph_context: GraphContext | None = None,
    ontology: OntologySchema | None = None,
) -> list[RawRelation]:
    """Extract typed relations (without roles) from a document.

    Args:
        document_text: Pre-formatted document content.
        document_id: Unique document identifier (for source attribution).
        client: An ``openai.OpenAI`` client instance.
        config: Domain configuration.
        graph_context: Optional context from prior graph state.
        ontology: Optional ontology schema (constrains types).

    Returns:
        List of ``RawRelation`` objects ready for role extraction.
    """
    instr_client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)

    # Build context section
    context_section = ""
    if graph_context and not graph_context.is_empty():
        context_section = graph_context.to_prompt_section()

    # Build ontology section (prefer graph_context-derived ontology)
    ontology_section = ""
    if graph_context and not graph_context.is_empty():
        ontology_section = _build_ontology_section_from_graph_context(
            graph_context,
            max_types=_MAX_ONTOLOGY_TYPES,
        )

    # Fallback: explicit ontology schema
    if not ontology_section and ontology and ontology.relation_types:
        capped = ontology.relation_types[:_MAX_ONTOLOGY_TYPES]
        lines = ["## Known relation types (prefer these when appropriate)"]
        for t in capped:
            tag = " (seed)" if t.is_seed else ""
            lines.append(f"- **{t.label}**: {t.definition}{tag}")
        if len(ontology.relation_types) > _MAX_ONTOLOGY_TYPES:
            lines.append(f"  … and {len(ontology.relation_types) - _MAX_ONTOLOGY_TYPES} more.")
        ontology_section = "\n".join(lines)

    system_prompt = RELATION_EXTRACTION_SYSTEM.format(
        domain_name=config.domain_name,
        domain_context=config.domain_context,
        ontology_section=ontology_section,
        graph_context=context_section,
    )

    user_prompt = RELATION_EXTRACTION_USER.format(document_text=document_text)

    result = instr_client.chat.completions.create(
        model=config.extraction_model,
        response_model=DocumentRawRelations,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        # Pass source text into Pydantic validators so verbatim quote
        # constraints can trigger built-in instructor retries.
        validation_context={"document_text": document_text},
        max_retries=3,
    )

    # Post-process: inject document_id (we don't require the LLM to get this right)
    for raw in result.relations:
        raw.source.document_id = document_id

    logger.info(
        "Extracted %d raw relations from document %s (model=%s).",
        len(result.relations), document_id, config.extraction_model,
    )
    return result.relations
