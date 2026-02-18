"""QC Agent — Agent Framework agent for extraction quality control.

Reviews the original document against extracted relations and flags:
1. Text spans likely containing unextracted relations.
2. Relations with suspiciously empty optional roles.

Usage::

    agent, session = create_qc_agent(config, document_text, relations)
    await agent.run(messages=[{"role": "user", "content": context}])
    # session.flags now contains QC issues
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent_framework import Agent, tool
from agent_framework.openai import OpenAIChatClient

from agent_kg.config import DomainConfig
from agent_kg.models.base import Relation

logger = logging.getLogger(__name__)


# ── Session models ──────────────────────────────────────────────────

@dataclass
class QCFlag:
    """A single QC issue."""

    kind: str  # "missing_relation" | "incomplete_roles"
    description: str
    text_span: str = ""
    relation_description: str = ""
    suggested_roles: list[str] = field(default_factory=list)


@dataclass
class QCSession:
    """Accumulates QC flags from the agent."""

    flags: list[QCFlag] = field(default_factory=list)
    coverage_score: float = 1.0


# ── Factory ─────────────────────────────────────────────────────────

def create_qc_agent(
    config: DomainConfig,
    document_text: str,
    relations: list[Relation],
) -> tuple[Agent, QCSession]:
    """Create a QC agent to review extraction completeness.

    Returns:
        ``(agent, session)`` — call ``await agent.run(...)`` then read
        ``session.flags``.
    """
    session = QCSession()

    # ── Tools ───────────────────────────────────────────────────

    @tool
    def flag_missing_relation(
        text_span: str,
        description: str,
    ) -> str:
        """Flag a text span that likely contains an unextracted relation.

        ``text_span``: the relevant excerpt from the document.
        ``description``: what relation you believe is missing.
        """
        session.flags.append(QCFlag(
            kind="missing_relation",
            description=description,
            text_span=text_span,
        ))
        return f"Flagged missing relation: {description[:60]}"

    @tool
    def flag_incomplete_roles(
        relation_description: str,
        missing_roles: str,
        reasoning: str,
    ) -> str:
        """Flag a relation with suspiciously empty optional roles.

        ``relation_description``: which relation is incomplete.
        ``missing_roles``: comma-separated role names that seem missing.
        ``reasoning``: why you believe these roles should be filled.
        """
        session.flags.append(QCFlag(
            kind="incomplete_roles",
            description=reasoning,
            relation_description=relation_description,
            suggested_roles=[r.strip() for r in missing_roles.split(",") if r.strip()],
        ))
        return f"Flagged incomplete roles for: {relation_description[:60]}"

    @tool
    def mark_review_complete(coverage_score: str) -> str:
        """Signal that QC review is complete.

        ``coverage_score``: estimated coverage of the document's relational
        content (0.0 to 1.0 as a string, e.g. '0.85').
        """
        try:
            session.coverage_score = max(0.0, min(1.0, float(coverage_score)))
        except ValueError:
            session.coverage_score = 0.5
        return f"QC complete. Coverage: {session.coverage_score:.0%}"

    # ── Agent ───────────────────────────────────────────────────

    agent = Agent(
        client=OpenAIChatClient(model=config.validation_model),
        name="QualityControl",
        instructions=(
            f"You are a quality control reviewer for knowledge extraction "
            f"in the **{config.domain_name}** domain.\n\n"
            "## Task\n"
            "Compare the original document against the extracted relations.\n"
            "Identify:\n"
            "1. **Missing relations** — text spans describing interactions, events, "
            "or states that were not extracted.\n"
            "2. **Incomplete roles** — extracted relations where optional semantic "
            "roles (instrument, purpose, context, etc.) seem suspiciously absent "
            "given the document.\n\n"
            "## Process\n"
            "1. Read the document carefully.\n"
            "2. For each text span with a potential unextracted relation, "
            "call `flag_missing_relation`.\n"
            "3. For each relation with likely missing roles, "
            "call `flag_incomplete_roles`.\n"
            "4. When done, call `mark_review_complete` with your coverage estimate.\n\n"
            "Be precise — only flag genuine gaps, not stylistic preferences.\n"
        ),
        tools=[flag_missing_relation, flag_incomplete_roles, mark_review_complete],
    )

    return agent, session


# ── Helper: format context for the QC agent ─────────────────────────

def format_qc_context(document_text: str, relations: list[Relation]) -> str:
    """Build the user message for the QC agent."""
    relations_block = "\n".join(
        f"  {i + 1}. [{r.relation_type.label}] {r.description}\n"
        f"     Agents: {', '.join(a.name for a in r.roles.agents)}\n"
        f"     Themes: {', '.join(t.name for t in r.roles.themes)}"
        for i, r in enumerate(relations)
    )
    return (
        f"## Original document\n{document_text}\n\n"
        f"## Extracted relations ({len(relations)} total)\n{relations_block}"
    )
