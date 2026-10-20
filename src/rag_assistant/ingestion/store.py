"""Weaviate access: connect, (re)create a stage collection and insert chunks."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict

import weaviate
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.data import DataObject
from weaviate.collections import Collection

from rag_assistant.config import WeaviateSettings
from rag_assistant.ingestion.chunker import Chunk

INSERT_BATCH = 100

PROPERTIES = [
    Property(name="text", data_type=DataType.TEXT),
    Property(name="doc_id", data_type=DataType.TEXT),
    Property(name="title", data_type=DataType.TEXT),
    Property(name="chunk_index", data_type=DataType.INT),
    Property(name="audience", data_type=DataType.TEXT),
    Property(name="region", data_type=DataType.TEXT),
    Property(name="doc_type", data_type=DataType.TEXT),
]


def open_client(cfg: WeaviateSettings) -> weaviate.WeaviateClient:
    """Open a Weaviate client; the caller must close it."""
    return weaviate.connect_to_custom(
        http_host=cfg.host,
        http_port=cfg.http_port,
        http_secure=cfg.secure,
        grpc_host=cfg.host,
        grpc_port=cfg.grpc_port,
        grpc_secure=cfg.secure,
    )


@contextmanager
def connect(cfg: WeaviateSettings) -> Iterator[weaviate.WeaviateClient]:
    """Open a Weaviate client and always close it afterwards."""
    client = open_client(cfg)
    try:
        yield client
    finally:
        client.close()


def recreate_collection(client: weaviate.WeaviateClient, name: str) -> Collection:
    """Drop ``name`` if it exists and create it empty, with self-provided vectors."""
    if client.collections.exists(name):
        client.collections.delete(name)
    return client.collections.create(
        name,
        vector_config=Configure.Vectors.self_provided(),
        properties=PROPERTIES,
    )


def insert_chunks(
    collection: Collection, chunks: Sequence[Chunk], vectors: Sequence[list[float]]
) -> int:
    """Insert chunks with their vectors; raise if Weaviate reports any error."""
    if len(chunks) != len(vectors):
        raise ValueError("chunks and vectors must have the same length")
    for start in range(0, len(chunks), INSERT_BATCH):
        objects = [
            DataObject(properties=asdict(chunk), vector=vector)
            for chunk, vector in zip(
                chunks[start : start + INSERT_BATCH],
                vectors[start : start + INSERT_BATCH],
                strict=True,
            )
        ]
        result = collection.data.insert_many(objects)
        if result.has_errors:
            first = next(iter(result.errors.values()))
            raise RuntimeError(f"Weaviate insert failed: {first.message}")
    return len(chunks)
