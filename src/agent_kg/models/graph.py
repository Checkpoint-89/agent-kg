"""Graph models and Neo4j builder.

The graph uses a **reified relation** model: relations are nodes,
not edges.  This allows relations to carry properties, provenance,
and multiple role-typed connections to entities.

Graph topology (diamond)::

    (Document)──HAS_CHUNK──►(Chunk)──HAS_MENTION──►(Mention)──REFERS_TO──►(Entity)
                                │                                              ▲
                                └──EXTRACTED_FROM──(Relation)──<ROLE>───────────┘

Two independent paths from Chunk to Entity form a **diamond**:

* **Mention path** — ``Chunk → Mention → Entity``: preserves the
  original surface form before entity resolution.  Every entity
  occurrence in a chunk produces a Mention node.
* **Relation path** — ``Chunk ← Relation → Entity``: captures the
  semantic fact using canonical (post-resolution) entity names.

``<ROLE>`` is one of: AGENT, THEME, TRIGGER, PURPOSE, REASON,
INSTRUMENT, BENEFICIARY, CONTEXT, ORIGIN, DESTINATION, TIME, LOCATION.

This enables: fine-grained provenance, non-destructive entity
resolution (all surface forms preserved), multi-document
corroboration, and model-version tracking.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_kg.models.base import Entity, Relation
from agent_kg.utils.chunking import Chunk

logger = logging.getLogger(__name__)


# =====================================================================
# Graph primitives
# =====================================================================

@dataclass
class GraphNode:
    """A node in the knowledge graph."""

    id: str
    labels: list[str]
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """A directed edge in the knowledge graph."""

    source_id: str
    target_id: str
    relation_type: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Mention:
    """A surface-form occurrence of an entity within a chunk.

    Separates the *mention* (what the text says) from the *entity*
    (what it refers to after resolution).  This enables non-destructive
    entity resolution: original surface forms are preserved as Mention
    nodes even after canonical merging.

    Graph edges::

        (Chunk)-[:HAS_MENTION]->(Mention)-[:REFERS_TO]->(Entity)
    """

    mention_id: str
    surface_form: str       # original entity name before ER
    entity_name: str        # canonical entity name after ER
    entity_label: str       # canonical entity label after ER
    chunk_id: str | None    # chunk this mention belongs to
    role: str               # semantic role in the relation (e.g. "agent")


# =====================================================================
# ID generation
# =====================================================================

def generate_id(data: dict[str, Any]) -> str:
    """Deterministic SHA-256 content hash → stable node ID."""
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def generate_mention_id(
    chunk_id: str | None,
    surface_form: str,
    entity_name: str,
    entity_label: str,
) -> str:
    """Deterministic Mention ID from its defining attributes."""
    return generate_id({
        "chunk_id": chunk_id or "",
        "surface_form": surface_form,
        "entity_name": entity_name,
        "entity_label": entity_label,
    })


# =====================================================================
# Graph element generation
# =====================================================================

def _entity_node(
    entity: Entity,
    extra_props: dict[str, Any] | None = None,
    embedding: list[float] | None = None,
) -> GraphNode:
    props = {
        "name": entity.name,
        "label_class": entity.label,
        "definition": entity.definition,
        "confidence": entity.confidence,
    }
    if extra_props:
        props.update(extra_props)

    # Surface aliases from entity resolution (if any)
    if entity.metadata and "aliases" in entity.metadata:
        props["aliases"] = ", ".join(entity.metadata["aliases"])

    if embedding is not None:
        props["embedding"] = embedding

    node_id = generate_id({"label": entity.label, "name": entity.name})
    # "Entity" common label enables the vector index; type-specific label kept for queries.
    return GraphNode(id=node_id, labels=["Entity", entity.label], properties=props)


def _chunk_node(
    chunk: Chunk,
    embedding: list[float] | None = None,
) -> GraphNode:
    """Create a graph node for a document chunk."""
    props: dict[str, Any] = {
        "document_id": chunk.document_id,
        "chunk_index": chunk.index,
        "text": chunk.text,
        "start_char": chunk.start_char,
        "end_char": chunk.end_char,
        "token_count": chunk.token_count,
    }
    if embedding is not None:
        props["embedding"] = embedding
    return GraphNode(id=chunk.chunk_id, labels=["Chunk"], properties=props)


def _mention_node(mention: Mention) -> GraphNode:
    """Create a graph node for a surface-form mention."""
    props: dict[str, Any] = {
        "surface_form": mention.surface_form,
        "entity_name": mention.entity_name,
        "entity_label": mention.entity_label,
        "role": mention.role,
    }
    if mention.chunk_id:
        props["chunk_id"] = mention.chunk_id
    return GraphNode(id=mention.mention_id, labels=["Mention"], properties=props)


def _relation_node(relation: Relation) -> GraphNode:
    props = {
        "description": relation.description,
        "generic": relation.generic or "",
        "specific": relation.specific or "",
        "axis": relation.relation_type.axis,
        "verb": relation.relation_type.verb,
        "target_category": relation.relation_type.target_category,
        "definition": relation.relation_type.definition,
        "confidence": relation.confidence,
        "quotes": "\n---\n".join(relation.source.quotes),
        "document_id": relation.source.document_id,
    }
    if relation.metadata:
        for k, v in relation.metadata.items():
            props[f"_meta_{k}"] = v

    node_id = generate_id({
        "verb": relation.relation_type.verb,
        "target": relation.relation_type.target_category,
        "description": relation.description,
        "doc": relation.source.document_id,
    })
    labels = [lbl for lbl in (relation.labels or []) if lbl]
    return GraphNode(id=node_id, labels=labels or ["Relation"], properties=props)


def build_graph_elements(
    relations: list[Relation],
    document_id: str,
    entity_embeddings: dict[str, list[float]] | None = None,
    chunks: list[Chunk] | None = None,
    chunk_embeddings: dict[str, list[float]] | None = None,
    mentions: list[Mention] | None = None,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Convert extracted relations into graph nodes and edges.

    Graph topology (diamond)::

        (Document)-[:HAS_CHUNK]->(Chunk)-[:HAS_MENTION]->(Mention)-[:REFERS_TO]->(Entity)
        (Relation)-[:EXTRACTED_FROM]->(Chunk)
        (Relation)-[:<ROLE>]->(Entity)

    The two paths from Chunk to Entity (via Mention and via Relation)
    form a diamond.  Mentions preserve original surface forms; Relations
    link to canonical entities after resolution.

    When *chunks* is ``None`` (backward compat), relations link
    directly to the Document node via ``EXTRACTED_FROM``.

    Args:
        relations: Validated relations to export.
        document_id: Source document identifier.
        entity_embeddings: Optional mapping of entity node id → embedding
            vector.  When provided, embeddings are stored on entity nodes
            to support vector-based entity linking.
        chunks: Optional list of :class:`Chunk` objects for the document.
            When provided, chunk nodes are created and relations are linked
            to their source chunk instead of the document.
        chunk_embeddings: Optional mapping of chunk_id → embedding vector.
        mentions: Optional list of :class:`Mention` objects.  Each mention
            generates a Mention node with ``HAS_MENTION`` (from its Chunk)
            and ``REFERS_TO`` (to the canonical Entity) edges.

    Returns:
        Tuple of ``(nodes, edges)`` ready for upload.
    """
    nodes: dict[str, GraphNode] = {}  # deduplicate by id
    edges: list[GraphEdge] = []

    # Document node
    doc_node = GraphNode(
        id=generate_id({"document_id": document_id}),
        labels=["Document"],
        properties={"document_id": document_id},
    )
    nodes[doc_node.id] = doc_node

    # Chunk nodes (Document → Chunk)
    chunk_id_set: set[str] = set()
    if chunks:
        for chunk in chunks:
            emb = chunk_embeddings.get(chunk.chunk_id) if chunk_embeddings else None
            c_node = _chunk_node(chunk, embedding=emb)
            nodes[c_node.id] = c_node
            chunk_id_set.add(chunk.chunk_id)
            edges.append(GraphEdge(
                source_id=doc_node.id,
                target_id=c_node.id,
                relation_type="HAS_CHUNK",
            ))

    for relation in relations:
        # Relation node
        rel_node = _relation_node(relation)
        nodes[rel_node.id] = rel_node

        # Relation → Chunk (preferred) or Relation → Document (fallback)
        chunk_id = relation.source.chunk_id
        if chunk_id and chunk_id in chunk_id_set:
            edges.append(GraphEdge(
                source_id=rel_node.id,
                target_id=chunk_id,
                relation_type="EXTRACTED_FROM",
            ))
        else:
            edges.append(GraphEdge(
                source_id=rel_node.id,
                target_id=doc_node.id,
                relation_type="EXTRACTED_FROM",
            ))

        # Role → entity edges
        role_map: list[tuple[str, list[Entity]]] = [
            ("AGENT", relation.roles.agents),
            ("THEME", relation.roles.themes),
        ]
        for ent in relation.roles.circumstances:
            role_map.append((ent.role.upper(), [ent]))  # type: ignore[attr-defined]
        for ent in relation.roles.context:
            role_map.append(("CONTEXT", [ent]))
        for ent in relation.roles.origin_destinations:
            role_map.append((ent.role.upper(), [ent]))  # type: ignore[attr-defined]
        for ent in relation.roles.time_locations:
            role_map.append((ent.role.upper(), [ent]))  # type: ignore[attr-defined]

        for role_label, entities in role_map:
            for entity in entities:
                ent_id = generate_id({"label": entity.label, "name": entity.name})
                emb = entity_embeddings.get(ent_id) if entity_embeddings else None
                ent_node = _entity_node(entity, embedding=emb)
                nodes[ent_node.id] = ent_node
                edges.append(GraphEdge(
                    source_id=rel_node.id,
                    target_id=ent_node.id,
                    relation_type=role_label,
                ))

    # Mention nodes (Chunk → Mention → Entity)
    if mentions:
        for mention in mentions:
            m_node = _mention_node(mention)
            nodes[m_node.id] = m_node

            # HAS_MENTION: Chunk → Mention (or Document → Mention if no chunk)
            if mention.chunk_id and mention.chunk_id in chunk_id_set:
                edges.append(GraphEdge(
                    source_id=mention.chunk_id,
                    target_id=m_node.id,
                    relation_type="HAS_MENTION",
                ))
            else:
                edges.append(GraphEdge(
                    source_id=doc_node.id,
                    target_id=m_node.id,
                    relation_type="HAS_MENTION",
                ))

            # REFERS_TO: Mention → Entity (canonical)
            ent_id = generate_id({
                "label": mention.entity_label,
                "name": mention.entity_name,
            })
            if ent_id in nodes:
                edges.append(GraphEdge(
                    source_id=m_node.id,
                    target_id=ent_id,
                    relation_type="REFERS_TO",
                ))

    return list(nodes.values()), edges


