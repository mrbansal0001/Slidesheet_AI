from app.db.models import Document
from app.db.session import SessionLocal


def test_document_lists_are_tenant_isolated(client, tenant_a_headers, tenant_b_headers):
    # Create identity records through the API.
    assert client.get("/api/v1/documents", headers=tenant_a_headers).status_code == 200
    assert client.get("/api/v1/documents", headers=tenant_b_headers).status_code == 200

    with SessionLocal() as db:
        db.add_all(
            [
                Document(
                    organization_id="org-a",
                    uploaded_by="user-a",
                    filename="a.pptx",
                    content_type="application/test",
                    storage_key="org-a/a.pptx",
                    size_bytes=10,
                ),
                Document(
                    organization_id="org-b",
                    uploaded_by="user-b",
                    filename="b.pptx",
                    content_type="application/test",
                    storage_key="org-b/b.pptx",
                    size_bytes=10,
                ),
            ]
        )
        db.commit()

    tenant_a = client.get("/api/v1/documents", headers=tenant_a_headers).json()
    tenant_b = client.get("/api/v1/documents", headers=tenant_b_headers).json()

    assert [item["filename"] for item in tenant_a] == ["a.pptx"]
    assert [item["filename"] for item in tenant_b] == ["b.pptx"]


def test_conversations_are_user_and_tenant_scoped(client, tenant_a_headers, tenant_b_headers):
    created = client.post(
        "/api/v1/conversations",
        headers=tenant_a_headers,
        json={"title": "Tenant A"},
    )
    assert created.status_code == 201

    assert len(client.get("/api/v1/conversations", headers=tenant_a_headers).json()) == 1
    assert client.get("/api/v1/conversations", headers=tenant_b_headers).json() == []
