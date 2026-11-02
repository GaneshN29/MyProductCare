from datetime import date, datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserCredentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address.")
        return normalized


class UserOut(BaseModel):
    id: str
    email: str
    created_at: datetime


class SessionOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserOut


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=120)
    purchase_date: date | None = None
    warranty_expiry_date: date | None = None
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
    suggested_warranty_expiry: date | None
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
