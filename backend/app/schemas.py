from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=120)
    purchase_date: date | None = None
    description: str | None = Field(default=None, max_length=4000)


class ProductUpdate(ProductCreate):
    pass


class ProductOut(ProductCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    created_at: datetime


class DocumentOut(BaseModel):
    id: str
    product_id: str
    filename: str
    document_type: str
    status: str
    error_message: str | None
    chunk_count: int
    created_at: datetime


class ChatRequest(BaseModel):
    product_id: str
    question: str = Field(min_length=2, max_length=2000)
    conversation_id: str | None = None


class Citation(BaseModel):
    document_id: str
    filename: str
    page_number: int | None
    section: str | None
    excerpt: str


class ChatResponse(BaseModel):
    conversation_id: str
    answer: str
    query_type: str
    citations: list[Citation]
    insufficient_evidence: bool


class MessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    citations: list[Citation]
    created_at: datetime
