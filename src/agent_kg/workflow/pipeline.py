"""Pipeline — thin async orchestrator using decomposed agents.

The pipeline wires together:
- ``extract_raw_relations`` — instructor call (relation extraction without roles).
- ``extract_roles``         — instructor call (role filling per relation).
- ``create_qc_agent``       — Agent Framework agent (extraction QC).
- ``create_arbiter``        — Agent Framework agent (type governance).
- ``create_validator``      — Agent Framework agent (neurosymbolic validation).
- Entity resolution, context retrieval, graph building — plain functions/executors.

Two execution modes:
1. **Full pipeline** (first run or stale ontology):
   Extract relations → Fill roles → QC → Arbiter → Entity Resolution → Validate → Graph
2. **Fast path** (fresh ontology):
   Context → Extract relations → Fill roles → Drift check → QC → Arbiter → ER → Validate → Graph

The framework handles the tool-calling loop inside each agent.
This module just sequences the calls.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from openai import OpenAI

from agent_kg.agents.arbiter_agent import (
    apply_arbiter_decisions,
    create_arbiter,
    format_candidates,
)
from agent_kg.agents.qc_agent import create_qc_agent, format_qc_context
from agent_kg.agents.relation_agent import extract_raw_relations
from agent_kg.agents.role_agent import extract_roles
from agent_kg.agents.validator import (
    create_validator,
    format_violations,
)
from agent_kg.config import DomainConfig
from agent_kg.executors.context import ContextRetriever, GraphContext
from agent_kg.executors.entity_resolution import resolve_entities
from agent_kg.models.base import CandidateType, Mention, RawRelation, Relation, ResolutionReport
from agent_kg.models.graph import (
    GraphEdge,
    GraphExporter,
    GraphNode,
    Neo4jExporter,
    build_graph_elements,
    generate_id,
    generate_mention_id,
)
from agent_kg.models.ontology import OntologySchema
from agent_kg.utils.chunking import Chunk, chunk_document
from agent_kg.utils.embeddings import compute_embeddings
from agent_kg.validation.rules import run_symbolic_validation

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────

def _overlap_length(text: str, query: str) -> int:
    """Count the longest common substring length (approximate via shared chars)."""
    # Fast heuristic: count how many characters of *query* appear in *text*.
    # Sufficient for choosing the best chunk when no chunk fully contains the quote.
    if not query:
        return 0
    t = set(range(len(text)))
    overlap = 0
    start = 0
    for _ in range(len(query)):
        pos = text.find(query[start : start + 1], max(0, start - 50))
        if pos != -1 and pos in t:
            overlap += 1
        start += 1
    # Simpler: just check character-by-character containment at boundaries.
    # This is a rough proxy — we want the chunk with the most shared text.
    best = 0
    for i in range(len(text)):
        j = 0
        while i + j < len(text) and j < len(query) and text[i + j] == query[j]:
            j += 1
        best = max(best, j)
    return best


@dataclass
class Document:
    """A pre-formatted document ready for extraction."""

    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Result of processing a batch of documents."""

    relations: list[Relation]
    ontology: OntologySchema | None
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    violations_count: int = 0
    rejected_relations_count: int = 0
    entities_merged: int = 0
    resolution_report: ResolutionReport | None = None
    qc_flags_count: int = 0
    documents_processed: int = 0


