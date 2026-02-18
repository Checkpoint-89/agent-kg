"""
Utility module for uploading graph data to Neo4j.

This module provides functionality to upload nodes and edges to a Neo4j graph database.
"""

from typing import List, Dict, Any, Tuple, cast
import logging
from neo4j import GraphDatabase, Query

logger = logging.getLogger(__name__)

def upload_graph_to_neo4j(
    uri: str, 
    auth: Tuple[str, str], 
    nodes: List[Dict[str, Any]], 
    edges: List[Dict[str, Any]], 
    clear_db: bool = False
) -> None:
    """
    Upload nodes and edges to Neo4j database.
    
    Args:
        uri (str): Neo4j connection URI
        auth (tuple): (username, password) tuple for authentication
        nodes (list): List of node dictionaries with '_id', 'labels', and 'properties'
        edges (list): List of edge dictionaries with 'source', 'target', 'type', and 'properties'
        clear_db (bool, optional): Whether to clear the database before uploading. Defaults to False.
    """
    logger.info(f"Connecting to Neo4j at {uri}")
    with GraphDatabase.driver(uri, auth=auth) as driver:
        with driver.session() as session:
            # Optionally clear the database
            if clear_db:
                logger.info("Clearing database...")
                session.run("MATCH (n) DETACH DELETE n")
                logger.info("Database cleared.")
            
            # Upload nodes
            logger.info(f"Uploading {len(nodes)} nodes...")
            for i, node in enumerate(nodes):
                # Convert list of labels to a string of Neo4j labels
                labels_str = ''.join([f':`{label}`' for label in node['labels']])
                
                # Create node with MERGE to avoid duplicates
                cypher: str = f"""
                MERGE (n {labels_str} {{id: $id}})
                SET n += $properties
                """
                
                session.run(
                    cypher, # type: ignore
                    id=node['id'],
                    properties=node.get('properties', {})
                )
                
                if (i + 1) % 10 == 0 or i == len(nodes) - 1:
                    logger.info(f"Uploaded {i + 1}/{len(nodes)} nodes")
            
            # Upload edges
            logger.info(f"Uploading {len(edges)} edges...")
            for i, edge in enumerate(edges):
                # Create relationship using MATCH to find nodes and MERGE to avoid duplicates
                cypher = f"""
                MATCH (source {{id: $source_id}})
                MATCH (target {{id: $target_id}})
                MERGE (source)-[r:`{edge['type']}`]->(target)
                SET r += $properties
                """
                
                session.run(
                    cypher, # type: ignore
                    source_id=edge['source'],
                    target_id=edge['target'],
                    properties=edge.get('properties', {})
                )
                
                if (i + 1) % 10 == 0 or i == len(edges) - 1:
                    logger.info(f"Uploaded {i + 1}/{len(edges)} edges")
            
            # Count nodes and relationships to verify upload
            node_count = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]
            edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"]
            
            logger.info(f"Upload complete! Database contains {node_count} nodes and {edge_count} relationships")