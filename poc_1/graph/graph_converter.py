\
import hashlib
import json
from typing import List, Tuple, Dict, Literal,Type, Any
from pydantic import BaseModel, Field, create_model
from shared.models.graph_base import Entity, RelationTypeClusters
from shared.models.event_base import EventBaseModel

def generate_biz_relation_type_models(
    relation_type_clusters: RelationTypeClusters
) -> list[Type[BaseModel]]:
    """
    Generates a list of dynamic Pydantic models based on the final synthesized clusters.
    Each model represents a specific business relation type (BizRelationType).
    """
    biz_relation_type_models: list[Type[BaseModel]] = []
    for cluster in relation_type_clusters.clusters:
        for relation_type in cluster.relation_types:
            # Name of the class
            label = relation_type.label
            if not label:
                raise ValueError("Relation type must have a label.")

            # Get all fields from the relation type
            data: dict[str, Any] = relation_type.model_dump()

            # Enrich data with cluster information
            data['cluster_name'] = cluster.name
            data['cluster_definition'] = cluster.definition

            # Start with special arguments for create_model
            model_kwargs: dict[str, Any] = {'__base__': BaseModel}

            # Dynamic buildging of annotation + Field(...)
            fields: dict[str, Any] = {}
            for field_name, value in data.items():
                annotation = Literal[value]
                fields[field_name] = (
                    annotation,
                    Field(
                        default=value,
                        description=f"Valeur fixe provenant de l’instance : {value!r}"
                    )
                )

            # Add the dynamic fields to the keyword arguments
            model_kwargs.update(fields)

            # Génère le modèle
            M = create_model(
                label,
                **model_kwargs,
            )
            biz_relation_type_models.append(M)

    return biz_relation_type_models

def generate_py(
            relation_type_clusters: RelationTypeClusters,
            save_to_file: bool = False,
            file_path: str = "shared/models/biz_relation_types.py",
        ) -> str:
    
    # Init
    biz_rel_type_models:list[Type[BaseModel]] = generate_biz_relation_type_models(relation_type_clusters)
    biz_rel_type_dict = {b.__name__: b for b in biz_rel_type_models}
    rel_registry: list[str] = []
    cluster_registry: list[str] = []

    # Imports
    lines = [
        "from pydantic import BaseModel, Field",
        "from typing import Literal, Type, Union",
        "",
    ]
    
    # Business relation types
    for model_name, model in biz_rel_type_dict.items():
        schema = model.model_fields
        lines.append(f"class {model_name}(BaseModel):")
        for fname, field in schema.items():
            if fname == "cluster_definition":
                # Skip cluster_definition field
                continue
            lit = repr(field.default)
            lines.append(f"    {fname}: Literal[{lit}] = Field(default={lit})")
        lines.append("")
        rel_registry.append(model_name)

    # Union of all relation types
    lines.append("")
    lines.append("UNION_BIZ_RELATION_TYPES_MODELS = Union[")
    lines.extend(f"    {identifier}," for identifier in rel_registry)
    lines.append("]")

    # Business clusters
    lines.append("")
    for model_name, model in biz_rel_type_dict.items():
        inst = model()

        identifier = getattr(inst, "cluster_name", "Cluster name not set")
        if identifier  == "Cluster name not set":
            raise ValueError(f"Model {model_name} does not have a cluster_name attribute.")
        
        definition = getattr(inst, "cluster_definition", "No definition provided")

        if identifier not in cluster_registry:
            lines.append(f"class {identifier}(BaseModel):")
            lines.append(f"    name: Literal['{identifier}'] = Field(default='{identifier}')")
            lines.append(f"    definition: Literal[{repr(definition)}] = Field(default={repr(definition)})")
            lines.append("")
            cluster_registry.append(f"{identifier}")

    # Union of all clusters
    lines.append("")
    lines.append("UNION_CLUSTERS = Union[")
    lines.extend(f"    {identifier}," for identifier in cluster_registry)
    lines.append("]")

    # Dict - cluster model: relation types of that cluster
    lines.append("")
    lines.append("DICT_CLUSTERS: dict[str, list[Type[BaseModel]]] = {")
    for identifier in cluster_registry:
        # 1. Get the names of the models that belong to the current cluster
        model_names_for_cluster = [
            model.__name__ 
            for model in biz_rel_type_dict.values() 
            if getattr(model(), "cluster_name", None) == identifier
        ]
        # 2. Format the list of names into a string like "[MODEL_A, MODEL_B]"
        models_list_str = f"[{', '.join(model_names_for_cluster)}]"
        
        # 3. Append the correctly formatted line
        lines.append(f'    "{identifier}": {models_list_str},')
    lines.append("}")

    file_str = "\n".join(lines)

    if save_to_file == True:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(file_str)

    return file_str

