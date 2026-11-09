from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.security import Principal, get_principal
from app.db.models import Conversation
from app.db.session import get_db
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationCreate,
    ConversationResponse,
)
from app.services.chat import answer_question
from app.services.tenancy import ensure_principal_records

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationResponse, status_code=201)
def create_conversation(
    payload: ConversationCreate,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> ConversationResponse:
    ensure_principal_records(db, principal)
    conversation = Conversation(
        organization_id=principal.organization_id,
        user_id=principal.user_id,
        title=payload.title,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return ConversationResponse(id=conversation.id, title=conversation.title)


@router.get("", response_model=list[ConversationResponse])
def list_conversations(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> list[ConversationResponse]:
    base_query = db.query(Conversation).filter(
        Conversation.organization_id == principal.organization_id,
        Conversation.user_id == principal.user_id,
    )
    response.headers["X-Total-Count"] = str(base_query.count())
    rows = (
        base_query.order_by(Conversation.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [ConversationResponse(id=row.id, title=row.title) for row in rows]


@router.post("/{conversation_id}/messages", response_model=ChatResponse)
def send_message(
    conversation_id: str,
    payload: ChatRequest,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> ChatResponse:
    try:
        return answer_question(
            db,
            principal,
            conversation_id,
            payload.question.strip(),
            payload.document_ids,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

