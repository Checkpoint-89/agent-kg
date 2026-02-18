"""
Module for clustering entities and relations to find stable representations.
"""
import logging
import json
from typing import Any, List, Tuple, Iterator, cast, Coroutine

import numpy as np

import tiktoken
from openai import OpenAI

from sklearn.decomposition import PCA
import umap
from hdbscan import HDBSCAN

from shared.models.graph_base import Relation, Entity
from shared.models.graph_base import RelationTypeClusters, EntityTypeClusters

MAX_TOKENS_TEXT_EMBEDDING_3_SMALL = 8192

# logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_token_batches(
    texts: List[str], encoding: tiktoken.Encoding, max_tokens: int
) -> Iterator[List[str]]:
    """
    Groups texts into batches that do not exceed a token limit.

    Yields:
        A list of texts representing one batch.
    """
    batch: List[str] = []
    current_tokens = 0
    for text in texts:
        item_tokens = len(encoding.encode(text))

        # If a single item exceeds the limit, it's an error.
        if item_tokens > max_tokens:
            raise ValueError(f"A single item exceeds the token limit of {max_tokens}")

        # If adding the next item would exceed the limit, yield the current batch.
        if batch and current_tokens + item_tokens > max_tokens:
            yield batch
            # Start a new batch with the current item
            batch = [text]
            current_tokens = item_tokens
        else:
            # Otherwise, add the item to the current batch.
            batch.append(text)
            current_tokens += item_tokens
    
    # Yield the final remaining batch after the loop.
    if batch:
        yield batch

        
def compute_embeddings(
        relations: List[Relation],
        model: str = "text-embedding-3-small"
    ) ->  Tuple[List[str], List[List[float]]]:
    """
    Compute embeddings for a list of relations.
    
    Args:
        relations: List of relations to embed
        model: OpenAI embedding model to use
        
    Returns:
        NumPy array of embeddings
    """

    if not relations:
        print("Warning: compute_embeddings received an empty list of relations. Returning empty lists.")
        return [], []
    
    print(f"Computing embeddings using model {model}")
    
    # Extract items to embed
    to_embed: List[str] = []
    for relation in relations:
        if relation.to_embed:
            to_embed.append(relation.to_embed)
        else:
            raise ValueError(f"to_embed is None for relation: {relation}")
    
    # Call OpenAI embeddings API
    print(f"Calling OpenAI API for {len(to_embed)} embeddings")
    
    client = OpenAI()
    encoding = tiktoken.encoding_for_model(model)
    embds: list[list[float]] = []
    
    # --- Logique de batching élégante ---
    # Itérer sur les lots générés, sans gérer les index manuellement.
    for i, batch in enumerate(create_token_batches(to_embed, encoding, MAX_TOKENS_TEXT_EMBEDDING_3_SMALL)):
        print(f"Processing batch #{i+1} with {len(batch)} items...")
        
        response_ = client.embeddings.create(
            model=model,
            input=batch
        )
        embds.extend([e.embedding for e in response_.data])

    # Attach embeddings to original objects
    print("Attaching embeddings to items")
    print(f"Number of relations: {len(relations)}")
    print(f"Number of items to embed: {len(to_embed)}")
    print(f"Number of computed embeddings: {len(embds)}")

    if len(relations) != len(embds):
        raise ValueError(f"Mismatch between number of relations ({len(relations)}) and computed embeddings ({len(embds)}).")

    for relation, embedding_vector in zip(relations, embds):
        if relation.metadata is None:
            relation.metadata = {}
        if embedding_vector is None: #type: ignore
            raise ValueError(f"Embedding vector is None for relation: {relation}")
        relation.metadata['embedding'] = embedding_vector

    return to_embed, embds


def cluster_embeddings(np_embds: np.ndarray[Any, Any]) -> HDBSCAN:

    np_embds_ = np_embds.copy()

    # 1) PCA
    pca = PCA(n_components=0.75)
    np_embds_ = cast(
        np.ndarray[Any, Any],
        pca.fit_transform(np_embds_)
    )
    print("Cumulative explained variance: ", pca.explained_variance_ratio_.sum())
    explained_variance_ratio = cast(
        np.ndarray[Any, Any],
        pca.explained_variance_ratio_,
    )
    print("Number of PCA components: ", len(explained_variance_ratio))

    # 2) UMAP
    umap_reducer = umap.UMAP(
        n_components=min(15, len(explained_variance_ratio)),
        n_neighbors=15,  # Valeur par défaut: 15, plus petit = structure plus locale
        min_dist=0.1, # Valeur par défaut: 0.1, plus petit = clusters plus serrés
        #random_state=42,
    )
    np_embds_ = cast(
        np.ndarray[Any, Any],
        umap_reducer.fit_transform(np_embds_)
    )
    print("UMAP embedding shape: ", np_embds_.shape)

    # 3) HDBSCAN clustering
    min_cluster_size = 3 # min nb of points in a cluster
    min_samples = None  # min number of neighbors for a point to be considered as a core point; None means it will be set to min_cluster_size
    cluster_selection_epsilon = 0.0
    clusterer = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_epsilon=cluster_selection_epsilon,
        metric='euclidean',
    )
    clusterer.fit(np_embds_)

    return clusterer

