from datetime import datetime, timedelta, timezone

from app.db.models import Document, DocumentStatus
from app.db.session import SessionLocal


def _make_documents(org_id: str, count: int) -> list[str]:
    """Insert `count` documents with strictly increasing created_at, oldest first.

    Returns their ids in newest-first order (matching the endpoint's own ordering), so tests
    can assert against a known, unambiguous sequence instead of relying on tie-breaking.
    """
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ids = []
    with SessionLocal() as db:
        for i in range(count):
            document = Document(
                organization_id=org_id,
                uploaded_by="user-a",
                filename=f"doc-{i}.xlsx",
                content_type="application/test",
                storage_key=f"{org_id}/doc-{i}.xlsx",
                size_bytes=10,
                status=DocumentStatus.ready,
                created_at=base_time + timedelta(minutes=i),
            )
            db.add(document)
            db.flush()
            ids.append(document.id)
        db.commit()
    return list(reversed(ids))  # newest first, matching order_by(created_at.desc())


def test_documents_default_limit_returns_everything_for_small_tenants(client, tenant_a_headers):
    _make_documents("org-a", 5)

    response = client.get("/api/v1/documents", headers=tenant_a_headers)

    assert response.status_code == 200
    assert len(response.json()) == 5
    assert response.headers["X-Total-Count"] == "5"


def test_documents_pagination_pages_through_results_without_overlap(client, tenant_a_headers):
    newest_first_ids = _make_documents("org-a", 5)

    page1 = client.get("/api/v1/documents?limit=2&offset=0", headers=tenant_a_headers)
    page2 = client.get("/api/v1/documents?limit=2&offset=2", headers=tenant_a_headers)
    page3 = client.get("/api/v1/documents?limit=2&offset=4", headers=tenant_a_headers)

    assert [d["id"] for d in page1.json()] == newest_first_ids[0:2]
    assert [d["id"] for d in page2.json()] == newest_first_ids[2:4]
    assert [d["id"] for d in page3.json()] == newest_first_ids[4:5]
    for page in (page1, page2, page3):
        assert page.headers["X-Total-Count"] == "5"


def test_documents_pagination_rejects_out_of_range_query_params(client, tenant_a_headers):
    assert client.get("/api/v1/documents?limit=0", headers=tenant_a_headers).status_code == 422
    assert client.get("/api/v1/documents?limit=201", headers=tenant_a_headers).status_code == 422
    assert client.get("/api/v1/documents?offset=-1", headers=tenant_a_headers).status_code == 422


def test_conversations_pagination_pages_through_results(client, tenant_a_headers):
    created_ids = []
    for i in range(5):
        created = client.post(
            "/api/v1/conversations",
            headers=tenant_a_headers,
            json={"title": f"Conversation {i}"},
        ).json()
        created_ids.append(created["id"])
    newest_first_ids = list(reversed(created_ids))

    page1 = client.get("/api/v1/conversations?limit=2&offset=0", headers=tenant_a_headers)
    page2 = client.get("/api/v1/conversations?limit=2&offset=2", headers=tenant_a_headers)

    assert [c["id"] for c in page1.json()] == newest_first_ids[0:2]
    assert [c["id"] for c in page2.json()] == newest_first_ids[2:4]
    assert page1.headers["X-Total-Count"] == "5"
