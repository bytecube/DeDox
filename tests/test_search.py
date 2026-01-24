"""Tests for the search functionality."""

import json
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from uuid import uuid4

from dedox.api.routes.search import (
    search_by_metadata,
    get_recent_documents,
)
from dedox.models.user import User, UserRole


@pytest.fixture
def mock_user():
    """Create a mock user."""
    return User(
        id=uuid4(),
        username="testuser",
        email="test@example.com",
        role=UserRole.USER,
        is_active=True,
    )


class TestMetadataSearch:
    """Tests for metadata-based search."""

    @pytest.mark.asyncio
    async def test_search_by_metadata_sender(self, mock_user, test_db, mock_settings):
        """Test searching by sender metadata."""
        mock_rows = [
            {
                "id": str(uuid4()),
                "filename": "doc.pdf",
                "original_filename": "invoice.pdf",
                "metadata": json.dumps({"sender": "Company A", "document_type": "invoice"}),
                "created_at": "2024-01-01T00:00:00",
            }
        ]

        mock_db = MagicMock()
        mock_db.fetch_all = AsyncMock(return_value=mock_rows)

        with patch('dedox.api.routes.search.get_database', return_value=mock_db):
            result = await search_by_metadata(
                current_user=mock_user,
                sender="Company A",
            )

        assert len(result["results"]) == 1
        assert result["results"][0]["metadata"]["sender"] == "Company A"

    @pytest.mark.asyncio
    async def test_search_by_metadata_amount_range(self, mock_user, test_db, mock_settings):
        """Test searching by amount range."""
        mock_rows = [
            {
                "id": str(uuid4()),
                "filename": "doc1.pdf",
                "original_filename": "invoice1.pdf",
                "metadata": json.dumps({"total_amount": 100.0}),
                "created_at": "2024-01-01T00:00:00",
            },
            {
                "id": str(uuid4()),
                "filename": "doc2.pdf",
                "original_filename": "invoice2.pdf",
                "metadata": json.dumps({"total_amount": 500.0}),
                "created_at": "2024-01-01T00:00:00",
            },
        ]

        mock_db = MagicMock()
        mock_db.fetch_all = AsyncMock(return_value=mock_rows)

        with patch('dedox.api.routes.search.get_database', return_value=mock_db):
            result = await search_by_metadata(
                current_user=mock_user,
                amount_min=50.0,
                amount_max=200.0,
            )

        # Only the first document should match (amount=100)
        assert len(result["results"]) == 1
        assert result["results"][0]["metadata"]["total_amount"] == 100.0

    @pytest.mark.asyncio
    async def test_search_by_metadata_document_type(self, mock_user, test_db, mock_settings):
        """Test searching by document type."""
        mock_rows = [
            {
                "id": str(uuid4()),
                "filename": "doc.pdf",
                "original_filename": "contract.pdf",
                "metadata": json.dumps({"document_type": "contract"}),
                "created_at": "2024-01-01T00:00:00",
            }
        ]

        mock_db = MagicMock()
        mock_db.fetch_all = AsyncMock(return_value=mock_rows)

        with patch('dedox.api.routes.search.get_database', return_value=mock_db):
            result = await search_by_metadata(
                current_user=mock_user,
                document_type="contract",
            )

        assert len(result["results"]) == 1
        assert result["results"][0]["metadata"]["document_type"] == "contract"

    @pytest.mark.asyncio
    async def test_search_by_metadata_empty_results(self, mock_user, test_db, mock_settings):
        """Test searching with no matching results."""
        mock_db = MagicMock()
        mock_db.fetch_all = AsyncMock(return_value=[])

        with patch('dedox.api.routes.search.get_database', return_value=mock_db):
            result = await search_by_metadata(
                current_user=mock_user,
                sender="NonExistent",
            )

        assert len(result["results"]) == 0
        assert result["total"] == 0


class TestRecentDocuments:
    """Tests for recent documents retrieval."""

    @pytest.mark.asyncio
    async def test_get_recent_documents(self, mock_user, test_db, mock_settings):
        """Test getting recent documents."""
        mock_rows = [
            {
                "id": str(uuid4()),
                "filename": "doc.pdf",
                "original_filename": "recent.pdf",
                "status": "completed",
                "metadata": json.dumps({}),
                "created_at": "2024-01-01T00:00:00",
                "processed_at": "2024-01-01T01:00:00",
            }
        ]

        mock_db = MagicMock()
        mock_db.fetch_all = AsyncMock(return_value=mock_rows)

        with patch('dedox.api.routes.search.get_database', return_value=mock_db):
            result = await get_recent_documents(current_user=mock_user, limit=10)

        assert "documents" in result
        assert len(result["documents"]) == 1
        assert result["documents"][0]["filename"] == "recent.pdf"
        assert result["documents"][0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_get_recent_documents_empty(self, mock_user, test_db, mock_settings):
        """Test getting recent documents when none exist."""
        mock_db = MagicMock()
        mock_db.fetch_all = AsyncMock(return_value=[])

        with patch('dedox.api.routes.search.get_database', return_value=mock_db):
            result = await get_recent_documents(current_user=mock_user, limit=10)

        assert "documents" in result
        assert len(result["documents"]) == 0
