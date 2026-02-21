"""Entity resolution — multi-stage instance-level deduplication.

Three stages, from cheap to expensive:

1. **Deterministic pre-grouping** — normalise surface forms (lowercase,
   strip punctuation, collapse whitespace) and group exact matches.
2. **Embedding-based candidate clustering** — embed each unique mention
   with instance-level context; cluster with agglomerative cosine
   distance at a tight threshold.
3. **LLM-assisted merge arbitration** — for each candidate cluster with
   >1 distinct surface form, ask the LLM to confirm/reject the merge
   and pick a canonical name.  Guards against false positives like
   "Apple Inc" vs "Apple (fruit)".

The resolver operates on ``list[Relation]`` **in place**: after
resolution every entity mention points to its canonical
``(name, label, definition)`` and carries ``aliases`` in metadata.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import instructor
import numpy as np

from agent_kg.executors.clustering import AgglomerativeStrategy
from agent_kg.models.base import (
    Entity,
    MergeDecision,
    Relation,
    ResolutionEntry,
    ResolutionReport,
)
from agent_kg.utils.embeddings import compute_embeddings

if TYPE_CHECKING:
    from openai import OpenAI

    from agent_kg.config import DomainConfig

logger = logging.getLogger(__name__)


# =====================================================================
# Mention — lightweight wrapper for tracking entity occurrences
# =====================================================================

@dataclass
class _Mention:
    """An entity mention enriched with its relational context."""

    entity: Entity
    relation_index: int  # index into the relations list
    role: str  # e.g. "agent", "theme", "context"

    # Normalised key for Stage 1 grouping
    norm_key: str = ""

    # Instance-level embedding text (built from name + label + definition + relation context)
    embed_text: str = ""

    # True if this mention represents a known entity from the graph (phantom mention)
    is_known: bool = False


# =====================================================================
# Stage 1 — deterministic normalisation
# =====================================================================

_STRIP_RE = re.compile(r"[^a-z0-9\s]")


def _normalise(name: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    import unicodedata

    s = unicodedata.normalize("NFD", name)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = _STRIP_RE.sub("", s.lower())
    return " ".join(s.split())


def _build_mentions(relations: list[Relation]) -> list[_Mention]:
    """Flatten all entity mentions from all relations."""
    mentions: list[_Mention] = []
    for ri, rel in enumerate(relations):
        role_lists = [
            ("agent", rel.roles.agents),
            ("theme", rel.roles.themes),
        ]
        for ent in rel.roles.circumstances:
            role_lists.append((ent.role, [ent]))  # type: ignore[attr-defined]
        for ent in rel.roles.context:
            role_lists.append(("context", [ent]))
        for ent in rel.roles.origin_destinations:
            role_lists.append((ent.role, [ent]))  # type: ignore[attr-defined]
        for ent in rel.roles.time_locations:
            role_lists.append((ent.role, [ent]))  # type: ignore[attr-defined]

        for role_name, entities in role_lists:
            for ent in entities:
                m = _Mention(
                    entity=ent,
                    relation_index=ri,
                    role=role_name,
                )
                m.norm_key = f"{_normalise(ent.label)}||{_normalise(ent.name)}"
                m.embed_text = (
                    f"{ent.name} | {ent.label} | {ent.definition}"
                )
                mentions.append(m)
    return mentions


def _group_by_norm_key(mentions: list[_Mention]) -> dict[str, list[_Mention]]:
    """Stage 1: group mentions that normalise to the same key."""
    groups: dict[str, list[_Mention]] = defaultdict(list)
    for m in mentions:
        groups[m.norm_key].append(m)
    return dict(groups)


# =====================================================================
# Stage 2 — embedding-based candidate clustering
# =====================================================================

def _cluster_groups(
    groups: dict[str, list[_Mention]],
    client: OpenAI,
    config: DomainConfig,
) -> list[list[_Mention]]:
    """Cluster the Stage-1 groups by embedding similarity.

    Groups that are already singletons (one unique surface form)
    pass through unchanged.  Multi-form clusters become merge
    candidates for Stage 3.
    """
    group_keys = list(groups.keys())
    if len(group_keys) <= 1:
        return [ms for ms in groups.values()]

    # Pick one representative text per Stage-1 group (first mention)
    representative_texts = [groups[k][0].embed_text for k in group_keys]

    embeddings = compute_embeddings(
        representative_texts, client, config.embedding_model,
    )

    strategy = AgglomerativeStrategy(
        distance_threshold=config.entity_resolution_similarity_threshold,
    )
    cluster_map = strategy.fit(embeddings)

    # Merge Stage-1 groups that land in the same Stage-2 cluster
    merged: list[list[_Mention]] = []
    for _, indices in cluster_map.items():
        combined: list[_Mention] = []
        for idx in indices:
            combined.extend(groups[group_keys[idx]])
        merged.append(combined)

    return merged


# =====================================================================
# Stage 3 — LLM-assisted merge arbitration
# =====================================================================

_ARBITRATION_PROMPT = """\
You are an entity resolution expert.  Given a cluster of entity mentions
extracted from documents, decide whether they all refer to the **same
real-world entity**.

