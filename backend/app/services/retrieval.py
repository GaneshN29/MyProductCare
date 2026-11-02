import re
from collections import defaultdict

from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import RETRIEVAL_CANDIDATES, RERANK_RESULTS
from ..models import Chunk, Document
from .ai_services import embed_texts, rerank
from .vector_store import search


def classify_query(question: str) -> str:
    text = question.lower()
    if any(term in text for term in ("weather", "stock price", "news today", "sports score")):
        return "unsupported"
    if any(term in text for term in ("warranty", "covered", "coverage", "claim", "guarantee")):
        return "warranty"
    if any(term in text for term in ("error", "troubleshoot", "stopped", "not working", "leak", "won't")):
        return "troubleshooting"
    if any(term in text for term in ("clean", "maintenance", "maintain", "service", "filter")):
        return "maintenance"
    if any(term in text for term in ("difference", "compare", "versus", " vs ")):
        return "comparison"
    return "factual"


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def reciprocal_rank_fusion(rankings: list[list[str]], constant: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] += 1 / (constant + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def retrieve(
    db: Session, question: str, product_id: str, user_id: str, query_type: str
) -> list[tuple[Chunk, Document, float]]:
    query_vector = embed_texts([question])[0]
    vector_hits = search(query_vector, user_id, product_id, RETRIEVAL_CANDIDATES)
    vector_ranking = [hit.payload["id"] for hit in vector_hits if hit.payload]

    rows = db.execute(
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(
            Chunk.user_id == user_id,
            Chunk.product_id == product_id,
            Document.status == "ready",
        )
    ).all()
    if not rows:
        return []
    corpus = [_tokens(chunk.text) for chunk, _ in rows]
    scores = BM25Okapi(corpus)
    bm25_scores = scores.get_scores(_tokens(question))
    keyword_ranking = [
        rows[index][0].id
        for index in sorted(range(len(rows)), key=lambda index: bm25_scores[index], reverse=True)[
            :RETRIEVAL_CANDIDATES
        ]
        if bm25_scores[index] > 0
    ]
    fused = reciprocal_rank_fusion([vector_ranking, keyword_ranking])
    fused_ids = [item_id for item_id, _ in fused[:RETRIEVAL_CANDIDATES]]
    row_by_id = {chunk.id: (chunk, document) for chunk, document in rows}

    if query_type == "warranty":
        fused_ids.sort(
            key=lambda item_id: (
                "warranty" not in (row_by_id[item_id][1].document_type or "").lower(),
                "warranty" not in (row_by_id[item_id][0].section or "").lower(),
            )
        )
    candidates = [row_by_id[item_id] for item_id in fused_ids]
    ranked = rerank(question, [chunk.text for chunk, _ in candidates])
    return [
        (candidates[index][0], candidates[index][1], score)
        for index, score in ranked[:RERANK_RESULTS]
    ]
