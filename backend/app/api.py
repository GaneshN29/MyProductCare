import hashlib
import json
import os
import secrets
from datetime import date
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import MAX_UPLOAD_BYTES
from .database import get_db
from .models import Chunk, Conversation, Document, Message, Product
from .schemas import (
    ChatRequest,
    ChatResponse,
    Citation,
    DocumentOut,
    MessageOut,
    ProductCreate,
    ProductOut,
)
from .services.ai_services import embed_texts
from .services.document_processor import chunk_pages, extract_pages
from .services.rag import INSUFFICIENT_MESSAGE, answer_question
from .services.retrieval import classify_query, retrieve
from .services.vector_store import delete_document, delete_product, upsert_chunks

router = APIRouter(prefix="/api")
bearer = HTTPBearer(auto_error=False)
ALLOWED_EXTENSIONS = {"pdf", "txt", "md", "markdown"}


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    expected = os.getenv("APP_API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(503, "Set APP_API_TOKEN before using protected endpoints.")
    if credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(401, "A valid bearer token is required.")
    return hashlib.sha256(expected.encode("utf-8")).hexdigest()


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
        chunk_count=len(document.chunks),
        created_at=document.created_at,
    )


@router.get("/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "generation_available": bool(os.getenv("OPENAI_API_KEY", "").strip()),
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
    document_type: str = "user_manual",
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
    if query_type == "unsupported":
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
