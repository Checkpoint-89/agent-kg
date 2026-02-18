"""Pluggable clustering executor.

Two strategies are provided:

1. **AgglomerativeClustering** — cosine distance with a configurable
   threshold.  Deterministic, simple, no data loss.
2. **DimReductionClustering** — PCA → UMAP → HDBSCAN.  More powerful
   on large corpora (100+ items) but non-deterministic and requires
   the ``hdbscan`` extra.

Both implement ``ClusteringStrategy`` and produce the same output:
a mapping from cluster ID to lists of indices into the input array.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import numpy as np
from sklearn.cluster import AgglomerativeClustering as SklearnAgglo
from sklearn.decomposition import PCA
from sklearn.metrics.pairwise import cosine_distances

logger = logging.getLogger(__name__)


# =====================================================================
# Protocol
# =====================================================================

class ClusteringStrategy(ABC):
    """Base class for clustering strategies.

    Subclasses receive pre-computed embedding vectors and return
    cluster assignments.
    """

    @abstractmethod
    def fit(self, embeddings: np.ndarray) -> dict[int, list[int]]:
        """Cluster the embeddings.

        Args:
            embeddings: (N, D) float32 array.

        Returns:
            Mapping from ``cluster_id`` → list of row indices.
        """
        ...


# =====================================================================
# Strategy 1: Agglomerative (default)
# =====================================================================

class AgglomerativeStrategy(ClusteringStrategy):
    """Cosine-distance agglomerative clustering.

    Deterministic and lossless — every item is assigned to a cluster.

    Args:
        distance_threshold: Maximum cosine distance for merging.
            Lower = more clusters, higher precision.
        linkage: Linkage criterion (``"average"`` recommended for cosine).
    """

    def __init__(
        self,
        distance_threshold: float = 0.35,
        linkage: str = "average",
    ) -> None:
        self.distance_threshold = distance_threshold
        self.linkage = linkage

    def fit(self, embeddings: np.ndarray) -> dict[int, list[int]]:
        if len(embeddings) < 2:
            return {0: list(range(len(embeddings)))}

        dist_matrix = cosine_distances(embeddings)

        model = SklearnAgglo(
            n_clusters=None,
            distance_threshold=self.distance_threshold,
            metric="precomputed",
            linkage=self.linkage,
        )
        labels = model.fit_predict(dist_matrix)

        clusters: dict[int, list[int]] = {}
        for idx, label in enumerate(labels):
            clusters.setdefault(int(label), []).append(idx)

        logger.info(
            "Agglomerative clustering: %d items → %d clusters (threshold=%.2f)",
            len(embeddings), len(clusters), self.distance_threshold,
        )
        return clusters


# =====================================================================
# Strategy 2: PCA → UMAP → HDBSCAN
# =====================================================================

class DimReductionStrategy(ClusteringStrategy):
    """PCA → UMAP → HDBSCAN pipeline.

    More powerful on large datasets but non-deterministic.
    Noise points (HDBSCAN label=-1) are reassigned to the nearest
    cluster by centroid distance instead of being discarded.

    Requires ``pip install agent-kg[hdbscan]``.

    Args:
        pca_variance: Fraction of variance to retain in PCA.
        umap_components: Target dimensionality for UMAP.
        umap_neighbors: UMAP ``n_neighbors`` parameter.
        min_cluster_size: HDBSCAN ``min_cluster_size``.
    """

    def __init__(
        self,
        pca_variance: float = 0.75,
        umap_components: int = 15,
        umap_neighbors: int = 15,
        min_cluster_size: int = 3,
    ) -> None:
        self.pca_variance = pca_variance
        self.umap_components = umap_components
        self.umap_neighbors = umap_neighbors
        self.min_cluster_size = min_cluster_size

    def fit(self, embeddings: np.ndarray) -> dict[int, list[int]]:
        try:
            import hdbscan  # type: ignore[import-untyped]
            import umap  # type: ignore[import-untyped]
        except ImportError as e:
            raise ImportError(
                "DimReductionStrategy requires the hdbscan extra: "
                "pip install agent-kg[hdbscan]"
            ) from e

        n_samples = len(embeddings)
        if n_samples < self.min_cluster_size:
            return {0: list(range(n_samples))}

        # 1. PCA
        n_pca = min(n_samples, embeddings.shape[1])
        pca = PCA(n_components=min(self.pca_variance, n_pca))
        reduced = pca.fit_transform(embeddings)
        logger.info("PCA: %d → %d components", embeddings.shape[1], reduced.shape[1])

        # 2. UMAP
        n_umap = min(self.umap_components, reduced.shape[1])
        n_neighbors = min(self.umap_neighbors, n_samples - 1)
        mapper = umap.UMAP(
            n_components=n_umap,
            n_neighbors=max(2, n_neighbors),
            min_dist=0.1,
            metric="euclidean",
        )
        projected = mapper.fit_transform(reduced)

        # 3. HDBSCAN
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=self.min_cluster_size,
            metric="euclidean",
        )
        labels = clusterer.fit_predict(projected)

        # 4. Reassign noise points to nearest cluster centroid
        clusters: dict[int, list[int]] = {}
        noise_indices: list[int] = []
        for idx, label in enumerate(labels):
            if label == -1:
                noise_indices.append(idx)
            else:
                clusters.setdefault(int(label), []).append(idx)

        if noise_indices and clusters:
            centroids = {
                cid: projected[members].mean(axis=0)
                for cid, members in clusters.items()
            }
            for idx in noise_indices:
                point = projected[idx]
                nearest_cid = min(
                    centroids,
                    key=lambda cid: float(np.linalg.norm(point - centroids[cid])),
                )
                clusters[nearest_cid].append(idx)
            logger.info("Reassigned %d noise points to nearest clusters.", len(noise_indices))
        elif noise_indices:
            # All points are noise — put them in cluster 0
            clusters[0] = noise_indices

        logger.info(
            "HDBSCAN clustering: %d items → %d clusters (min_size=%d)",
            n_samples, len(clusters), self.min_cluster_size,
        )
        return clusters


# =====================================================================
# Factory
# =====================================================================

def create_clustering_strategy(
    method: str,
    **kwargs: Any,
) -> ClusteringStrategy:
    """Create a clustering strategy by name.

    Args:
        method: ``"agglomerative"`` or ``"hdbscan"``.
        **kwargs: Forwarded to the strategy constructor.
    """
    strategies: dict[str, type[ClusteringStrategy]] = {
        "agglomerative": AgglomerativeStrategy,
        "hdbscan": DimReductionStrategy,
    }
    cls = strategies.get(method.lower())
    if cls is None:
        raise ValueError(f"Unknown clustering method: {method!r}. Choose from {list(strategies)}.")
    return cls(**kwargs)
