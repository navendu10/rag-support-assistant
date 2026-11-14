"""Weaviate access: connect, (re)create a stage collection and insert chunks."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict

import weaviate
from pydantic import SecretStr
from weaviate.classes.config import Configure, DataType, Property, Tokenization
from weaviate.classes.data import DataObject
from weaviate.classes.init import Auth
from weaviate.collections import Collection

from rag_assistant.config import Settings, WeaviateSettings
from rag_assistant.ingestion.chunker import Chunk

INSERT_BATCH = 100

# Tokenization decides how keyword (BM25) search and filters see a value:
# WORD splits on anything that is not a letter or digit, so a question containing
# "XR-2200" matches product_code "XR-2200" as "xr" + "2200"; FIELD keeps the whole
# value as one token, which is what exact filters on ids, regions and audiences need.
PROPERTIES = [
    Property(name="text", data_type=DataType.TEXT, tokenization=Tokenization.WORD),
    Property(name="doc_id", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
    Property(name="title", data_type=DataType.TEXT, tokenization=Tokenization.WORD),
    Property(name="chunk_index", data_type=DataType.INT),
    Property(name="audience", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
    Property(name="region", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
    Property(name="doc_type", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
    Property(name="product_code", data_type=DataType.TEXT, tokenization=Tokenization.WORD),
    Property(name="effective_date", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
    Property(name="section", data_type=DataType.TEXT, tokenization=Tokenization.WORD),
    Property(name="page", data_type=DataType.INT),
    Property(name="trust", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
]


class MissingKeyError(RuntimeError):
    """A Weaviate API key the caller needs is not configured."""


def open_client(cfg: WeaviateSettings, api_key: SecretStr | None) -> weaviate.WeaviateClient:
    """Open a Weaviate client authenticated with ``api_key``; the caller must close it.

    ``None`` connects anonymously, which a Weaviate with RBAC (Session 7) refuses.
    """
    return weaviate.connect_to_custom(
        http_host=cfg.host,
        http_port=cfg.http_port,
        http_secure=cfg.secure,
        grpc_host=cfg.host,
        grpc_port=cfg.grpc_port,
        grpc_secure=cfg.secure,
        auth_credentials=None if api_key is None else Auth.api_key(api_key.get_secret_value()),
    )


@contextmanager
def connect(cfg: WeaviateSettings, api_key: SecretStr | None) -> Iterator[weaviate.WeaviateClient]:
    """Open an authenticated Weaviate client and always close it afterwards."""
    client = open_client(cfg, api_key)
    try:
        yield client
    finally:
        client.close()


def admin_key(settings: Settings) -> SecretStr:
    """The admin key: ingestion, setup_access.py and the insecure v0-v4 stages only."""
    if settings.weaviate_admin_key is None:
        raise MissingKeyError(
            "WEAVIATE_ADMIN_KEY is not set. Run: python scripts/setup_access.py --init-admin-key"
        )
    return settings.weaviate_admin_key


def connect_admin(settings: Settings) -> AbstractContextManager[weaviate.WeaviateClient]:
    """``connect`` with the admin key."""
    return connect(settings.weaviate, admin_key(settings))


def recreate_collection(client: weaviate.WeaviateClient, name: str) -> Collection:
    """Drop ``name`` if it exists and create it empty, with self-provided vectors."""
    if client.collections.exists(name):
        client.collections.delete(name)
    return client.collections.create(
        name,
        vector_config=Configure.Vectors.self_provided(),
        properties=PROPERTIES,
    )


def _properties(chunk: Chunk) -> dict[str, object]:
    """Chunk fields as Weaviate properties, leaving out empty (None) values."""
    return {key: value for key, value in asdict(chunk).items() if value is not None}


def insert_chunks(
    collection: Collection, chunks: Sequence[Chunk], vectors: Sequence[list[float]]
) -> int:
    """Insert chunks with their vectors; raise if Weaviate reports any error."""
    if len(chunks) != len(vectors):
        raise ValueError("chunks and vectors must have the same length")
    for start in range(0, len(chunks), INSERT_BATCH):
        objects = [
            DataObject(properties=_properties(chunk), vector=vector)
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
