"""FastEmbed wrapper used for both documents and queries."""

from __future__ import annotations

from functools import cached_property

from fastembed import TextEmbedding

from rag_assistant.config import EmbeddingSettings


class Embedder:
    """Embeds text with the configured FastEmbed model (loaded on first use).

    v0 embeds queries exactly like documents: no instruction prefix.
    """

    def __init__(self, cfg: EmbeddingSettings, batch_size: int = 64) -> None:
        """Remember the model settings; the model itself loads lazily."""
        self._cfg = cfg
        self._batch_size = batch_size

    @cached_property
    def _model(self) -> TextEmbedding:
        """The FastEmbed model (downloads on the very first run)."""
        return TextEmbedding(model_name=self._cfg.model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts, checking the vector size matches the config."""
        vectors = [v.tolist() for v in self._model.embed(texts, batch_size=self._batch_size)]
        for vector in vectors:
            if len(vector) != self._cfg.dimension:
                raise ValueError(
                    f"{self._cfg.model_name} returned {len(vector)} dims, "
                    f"config says {self._cfg.dimension}"
                )
        return vectors

    def embed_one(self, text: str) -> list[float]:
        """Embed a single text."""
        return self.embed([text])[0]
