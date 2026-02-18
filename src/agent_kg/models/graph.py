"""Graph models and Neo4j builder.

The graph uses a **reified relation** model: relations are nodes,
not edges.  This allows relations to carry properties, provenance,
and multiple role-typed connections to entities.

Graph topology (v1)::

    (Document)──EXTRACTED_FROM──►(Relation)──<ROLE>──►(Entity)

Where ``<ROLE>`` is one of: AGENT, THEME, TRIGGER, PURPOSE, REASON,
INSTRUMENT, BENEFICIARY, CONTEXT, ORIGIN, DESTINATION, TIME, LOCATION.

.. note:: **Planned for v2** (post iteration round 1):

   Introduce a **Mention** layer to separate surface forms from
   canonical entities, and an **Assertion** layer to separate the
   extraction event from the semantic fact::

       (Document)──HAS_MENTION──►(Mention)──REFERS_TO──►(Entity)
       (Document)──HAS_ASSERTION──►(Assertion)──ABOUT──►(Relation)
       (Relation)──<ROLE>──►(Entity)

   This enables: fine-grained provenance, non-destructive entity
   resolution (keep all surface forms), multi-document corroboration,
   contradiction detection, and model-version tracking.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_kg.models.base import Entity, Relation

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


# =====================================================================
# ID generation
# =====================================================================

def generate_id(data: dict[str, Any]) -> str:
    """Deterministic SHA-256 content hash → stable node ID."""
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# =====================================================================
# Graph element generation
# =====================================================================

def _entity_node(entity: Entity, extra_props: dict[str, Any] | None = None) -> GraphNode:
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

    node_id = generate_id({"label": entity.label, "name": entity.name})
    return GraphNode(id=node_id, labels=[entity.label], properties=props)


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
        "quote": relation.provenance.quote,
        "document_id": relation.provenance.document_id,
    }
    if relation.metadata:
        for k, v in relation.metadata.items():
            props[f"_meta_{k}"] = v

    node_id = generate_id({
        "verb": relation.relation_type.verb,
        "target": relation.relation_type.target_category,
        "description": relation.description,
        "doc": relation.provenance.document_id,
    })
    labels = [lbl for lbl in (relation.labels or []) if lbl]
    return GraphNode(id=node_id, labels=labels or ["Relation"], properties=props)


def build_graph_elements(
    relations: list[Relation],
    document_id: str,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Convert extracted relations into graph nodes and edges.

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

    for relation in relations:
        # Relation node
        rel_node = _relation_node(relation)
        nodes[rel_node.id] = rel_node

        # Document → Relation
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
                ent_node = _entity_node(entity)
                nodes[ent_node.id] = ent_node
                edges.append(GraphEdge(
                    source_id=rel_node.id,
                    target_id=ent_node.id,
                    relation_type=role_label,
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
