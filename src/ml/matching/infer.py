from __future__ import annotations

from typing import Iterable

import numpy as np

from src.core.runtime_compat import patch_multiprocess_resource_tracker

patch_multiprocess_resource_tracker()

from sentence_transformers import SentenceTransformer, util


class ProductMatcher:
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2", device: str | None = None):
        patch_multiprocess_resource_tracker()
        self.model = SentenceTransformer(model_name, device=device)

    def similarity(self, text_a: str, text_b: str) -> float:
        emb = self.model.encode([text_a, text_b], convert_to_tensor=True)
        score = util.cos_sim(emb[0], emb[1]).item()
        return float(score)

    def encode(self, texts: Iterable[str]) -> np.ndarray:
        vectors = self.model.encode(list(texts), convert_to_numpy=True, normalize_embeddings=True)
        return vectors

    def query_to_candidates(self, query: str, candidates: list[str]) -> list[float]:
        vectors = self.encode([query] + candidates)
        q = vectors[0]
        docs = vectors[1:]
        scores = docs @ q
        return scores.tolist()
