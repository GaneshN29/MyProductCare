from backend.app.services.document_processor import ExtractedPage, chunk_pages, clean_text
from backend.app.services.rag import answer_question, source_excerpt
from backend.app.models import Chunk, Document
from backend.app.services.retrieval import classify_query, reciprocal_rank_fusion


def test_text_cleaning_normalizes_whitespace():
    assert clean_text("A\r\n\r\n\r\nB\x00") == "A\n\nB"


def test_chunking_preserves_page_and_sections():
    chunks = chunk_pages(
        [ExtractedPage(7, "# TROUBLESHOOTING\n\n4C means check the water supply.")]
    )
    assert len(chunks) == 1
    assert chunks[0].page_number == 7
    assert chunks[0].section == "TROUBLESHOOTING"
    assert "4C" in chunks[0].text


def test_query_classification_routes_warranty_and_unsupported_queries():
    assert classify_query("Is this repair covered by warranty?") == "warranty"
    assert classify_query("What does error 4C mean?") == "troubleshooting"
    assert classify_query("What is the weather today?") == "unsupported"


def test_reciprocal_rank_fusion_combines_and_deduplicates_results():
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    assert [item_id for item_id, _score in fused] == ["b", "a", "c"]


def test_source_excerpt_centers_an_error_code_in_a_long_passage():
    passage = "General care instructions. " * 35 + (
        "Error 4C means the washer did not detect adequate water supply. "
    ) + "Other information. " * 35
    excerpt = source_excerpt("What does error code 4C mean?", passage, limit=180)
    assert "4C means the washer" in excerpt
    assert len(excerpt) <= 182


def test_answers_reject_low_relevance_chunks_and_cite_the_matching_passage(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    document = Document(
        id="document-1",
        user_id="user-1",
        product_id="product-1",
        filename="manual.txt",
        document_type="user_manual",
        status="ready",
    )
    irrelevant = Chunk(
        id="chunk-1",
        user_id="user-1",
        product_id="product-1",
        document_id="document-1",
        chunk_index=0,
        page_number=1,
        section="Specifications",
        text="Maximum capacity is 8 kg.",
    )
    relevant = Chunk(
        id="chunk-2",
        user_id="user-1",
        product_id="product-1",
        document_id="document-1",
        chunk_index=1,
        page_number=2,
        section="Troubleshooting",
        text="Error 4C indicates the washer did not detect an adequate water supply.",
    )

    answer, citations, insufficient = answer_question(
        "What does error code 4C mean?",
        [(irrelevant, document, 0.0), (relevant, document, 0.931)],
    )

    assert not insufficient
    assert "Error 4C" in answer
    assert len(citations) == 1
    assert citations[0].page_number == 2
    assert "Error 4C" in citations[0].excerpt


def test_health_reports_whether_answer_generation_is_configured(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app.main import app

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {
            "status": "ok",
            "generation_available": False,
        }

    monkeypatch.setenv("OPENAI_API_KEY", "local-test-key")
    with TestClient(app) as client:
        assert client.get("/api/health").json()["generation_available"] is True
