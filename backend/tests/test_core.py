from datetime import date

from backend.app.services.document_processor import ExtractedPage, chunk_pages, clean_text
from backend.app.services.rag import answer_question, source_excerpt
from backend.app.models import Chunk, Document
from backend.app.services.retrieval import classify_query, reciprocal_rank_fusion
from backend.app.services.warranty import suggest_warranty_expiry


def test_warranty_expiry_is_suggested_from_explicit_dates():
    assert suggest_warranty_expiry(
        "Warranty expires on 2027-08-31.", None
    ).isoformat() == "2027-08-31"
    assert suggest_warranty_expiry(
        "Warranty valid until December 31, 2027.", None
    ).isoformat() == "2027-12-31"


def test_warranty_duration_requires_purchase_anchor():
    text = "This warranty provides 2 years of coverage from the date of purchase."
    assert suggest_warranty_expiry(text, None) is None
    assert suggest_warranty_expiry(text, date(2024, 2, 29)).isoformat() == "2026-02-28"


def test_ambiguous_or_unrelated_dates_are_not_suggested():
    assert suggest_warranty_expiry("Warranty expires on 03/04/2027.", None) is None
    assert suggest_warranty_expiry("The product was purchased on 2025-01-01.", None) is None


def test_existing_database_gets_warranty_columns(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from backend.app.database import ensure_schema_compatibility

    test_engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with test_engine.begin() as connection:
        connection.execute(text("CREATE TABLE products (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE documents (id VARCHAR(36) PRIMARY KEY)"))

    ensure_schema_compatibility(test_engine)

    assert "warranty_expiry_date" in {
        column["name"] for column in inspect(test_engine).get_columns("products")
    }
    assert "suggested_warranty_expiry" in {
        column["name"] for column in inspect(test_engine).get_columns("documents")
    }
    test_engine.dispose()


def test_product_api_persists_warranty_expiry_date(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import backend.app.api as api
    from backend.app.database import Base, get_db
    from backend.app.main import app

    test_engine = create_engine(f"sqlite:///{tmp_path / 'warranty-date.db'}")
    Base.metadata.create_all(test_engine)
    test_sessions = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_db():
        with test_sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[api.current_user] = lambda: "test-user"
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/products",
                json={
                    "name": "Test washer",
                    "brand": "Example",
                    "model": "TEST-1",
                    "category": "Washing machine",
                    "purchase_date": "2025-01-15",
                    "warranty_expiry_date": "2027-01-15",
                },
            )
        assert response.status_code == 201
        assert response.json()["warranty_expiry_date"] == "2027-01-15"
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()


def test_warranty_upload_uses_multipart_type_and_suggests_expiry(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import backend.app.api as api
    from backend.app.database import Base, get_db
    from backend.app.main import app
    from backend.app.models import Product

    test_engine = create_engine(f"sqlite:///{tmp_path / 'warranty-upload.db'}")
    Base.metadata.create_all(test_engine)
    test_sessions = sessionmaker(bind=test_engine, expire_on_commit=False)
    with test_sessions() as db:
        db.add(
            Product(
                id="test-product",
                user_id="test-user",
                name="Test washer",
                brand="Example",
                model="TEST-1",
                category="Washing machine",
            )
        )
        db.commit()

    def override_db():
        with test_sessions() as db:
            yield db

    monkeypatch.setattr(api, "embed_texts", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(api, "upsert_chunks", lambda *_args, **_kwargs: None)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[api.current_user] = lambda: "test-user"
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/products/test-product/documents",
                data={"document_type": "warranty"},
                files={
                    "file": (
                        "warranty.txt",
                        "Warranty expires on 2027-08-31.",
                        "text/plain",
                    )
                },
            )
        assert response.status_code == 201
        assert response.json()["document_type"] == "warranty"
        assert response.json()["suggested_warranty_expiry"] == "2027-08-31"
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()


def test_account_registration_login_and_logout(tmp_path):
    from datetime import datetime, timedelta, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.app.database import Base, get_db
    from backend.app.main import app
    from backend.app.models import AuthSession, User
    from backend.app.services.auth import hash_token

    test_engine = create_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(test_engine)
    test_sessions = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_db():
        with test_sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/auth/register",
                json={"email": " Person@Example.com ", "password": "correct horse battery"},
            )
            assert registered.status_code == 201
            registration = registered.json()
            assert registration["user"]["email"] == "person@example.com"
            assert registration["token_type"] == "bearer"

            with test_sessions() as db:
                user = db.get(User, registration["user"]["id"])
                assert user is not None
                assert user.password_hash != "correct horse battery"
                registered_session = db.query(AuthSession).filter_by(user_id=user.id).one()
                assert registered_session.token_hash != registration["access_token"]

            duplicate = client.post(
                "/api/auth/register",
                json={"email": "person@example.com", "password": "another safe password"},
            )
            assert duplicate.status_code == 409

            bad_login = client.post(
                "/api/auth/login",
                json={"email": "person@example.com", "password": "wrong password"},
            )
            assert bad_login.status_code == 401

            login = client.post(
                "/api/auth/login",
                json={"email": "PERSON@example.com", "password": "correct horse battery"},
            )
            assert login.status_code == 200
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            assert client.get("/api/auth/me", headers=headers).json()["email"] == "person@example.com"
            assert client.post("/api/auth/logout", headers=headers).status_code == 204
            assert client.get("/api/auth/me", headers=headers).status_code == 401
            with test_sessions() as db:
                db.add(
                    AuthSession(
                        user_id=registration["user"]["id"],
                        token_hash=hash_token("expired-session"),
                        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                    )
                )
                db.commit()
            assert client.get(
                "/api/auth/me",
                headers={"Authorization": "Bearer expired-session"},
            ).status_code == 401
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()


def test_users_receive_isolated_product_libraries(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.app.database import Base, get_db
    from backend.app.main import app

    test_engine = create_engine(f"sqlite:///{tmp_path / 'account-isolation.db'}")
    Base.metadata.create_all(test_engine)
    test_sessions = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_db():
        with test_sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            tokens = []
            for email in ("one@example.com", "two@example.com"):
                response = client.post(
                    "/api/auth/register",
                    json={"email": email, "password": "safe password 123"},
                )
                assert response.status_code == 201
                tokens.append(response.json()["access_token"])

            first_headers = {"Authorization": f"Bearer {tokens[0]}"}
            second_headers = {"Authorization": f"Bearer {tokens[1]}"}
            created = client.post(
                "/api/products",
                headers=first_headers,
                json={
                    "name": "First user's washer",
                    "brand": "Example",
                    "model": "W1",
                    "category": "Washer",
                },
            )
            assert created.status_code == 201
            assert len(client.get("/api/products", headers=first_headers).json()) == 1
            assert client.get("/api/products", headers=second_headers).json() == []
            assert client.get(
                f"/api/products/{created.json()['id']}", headers=second_headers
            ).status_code == 404
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()


def test_first_account_keeps_existing_single_token_library(monkeypatch, tmp_path):
    import hashlib

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.app.database import Base, get_db
    from backend.app.main import app
    from backend.app.models import Product

    legacy_token = "old-local-token-for-migration"
    legacy_user_id = hashlib.sha256(legacy_token.encode()).hexdigest()
    monkeypatch.setenv("APP_API_TOKEN", legacy_token)
    test_engine = create_engine(f"sqlite:///{tmp_path / 'legacy-owner.db'}")
    Base.metadata.create_all(test_engine)
    test_sessions = sessionmaker(bind=test_engine, expire_on_commit=False)
    with test_sessions() as db:
        db.add(
            Product(
                id="legacy-product",
                user_id=legacy_user_id,
                name="Existing washer",
                brand="Example",
                model="W1",
                category="Washer",
            )
        )
        db.commit()

    def override_db():
        with test_sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            registered = client.post(
                "/api/auth/register",
                json={"email": "owner@example.com", "password": "safe password 123"},
            )
            assert registered.status_code == 201
            assert registered.json()["user"]["id"] == legacy_user_id
            products = client.get(
                "/api/products",
                headers={"Authorization": f"Bearer {registered.json()['access_token']}"},
            )
            assert [product["id"] for product in products.json()] == ["legacy-product"]
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()


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
    assert classify_query("Hi!") == "greeting"


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
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
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

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {
            "status": "ok",
            "llm_provider": "openai",
            "generation_available": False,
        }

    monkeypatch.setenv("OPENAI_API_KEY", "local-test-key")
    with TestClient(app) as client:
        assert client.get("/api/health").json()["generation_available"] is True
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        response = client.get("/api/health").json()
        assert response == {
            "status": "ok",
            "llm_provider": "gemini",
            "generation_available": False,
        }


def test_gemini_provider_generates_and_validates_citations(monkeypatch):
    from types import SimpleNamespace

    from google import genai

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "local-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls = {}
    client_options = {}

    def generate_content(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(text="Error 4C means check water supply [1]. [9]")

    monkeypatch.setattr(
        genai,
        "Client",
        lambda **kwargs: (
            client_options.update(kwargs)
            or SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
        ),
    )
    document = Document(
        id="document-1",
        user_id="user-1",
        product_id="product-1",
        filename="manual.txt",
        document_type="user_manual",
        status="ready",
    )
    chunk = Chunk(
        id="chunk-1",
        user_id="user-1",
        product_id="product-1",
        document_id="document-1",
        chunk_index=0,
        page_number=2,
        section="Troubleshooting",
        text="Error 4C indicates the washer did not detect an adequate water supply.",
    )

    answer, citations, insufficient = answer_question(
        "What does error code 4C mean?", [(chunk, document, 0.931)]
    )

    assert not insufficient
    assert "Error 4C means check water supply [1]." in answer
    assert "[9]" not in answer
    assert len(citations) == 1
    assert calls["model"] == "gemini-3.5-flash-lite"
    assert calls["config"].temperature == 0
    assert client_options["http_options"].timeout == 30_000


def test_gemini_timeout_returns_a_clear_error(monkeypatch):
    from types import SimpleNamespace

    import httpx

    from google import genai

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "local-test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def generate_content(**_kwargs):
        raise httpx.ReadTimeout("request timed out")

    monkeypatch.setattr(
        genai,
        "Client",
        lambda **_kwargs: SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content)
        ),
    )
    document = Document(
        id="document-1",
        user_id="user-1",
        product_id="product-1",
        filename="manual.txt",
        document_type="user_manual",
        status="ready",
    )
    chunk = Chunk(
        id="chunk-1",
        user_id="user-1",
        product_id="product-1",
        document_id="document-1",
        chunk_index=0,
        page_number=2,
        section="Troubleshooting",
        text="Error 4C indicates the washer did not detect an adequate water supply.",
    )

    try:
        answer_question(
            "What does error code 4C mean?", [(chunk, document, 0.931)]
        )
    except RuntimeError as exc:
        assert "timed out after 30 seconds" in str(exc)
    else:
        raise AssertionError("Gemini timeout was not reported.")


def test_chat_returns_clear_error_when_openai_quota_is_exhausted(monkeypatch, tmp_path):
    import httpx
    from fastapi.testclient import TestClient
    from openai import RateLimitError
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import backend.app.api as api
    from backend.app.database import Base, get_db
    from backend.app.main import app
    from backend.app.models import Product

    test_engine = create_engine(f"sqlite:///{tmp_path / 'quota-test.db'}")
    Base.metadata.create_all(test_engine)
    test_sessions = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_sessions() as db:
        product = Product(
            id="test-product",
            user_id="test-user",
            name="Test washer",
            brand="Example",
            model="TEST-1",
            category="Washing machine",
        )
        db.add(product)
        db.commit()

    def override_db():
        with test_sessions() as db:
            yield db

    def raise_quota_error(*_args, **_kwargs):
        response = httpx.Response(
            429,
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
        )
        raise RateLimitError(
            "quota exhausted",
            response=response,
            body={"error": {"code": "credit_balance_exhausted"}},
        )

    monkeypatch.setattr(api, "retrieve", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(api, "answer_question", raise_quota_error)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[api.current_user] = lambda: "test-user"
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"product_id": "test-product", "question": "What is its capacity?"},
            )
        assert response.status_code == 503
        assert "credits" in response.json()["detail"].lower()
        assert "OpenAI" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()
