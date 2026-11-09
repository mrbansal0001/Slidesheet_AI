import io

import openpyxl

from app.db.models import DocumentChunk
from app.db.session import SessionLocal
from app.services.openai_service import OpenAIService


def _workbook_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sales"
    sheet.append(["Product", "Revenue"])
    sheet.append(["Alpha", 125000])
    sheet.append(["Beta", 75000])
    content = io.BytesIO()
    workbook.save(content)
    return content.getvalue()


def _big_workbook_bytes(rows: int = 150) -> bytes:
    """Enough rows to exceed the 300-token chunk size and force multiple chunks."""
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Sales"
    sheet.append(["Product", "Revenue"])
    for i in range(rows):
        sheet.append([f"Product-{i}", 1000 + i])
    content = io.BytesIO()
    workbook.save(content)
    return content.getvalue()


def test_upload_index_chat_and_citations(client, tenant_a_headers, monkeypatch):
    monkeypatch.setattr(
        OpenAIService,
        "embed",
        lambda self, texts, organization_id=None: [[1.0] + [0.0] * 1535 for _ in texts],
    )
    monkeypatch.setattr(
        OpenAIService,
        "answer",
        lambda self, question, context, organization_id=None: (
            "Alpha has the highest revenue at 125000. [1]"
        ),
    )

    upload = client.post(
        "/api/v1/documents",
        headers=tenant_a_headers,
        files={
            "file": (
                "sales.xlsx",
                _workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    document = upload.json()["document"]
    assert document["status"] == "ready"

    conversation = client.post(
        "/api/v1/conversations",
        headers=tenant_a_headers,
        json={"title": "Sales analysis"},
    ).json()
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=tenant_a_headers,
        json={
            "question": "Which product has the highest revenue?",
            "document_ids": [document["id"]],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "Alpha" in payload["answer"]
    assert payload["citations"][0]["filename"] == "sales.xlsx"
    assert payload["citations"][0]["source"] == "Sheet: Sales"

    with SessionLocal() as db:
        stored_chunks = db.query(DocumentChunk).filter(
            DocumentChunk.document_id == document["id"]
        ).all()
    assert stored_chunks
    for chunk in stored_chunks:
        assert chunk.embedding_model == "text-embedding-3-small"
        assert chunk.embedding_dims == 1536


def test_low_similarity_returns_insufficient_evidence_without_calling_llm(
    client, tenant_a_headers, monkeypatch
):
    from app.core.config import get_settings

    monkeypatch.setattr(
        OpenAIService,
        "embed",
        lambda self, texts, organization_id=None: [[1.0] + [0.0] * 1535 for _ in texts],
    )

    def _fail_if_called(self, question, context, organization_id=None):
        raise AssertionError("answer() should not be called when evidence is below threshold")

    monkeypatch.setattr(OpenAIService, "answer", _fail_if_called)

    upload = client.post(
        "/api/v1/documents",
        headers=tenant_a_headers,
        files={
            "file": (
                "sales.xlsx",
                _workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    document = upload.json()["document"]

    conversation = client.post(
        "/api/v1/conversations",
        headers=tenant_a_headers,
        json={"title": "Threshold test"},
    ).json()

    # 1.5 is above the maximum possible cosine similarity (1.0) — guarantees rejection
    # regardless of the actual computed score, to test the mechanism in isolation.
    monkeypatch.setattr(get_settings(), "retrieval_min_score", 1.5)

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=tenant_a_headers,
        json={"question": "What is the revenue?", "document_ids": [document["id"]]},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "couldn't find sufficiently relevant evidence" in payload["answer"]
    assert payload["citations"] == []


def test_summary_question_ignores_similarity_threshold(client, tenant_a_headers, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(
        OpenAIService,
        "embed",
        lambda self, texts, organization_id=None: [[1.0] + [0.0] * 1535 for _ in texts],
    )
    monkeypatch.setattr(
        OpenAIService,
        "answer",
        lambda self, question, context, organization_id=None: "Summary of the workbook.",
    )

    upload = client.post(
        "/api/v1/documents",
        headers=tenant_a_headers,
        files={
            "file": (
                "sales.xlsx",
                _workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    document = upload.json()["document"]
    conversation = client.post(
        "/api/v1/conversations",
        headers=tenant_a_headers,
        json={"title": "Summary threshold test"},
    ).json()

    # Same unreachable threshold as above — must NOT block a summary-style question, since
    # get_summary_chunks() uses document order, not a real similarity score.
    monkeypatch.setattr(get_settings(), "retrieval_min_score", 1.5)

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=tenant_a_headers,
        json={"question": "Summarize this document", "document_ids": [document["id"]]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "Summary of the workbook."


def test_reranker_is_invoked_and_its_order_is_applied_when_candidates_exceed_top_k(
    client, tenant_a_headers, monkeypatch
):
    from app.core.config import get_settings

    monkeypatch.setattr(
        OpenAIService,
        "embed",
        lambda self, texts, organization_id=None: [[1.0] + [0.0] * 1535 for _ in texts],
    )
    monkeypatch.setattr(
        OpenAIService,
        "answer",
        lambda self, question, context, organization_id=None: "Answer.",
    )

    rerank_calls = []

    def fake_rerank(self, question, candidates, top_n, organization_id=None):
        rerank_calls.append(len(candidates))
        # Deterministic reverse order — proves chat.py applies the returned indices rather than
        # silently keeping the original retrieval order.
        return list(reversed(range(len(candidates))))[:top_n]

    monkeypatch.setattr(OpenAIService, "rerank", fake_rerank)

    settings = get_settings()
    monkeypatch.setattr(settings, "retrieval_top_k", 2)
    monkeypatch.setattr(settings, "retrieval_candidates", 20)

    upload = client.post(
        "/api/v1/documents",
        headers=tenant_a_headers,
        files={
            "file": (
                "big_sales.xlsx",
                _big_workbook_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    document = upload.json()["document"]

    with SessionLocal() as db:
        stored_chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document["id"])
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
    assert len(stored_chunks) > 2, "test setup should produce more than retrieval_top_k chunks"

    conversation = client.post(
        "/api/v1/conversations",
        headers=tenant_a_headers,
        json={"title": "Rerank test"},
    ).json()

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=tenant_a_headers,
        json={"question": "List all products", "document_ids": [document["id"]]},
    )

    assert response.status_code == 200, response.text
    assert rerank_calls, "rerank() should have been called since candidates > retrieval_top_k"
    assert rerank_calls[0] == len(stored_chunks)

    payload = response.json()
    assert len(payload["citations"]) == 2
    # fake_rerank returns [n-1, n-2, ...] — the last two chunks, in reverse order — proving
    # chat.py actually applies the returned order instead of just taking candidates[:top_n].
    assert payload["citations"][0]["excerpt"] == stored_chunks[-1].text[:240]
    assert payload["citations"][1]["excerpt"] == stored_chunks[-2].text[:240]
