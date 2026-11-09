import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["ALLOW_DEV_AUTH"] = "true"
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["OPENAI_API_KEY"] = "test-key"
os.environ["STORAGE_PATH"] = "./test-storage"

import pytest
from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def tenant_a_headers():
    return {"X-User-ID": "user-a", "X-Org-ID": "org-a"}


@pytest.fixture
def tenant_b_headers():
    return {"X-User-ID": "user-b", "X-Org-ID": "org-b"}

