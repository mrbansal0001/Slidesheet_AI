from __future__ import annotations

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=255)


class ConversationResponse(BaseModel):
    id: str
    title: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000)
    document_ids: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    document_id: str
    filename: str
    source: str
    excerpt: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]

