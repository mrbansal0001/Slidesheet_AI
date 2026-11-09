from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


class StorageService:
    """Local implementation. Replace this interface with S3/MinIO in deployment."""

    def __init__(self) -> None:
        self.root = get_settings().storage_path
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, content: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def delete(self, key: str) -> None:
        path = self.root / key
        if path.exists():
            path.unlink()

