import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from openai import APIError as OpenAIAPIError
from openai import RateLimitError as OpenAIRateLimitError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import LLM_PROVIDER, MAX_UPLOAD_BYTES
from .database import get_db
from .models import Chunk, Conversation, Document, Message, Product, User
from .schemas import (
    ChatRequest,
    ChatResponse,
    Citation,
    DocumentOut,
    MessageOut,
    ProductCreate,
    ProductOut,
    SessionOut,
    UserCredentials,
    UserOut,
)
from .services.auth import (
    DUMMY_PASSWORD_HASH,
    authenticate_session,
    create_session,
    hash_token,
    password_hash,
    revoke_session,
)
from .services.ai_services import embed_texts
from .services.document_processor import chunk_pages, extract_pages
from .services.rag import INSUFFICIENT_MESSAGE, answer_question
from .services.retrieval import classify_query, retrieve
from .services.vector_store import delete_document, delete_product, upsert_chunks
from .services.warranty import suggest_warranty_expiry

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)
ALLOWED_EXTENSIONS = {"pdf", "txt", "md", "markdown"}


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> str:
    if credentials is None:
        raise HTTPException(401, "Sign in to access your product library.")
    user = authenticate_session(db, credentials.credentials)
    if user is None:
        raise HTTPException(401, "Your session is invalid or expired. Please sign in again.")
    return user.id


@router.post("/auth/register", response_model=SessionOut, status_code=201)
def register(payload: UserCredentials, db: Session = Depends(get_db)) -> SessionOut:
    if db.scalar(select(User.id).where(User.email == payload.email)):
        raise HTTPException(409, "An account with this email already exists.")

    user_id = str(uuid4())
    has_users = db.scalar(select(User.id).limit(1)) is not None
    legacy_token = os.getenv("APP_API_TOKEN", "").strip()
    if not has_users and legacy_token:
        legacy_user_id = hash_token(legacy_token)
        if db.scalar(select(Product.id).where(Product.user_id == legacy_user_id).limit(1)):
            user_id = legacy_user_id

    user = User(
        id=user_id,
        email=payload.email,
        password_hash=password_hash.hash(payload.password),
    )
    db.add(user)
    token = create_session(db, user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "An account with this email already exists.") from exc
    db.refresh(user)
    return SessionOut(
        access_token=token,
        user=UserOut(id=user.id, email=user.email, created_at=user.created_at),
    )


@router.post("/auth/login", response_model=SessionOut)
def login(payload: UserCredentials, db: Session = Depends(get_db)) -> SessionOut:
    user = db.scalar(select(User).where(User.email == payload.email))
    password_matches = password_hash.verify(
        payload.password, user.password_hash if user else DUMMY_PASSWORD_HASH
    )
    if user is None or not password_matches:
        raise HTTPException(401, "Email or password is incorrect.")
    token = create_session(db, user)
    db.commit()
    db.refresh(user)
    return SessionOut(
        access_token=token,
        user=UserOut(id=user.id, email=user.email, created_at=user.created_at),
    )


@router.get("/auth/me", response_model=UserOut)
def get_current_account(
    db: Session = Depends(get_db), user_id: str = Depends(current_user)
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(401, "Your account could not be found. Please sign in again.")
    return user


@router.post("/auth/logout", status_code=204)
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
    _user_id: str = Depends(current_user),
) -> None:
    if credentials is not None:
        revoke_session(db, credentials.credentials)
        db.commit()


def owned_product(db: Session, product_id: str, user_id: str) -> Product:
    product = db.scalar(
        select(Product).where(Product.id == product_id, Product.user_id == user_id)
    )
    if product is None:
        raise HTTPException(404, "Product not found.")
    return product


def document_output(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        product_id=document.product_id,
        filename=document.filename,
        document_type=document.document_type,
        status=document.status,
        error_message=document.error_message,
        suggested_warranty_expiry=document.suggested_warranty_expiry,
        chunk_count=len(document.chunks),
        created_at=document.created_at,
    )


