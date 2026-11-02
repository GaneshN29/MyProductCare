from functools import lru_cache

from sentence_transformers import CrossEncoder, SentenceTransformer

from ..config import EMBEDDING_MODEL, RERANKER_MODEL


@lru_cache(maxsize=1)
def embedding_model() -> SentenceTransformer:
    return SentenceTransformer(EMBEDDING_MODEL)


@lru_cache(maxsize=1)
def reranker_model() -> CrossEncoder:
    return CrossEncoder(RERANKER_MODEL)


def embed_texts(texts: list[str]) -> list[list[float]]:
    vectors = embedding_model().encode(texts, normalize_embeddings=True)
    return vectors.tolist()


def rerank(query: str, passages: list[str]) -> list[tuple[int, float]]:
    scores = reranker_model().predict([(query, passage) for passage in passages])
    return sorted(
        ((index, float(score)) for index, score in enumerate(scores)),
        key=lambda item: item[1],
        reverse=True,
    )
