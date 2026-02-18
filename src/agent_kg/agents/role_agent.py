"""Role extraction — structured output via instructor (per relation).

For each ``RawRelation``, fills the semantic role slots (agent, theme,
circumstances, etc.) to produce a full ``Relation``.

This step sees the original document text **and** the relation
description and assigns entities to roles.  The prompt prefix
(system + ontology + document) is cached by OpenAI, so the
incremental cost per relation is low.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import instructor
from openai import OpenAI

from agent_kg.agents.prompts import (
    ROLE_EXTRACTION_SYSTEM,
    ROLE_EXTRACTION_USER,
    format_role_descriptions,
)
from agent_kg.config import DomainConfig
from agent_kg.executors.context import GraphContext
from agent_kg.models.base import RawRelation, Relation, Roles

if TYPE_CHECKING:
    from agent_kg.models.ontology import OntologySchema

logger = logging.getLogger(__name__)

# Maximum ontology types injected into prompts to bound context size.
_MAX_ONTOLOGY_TYPES = 50


def extract_roles(
    raw_relation: RawRelation,
    document_text: str,
    client: OpenAI,
    config: DomainConfig,
    *,
    ontology: OntologySchema | None = None,
    graph_context: GraphContext | None = None,
) -> Relation | None:
    """Fill semantic roles for a single raw relation.

    Args:
        raw_relation: A typed relation without roles.
        document_text: Full source document text (for context).
        client: An ``openai.OpenAI`` client instance.
        config: Domain configuration.
        ontology: Optional ontology (guides entity type labels).
        graph_context: Optional known entities/relations from the graph.

    Returns:
        A full ``Relation`` with roles, or ``None`` if extraction fails.
    """
    instr_client = instructor.from_openai(client, mode=instructor.Mode.TOOLS_STRICT)

    # Build entity type section (capped)
    entity_type_section = ""
    if ontology and ontology.entity_types:
        capped = ontology.entity_types[:_MAX_ONTOLOGY_TYPES]
        lines = ["## Known entity types (prefer these labels when appropriate)"]
        for t in capped:
            lines.append(f"- **{t.label}**: {t.definition}")
        if len(ontology.entity_types) > _MAX_ONTOLOGY_TYPES:
            lines.append(f"  … and {len(ontology.entity_types) - _MAX_ONTOLOGY_TYPES} more.")
        entity_type_section = "\n".join(lines)

    # Build graph context section
    context_section = ""
    if graph_context and not graph_context.is_empty():
        context_section = graph_context.to_prompt_section()

    system_prompt = ROLE_EXTRACTION_SYSTEM.format(
        domain_name=config.domain_name,
        domain_context=config.domain_context,
        role_descriptions=format_role_descriptions(config.roles),
        blocklist=", ".join(config.generic_entity_blocklist[:15]),
        entity_type_section=entity_type_section,
        graph_context=context_section,
    )

    rel_type_label = raw_relation.relation_type.label or raw_relation.relation_type.verb
    user_prompt = ROLE_EXTRACTION_USER.format(
        relation_description=raw_relation.description,
        relation_type=rel_type_label,
        relation_definition=raw_relation.relation_type.definition,
        quote=raw_relation.provenance.quote,
        document_text=document_text,
    )

    try:
        roles = instr_client.chat.completions.create(
            model=config.extraction_model,
            response_model=Roles,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_retries=3,
        )
    except Exception as e:
        logger.warning(
            "Role extraction failed for relation '%s': %s",
            rel_type_label, e,
        )
        return None

    # Validate entity labels against blocklist
    try:
        for entity in roles.all_entities():
            entity.check_not_generic(config.generic_entity_blocklist)
    except ValueError as e:
        logger.warning("Skipping relation due to entity validation: %s", e)
        return None

    relation = raw_relation.with_roles(roles)

    logger.debug(
        "Roles filled for relation '%s': %d entities.",
        rel_type_label,
        len(roles.all_entities()),
    )
    return relation
