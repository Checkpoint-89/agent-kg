"""Type Arbiter — Agent Framework agent for ontology governance.

Reviews candidate types (both relation and entity types) discovered
during extraction and decides whether to **accept**, **merge** with
an existing ontology type, or **reject** each candidate.

Replaces the Ontology Negotiator for incremental type governance.
For the first run with many candidates, the pipeline may pre-cluster
them by embedding similarity before presenting to the Arbiter.

Usage::

    agent, session = create_arbiter(config, candidates, current_ontology)
    await agent.run(messages=[{"role": "user", "content": candidates_text}])
    new_ontology = apply_arbiter_decisions(session.decisions, current_ontology)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent_framework import Agent, tool
from agent_framework.openai import OpenAIChatClient

from agent_kg.agents.prompts import format_seed_ontology_section
from agent_kg.config import DomainConfig
from agent_kg.models.base import CandidateType
from agent_kg.models.ontology import OntologySchema, OntologyType

logger = logging.getLogger(__name__)


# ── Session models ──────────────────────────────────────────────────

@dataclass
class ArbiterDecision:
    """A single Arbiter decision about a candidate type."""

    action: str  # "accept" | "merge" | "reject"
    kind: str  # "relation" | "entity"
    label: str
    definition: str = ""
    reasoning: str = ""
    merge_target: str = ""  # existing label to merge into


@dataclass
class ArbiterSession:
    """Accumulates decisions produced by the Arbiter agent."""

    decisions: list[ArbiterDecision] = field(default_factory=list)


# ── Factory ─────────────────────────────────────────────────────────

def create_arbiter(
    config: DomainConfig,
    candidates: list[CandidateType],
    current_ontology: OntologySchema | None = None,
) -> tuple[Agent, ArbiterSession]:
    """Create a Type Arbiter agent.

    Returns:
        ``(agent, session)`` — call ``await agent.run(...)`` then read
        ``session.decisions``.
    """
    session = ArbiterSession()

    # ── Tools ───────────────────────────────────────────────────

    @tool
    def accept_type(
        kind: str,
        label: str,
        definition: str,
        reasoning: str,
    ) -> str:
        """Accept a candidate type into the ontology.

        ``kind``: 'relation' or 'entity'.
        ``label``: canonical type label.
        ``definition``: final definition for the type.
        ``reasoning``: why this type should be accepted.
        """
        session.decisions.append(ArbiterDecision(
            action="accept",
            kind=kind,
            label=label,
            definition=definition,
            reasoning=reasoning,
        ))
        return f"Accepted {kind} type: {label}"

    @tool
    def merge_with_existing(
        kind: str,
        candidate_label: str,
        existing_label: str,
        reasoning: str,
    ) -> str:
        """Merge a candidate type with an existing ontology type.

        ``kind``: 'relation' or 'entity'.
        ``candidate_label``: the candidate being merged.
        ``existing_label``: the existing type to merge into.
        ``reasoning``: why these types are equivalent.
        """
        session.decisions.append(ArbiterDecision(
            action="merge",
            kind=kind,
            label=candidate_label,
            merge_target=existing_label,
            reasoning=reasoning,
        ))
        return f"Merged {kind} '{candidate_label}' → '{existing_label}'"

    @tool
    def reject_type(
        kind: str,
        label: str,
        reasoning: str,
    ) -> str:
        """Reject a candidate type (too vague, duplicate, or noise).

        ``kind``: 'relation' or 'entity'.
        ``label``: the candidate being rejected.
        ``reasoning``: why this type should be rejected.
        """
        session.decisions.append(ArbiterDecision(
            action="reject",
            kind=kind,
            label=label,
            reasoning=reasoning,
        ))
        return f"Rejected {kind} type: {label}"

    # ── Build ontology context for instructions ─────────────────

    seed_info = format_seed_ontology_section(config.seed_ontology)

    current_types_text = ""
    if current_ontology:
        rel_lines = [
            f"  - {t.label}: {t.definition}"
            for t in current_ontology.relation_types
        ]
        ent_lines = [
            f"  - {t.label}: {t.definition}"
            for t in current_ontology.entity_types
        ]
        current_types_text = (
            "## Current ontology\n"
            "### Relation types:\n" + ("\n".join(rel_lines) or "  (none)") + "\n"
            "### Entity types:\n" + ("\n".join(ent_lines) or "  (none)")
        )

    # ── Agent ───────────────────────────────────────────────────

    agent = Agent(
        client=OpenAIChatClient(model=config.reasoning_model),
        name="TypeArbiter",
        instructions=(
            f"You are a type arbiter for the **{config.domain_name}** domain "
            f"knowledge-graph ontology.\n\n"
            "## Task\n"
            "Review each candidate type proposed during extraction and decide:\n"
            "- **accept** — genuinely new and well-defined type.\n"
            "- **merge** — semantically equivalent to an existing ontology type.\n"
            "- **reject** — too vague, redundant, or noisy.\n\n"
            "## Decision criteria\n"
            "- Prefer fewer, well-defined types over many overlapping ones.\n"
            "- Each type must have a clear, non-overlapping definition.\n"
            "- If a candidate closely matches a seed or existing type, merge it.\n"
            "- Do not over-generalise — preserve domain nuance.\n"
            "- When accepting, refine the definition if needed.\n\n"
            "## Process\n"
            "For **every** candidate in the list, call exactly one of:\n"
            "`accept_type`, `merge_with_existing`, or `reject_type`.\n\n"
            f"{seed_info}\n\n{current_types_text}"
        ),
        tools=[accept_type, merge_with_existing, reject_type],
    )

    return agent, session


# ── Format helpers ──────────────────────────────────────────────────

def format_candidates(candidates: list[CandidateType]) -> str:
    """Format candidate types for the Arbiter's user message."""
    rel_candidates = [c for c in candidates if c.kind == "relation"]
    ent_candidates = [c for c in candidates if c.kind == "entity"]

    parts: list[str] = []
    if rel_candidates:
        parts.append("## Candidate relation types")
        for c in rel_candidates:
            line = f"- **{c.label}**: {c.definition}"
            if c.source_description:
                line += f"\n  Source: {c.source_description[:120]}"
            parts.append(line)

    if ent_candidates:
        parts.append("\n## Candidate entity types")
        for c in ent_candidates:
            line = f"- **{c.label}**: {c.definition}"
            if c.source_description:
                line += f"\n  Source: {c.source_description[:120]}"
            parts.append(line)

    return "\n".join(parts)


