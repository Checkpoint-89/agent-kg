"""Context retriever — queries Neo4j for the feedback loop.

Before extracting from a new document, the context retriever
fetches relevant existing entities and relations from the graph
to inject as context into the extraction prompt.  This is the
lightweight GraphRAG pattern: 1-hop neighborhood retrieval,
not full community summarisation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

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
            for ent in self.known_entities[:30]:  # cap to avoid prompt bloat
                parts.append(f"- **{ent.get('name', '?')}** ({ent.get('label', '?')})")

        if self.related_relations:
            parts.append("\n### Relations already in the knowledge graph:")
            for rel in self.related_relations[:20]:
                parts.append(f"- {rel.get('generic', rel.get('description', '?'))}")

        parts.append(
            "\nWhen extracting, reuse these entity names and labels "
            "when referring to the same real-world entities.  Do not "
            "create duplicates.\n"
        )
        return "\n".join(parts)


class ContextRetriever:
    """Retrieves graph context from Neo4j.

    Given a document's text, the retriever:
    1. Extracts candidate entity names (from the known entity index).
    2. Fuzzy-matches them against Neo4j.
    3. Retrieves the 1-hop neighborhood (relations connected to
       matched entities).

    Args:
        uri: Neo4j bolt URI.
        auth: ``(username, password)`` tuple.
        database: Neo4j database name.
    """

    def __init__(
        self,
        uri: str,
        auth: tuple[str, str],
        database: str = "neo4j",
    ) -> None:
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(uri, auth=auth)
        self._database = database

    def close(self) -> None:
        self._driver.close()

    def retrieve(self, document_text: str, max_entities: int = 30) -> GraphContext:
        """Retrieve known graph context relevant to *document_text*.

        Strategy:
        - Get all entity names from the graph (lightweight — the index
          is small for typical KG sizes).
        - Check which names appear (case-insensitive substring) in the
          document text.
        - For matched entities, fetch their 1-hop relation neighborhood.
        """
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
        # TODO: replace naive substring with fuzzy/embedding ranking —
        #       short names cause false positives, no relevance ordering,
        #       and only incoming relations are traversed (see review notes).
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
                    "LIMIT 50",
                    ids=matched_ids,
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
