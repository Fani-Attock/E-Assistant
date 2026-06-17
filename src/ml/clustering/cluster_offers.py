import os
import platform

import numpy as np
from sklearn.cluster import AgglomerativeClustering


def cluster_embeddings(
    embeddings: np.ndarray,
    distance_threshold: float = 0.3,
    min_cluster_size: int = 2,
) -> list[int]:
    if len(embeddings) == 0:
        return []
    if len(embeddings) == 1:
        return [0]
    backend = os.getenv("CLUSTER_BACKEND", "auto").strip().lower()
    allow_hdbscan = backend in {"auto", "hdbscan"}
    if platform.system().lower() == "windows" and backend == "auto":
        # hdbscan can emit noisy shutdown exceptions on Windows in some envs.
        allow_hdbscan = False

    if allow_hdbscan:
        try:
            import hdbscan  # type: ignore

            clusterer = hdbscan.HDBSCAN(
                min_cluster_size=max(min_cluster_size, 2),
                metric="euclidean",
                cluster_selection_method="eom",
            )
            labels = clusterer.fit_predict(embeddings)
            # Assign noise points to unique singleton clusters for deterministic output.
            if any(label == -1 for label in labels):
                next_label = int(max(labels)) + 1
                for i, label in enumerate(labels):
                    if label == -1:
                        labels[i] = next_label
                        next_label += 1
            return labels.tolist()
        except Exception:
            pass

    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage="average",
    )
    labels = model.fit_predict(embeddings)
    return labels.tolist()
