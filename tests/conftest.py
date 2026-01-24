"""Pytest configuration and fixtures."""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient

# Set test environment
os.environ["DEDOX_CONFIG_DIR"] = ""


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_settings(temp_dir: Path):
    """Create test settings."""
    from dedox.core.config import Settings, ServerSettings, StorageSettings, OCRSettings, LLMSettings, PaperlessSettings, AuthSettings, DatabaseSettings

    return Settings(
        server=ServerSettings(
            host="127.0.0.1",
            port=8000,
            debug=True,
            cors_origins=["*"],
        ),
        storage=StorageSettings(
            base_path=str(temp_dir),
            upload_path=str(temp_dir / "uploads"),
            processed_path=str(temp_dir / "processed"),
        ),
        database=DatabaseSettings(
            path=str(temp_dir / "test.db"),
        ),
        ocr=OCRSettings(
            languages=["eng"],
            tesseract_path="tesseract",
            dpi=300,
            min_confidence=0.6,
        ),
        llm=LLMSettings(
            ollama_url="http://localhost:11434",
            model="qwen2.5:14b",
            timeout_seconds=60,
            max_retries=3,
        ),
        paperless=PaperlessSettings(
            url="http://localhost:8080",
            api_token="test-token",
            processing_tag="Processing...",
            default_correspondent="DeDox",
        ),
        auth=AuthSettings(
            jwt_secret="test-secret-key-for-testing-only",
            jwt_algorithm="HS256",
            token_expire_hours=24,
            allow_registration=True,
        ),
    )


@pytest.fixture
def mock_settings(test_settings, monkeypatch):
    """Mock the global settings."""
    from dedox.core import config
    
    # Create a mock that returns our test settings
    monkeypatch.setattr(config, "_settings", test_settings)
    monkeypatch.setattr(config, "_metadata_fields", {})
    monkeypatch.setattr(config, "_document_types", {})
    monkeypatch.setattr(config, "_urgency_rules", {})
    
    return test_settings


@pytest_asyncio.fixture
async def test_db(temp_dir: Path):
    """Create a test database."""
    from dedox.db.database import Database
    
    db_path = temp_dir / "test.db"
    db = Database(str(db_path))
    await db.connect()
    await db.init_schema()
    
    yield db
    
    await db.disconnect()


@pytest_asyncio.fixture
async def test_user(test_db):
    """Create a test user."""
    from dedox.db.repositories.user_repository import UserRepository
    from dedox.models.user import UserCreate, UserRole

    repo = UserRepository(test_db)
    unique_id = str(uuid4())[:8]

    user_create = UserCreate(
        username=f"testuser_{unique_id}",
        email=f"test_{unique_id}@example.com",
        password="testpassword123",
        role=UserRole.USER,
    )

    user = await repo.create(user_create)
    return user


@pytest_asyncio.fixture
async def admin_user(test_db):
    """Create an admin user."""
    from dedox.db.repositories.user_repository import UserRepository
    from dedox.models.user import UserCreate, UserRole

    repo = UserRepository(test_db)
    unique_id = str(uuid4())[:8]

    user_create = UserCreate(
        username=f"admin_{unique_id}",
        email=f"admin_{unique_id}@example.com",
        password="adminpassword123",
        role=UserRole.ADMIN,
    )

    user = await repo.create(user_create)
    return user


@pytest.fixture
def auth_token(test_user, mock_settings):
    """Create an auth token for the test user."""
    from dedox.api.deps import create_access_token
    
    return create_access_token(str(test_user.id), test_user.role)


@pytest.fixture
def admin_token(admin_user, mock_settings):
    """Create an auth token for the admin user."""
    from dedox.api.deps import create_access_token
    
    return create_access_token(str(admin_user.id), admin_user.role)


@pytest.fixture
def sample_image() -> bytes:
    """Create a sample test image."""
    from PIL import Image
    import io
    
    # Create a simple test image with text-like content
    img = Image.new("RGB", (800, 600), color="white")
    
    # Draw some basic shapes to simulate document content
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 50, 750, 550], outline="black", width=2)
    draw.text((100, 100), "Test Document", fill="black")
    draw.text((100, 150), "This is a sample document for testing.", fill="black")
    
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def sample_pdf() -> bytes:
    """Create a sample PDF (placeholder - just returns bytes)."""
    # In real tests, you'd use a library like reportlab or fpdf
    # For now, return minimal PDF bytes
    return b"%PDF-1.4 test pdf content"
