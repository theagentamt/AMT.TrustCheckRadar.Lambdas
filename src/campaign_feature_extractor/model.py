from __future__ import annotations

import math


class LocalSentenceEncoder:
    """Loads only model files baked into the immutable container image."""

    def __init__(self, model_path: str):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_path, local_files_only=True, device="cpu")

    def encode(self, text: str) -> list[float]:
        vector = self._model.encode(
            [text],
            normalize_embeddings=True,
            show_progress_bar=False,
        )[0]
        result = [float(value) for value in vector]
        if not 1 <= len(result) <= 384 or not all(math.isfinite(value) for value in result):
            raise ValueError("Encoder returned an invalid vector")
        return [round(max(-1.0, min(1.0, value)), 7) for value in result]
