"""Validator — Agent Framework agent for neurosymbolic validation.

Two layers:
1. **Symbolic** (deterministic): runs *outside* the agent via
   ``validation.rules.run_symbolic_validation``.
2. **Neural** (agentic): an ``agent_framework.Agent`` that *reasons*
   about violations and records resolutions through a ``@tool``.

The pipeline runs symbolic checks first, then — only if errors are
found — creates a validator agent on demand to resolve them.

Usage::

    violations = run_symbolic_validation(relations, blocklist)
    if any(v.severity == "error" for v in violations):
        agent, session = create_validator(config, violations)
        await agent.run(messages=[{"role": "user", "content": "..."}])
        # session.resolutions now contains the agent's decisions
"""

from __future__ import annotations

from typing import Literal

import logging
from dataclasses import dataclass, field

from agent_framework import Agent, tool
from agent_framework.openai import OpenAIChatClient

from agent_kg.config import DomainConfig
from agent_kg.validation.rules import Violation

logger = logging.getLogger(__name__)


# ── Resolution model ────────────────────────────────────────────────

@dataclass
class Resolution:
    """An agent-decided resolution for one violation."""

    violation_rule: str
    action: Literal["correct", "escalate", "override"]
    reasoning: str
    correction: str | None = None


# ── Session ─────────────────────────────────────────────────────────

@dataclass
class ValidatorSession:
    """Accumulates resolutions produced by the validator agent."""

    violations: list[Violation] = field(default_factory=list)
    resolutions: list[Resolution] = field(default_factory=list)


# ── Factory ─────────────────────────────────────────────────────────

def create_validator(
    config: DomainConfig,
    violations: list[Violation],
) -> tuple[Agent, ValidatorSession]:
    """Create a validator agent pre-loaded with violations to resolve.

    Returns:
        ``(agent, session)`` — call ``await agent.run(...)`` then read
        ``session.resolutions``.
    """
    session = ValidatorSession(violations=list(violations))

    # ── Tool (closure captures session) ─────────────────────────

    @tool
    def record_resolution(
        violation_rule: str,
        action: str,
        reasoning: str,
        correction: str = "",
    ) -> str:
        """Record your resolution for a constraint violation.

        ``action`` must be one of: correct, escalate, override.
        ``correction``: the fixed value (only when action=correct).
        """
        session.resolutions.append(Resolution(
            violation_rule=violation_rule,
            action=action,
            reasoning=reasoning,
            correction=correction or None,
        ))
        return f"Recorded: {action} for '{violation_rule}'."

    # ── Agent ───────────────────────────────────────────────────

    agent = Agent(
        client=OpenAIChatClient(model=config.validation_model),
        name="Validator",
        instructions=(
            f"You are a knowledge-graph quality validator for the "
            f"**{config.domain_name}** domain.\n\n"
            "You receive a list of constraint violations found by the "
            "symbolic validation layer.  For **each** violation, reason "
            "about the best resolution and call `record_resolution`:\n\n"
            "- **correct** — you can fix the issue (provide the fix in `correction`).\n"
            "- **escalate** — the issue requires human review.\n"
            "- **override** — the violation is acceptable in context.\n\n"
            "Be concise in reasoning.  Process every violation.\n"
        ),
        tools=[record_resolution],
    )

    return agent, session


# ── Helper: format violations for the agent prompt ──────────────────

def format_violations(violations: list[Violation]) -> str:
    """Turn a list of ``Violation`` into a prompt-ready text block."""
    return "\n".join(
        f"- [{v.severity}] **{v.rule_name}**: {v.message}"
        for v in violations
    )
