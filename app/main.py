"""
main.py – FastAPI backend for the Document Analysis Chatbot.

Endpoints
---------
POST /upload        Upload one or more .pptx / .xlsx files.
POST /chat          Send a question; get an AI-generated answer.
GET  /status        Check whether any documents are loaded.
DELETE /reset       Clear all uploaded documents and reset the index.
GET  /              Serve the frontend HTML.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.parsers import parse_pptx, parse_xlsx
from app.rag import DocumentChatbot

load_dotenv()

app = FastAPI(title="Document Analysis Chatbot")

# One global chatbot instance (single-user / demo mode).
# For multi-user deployments, replace with a session-keyed store.
chatbot = DocumentChatbot()

STATIC_DIR = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

ALLOWED_EXTENSIONS = {".pptx", ".xlsx"}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    sources: List[str]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.post("/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    """Accept .pptx and .xlsx files, parse them, and add to the RAG index."""
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured on the server.",
        )

    processed = []
    errors = []

    for upload in files:
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            errors.append(f"{upload.filename}: unsupported file type (use .pptx or .xlsx)")
            continue

        raw = await upload.read()
        try:
            if suffix == ".pptx":
                chunks = parse_pptx(raw)
            else:
                chunks = parse_xlsx(raw)

            if not chunks:
                errors.append(f"{upload.filename}: no readable content found")
                continue

            chatbot.add_chunks(chunks)
            chatbot.add_document_name(upload.filename)
            processed.append({"filename": upload.filename, "chunks": len(chunks)})
        except Exception:
            errors.append(f"{upload.filename}: failed to parse file – check that it is a valid .pptx or .xlsx document")

    if not processed and errors:
        raise HTTPException(status_code=422, detail=errors)

    return JSONResponse(
        {
            "processed": processed,
            "errors": errors,
            "total_documents": len(chatbot.document_names()),
            "loaded_documents": chatbot.document_names(),
        }
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Answer a question based on the uploaded documents."""
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty.")

    result = chatbot.query(request.question.strip())
    return ChatResponse(answer=result["answer"], sources=result["sources"])


@app.get("/status")
async def status():
    """Return the current state of the chatbot index."""
    return {
        "ready": chatbot.is_ready(),
        "loaded_documents": chatbot.document_names(),
        "api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
    }


@app.delete("/reset")
async def reset():
    """Clear all uploaded documents and reset the chatbot."""
    global chatbot
    chatbot = DocumentChatbot()
    return {"message": "Chatbot reset. All documents cleared."}