# ── Decision application (pure function, no LLM) ───────────────────

def apply_arbiter_decisions(
    decisions: list[ArbiterDecision],
    current_ontology: OntologySchema | None = None,
) -> OntologySchema:
    """Build a new ``OntologySchema`` by applying Arbiter decisions.

    Handles both entity and relation types via ``ArbiterDecision.kind``.
    """
    existing_rel: dict[str, OntologyType] = {}
    existing_ent: dict[str, OntologyType] = {}

    if current_ontology:
        existing_rel = {t.label: t for t in current_ontology.relation_types}
        existing_ent = {t.label: t for t in current_ontology.entity_types}

    for d in decisions:
        target = existing_rel if d.kind == "relation" else existing_ent

        if d.action == "accept":
            target[d.label] = OntologyType(
                label=d.label,
                definition=d.definition or d.reasoning,
                is_seed=False,
            )
        elif d.action == "merge":
            # The candidate is absorbed into the existing type; remove candidate
            target.pop(d.label, None)
            # merge_target already exists in the ontology — no change needed
        elif d.action == "reject":
            target.pop(d.label, None)

    new_version = (current_ontology.version + 1) if current_ontology else 1
    parent = current_ontology.version if current_ontology else None

    return OntologySchema(
        version=new_version,
        parent_version=parent,
        entity_types=list(existing_ent.values()),
        relation_types=list(existing_rel.values()),
        documents_since_last_negotiation=0,
    )