def generate_unique_id(item: BaseModel) -> str:
    """Generates a unique ID for an item based on its content."""
    to_hash = json.dumps(item.model_dump(exclude_none=True), sort_keys=True)
    return hashlib.sha256(to_hash.encode()).hexdigest()


def generate_graph_elements(event_dict: dict[str, EventBaseModel]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Converts an event_dict into lists of nodes and edges 
    suitable for a graph database insertion.

    Returns:
        Tuple[List[Dict], List[Dict]]: A tuple containing a list of node dictionaries 
                                       and a list of edge dictionaries.
    """
    nodes: Dict[str,Dict[str,Any]] = {}
    edges: List[Dict[str,Any]] = []

    def _process_entity(entity: Entity, metadata: dict[str, Any] | None) -> str:
        entity_id = generate_unique_id(entity)
        properties = {}
        if metadata is not None:
            properties.update(metadata)
        properties.update({'_xent_name': entity.name})
        if entity_id not in nodes:
            if entity.properties:
                for prop in entity.properties:
                    properties.update({
                        "prop_" + prop.key: prop.value
                    })
            nodes[entity_id] = {
                'id': entity_id,
                'labels': ["_" + entity.label],
                'properties': properties
            }
        return entity_id

    for event_id, event in event_dict.items():

        # If no relations, skip the event
        if event.processed is None or '_xrel_relations' not in event.processed:
            continue 

        # Get metadata from event
        if event.event_id in nodes or event.event_id is None:
            continue  # Skip if event_id already processed or not set

        # Create a node for the event
        nodes[event.event_id] = {
            'id': event.event_id,
            'labels': ['Event',],
            'properties': event.metadata,
        }

        # Process the relations
        for relation in event.processed['_xrel_relations']:

            # Generate a unique ID for the relation
            relation_id = generate_unique_id(relation)
            if relation_id in nodes:
                continue

            # Create a node and edge for the relation (reifying the relation)
            relation_properties: dict[str, str] = {
                '_xrel_generic': relation.generic,
                '_xrel_specific': relation.specific,
                '_xrel_contextual': relation.contextual,
                '_xrel_quote': relation.quote,
                '_xrel_contextual': relation.contextual,
                '_xrel_verb': relation.relation_type.verb,
                '_xrel_target_object': relation.relation_type.target_object,
                '_xrel_cluster_name': relation.relation_type.cluster_name,
                '_xrel_definition': relation.relation_type.definition,
            }

            if event.metadata is not None:
                relation_properties.update(event.metadata)
            if relation.properties:
                for prop in relation.properties:
                    relation_properties.update({
                        "prop_" + prop.key: prop.value
                    })

            nodes[relation_id] = {
            'id': relation_id,
            'labels': relation.labels,
            'properties': relation_properties
            }

            edges.append({
                'source': relation_id,
                'target': event_id,
                'type': 'EXTRACTED_FROM',
                'properties': {}
            })

            # Process entities and create edges to the relation node
            entity_1_id = _process_entity(relation.entity_1, event.metadata)
            edges.append({
                'source': entity_1_id,
                'target': relation_id,
                'type': 'SUBJECT_TO',
                'properties': {}
            })

            entity_2_id = _process_entity(relation.entity_2, event.metadata)
            edges.append({
                'source': entity_2_id,
                'target': relation_id,
                'type': 'OBJECT_OF',
                'properties': {}
            })
            
    return list(nodes.values()), edges