def get_artefacts_clusters(
        artefacts: List[Relation] | List[Relation],
        embds_model: str = "text-embedding-3-small"
    ) -> dict[int, dict[str, Any]]:

    # Compute embeddings
    to_embed, embds = compute_embeddings(artefacts, model=embds_model)

    # Cluster embeddings
    np_embds: np.ndarray[Any, np.dtype[np.float64]] = np.array(embds, dtype=np.float64)
    clusterer = cluster_embeddings(np_embds)
    labels = cast(np.ndarray[Any,Any], clusterer.labels_)

    # Print stats
    unique_labels = set(labels) 
    n_clusters = len(unique_labels) - (1 if -1 in unique_labels else 0)  
    n_noise = np.sum(labels == -1)
    print(f"Number of clusters found: {n_clusters}")
    print(f"Number of data points: {len(labels)}")
    print(f"Number of noise points: {n_noise}")

    # Build the cluster dictionary
    clusters: dict[int, dict[str, Any]] = {
        int(label): {"to_embed": [], "embds": []}
        for label in unique_labels if label != -1
    }

    # Loop through the data to populate the clusters
    for i, label_int in enumerate(labels):
        # Skip noise points
        if label_int == -1:
            continue
        
        label = int(label_int)
        clusters[label]["to_embed"].append(to_embed[i])
        clusters[label]["embds"].append(np_embds[i])

    return clusters

from instructor import AsyncInstructor
import asyncio
import random

async def sub_cluster(
        clusters: dict[int, dict[str, Any]],
        client: AsyncInstructor,
        llm_model: str = 'gpt-4o-mini',
        max_examples_per_prompt: int = 100,
        type: str = 'relation'  # 'relation' or 'entity'
) -> list[RelationTypeClusters]|list[EntityTypeClusters]:

    tasks: list[Coroutine[Any, Any, RelationTypeClusters ]] | list[Coroutine[Any, Any, EntityTypeClusters ]] = []
    for i, (label, cluster_data) in enumerate(clusters.items()):

        examples = cluster_data['to_embed']
        print(f"Preparing task for cluster {label} ({i+1}/{len(clusters)}), which has {len(examples)} examples.")

        # Safeguard against excessively large prompts
        if len(examples) > max_examples_per_prompt:
            print(f"Warning: Cluster {label} has too many examples. Sampling {max_examples_per_prompt} of them.")
            examples = random.sample(examples, max_examples_per_prompt)

        examples_json = json.dumps(examples, indent=2)

        prompt = f"""
        Voici une série de points métiers issus du même cluster de classification automatique n° {label}:
        ##########################
        {examples_json}
        ##########################
        Ta tâche est de définir les sous clusters présents dans ce cluster, et de les décrire selon le modèle fourni.
        """

        # Create a coroutine for the API call.
        task = client.chat.completions.create(
            model=llm_model,
            messages=[
                {
                    "role": "system",
                    "content": prompt,
                },
            ],
            temperature=0.0,
            max_retries=2,
            response_model=RelationTypeClusters if type == 'relation' else EntityTypeClusters,
            timeout=60,
        )
        tasks.append(task)

    # Now, run all the prepared tasks concurrently.
    print(f"\nExecuting {len(tasks)} sub-clustering tasks in parallel...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results, filtering out any that may have failed
    artefacts_type_clusters: list[RelationTypeClusters] | list[EntityTypeClusters] = []
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            print(f"Error processing task for cluster #{i+1}: {res}")
        elif type == 'relation':
            artefacts_type_clusters.append(cast(RelationTypeClusters, res))
        else:
            artefacts_type_clusters.append(cast(EntityTypeClusters, res))

    return artefacts_type_clusters

async def synth_clusters(
        relation_type_clusters: list[RelationTypeClusters],
        client: AsyncInstructor,
        llm_model: str = 'gpt-4o-mini',
        max_examples_for_synthesis: int = 200,
)->RelationTypeClusters:
    
    exemples: list[str] = []
    for type_cluster in relation_type_clusters:

        for cluster in type_cluster.clusters:
            for relation_type in cluster.relation_types:
                label = (
                "*****************************************\n"
                f"Cas métier: {cluster.name}. "
                f"Sous cas métier: {relation_type.verb} "
                f"{relation_type.target_category if relation_type.target_category else ''}: "
                f"{relation_type.definition}"
            )
                exemples.append(label)
    
    if len(exemples) > max_examples_for_synthesis:
        print(f"Warning: Too many examples for synthesis ({len(exemples)}). "
              f"Sampling {max_examples_for_synthesis} to avoid context window overflow.")
        exemples = random.sample(exemples, max_examples_for_synthesis)

    prompt_content = "\n---\n".join(exemples)
    prompt = f"""
    Voici une série de relations issus de différents clusters:
    ##########################
    {prompt_content}
    ##########################
    Ta tâche est d'identifier des clusters de cas métiers pour définir les processus de l'entreprise qui seront utilisés dans un graphe de connaissance. Adapte l'input pour que chaque cluster reflète une étape spécifique et bien délimitée de la relation client. Fusionne les cas sémantiquement similaires.
    """

    response = await client.chat.completions.create(
        model=llm_model,
        messages=[
            {
                "role": "system",
                "content": prompt,
            },
        ],
        temperature=0.0 if llm_model != 'o3' else 1.0,
        max_retries=2,
        response_model=RelationTypeClusters,
        timeout=300,
    )

    return response