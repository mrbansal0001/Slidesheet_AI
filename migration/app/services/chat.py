from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import Principal
from app.db.models import Conversation, Document, Message
from app.schemas.chat import ChatResponse, Citation
from app.services.openai_service import OpenAIService
from app.services.retrieval import get_summary_chunks, retrieve_chunks


SUMMARY_TERMS = ("summarize", "summarise", "overview", "main topics", "key points")


def answer_question(
    db: Session,
    principal: Principal,
    conversation_id: str,
    question: str,
    document_ids: list[str],
) -> ChatResponse:
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.organization_id == principal.organization_id,
            Conversation.user_id == principal.user_id,
        )
        .one_or_none()
    )
    if conversation is None:
        raise LookupError("Conversation not found")

    if document_ids:
        accessible_count = (
            db.query(Document)
            .filter(
                Document.organization_id == principal.organization_id,
                Document.id.in_(document_ids),
            )
            .count()
        )
        if accessible_count != len(set(document_ids)):
            raise PermissionError("One or more documents are unavailable")

    openai = OpenAIService()
    settings = get_settings()
    normalized = question.casefold()
    is_summary_question = any(term in normalized for term in SUMMARY_TERMS)

    if is_summary_question:
        chunks = get_summary_chunks(db, principal.organization_id, document_ids)
    else:
        embedding = openai.embed([question], organization_id=principal.organization_id)[0]
        candidates = retrieve_chunks(
            db,
            principal.organization_id,
            embedding,
            question,
            document_ids,
            settings.retrieval_candidates,
        )

        if candidates and settings.retrieval_min_score > 0 and (
            candidates[0].score < settings.retrieval_min_score
        ):
            return ChatResponse(
                answer=(
                    "I couldn't find sufficiently relevant evidence in the selected documents to "
                    "answer this confidently. Try rephrasing the question or selecting different "
                    "documents."
                ),
                citations=[],
            )

        if len(candidates) > settings.retrieval_top_k:
            order = openai.rerank(
                question,
                candidates,
                settings.retrieval_top_k,
                organization_id=principal.organization_id,
            )
            chunks = [candidates[index] for index in order]
        else:
            chunks = candidates

    if not chunks:
        return ChatResponse(
            answer="No ready documents matched this conversation.",
            citations=[],
        )

    context = "\n\n".join(
        f"[{index}] {chunk.filename} — {chunk.source}\n{chunk.text}"
        for index, chunk in enumerate(chunks, start=1)
    )
    answer = openai.answer(question, context, organization_id=principal.organization_id)
    citations = [
        Citation(
            document_id=chunk.document_id,
            filename=chunk.filename,
            source=chunk.source,
            excerpt=chunk.text[:240],
        )
        for chunk in chunks
    ]

    db.add(
        Message(
            conversation_id=conversation.id,
            organization_id=principal.organization_id,
            role="user",
            content=question,
        )
    )
    db.add(
        Message(
            conversation_id=conversation.id,
            organization_id=principal.organization_id,
            role="assistant",
            content=answer,
            citations=[citation.model_dump() for citation in citations],
        )
    )
    db.commit()
    return ChatResponse(answer=answer, citations=citations)

