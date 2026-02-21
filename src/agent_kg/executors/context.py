"""Context retriever — queries Neo4j for the feedback loop.

Before extracting from a new document, the context retriever
fetches relevant existing entities and relations from the graph
to inject as context into the extraction prompt.

Two retrieval strategies:
1. **Chunk-based (preferred)**: embed incoming document chunks,
   vector-search similar Chunk nodes, traverse to the relations
   and entities attached to those chunks.
2. **Substring fallback**: match entity names against document text
   (used when no chunk embeddings are available or the chunk vector
   index does not exist).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI

logger = logging.getLogger(__name__)


@dataclass
class GraphContext:
    """Retrieved graph context for a single document extraction."""

    known_entities: list[dict[str, str]]  # [{name, label, definition}, ...]
    related_relations: list[dict[str, str]]  # [{generic, verb, description}, ...]

    def is_empty(self) -> bool:
        return not self.known_entities and not self.related_relations

    def to_prompt_section(self) -> str:
        """Format as a text block for injection into the extraction prompt."""
        if self.is_empty():
            return ""

        parts = ["## Known entities and relationships from prior extractions\n"]

        if self.known_entities:
            parts.append("### Entities already in the knowledge graph:")
            for ent in self.known_entities:
                parts.append(f"- **{ent.get('name', '?')}** ({ent.get('label', '?')})")

        if self.related_relations:
            parts.append("\n### Relations already in the knowledge graph:")
            for rel in self.related_relations:
                parts.append(f"- {rel.get('generic', rel.get('description', '?'))}")

        parts.append(
            "\nWhen extracting, reuse these entity names and labels "
            "when referring to the same real-world entities.  Do not "
            "create duplicates.\n"
        )
        return "\n".join(parts)


class ContextRetriever:
    """Retrieves graph context from Neo4j.

    Two retrieval modes:

    1. **Chunk vector search** (when *client* and *embedding_model* are
       provided): chunk the incoming document, embed each chunk, and
       vector-search the ``chunk_embeddings`` index.  From the matched
       Chunk nodes, traverse ``(:Chunk)<-[:EXTRACTED_FROM]-(:Relation)``
       to retrieve relations and their connected entities.

    2. **Substring fallback**: match known entity names against the
       document text, then fetch their 1-hop relation neighborhood.
       Used when embeddings are unavailable or the vector index doesn't
       exist.

    Args:
        uri: Neo4j bolt URI.
        auth: ``(username, password)`` tuple.
        database: Neo4j database name.
        client: OpenAI client (needed for chunk-based retrieval).
        embedding_model: Embedding model name.
    """

    def __init__(
        self,
        uri: str,
        auth: tuple[str, str],
        database: str = "neo4j",
        client: OpenAI | None = None,
        embedding_model: str = "text-embedding-3-small",
    ) -> None:
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(uri, auth=auth)
        self._database = database
        self._client = client
        self._embedding_model = embedding_model

    def close(self) -> None:
        self._driver.close()

    # ── Main entry point ────────────────────────────────────────────

    def retrieve(
        self,
        document_text: str,
        max_chunks: int = 5,
        max_entities: int = 30,
        max_relations: int = 20,
    ) -> GraphContext:
        """Retrieve known graph context relevant to *document_text*.

        Prefers chunk-based vector search when an embedding client is
        available; falls back to substring matching otherwise.

        Limits belong here (retrieval), not in ``GraphContext.to_prompt_section()``,
        so callers can control prompt size deterministically.
        """
        if self._client is not None:
            try:
                ctx = self._retrieve_via_chunks(
                    document_text,
                    max_chunks=max_chunks,
                    max_entities=max_entities,
                    max_relations=max_relations,
                )
                if not ctx.is_empty():
                    return ctx
                logger.info(
                    "Chunk vector search returned empty — "
                    "falling back to substring matching."
                )
            except Exception:
                logger.warning(
                    "Chunk-based retrieval failed — falling back to "
                    "substring matching.",
                    exc_info=True,
                )

        return self._retrieve_via_substring(
            document_text,
            max_entities=max_entities,
            max_relations=max_relations,
        )

    # ── Chunk-based vector retrieval ────────────────────────────────

    def _retrieve_via_chunks(
        self,
        document_text: str,
        *,
        max_chunks: int = 5,
        max_entities: int = 30,
        max_relations: int = 20,
    ) -> GraphContext:
        """Embed document chunks → vector search Chunk nodes → traverse.

        Traversal pattern::

            matched Chunk <-[:EXTRACTED_FROM]- Relation -[:<ROLE>]-> Entity
        """
        from agent_kg.utils.chunking import chunk_document
        from agent_kg.utils.embeddings import compute_embeddings

        assert self._client is not None  # guarded by caller

        # 1. Chunk the incoming document
        chunks = chunk_document(
            document_text,
            document_id="__query__",  # ephemeral — not stored
            max_tokens=1024,
            overlap_tokens=128,
        )
        if not chunks:
            return GraphContext(known_entities=[], related_relations=[])

        # 2. Embed each chunk
        chunk_texts = [c.text for c in chunks]
        embeddings = compute_embeddings(
            chunk_texts, self._client, self._embedding_model,
        )
        if embeddings.size == 0:
            return GraphContext(known_entities=[], related_relations=[])

        # 3. Vector search chunk nodes — deduplicate across query chunks
        matched_chunk_ids: dict[str, float] = {}  # chunk_id → best score
        with self._driver.session(database=self._database) as session:
            for emb in embeddings.tolist():
                result = session.run(
                    "CALL db.index.vector.queryNodes("
                    "'chunk_embeddings', $top_k, $embedding"
                    ") YIELD node, score "
                    "RETURN node.id AS chunk_id, score",
                    top_k=max_chunks,
                    embedding=emb,
                )
                for record in result:
                    cid = record["chunk_id"]
                    sc = record["score"]
                    if cid not in matched_chunk_ids or sc > matched_chunk_ids[cid]:
                        matched_chunk_ids[cid] = sc

        if not matched_chunk_ids:
            return GraphContext(known_entities=[], related_relations=[])

        # Keep top-K by score
        sorted_ids = sorted(
            matched_chunk_ids, key=matched_chunk_ids.get, reverse=True,
        )[:max_chunks]

        # 4. Traverse: Chunk ← Relation → Entity
        with self._driver.session(database=self._database) as session:
            # Relations extracted from matched chunks
            rel_result = session.run(
                "UNWIND $ids AS cid "
                "MATCH (rel)-[:EXTRACTED_FROM]->(c:Chunk {id: cid}) "
                "WHERE rel.generic IS NOT NULL "
                "RETURN DISTINCT rel.generic AS generic, "
                "       rel.verb AS verb, "
                "       rel.description AS description "
                "LIMIT $limit",
                ids=sorted_ids,
                limit=max_relations,
            )
            related_relations = [dict(r) for r in rel_result]

            # Entities connected to those relations
            ent_result = session.run(
                "UNWIND $ids AS cid "
                "MATCH (rel)-[:EXTRACTED_FROM]->(c:Chunk {id: cid}) "
                "MATCH (rel)-[role]->(e:Entity) "
                "RETURN DISTINCT e.name AS name, "
                "       e.label_class AS label, "
                "       e.definition AS definition "
                "LIMIT $limit",
                ids=sorted_ids,
                limit=max_entities,
            )
            known_entities = [dict(r) for r in ent_result]

        logger.info(
            "Chunk retrieval: %d chunks matched → %d entities, %d relations.",
            len(sorted_ids), len(known_entities), len(related_relations),
        )
        return GraphContext(
            known_entities=known_entities,
            related_relations=related_relations,
        )

    # ── Substring fallback ──────────────────────────────────────────

    def _retrieve_via_substring(
        self,
        document_text: str,
        *,
        max_entities: int = 30,
        max_relations: int = 20,
    ) -> GraphContext:
        """Original substring-matching strategy (fallback)."""
        with self._driver.session(database=self._database) as session:
            # 1. Get known entity names
            result = session.run(
                "MATCH (e) WHERE e.name IS NOT NULL "
                "RETURN e.id AS id, e.name AS name, "
                "       e.label_class AS label, e.definition AS definition "
                "LIMIT 500"
            )
            all_entities = [dict(record) for record in result]

        # 2. Substring match against document
        doc_lower = document_text.lower()
        matched_entities = [
            ent for ent in all_entities
            if ent.get("name", "").lower() in doc_lower
        ][:max_entities]

        if not matched_entities:
            return GraphContext(known_entities=[], related_relations=[])

        # 3. Fetch 1-hop relations for matched entities
        matched_ids = [ent["id"] for ent in matched_entities if ent.get("id")]
        related_relations: list[dict[str, str]] = []

        if matched_ids:
            with self._driver.session(database=self._database) as session:
                result = session.run(
                    "UNWIND $ids AS eid "
                    "MATCH (e {id: eid})<-[r]-(rel) "
                    "WHERE rel.generic IS NOT NULL "
                    "RETURN DISTINCT rel.generic AS generic, "
                    "       rel.verb AS verb, "
                    "       rel.description AS description "
                    "LIMIT $limit",
                    ids=matched_ids,
                    limit=max_relations,
                )
                related_relations = [dict(record) for record in result]

        logger.info(
            "Context retriever: %d entity matches, %d related relations.",
            len(matched_entities), len(related_relations),
        )
        return GraphContext(
            known_entities=matched_entities,
            related_relations=related_relations,
        )

    def fetch_all_entities(self, limit: int = 2000) -> list[dict[str, str]]:
        """Retrieve all canonical entities from the graph.

        Used by entity resolution to compare new mentions against
        the existing entity catalog (cross-batch dedup).

        Returns:
            List of ``{name, label, definition}`` dicts.
        """
        with self._driver.session(database=self._database) as session:
            result = session.run(
                "MATCH (e) WHERE e.name IS NOT NULL AND e.label_class IS NOT NULL "
                "RETURN DISTINCT e.name AS name, "
                "       e.label_class AS label, "
                "       e.definition AS definition "
                "LIMIT $limit",
                limit=limit,
            )
            entities = [dict(record) for record in result]

        logger.info("Fetched %d known entities from graph.", len(entities))
        return entities

    def find_similar_entities(
        self,
        query_embeddings: list[list[float]],
        top_k: int = 5,
    ) -> list[dict[str, str]]:
        """Find entities in the graph similar to query embeddings.

        Uses the ``entity_embeddings`` Neo4j vector index to retrieve
        the top-K nearest neighbours for each query vector, then
        deduplicates across queries.

        Falls back to an empty list if the vector index does not exist
        or the database does not support vector search.

        Returns:
            Deduplicated list of ``{name, label, definition}`` dicts.
        """
        if not query_embeddings:
            return []

        candidates: dict[str, dict[str, str]] = {}

        try:
            with self._driver.session(database=self._database) as session:
                for emb in query_embeddings:
                    result = session.run(
                        "CALL db.index.vector.queryNodes("
                        "'entity_embeddings', $top_k, $embedding"
                        ") YIELD node, score "
                        "RETURN node.name AS name, "
                        "       node.label_class AS label, "
                        "       node.definition AS definition, "
                        "       score",
                        top_k=top_k,
                        embedding=emb,
                    )
                    for record in result:
                        name = record["name"] or ""
                        label = record["label"] or ""
                        key = f"{label}||{name}"
                        if key not in candidates:
                            candidates[key] = {
                                "name": name,
                                "label": label,
                                "definition": record["definition"] or "",
                            }
        except Exception:
            logger.warning(
                "Vector search failed — falling back to no known entities.",
                exc_info=True,
            )
            return []

        logger.info(
            "Vector search: %d queries → %d unique candidate entities.",
            len(query_embeddings), len(candidates),
        )
        return list(candidates.values())