Consider:
- Abbreviations, acronyms, and name variants often refer to the same entity.
- Homonyms (same name, different domain) must NOT be merged.
- Use the provided relation context to disambiguate.

Cluster mentions:
{mentions_block}
"""


def _needs_arbitration(mentions: list[_Mention]) -> bool:
    """True if the cluster contains >1 distinct (label, name) pair."""
    keys = {(m.entity.label, m.entity.name) for m in mentions}
    return len(keys) > 1


def _format_mentions_block(mentions: list[_Mention], relations: list[Relation]) -> str:
    """Format a cluster's mentions for the LLM prompt."""
    lines: list[str] = []
    seen: set[str] = set()
    for m in mentions:
        key = f"{m.entity.label}|{m.entity.name}"
        if key in seen:
            continue
        seen.add(key)
        rel = relations[m.relation_index]
        lines.append(
            f"- Name: {m.entity.name!r}, Label: {m.entity.label!r}, "
            f"Definition: {m.entity.definition!r}, "
            f"Role: {m.role}, "
            f"Relation context: {rel.description!r}"
        )
    return "\n".join(lines)


def _arbitrate_cluster(
    mentions: list[_Mention],
    relations: list[Relation],
    client: OpenAI,
    model: str,
) -> MergeDecision:
    """Ask the LLM whether this cluster should be merged."""
    instructor_client = instructor.from_openai(client)
    block = _format_mentions_block(mentions, relations)
    resp: MergeDecision = instructor_client.chat.completions.create(
        model=model,
        response_model=MergeDecision,
        messages=[
            {"role": "system", "content": _ARBITRATION_PROMPT.format(mentions_block=block)},
            {"role": "user", "content": "Should these mentions be merged? Return your decision."},
        ],
    )
    return resp


# =====================================================================
# Apply merges — mutate entities in-place
# =====================================================================

def _pick_canonical(mentions: list[_Mention]) -> tuple[str, str, str, bool]:
    """Pick canonical entity from the cluster.

    Priority: known entities (from graph) win unconditionally.
    Among equals, highest confidence wins.

    Returns:
        (name, label, definition, from_graph) — *from_graph* is True
        when the chosen canonical originates from a known graph entity.
    """
    known = [m for m in mentions if m.is_known]
    if known:
        best = known[0]  # known entities are already canonical
    else:
        best = max(mentions, key=lambda m: m.entity.confidence)
    return best.entity.name, best.entity.label, best.entity.definition, best.is_known


def _apply_merge(
    mentions: list[_Mention],
    canonical_name: str,
    canonical_label: str,
    canonical_definition: str,
) -> list[str]:
    """Rewrite all mentions in the cluster to point to the canonical entity.

    Returns the list of alias names (original names that differ from canonical).
    """
    aliases: list[str] = []
    for m in mentions:
        original_name = m.entity.name
        if original_name != canonical_name:
            aliases.append(original_name)

        # Skip mutation of phantom (known) entities — they are anchors, not targets.
        if m.is_known:
            continue

        # Mutate the entity object in-place (it's the same object
        # referenced by the Relation in the relations list).
        m.entity.name = canonical_name
        m.entity.label = canonical_label
        m.entity.definition = canonical_definition

        # Store aliases in metadata
        if aliases:
            if m.entity.metadata is None:
                m.entity.metadata = {}
            m.entity.metadata["aliases"] = list(set(aliases))

        # Recompute derived fields
        m.entity.to_embed = f"Entity class: {canonical_label}. Definition: {canonical_definition}"
        if m.entity.entity_type is not None:
            m.entity.entity_type.label = canonical_label
            m.entity.entity_type.definition = canonical_definition
            m.entity.entity_type.to_embed = (
                f"Entity type: {canonical_label}. Definition: {canonical_definition}"
            )

    return list(set(aliases))


# =====================================================================
# Public API
# =====================================================================

def _build_known_mentions(known_entities: list[dict[str, str]]) -> list[_Mention]:
    """Build phantom _Mention objects from known graph entities.

    These don't belong to any relation (relation_index=-1) and are
    never mutated.  They exist solely to attract new mentions into
    existing canonical groups during clustering.
    """
    phantoms: list[_Mention] = []
    for ent_dict in known_entities:
        name = ent_dict.get("name", "")
        label = ent_dict.get("label", "")
        definition = ent_dict.get("definition", "")
        if not name or not label:
            continue

        # Build a lightweight Entity (not attached to any relation)
        entity = Entity(
            label=label,
            name=name,
            definition=definition or f"{label} entity.",
            confidence=1.0,  # known entities have full confidence
        )
        m = _Mention(
            entity=entity,
            relation_index=-1,
            role="known",
            is_known=True,
        )
        m.norm_key = f"{_normalise(entity.label)}||{_normalise(entity.name)}"
        m.embed_text = f"{entity.name} | {entity.label} | {entity.definition}"
        phantoms.append(m)
    return phantoms