@router.get("/health")
def health() -> dict[str, str | bool]:
    provider = os.getenv("LLM_PROVIDER", LLM_PROVIDER).strip().lower()
    api_key_name = {"openai": "OPENAI_API_KEY", "gemini": "GEMINI_API_KEY"}.get(
        provider
    )
    return {
        "status": "ok",
        "llm_provider": provider,
        "generation_available": bool(
            api_key_name and os.getenv(api_key_name, "").strip()
        ),
    }


@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(
    payload: ProductCreate, db: Session = Depends(get_db), user_id: str = Depends(current_user)
) -> Product:
    product = Product(user_id=user_id, **payload.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.get("/products", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db), user_id: str = Depends(current_user)) -> list[Product]:
    return list(
        db.scalars(select(Product).where(Product.user_id == user_id).order_by(Product.created_at.desc()))
    )


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(
    product_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)
) -> Product:
    return owned_product(db, product_id, user_id)


@router.put("/products/{product_id}", response_model=ProductOut)
def update_product(
    product_id: str,
    payload: ProductCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(current_user),
) -> Product:
    product = owned_product(db, product_id, user_id)
    for key, value in payload.model_dump().items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return product


@router.delete("/products/{product_id}", status_code=204)
def remove_product(
    product_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)
) -> None:
    product = owned_product(db, product_id, user_id)
    delete_product(product.id, user_id)
    db.delete(product)
    db.commit()


@router.post("/products/{product_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    product_id: str,
    file: UploadFile = File(...),
    document_type: str = Form(default="user_manual"),
    db: Session = Depends(get_db),
    user_id: str = Depends(current_user),
) -> DocumentOut:
    product = owned_product(db, product_id, user_id)
    filename = Path(file.filename or "upload").name
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Supported formats are PDF, TXT, and Markdown.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")
    if document_type not in {"user_manual", "warranty", "specification", "troubleshooting", "other"}:
        raise HTTPException(422, "Unsupported document type.")

    document = Document(
        id=str(uuid4()),
        user_id=user_id,
        product_id=product.id,
        filename=filename[:255],
        document_type=document_type,
        status="processing",
    )
    db.add(document)
    db.commit()
    try:
        pages = extract_pages(filename, content)
        if document_type == "warranty":
            document.suggested_warranty_expiry = suggest_warranty_expiry(
                "\n".join(page.text for page in pages), product.purchase_date
            )
        chunks = chunk_pages(pages)
        if not chunks:
            raise ValueError("No text chunks could be created from the document.")
        embeddings = embed_texts([chunk.text for chunk in chunks])
        records = [
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "product_id": product.id,
                "document_id": document.id,
                "filename": document.filename,
                "document_type": document.document_type,
                "page_number": chunk.page_number,
                "section": chunk.section,
                "chunk_index": chunk.chunk_index,
            }
            for chunk in chunks
        ]
        upsert_chunks(records, embeddings)
        for record, chunk in zip(records, chunks, strict=True):
            db.add(
                Chunk(
                    id=record["id"],
                    user_id=user_id,
                    product_id=product.id,
                    document_id=document.id,
                    chunk_index=chunk.chunk_index,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    text=chunk.text,
                )
            )
        document.status = "ready"
        document.error_message = None
        db.commit()
    except (ValueError, RuntimeError, OSError) as exc:
        delete_document(document.id)
        db.rollback()
        failed = db.get(Document, document.id)
        if failed:
            failed.status = "failed"
            failed.error_message = str(exc)[:1000]
            db.commit()
        raise HTTPException(422, f"Document processing failed: {exc}") from exc
    db.refresh(document)
    return document_output(document)