class Pipeline:
    """Main pipeline orchestrator.

    Args:
        config: Domain configuration.
        neo4j_uri: Neo4j bolt URI (optional — skip graph export if ``None``).
        neo4j_auth: ``(username, password)`` tuple.
        neo4j_database: Neo4j database name.
    """

    def __init__(
        self,
        config: DomainConfig,
        neo4j_uri: str | None = None,
        neo4j_auth: tuple[str, str] | None = None,
        neo4j_database: str = "neo4j",
    ) -> None:
        self._config = config
        self._ontology: OntologySchema | None = None

        # Cached embeddings for drift detection
        self._cached_ontology_type_texts: list[str] = []
        self._cached_ontology_type_embeddings: np.ndarray | None = None

        # Shared OpenAI client
        self._client = OpenAI()

        # Neo4j (optional)
        self._exporter: GraphExporter | None = None
        self._context_retriever: ContextRetriever | None = None

        if neo4j_uri and neo4j_auth:
            self._exporter = Neo4jExporter(neo4j_uri, neo4j_auth, neo4j_database)
            self._context_retriever = ContextRetriever(
                neo4j_uri, neo4j_auth, neo4j_database,
                client=self._client,
                embedding_model=config.embedding_model,
            )
            # Ensure vector indexes exist (entities + chunks).
            self._exporter.ensure_vector_index(
                dimensions=config.embedding_dimensions,
            )

    def close(self) -> None:
        """Release external connections."""
        if isinstance(self._exporter, Neo4jExporter):
            self._exporter.close()
        if self._context_retriever:
            self._context_retriever.close()

    # ── Public API ──────────────────────────────────────────────────

    def process(self, documents: list[Document]) -> PipelineResult:
        """Process a batch of documents (sync entry point)."""
        return asyncio.run(self._process_async(documents))

    async def process_async(self, documents: list[Document]) -> PipelineResult:
        """Process a batch of documents (async entry point)."""
        return await self._process_async(documents)

    # ── Internal routing ────────────────────────────────────────────

    async def _process_async(self, documents: list[Document]) -> PipelineResult:
        # Negotiation trigger = N-documents staleness OR drift.
        # N-documents can be checked upfront; drift requires seeing recent extractions.
        if self._ontology is None:
            return await self._full_pipeline(documents)

        if self._ontology.is_stale(self._config.ontology_staleness_threshold):
            return await self._full_pipeline(documents)

        return await self._fast_path(documents)

    # ── Full pipeline ───────────────────────────────────────────────

    async def _full_pipeline(self, documents: list[Document]) -> PipelineResult:
        logger.info("Running FULL pipeline on %d documents.", len(documents))

        # 1. Relation extraction (no roles)
        all_raw = self._extract_raw_batch(documents)

        if not all_raw:
            return PipelineResult(
                relations=[], ontology=self._ontology,
                nodes=[], edges=[], documents_processed=len(documents),
            )

        # 2. Role extraction (per relation)
        all_relations = self._fill_roles_batch(documents, all_raw)

        if not all_relations:
            return PipelineResult(
                relations=[], ontology=self._ontology,
                nodes=[], edges=[], documents_processed=len(documents),
            )

        # 3. QC (optional)
        qc_flags_count = 0
        if self._config.qc_enabled:
            qc_flags_count = await self._run_qc(documents, all_relations)

        # 4. Collect candidate types → Arbiter
        candidates = self._collect_candidates(all_relations)
        if candidates:
            await self._run_arbiter(candidates)

        # 4b. Snapshot original surface forms (before ER mutates entities)
        surface_snapshot = self._snapshot_surface_forms(all_relations)

        # 5. Entity resolution
        all_relations, resolution_report = self._resolve_entities(all_relations)

        # 6. Chunk documents + assign chunk_ids (before validation)
        doc_chunks = self._chunk_documents(documents)
        self._assign_chunk_ids(all_relations, doc_chunks)
        chunk_texts = {
            c.chunk_id: c.text
            for chunks in doc_chunks.values()
            for c in chunks
        }

        # 6b. Collect mentions (original surface forms → canonical entities)
        mentions = self._collect_mentions(all_relations, surface_snapshot)

        # 7. Validate (fail-closed per relation, chunk-scoped)
        doc_texts = {d.id: d.text for d in documents}
        valid_relations, violations_count, rejected_count = await self._validate(
            all_relations, doc_texts=doc_texts, chunk_texts=chunk_texts,
        )

        # 8. Build graph & export (with mentions)
        # Mentions from rejected relations are harmless — their Entity
        # nodes won't exist, so REFERS_TO edges simply won't match.
        result = self._build_and_export(
            valid_relations,
            documents,
            doc_chunks=doc_chunks,
            mentions=mentions,
            violations_count=violations_count,
            rejected_relations_count=rejected_count,
            resolution_report=resolution_report,
            qc_flags_count=qc_flags_count,
        )

        if self._ontology:
            self._ontology.documents_since_last_negotiation = len(documents)

        return result

    # ── Fast path ───────────────────────────────────────────────────

    async def _fast_path(self, documents: list[Document]) -> PipelineResult:
        logger.info("Running FAST PATH on %d documents.", len(documents))

        # 1. Relation extraction (with context + ontology)
        all_raw = self._extract_raw_batch(
            documents, ontology=self._ontology, with_context=True,
        )

        # 2. Role extraction
        all_relations = self._fill_roles_batch(documents, all_raw)

        # 3. Drift check
        if self._should_negotiate_by_drift(all_relations):
            logger.info("Drift detected — switching to FULL pipeline.")
            return await self._full_pipeline(documents)

        # 4. QC (optional)
        qc_flags_count = 0
        if self._config.qc_enabled:
            qc_flags_count = await self._run_qc(documents, all_relations)

        # 5. Candidates → Arbiter
        candidates = self._collect_candidates(all_relations)
        if candidates:
            await self._run_arbiter(candidates)

        # 5b. Snapshot original surface forms (before ER mutates entities)
        surface_snapshot = self._snapshot_surface_forms(all_relations)

        # 6. Entity resolution
        all_relations, resolution_report = self._resolve_entities(all_relations)

        # 7. Chunk documents + assign chunk_ids (before validation)
        doc_chunks = self._chunk_documents(documents)
        self._assign_chunk_ids(all_relations, doc_chunks)
        chunk_texts = {
            c.chunk_id: c.text
            for chunks in doc_chunks.values()
            for c in chunks
        }

        # 7b. Collect mentions (original surface forms → canonical entities)
        mentions = self._collect_mentions(all_relations, surface_snapshot)

        # 8. Validate (chunk-scoped)
        doc_texts = {d.id: d.text for d in documents}
        valid_relations, violations_count, rejected_count = await self._validate(
            all_relations, doc_texts=doc_texts, chunk_texts=chunk_texts,
        )

        # 9. Build graph & export
        result = self._build_and_export(
            valid_relations,
            documents,
            doc_chunks=doc_chunks,
            mentions=mentions,
            violations_count=violations_count,
            rejected_relations_count=rejected_count,
            resolution_report=resolution_report,
            qc_flags_count=qc_flags_count,
        )

        if self._ontology:
            self._ontology.documents_since_last_negotiation += len(documents)

        return result

    # ── Extraction helpers ──────────────────────────────────────────

    def _extract_raw_batch(
        self,
        documents: list[Document],
        *,
        ontology: OntologySchema | None = None,
        with_context: bool = False,
    ) -> list[RawRelation]:
        """Extract raw relations (no roles) from all documents."""
        all_raw: list[RawRelation] = []
        for doc in documents:
            context = self._get_context(doc.text) if with_context else None
            raw = extract_raw_relations(
                doc.text, doc.id, self._client, self._config,
                graph_context=context, ontology=ontology,
            )
            all_raw.extend(raw)
        return all_raw

    def _fill_roles_batch(
        self,
        documents: list[Document],
        raw_relations: list[RawRelation],
    ) -> list[Relation]:
        """Fill semantic roles for each raw relation."""
        doc_texts = {d.id: d.text for d in documents}

        # Fetch graph context once (shared across all relations in the batch)
        sample_text = documents[0].text if documents else ""
        graph_context = self._get_context(sample_text) if documents else None

        relations: list[Relation] = []
        for raw in raw_relations:
            doc_text = doc_texts.get(raw.source.document_id, "")
            relation = extract_roles(
                raw, doc_text, self._client, self._config,
                ontology=self._ontology,
                graph_context=graph_context,
            )
            if relation is not None:
                relations.append(relation)
        logger.info(
            "Role extraction: %d/%d relations filled successfully.",
            len(relations), len(raw_relations),
        )
        return relations

    # ── Candidate collection & filtering ──────────────────────────────

    def _collect_candidates(self, relations: list[Relation]) -> list[CandidateType]:
        """Identify novel types not in the current ontology.

        Three-stage filter (cheap → expensive):
        1. **Label normalisation** — exact label match after normalisation
           catches trivial casing/whitespace variants.
        2. **Embedding similarity** — candidates within cosine threshold of
           an existing ontology type are auto-merged (no Arbiter needed).
        3. **Remainder** → forwarded to the Arbiter agent.

        Candidates come from two sources:
        - Relation types and entity labels extracted in the main flow.
        - ``Roles.candidate_entity_types`` proposed inline by the Role Agent.
        """
        known_rel_labels: set[str] = set()
        known_ent_labels: set[str] = set()
        if self._ontology:
            known_rel_labels = {t.label for t in self._ontology.relation_types}
            known_ent_labels = {t.label for t in self._ontology.entity_types}

        # ── Harvest raw candidates ──────────────────────────────────
        raw_candidates: list[CandidateType] = []
        seen: set[tuple[str, str]] = set()

        for rel in relations:
            # Relation type candidates
            rt = rel.relation_type
            key_rel = ("relation", rt.label or "")
            if rt.label and key_rel not in seen:
                seen.add(key_rel)
                raw_candidates.append(CandidateType(
                    kind="relation",
                    label=rt.label,
                    definition=rt.definition,
                    source_description=rel.description[:200],
                ))

            # Entity type candidates (from extraction)
            for ent in rel.roles.all_entities():
                key_ent = ("entity", ent.label)
                if ent.label and key_ent not in seen:
                    seen.add(key_ent)
                    raw_candidates.append(CandidateType(
                        kind="entity",
                        label=ent.label,
                        definition=ent.definition,
                        source_description=rel.description[:200],
                    ))

            # Entity type candidates proposed inline by the Role Agent
            for et in rel.roles.candidate_entity_types:
                key_inline = ("entity", et.label)
                if et.label and key_inline not in seen:
                    seen.add(key_inline)
                    raw_candidates.append(CandidateType(
                        kind="entity",
                        label=et.label,
                        definition=et.definition,
                        source_description=rel.description[:200],
                    ))

        # ── Stage 1: label normalisation ────────────────────────────
        # Drop candidates whose normalised label already exists in ontology.
        from agent_kg.utils.sanitize import sanitize_for_identifier

        norm_known_rel = {sanitize_for_identifier(l, style="upper") for l in known_rel_labels}
        norm_known_ent = {sanitize_for_identifier(l, style="upper") for l in known_ent_labels}

        after_stage1: list[CandidateType] = []
        for c in raw_candidates:
            norm = sanitize_for_identifier(c.label, style="upper")
            known = norm_known_rel if c.kind == "relation" else norm_known_ent
            if norm not in known:
                after_stage1.append(c)

        if not after_stage1:
            logger.info("Candidate filtering: all %d candidates matched by label.", len(raw_candidates))
            return []

        # ── Stage 2: embedding similarity ───────────────────────────
        # Compare each remaining candidate against existing ontology types.
        # Candidates within threshold are auto-merged (added to ontology directly).
        auto_merged = self._embedding_filter_candidates(after_stage1)
        after_stage2 = [c for c in after_stage1 if c not in auto_merged]

        logger.info(
            "Candidate filtering: %d raw → %d after label match → %d auto-merged → %d for Arbiter.",
            len(raw_candidates), len(after_stage1), len(auto_merged), len(after_stage2),
        )
        return after_stage2

    def _embedding_filter_candidates(
        self,
        candidates: list[CandidateType],
    ) -> list[CandidateType]:
        """Auto-merge candidates that are near-duplicates of existing ontology types.

        Returns the list of candidates that were auto-merged (removed from Arbiter queue).
        """
        if not self._ontology:
            return []

        # Collect existing types with their embed texts
        existing_texts: list[str] = []
        existing_labels: list[str] = []
        for t in self._ontology.relation_types:
            existing_texts.append(f"{t.label}: {t.definition}")
            existing_labels.append(t.label)
        for t in self._ontology.entity_types:
            existing_texts.append(f"{t.label}: {t.definition}")
            existing_labels.append(t.label)

        if not existing_texts:
            return []

        # Embed candidates + existing types together
        candidate_texts = [f"{c.label}: {c.definition}" for c in candidates]
        all_texts = candidate_texts + existing_texts

        all_embeddings = compute_embeddings(all_texts, self._client, self._config.embedding_model)
        if all_embeddings.size == 0:
            return []

        n_cand = len(candidate_texts)
        cand_emb = all_embeddings[:n_cand]
        exist_emb = all_embeddings[n_cand:]

        # Normalise for cosine similarity
        cand_norm = cand_emb / (np.linalg.norm(cand_emb, axis=1, keepdims=True) + 1e-12)
        exist_norm = exist_emb / (np.linalg.norm(exist_emb, axis=1, keepdims=True) + 1e-12)

        # Cosine similarities: (n_cand, n_existing)
        sims = cand_norm @ exist_norm.T

        auto_merge_threshold = 0.90  # high similarity = obvious duplicate
        merged: list[CandidateType] = []
        for i, c in enumerate(candidates):
            max_sim = float(np.max(sims[i]))
            if max_sim >= auto_merge_threshold:
                best_idx = int(np.argmax(sims[i]))
                logger.info(
                    "Auto-merged candidate '%s' → existing '%s' (sim=%.3f).",
                    c.label, existing_labels[best_idx], max_sim,
                )
                merged.append(c)

        return merged

    # ── Agent execution ─────────────────────────────────────────────

    async def _run_arbiter(self, candidates: list[CandidateType]) -> None:
        """Run the Type Arbiter on candidate types."""
        agent, session = create_arbiter(
            self._config, candidates, self._ontology,
        )
        await agent.run(messages=[{
            "role": "user",
            "content": (
                f"Review these {len(candidates)} candidate types and make decisions.\n\n"
                + format_candidates(candidates)
            ),
        }])
        self._ontology = apply_arbiter_decisions(
            session.decisions, self._ontology,
        )
        logger.info(
            "Arbiter: %d decisions → ontology v%d.",
            len(session.decisions),
            self._ontology.version if self._ontology else 0,
        )

    async def _run_qc(
        self,
        documents: list[Document],
        relations: list[Relation],
    ) -> int:
        """Run QC agent per document. Returns total flag count."""
        total_flags = 0
        for doc in documents:
            doc_rels = [r for r in relations if r.source.document_id == doc.id]
            if not doc_rels:
                continue
            agent, session = create_qc_agent(self._config, doc.text, doc_rels)
            await agent.run(messages=[{
                "role": "user",
                "content": format_qc_context(doc.text, doc_rels),
            }])
            total_flags += len(session.flags)
            if session.flags:
                logger.info(
                    "QC doc %s: %d flags (coverage %.0f%%).",
                    doc.id, len(session.flags), session.coverage_score * 100,
                )
                for flag in session.flags:
                    logger.info("  [%s] %s", flag.kind, flag.description[:80])
        return total_flags

    async def _validate(
        self,
        relations: list[Relation],
        doc_texts: dict[str, str] | None = None,
        chunk_texts: dict[str, str] | None = None,
    ) -> tuple[list[Relation], int, int]:
        """Validate relations.

        Policy: fail-closed *per relation*.
        - Relations that violate **error** invariants are blocked from export.
        - Warnings do not block export.

        Args:
            chunk_texts: Optional ``chunk_id → text`` mapping.  When provided,
                quote-verbatim checks run against the chunk (tighter than doc).

        Returns:
            (valid_relations, violations_count, rejected_relations_count)
        """
        if not relations:
            return [], 0, 0

        valid: list[Relation] = []
        rejected: list[Relation] = []
        all_violations = []
        all_errors = []

        # Evaluate violations at relation granularity so we can block only the violating facts.
        for rel in relations:
            rel_violations = run_symbolic_validation(
                [rel],
                blocklist=self._config.generic_entity_blocklist,
                doc_texts=doc_texts,
                chunk_texts=chunk_texts,
            )
            all_violations.extend(rel_violations)

            rel_errors = [v for v in rel_violations if v.severity == "error"]
            if rel_errors:
                rejected.append(rel)
                all_errors.extend(rel_errors)
            else:
                valid.append(rel)

        if not all_errors:
            logger.info("Validation passed — no errors.")
            return valid, len(all_violations), 0

        logger.info(
            "Validation blocked %d/%d relations (%d error violations).",
            len(rejected), len(relations), len(all_errors),
        )

        # Create agent on demand — only when error violations exist.
        # Note: we currently do not auto-apply corrections; we only log resolutions.
        agent, session = create_validator(self._config, all_errors)
        await agent.run(messages=[{
            "role": "user",
            "content": "Resolve these constraint violations:\n\n" + format_violations(all_errors),
        }])

        for res in session.resolutions:
            logger.info(
                "Resolution [%s] %s: %s",
                res.action,
                res.violation_rule,
                res.reasoning,
            )

        return valid, len(all_violations), len(rejected)

    def _resolve_entities(
        self,
        relations: list[Relation],
    ) -> tuple[list[Relation], ResolutionReport | None]:
        """Run entity resolution if enabled in config.

        When a Neo4j graph exists, retrieves candidate entities via
        vector similarity (embedding-based entity linking) so that
        new mentions are resolved against relevant existing entities
        without fetching the full catalog.
        """
        if not self._config.entity_resolution_enabled:
            return relations, None

        known_entities: list[dict[str, str]] | None = None
        if self._context_retriever:
            try:
                # Collect unique mention embed texts
                seen: set[tuple[str, str]] = set()
                embed_texts: list[str] = []
                for rel in relations:
                    for ent in rel.roles.all_entities():
                        key = (ent.label, ent.name)
                        if key not in seen:
                            seen.add(key)
                            embed_texts.append(
                                f"{ent.name} | {ent.label} | {ent.definition}"
                            )

                if embed_texts:
                    mention_embeddings = compute_embeddings(
                        embed_texts,
                        self._client,
                        self._config.embedding_model,
                    )
                    known_entities = self._context_retriever.find_similar_entities(
                        mention_embeddings.tolist(),
                        top_k=10,
                    )
            except Exception:
                logger.warning(
                    "Vector-based entity candidate retrieval failed.",
                    exc_info=True,
                )

        return resolve_entities(
            relations, self._client, self._config,
            known_entities=known_entities,
        )

    def _build_and_export(
        self,
        relations: list[Relation],
        documents: list[Document],
        *,
        doc_chunks: dict[str, list[Chunk]] | None = None,
        mentions: list[Mention] | None = None,
        violations_count: int,
        rejected_relations_count: int,
        resolution_report: ResolutionReport | None = None,
        qc_flags_count: int = 0,
    ) -> PipelineResult:
        """Build graph elements and export to Neo4j.

        When a Neo4j exporter is configured the method also:
        - Embeds pre-computed chunks for the ``chunk_embeddings``
          vector index.
        - Computes entity embeddings for the ``entity_embeddings``
          vector index.

        Args:
            doc_chunks: Pre-computed ``document_id → list[Chunk]`` mapping.
                Chunking and ``chunk_id`` assignment happen upstream (before
                validation) so that quote checks are chunk-scoped.
            mentions: Mention objects linking surface forms to canonical
                entities.  Translated into Mention graph nodes with
                ``HAS_MENTION`` / ``REFERS_TO`` edges.
        """
        # ── Entity embeddings ───────────────────────────────────────
        entity_embeddings: dict[str, list[float]] | None = None
        if self._exporter:
            seen: set[str] = set()
            embed_texts: list[str] = []
            node_ids: list[str] = []
            for rel in relations:
                for ent in rel.roles.all_entities():
                    nid = generate_id({"label": ent.label, "name": ent.name})
                    if nid not in seen:
                        seen.add(nid)
                        embed_texts.append(
                            ent.to_embed
                            or f"Entity class: {ent.label}. Definition: {ent.definition}"
                        )
                        node_ids.append(nid)

            if embed_texts:
                emb_array = compute_embeddings(
                    embed_texts, self._client, self._config.embedding_model,
                )
                entity_embeddings = {
                    nid: emb_array[i].tolist()
                    for i, nid in enumerate(node_ids)
                }

        # ── Chunk embeddings ────────────────────────────────────────
        if doc_chunks is None:
            doc_chunks = self._chunk_documents(documents)

        doc_chunk_embeddings: dict[str, dict[str, list[float]]] = {}
        if self._exporter:
            for doc in documents:
                chunks = doc_chunks.get(doc.id, [])
                if chunks:
                    chunk_txts = [c.text for c in chunks]
                    c_emb = compute_embeddings(
                        chunk_txts, self._client, self._config.embedding_model,
                    )
                    doc_chunk_embeddings[doc.id] = {
                        chunks[i].chunk_id: c_emb[i].tolist()
                        for i in range(len(chunks))
                    }

        # ── Build graph elements ────────────────────────────────────
        all_nodes: list[GraphNode] = []
        all_edges: list[GraphEdge] = []

        for doc in documents:
            doc_rels = [r for r in relations if r.source.document_id == doc.id]
            # Filter mentions for this document's chunks
            doc_mentions: list[Mention] | None = None
            if mentions:
                doc_chunk_ids = {c.chunk_id for c in doc_chunks.get(doc.id, [])}
                doc_mentions = [
                    m for m in mentions
                    if m.chunk_id in doc_chunk_ids or m.chunk_id is None
                ] or None
            nodes, edges = build_graph_elements(
                doc_rels,
                doc.id,
                entity_embeddings=entity_embeddings,
                chunks=doc_chunks.get(doc.id),
                chunk_embeddings=doc_chunk_embeddings.get(doc.id),
                mentions=doc_mentions,
            )
            all_nodes.extend(nodes)
            all_edges.extend(edges)

        if self._exporter:
            self._exporter.export(all_nodes, all_edges)

        return PipelineResult(
            relations=relations,
            ontology=self._ontology,
            nodes=all_nodes,
            edges=all_edges,
            violations_count=violations_count,
            rejected_relations_count=rejected_relations_count,
            entities_merged=len(resolution_report.merges) if resolution_report else 0,
            resolution_report=resolution_report,
            qc_flags_count=qc_flags_count,
            documents_processed=len(documents),
        )

    @staticmethod
    def _chunk_documents(
        documents: list[Document],
    ) -> dict[str, list[Chunk]]:
        """Chunk all documents. Called before validation so chunk_ids
        are available for chunk-scoped quote checks."""
        doc_chunks: dict[str, list[Chunk]] = {}
        for doc in documents:
            doc_chunks[doc.id] = chunk_document(
                doc.text,
                doc.id,
                max_tokens=1024,
                overlap_tokens=128,
            )
        return doc_chunks

    @staticmethod
    def _assign_chunk_ids(
        relations: list[Relation],
        doc_chunks: dict[str, list[Chunk]],
    ) -> None:
        """Set ``chunk_id`` on each evidence item.

        For each evidence quote we find the chunk whose text contains
        the quote.  If no chunk matches (e.g. quote spans a boundary),
        we pick the chunk with the largest overlap.
        """
        for rel in relations:
            chunks = doc_chunks.get(rel.source.document_id, [])
            if not chunks:
                continue

            for evidence in rel.source.evidence:
                quote = evidence.quote.strip()
                if not quote:
                    continue

                # Exact containment (fast path)
                for chunk in chunks:
                    if quote in chunk.text:
                        evidence.chunk_id = chunk.chunk_id
                        break
                else:
                    # Largest overlap heuristic
                    best_chunk = max(
                        chunks,
                        key=lambda c: _overlap_length(c.text, quote),
                    )
                    evidence.chunk_id = best_chunk.chunk_id

    @staticmethod
    def _snapshot_surface_forms(
        relations: list[Relation],
    ) -> dict[int, list[tuple[str, str, str]]]:
        """Capture original entity (name, label, role) before ER mutates them.

        Returns a mapping of ``d → [(name, label, role), ...]``
        preserving the order and identity of entities in each relation.
        """
        snapshot: dict[int, list[tuple[str, str, str]]] = {}
        for rel in relations:
            forms: list[tuple[str, str, str]] = []
            for ent in rel.roles.agents:
                forms.append((ent.name, ent.label, "agent"))
            for ent in rel.roles.themes:
                forms.append((ent.name, ent.label, "theme"))
            for ent in rel.roles.circumstances:
                forms.append((ent.name, ent.label, ent.role))  # type: ignore[attr-defined]
            for ent in rel.roles.context:
                forms.append((ent.name, ent.label, "context"))
            for ent in rel.roles.origin_destinations:
                forms.append((ent.name, ent.label, ent.role))  # type: ignore[attr-defined]
            for ent in rel.roles.time_locations:
                forms.append((ent.name, ent.label, ent.role))  # type: ignore[attr-defined]
            snapshot[id(rel)] = forms
        return snapshot

    @staticmethod
    def _collect_mentions(
        relations: list[Relation],
        surface_snapshot: dict[int, list[tuple[str, str, str]]],
    ) -> list[Mention]:
        """Build Mention objects from pre-ER surface forms and post-ER canonical entities.

        Each entity occurrence becomes a Mention linking the original
        surface form (from the snapshot) to the canonical entity (current
        state after resolution).

        The chunk_id is taken from the relation's *first* evidence item
        (best-effort locality).  In the future, entity-level offsets could
        pin each mention to its exact evidence item.
        """
        mentions: list[Mention] = []
        seen: set[str] = set()  # deduplicate by mention_id

        for rel in relations:
            original_forms = surface_snapshot.get(id(rel), [])
            canonical_entities = rel.roles.all_entities()

            # Best-effort chunk: first evidence item's chunk_id
            chunk_id = rel.source.chunk_id

            # Both lists have the same length and order (ER mutates in-place).
            for (orig_name, orig_label, role), entity in zip(
                original_forms, canonical_entities, strict=False,
            ):
                mid = generate_mention_id(
                    chunk_id, orig_name, entity.name, entity.label,
                )
                if mid in seen:
                    continue
                seen.add(mid)
                mentions.append(Mention(
                    mention_id=mid,
                    surface_form=orig_name,
                    entity_name=entity.name,
                    entity_label=entity.label,
                    chunk_id=chunk_id,
                    role=role,
                ))

        logger.info(
            "Collected %d mentions (%d with surface ≠ canonical).",
            len(mentions),
            sum(1 for m in mentions if m.surface_form != m.entity_name),
        )
        return mentions

    # ── Drift detection (pure compute) ─────────────────────────────

    def _should_negotiate_by_drift(self, extracted_relations: list[Relation]) -> bool:
        """Decide whether to renegotiate ontology based on extraction drift.

        Drift is computed as: 1 - mean(max cosine similarity) between each extracted
        relation embedding and the closest current ontology relation-type embedding.

        This is intentionally deterministic (no agent) and cheap enough for steady-state.
        """
        if not self._ontology or not self._ontology.relation_types:
            return True

        # Avoid triggering on tiny batches.
        min_relations = 10
        if len(extracted_relations) < min_relations:
            return False

        drift_score = self._compute_drift_score(extracted_relations)
        # Heuristic threshold; later this should live in config.
        drift_threshold = 0.25  # i.e. mean similarity < 0.75
        logger.info("Drift score: %.3f (threshold=%.3f)", drift_score, drift_threshold)
        return drift_score >= drift_threshold

    def _compute_drift_score(self, extracted_relations: list[Relation]) -> float:
        if not self._ontology or not self._ontology.relation_types or not extracted_relations:
            return 1.0

        type_texts = [f"{t.label}: {t.definition}" for t in self._ontology.relation_types]
        if type_texts != self._cached_ontology_type_texts or self._cached_ontology_type_embeddings is None:
            self._cached_ontology_type_texts = type_texts
            self._cached_ontology_type_embeddings = compute_embeddings(
                type_texts, self._client, self._config.embedding_model,
            )

        type_emb = self._cached_ontology_type_embeddings
        if type_emb.size == 0:
            return 1.0

        rel_texts = [r.to_embed or "" for r in extracted_relations]
        rel_emb = compute_embeddings(rel_texts, self._client, self._config.embedding_model)
        if rel_emb.size == 0:
            return 0.0

        # Normalize for cosine similarity
        type_norm = type_emb / (np.linalg.norm(type_emb, axis=1, keepdims=True) + 1e-12)
        rel_norm = rel_emb / (np.linalg.norm(rel_emb, axis=1, keepdims=True) + 1e-12)

        # Cosine similarities: (N_rel, N_types)
        sims = rel_norm @ type_norm.T
        max_sims = np.max(sims, axis=1)
        mean_sim = float(np.mean(max_sims))
        return max(0.0, 1.0 - mean_sim)

    def _get_context(self, document_text: str) -> GraphContext | None:
        """Retrieve graph context if available."""
        if self._context_retriever:
            try:
                return self._context_retriever.retrieve(document_text)
            except Exception:
                logger.warning("Context retrieval failed.", exc_info=True)
        return None


# ── Module-level helpers ────────────────────────────────────────────

def _overlap_length(text: str, quote: str) -> int:
    """Longest common substring length (approximate, via sliding window)."""
    best = 0
    q_len = len(quote)
    for start in range(0, len(text) - 10, 10):
        window = text[start : start + q_len + 20]
        for i in range(len(window)):
            k = 0
            while i + k < len(window) and k < q_len and window[i + k] == quote[k]:
                k += 1
            best = max(best, k)
    return best