# =====================================================================
# Neo4j exporter
# =====================================================================

class GraphExporter(Protocol):
    """Protocol for graph export backends."""

    def export(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None: ...
    def clear(self) -> None: ...


class Neo4jExporter:
    """Idempotent batch exporter to Neo4j.

    Uses ``MERGE`` for upsert semantics and ``UNWIND`` for batch
    performance.

    Args:
        uri: Neo4j bolt URI (e.g. ``bolt://localhost:7687``).
        auth: Tuple of ``(username, password)``.
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

    def clear(self) -> None:
        """Delete all nodes and relationships in the database."""
        with self._driver.session(database=self._database) as session:
            session.run("MATCH (n) DETACH DELETE n")
            logger.info("Cleared Neo4j database.")

    def export(self, nodes: list[GraphNode], edges: list[GraphEdge]) -> None:
        """Upsert nodes and edges in batches."""
        with self._driver.session(database=self._database) as session:
            self._upsert_nodes(session, nodes)
            self._upsert_edges(session, edges)
            logger.info("Exported %d nodes, %d edges to Neo4j.", len(nodes), len(edges))

    @staticmethod
    def _upsert_nodes(session: Any, nodes: list[GraphNode]) -> None:
        # Group by label set for efficient UNWIND
        by_labels: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            key = ":".join(sorted(node.labels)) or "Node"
            by_labels.setdefault(key, []).append({
                "id": node.id,
                "props": node.properties,
            })

        for label_str, items in by_labels.items():
            labels = ":".join(f"`{l}`" for l in label_str.split(":"))
            query = (
                f"UNWIND $items AS item "
                f"MERGE (n:{labels} {{id: item.id}}) "
                f"SET n += item.props"
            )
            session.run(query, items=items)

    def ensure_vector_index(self, dimensions: int = 1536) -> None:
        """Create vector indexes on Entity and Chunk nodes if they don't exist.

        Requires Neo4j 5.11+.  Logs a warning and continues gracefully
        if the database does not support vector indexes.
        """
        self._ensure_single_vector_index(
            "entity_embeddings", "Entity", "embedding", dimensions,
        )
        self._ensure_single_vector_index(
            "chunk_embeddings", "Chunk", "embedding", dimensions,
        )

    def _ensure_single_vector_index(
        self,
        index_name: str,
        label: str,
        property_name: str,
        dimensions: int,
    ) -> None:
        """Create a single vector index if it doesn't already exist."""
        with self._driver.session(database=self._database) as session:
            try:
                result = session.run(
                    "SHOW INDEXES YIELD name "
                    "WHERE name = $name "
                    "RETURN name",
                    name=index_name,
                )
                if result.single() is not None:
                    return  # index already exists

                session.run(
                    "CALL db.index.vector.createNodeIndex("
                    f"'{index_name}', '{label}', '{property_name}', "
                    "$dimensions, 'cosine')",
                    dimensions=dimensions,
                )
                logger.info(
                    "Created vector index '%s' on :%s.%s (dim=%d).",
                    index_name, label, property_name, dimensions,
                )
            except Exception:
                logger.warning(
                    "Could not create vector index '%s' — "
                    "vector search will not be available.",
                    index_name,
                    exc_info=True,
                )

    @staticmethod
    def _upsert_edges(session: Any, edges: list[GraphEdge]) -> None:
        # Group by relation type for efficient UNWIND
        by_type: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            by_type.setdefault(edge.relation_type, []).append({
                "src": edge.source_id,
                "tgt": edge.target_id,
                "props": edge.properties,
            })

        for rel_type, items in by_type.items():
            query = (
                f"UNWIND $items AS item "
                f"MATCH (a {{id: item.src}}) "
                f"MATCH (b {{id: item.tgt}}) "
                f"MERGE (a)-[r:`{rel_type}`]->(b) "
                f"SET r += item.props"
            )
            session.run(query, items=items)