@router.get("/products/{product_id}/documents", response_model=list[DocumentOut])
def list_documents(
    product_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)
) -> list[DocumentOut]:
    owned_product(db, product_id, user_id)
    documents = db.scalars(
        select(Document)
        .where(Document.product_id == product_id, Document.user_id == user_id)
        .order_by(Document.created_at.desc())
    )
    return [document_output(document) for document in documents]


@router.delete("/documents/{document_id}", status_code=204)
def remove_document(
    document_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)
) -> None:
    document = db.scalar(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    if document is None:
        raise HTTPException(404, "Document not found.")
    delete_document(document.id)
    db.delete(document)
    db.commit()


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest, db: Session = Depends(get_db), user_id: str = Depends(current_user)
) -> ChatResponse:
    owned_product(db, payload.product_id, user_id)
    conversation = None
    if payload.conversation_id:
        conversation = db.scalar(
            select(Conversation).where(
                Conversation.id == payload.conversation_id,
                Conversation.user_id == user_id,
                Conversation.product_id == payload.product_id,
            )
        )
        if conversation is None:
            raise HTTPException(404, "Conversation not found.")
    else:
        conversation = Conversation(user_id=user_id, product_id=payload.product_id)
        db.add(conversation)
        db.flush()

    query_type = classify_query(payload.question)
    if query_type == "greeting":
        answer = (
            "Hi! I'm your product assistant. Ask me about this product's "
            "specifications, troubleshooting, maintenance, or warranty."
        )
        citations, insufficient = [], False
    elif query_type == "unsupported":
        answer, citations, insufficient = INSUFFICIENT_MESSAGE, [], True
    else:
        try:
            history = [
                {"role": message.role, "content": message.content}
                for message in sorted(
                    conversation.messages, key=lambda message: message.created_at
                )[-8:]
            ]
            previous_user_questions = [
                item["content"] for item in history if item["role"] == "user"
            ][-2:]
            retrieval_question = " ".join([*previous_user_questions, payload.question])
            results = retrieve(
                db, retrieval_question, payload.product_id, user_id, query_type
            )
            answer, citations, insufficient = answer_question(
                payload.question, results, history=history
            )
        except (OSError, RuntimeError, ValueError) as exc:
            db.rollback()
            raise HTTPException(503, f"Retrieval or answer generation failed: {exc}") from exc
        except OpenAIRateLimitError as exc:
            db.rollback()
            logger.warning("OpenAI rejected answer generation because of rate limits or account quota.")
            raise HTTPException(
                503,
                "OpenAI cannot generate an answer because the API rate limit or account credits "
                "are exhausted. Check your OpenAI API billing and usage limits.",
            ) from exc
        except OpenAIAPIError as exc:
            db.rollback()
            logger.exception("OpenAI answer generation failed.")
            raise HTTPException(
                503,
                "The OpenAI API request failed. Check your API key, account billing, and model access.",
            ) from exc

    db.add(Message(conversation_id=conversation.id, role="user", content=payload.question))
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
            citations_json=json.dumps([citation.model_dump() for citation in citations]),
        )
    )
    db.commit()
    return ChatResponse(
        conversation_id=conversation.id,
        answer=answer,
        query_type=query_type,
        citations=citations,
        insufficient_evidence=insufficient,
    )


@router.post("/chat/new", status_code=201)
def new_conversation(
    product_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)
) -> dict[str, str]:
    owned_product(db, product_id, user_id)
    conversation = Conversation(user_id=user_id, product_id=product_id)
    db.add(conversation)
    db.commit()
    return {"id": conversation.id}


@router.get("/chat/history/{conversation_id}", response_model=list[MessageOut])
def chat_history(
    conversation_id: str, db: Session = Depends(get_db), user_id: str = Depends(current_user)
) -> list[MessageOut]:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
    )
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    result = []
    for message in db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at, Message.id)
    ):
        result.append(
            MessageOut(
                id=message.id,
                role=message.role,
                content=message.content,
                citations=[Citation(**item) for item in json.loads(message.citations_json)],
                created_at=message.created_at,
            )
        )
    return result
