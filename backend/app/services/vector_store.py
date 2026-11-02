from functools import lru_cache
from uuid import UUID

from qdrant_client import QdrantClient, models

from ..config import QDRANT_COLLECTION, QDRANT_PATH


@lru_cache(maxsize=1)
def client() -> QdrantClient:
    QDRANT_PATH.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(QDRANT_PATH))


def ensure_collection(vector_size: int) -> None:
    store = client()
    if not store.collection_exists(QDRANT_COLLECTION):
        store.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        )


def upsert_chunks(records: list[dict], vectors: list[list[float]]) -> None:
    if not records:
        return
    ensure_collection(len(vectors[0]))
    client().upsert(
        collection_name=QDRANT_COLLECTION,
        points=[
            models.PointStruct(id=str(UUID(record["id"])), vector=vector, payload=record)
            for record, vector in zip(records, vectors, strict=True)
        ],
        wait=True,
    )


def search(vector: list[float], user_id: str, product_id: str, limit: int) -> list:
    if not client().collection_exists(QDRANT_COLLECTION):
        return []
    result = client().query_points(
        collection_name=QDRANT_COLLECTION,
        query=vector,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
                models.FieldCondition(key="product_id", match=models.MatchValue(value=product_id)),
            ]
        ),
        limit=limit,
        with_payload=True,
    )
    return result.points


def delete_document(document_id: str) -> None:
    if not client().collection_exists(QDRANT_COLLECTION):
        return
    client().delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
            )
        ),
        wait=True,
    )


def delete_product(product_id: str, user_id: str) -> None:
    if not client().collection_exists(QDRANT_COLLECTION):
        return
    client().delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(key="product_id", match=models.MatchValue(value=product_id)),
                    models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
                ]
            )
        ),
        wait=True,
    )