def resolve_entities(
    relations: list[Relation],
    client: OpenAI,
    config: DomainConfig,
    known_entities: list[dict[str, str]] | None = None,
) -> tuple[list[Relation], ResolutionReport]:
    """Run multi-stage entity resolution on extracted relations.

    Mutates entities **in place** within the provided relations so
    that downstream graph building sees canonical names.

    Args:
        relations: Validated relations (post-validation, pre-export).
        client: OpenAI client for embeddings and LLM calls.
        config: Domain configuration with resolution parameters.
        known_entities: Existing canonical entities from the graph
            (list of ``{name, label, definition}`` dicts).  When
            provided, new mentions are resolved against these first,
            ensuring cross-batch consistency.

    Returns:
        Tuple of ``(relations, report)`` — same list (mutated) + stats.
    """
    if not relations:
        return relations, ResolutionReport()

    # ── Collect all mentions ────────────────────────────────────────
    mentions = _build_mentions(relations)

    # Inject phantom mentions from known graph entities
    if known_entities:
        phantoms = _build_known_mentions(known_entities)
        mentions.extend(phantoms)
        logger.info(
            "Injected %d known entities as resolution anchors.",
            len(phantoms),
        )

    unique_before = len({(m.entity.label, m.entity.name) for m in mentions if not m.is_known})

    logger.info(
        "Entity resolution: %d mentions, %d unique (label, name) pairs.",
        len([m for m in mentions if not m.is_known]), unique_before,
    )

    # ── Stage 1: deterministic grouping ─────────────────────────────
    norm_groups = _group_by_norm_key(mentions)
    logger.info("Stage 1 (normalisation): %d → %d groups.", unique_before, len(norm_groups))

    # ── Stage 2: embedding-based clustering ─────────────────────────
    candidate_clusters = _cluster_groups(norm_groups, client, config)
    logger.info("Stage 2 (embeddings): %d candidate clusters.", len(candidate_clusters))

    # ── Stage 3: LLM arbitration + apply ────────────────────────────
    report_entries: list[ResolutionEntry] = []

    for cluster in candidate_clusters:
        if not _needs_arbitration(cluster):
            # Single surface form → already resolved by Stage 1
            canonical_name, canonical_label, canonical_def, from_graph = _pick_canonical(cluster)
            report_entries.append(ResolutionEntry(
                canonical_name=canonical_name,
                canonical_label=canonical_label,
                aliases=[],
                mention_count=len(cluster),
                method="exact",
                canonical_source="graph" if from_graph else "batch",
            ))
            continue

        if config.entity_resolution_llm_arbitration:
            decision = _arbitrate_cluster(
                cluster, relations, client, config.extraction_model,
            )
            if decision.should_merge:
                aliases = _apply_merge(
                    cluster,
                    decision.canonical_name,
                    decision.canonical_label,
                    decision.canonical_definition,
                )
                from_graph = any(
                    m.is_known and m.entity.name == decision.canonical_name
                    for m in cluster
                )
                report_entries.append(ResolutionEntry(
                    canonical_name=decision.canonical_name,
                    canonical_label=decision.canonical_label,
                    aliases=aliases,
                    mention_count=len(cluster),
                    method="llm",
                    canonical_source="graph" if from_graph else "batch",
                ))
                logger.info(
                    "LLM merge: %r ← %s (%s)",
                    decision.canonical_name,
                    aliases,
                    decision.reasoning[:80],
                )
            else:
                # LLM says don't merge — split back into sub-groups
                sub_groups = _group_by_norm_key(cluster)
                for _key, sub in sub_groups.items():
                    cn, cl, cd, fg = _pick_canonical(sub)
                    report_entries.append(ResolutionEntry(
                        canonical_name=cn,
                        canonical_label=cl,
                        aliases=[],
                        mention_count=len(sub),
                        method="llm_rejected",
                        canonical_source="graph" if fg else "batch",
                    ))
                logger.info(
                    "LLM rejected merge for cluster with %d forms: %s",
                    len(sub_groups),
                    decision.reasoning[:80],
                )
        else:
            # No LLM — merge by embedding proximity (confidence heuristic)
            canonical_name, canonical_label, canonical_def, from_graph = _pick_canonical(cluster)
            aliases = _apply_merge(
                cluster, canonical_name, canonical_label, canonical_def,
            )
            report_entries.append(ResolutionEntry(
                canonical_name=canonical_name,
                canonical_label=canonical_label,
                aliases=aliases,
                mention_count=len(cluster),
                method="embedding",
                canonical_source="graph" if from_graph else "batch",
            ))

    unique_after = len({(m.entity.label, m.entity.name) for m in mentions if not m.is_known})

    report = ResolutionReport(
        total_mentions=len(mentions),
        unique_before=unique_before,
        unique_after=unique_after,
        merges=[e for e in report_entries if e.aliases],
    )

    logger.info(
        "Entity resolution complete: %d → %d unique entities (%d merges).",
        unique_before, unique_after, len(report.merges),
    )

    return relations, report
