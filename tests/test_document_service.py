"""Tests for the DocumentService."""

import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from uuid import uuid4

from dedox.services.document_service import DocumentService
from dedox.models.document import Document, DocumentStatus
from dedox.models.job import Job, JobStatus


@pytest.fixture
def document_service():
    """Create a DocumentService instance."""
    return DocumentService()


@pytest.fixture
def mock_document():
    """Create a mock document."""
    return Document(
        id=uuid4(),
        filename="test.pdf",
        original_filename="test_document.pdf",
        content_type="application/pdf",
        file_size=1000,
        status=DocumentStatus.COMPLETED,
        user_id=str(uuid4()),
    )


class TestReprocessDocument:
    """Tests for the reprocess_document method."""

    @pytest.mark.asyncio
    async def test_reprocess_document_creates_new_job(
        self, document_service, mock_document, test_db, mock_settings
    ):
        """Test that reprocessing creates a new job."""
        new_job = Job(
            id=uuid4(),
            document_id=mock_document.id,
            status=JobStatus.QUEUED,
        )

        mock_doc_repo = MagicMock()
        mock_doc_repo.update_by_id = AsyncMock()

        mock_job_repo = MagicMock()
        mock_job_repo.create = AsyncMock(return_value=new_job)

        with patch('dedox.services.document_service.get_database', return_value=test_db):
            with patch('dedox.services.document_service.DocumentRepository', return_value=mock_doc_repo):
                with patch('dedox.services.document_service.JobRepository', return_value=mock_job_repo):
                    with patch.object(document_service, '_queue_job', new_callable=AsyncMock):
                        job = await document_service.reprocess_document(mock_document)

        assert job == new_job
        mock_doc_repo.update_by_id.assert_called_once()
        mock_job_repo.create.assert_called_once()


class TestDeleteDocument:
    """Tests for the delete_document method."""

    @pytest.mark.asyncio
    async def test_delete_document_removes_files_and_records(
        self, document_service, mock_document, test_db, mock_settings, temp_dir
    ):
        """Test that delete removes files and database records."""
        # Create mock file paths
        original_path = temp_dir / "originals" / mock_document.filename
        processed_path = temp_dir / "processed" / f"{Path(mock_document.filename).stem}.pdf"

        # Create directories and files
        original_path.parent.mkdir(parents=True, exist_ok=True)
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        original_path.write_text("original content")
        processed_path.write_text("processed content")

        mock_doc_repo = MagicMock()
        mock_doc_repo.delete = AsyncMock()

        with patch('dedox.services.document_service.get_database', return_value=test_db):
            with patch('dedox.services.document_service.DocumentRepository', return_value=mock_doc_repo):
                with patch.object(document_service, '_get_original_path', return_value=original_path):
                    with patch.object(document_service, '_get_processed_path', return_value=processed_path):
                        await document_service.delete_document(mock_document)

        # Verify files are deleted
        assert not original_path.exists()
        assert not processed_path.exists()

        # Verify database record is deleted
        mock_doc_repo.delete.assert_called_once()


class TestGetDocumentWithMetadata:
    """Tests for the get_document_with_metadata method."""

    @pytest.mark.asyncio
    async def test_get_document_with_metadata_found(
        self, document_service, mock_document, test_db, mock_settings
    ):
        """Test getting document with metadata when document exists."""
        metadata = {"document_type": "invoice", "sender": "Test Company"}

        mock_doc_repo = MagicMock()
        mock_doc_repo.get_by_id = AsyncMock(return_value=mock_document)
        mock_doc_repo.get_metadata = AsyncMock(return_value=metadata)

        with patch('dedox.services.document_service.get_database', return_value=test_db):
            with patch('dedox.services.document_service.DocumentRepository', return_value=mock_doc_repo):
                result = await document_service.get_document_with_metadata(str(mock_document.id))

        assert result is not None
        assert result["document"] == mock_document
        assert result["metadata"] == metadata

    @pytest.mark.asyncio
    async def test_get_document_with_metadata_not_found(
        self, document_service, test_db, mock_settings
    ):
        """Test getting document when not found."""
        mock_doc_repo = MagicMock()
        mock_doc_repo.get_by_id = AsyncMock(return_value=None)

        with patch('dedox.services.document_service.get_database', return_value=test_db):
            with patch('dedox.services.document_service.DocumentRepository', return_value=mock_doc_repo):
                result = await document_service.get_document_with_metadata(str(uuid4()))

        assert result is None


class TestPathHelpers:
    """Tests for path helper methods."""

    def test_get_original_path(self, document_service, mock_settings):
        """Test getting original file path."""
        path = document_service._get_original_path("test.pdf")
        assert "originals" in str(path)
        assert "test.pdf" in str(path)

    def test_get_processed_path(self, document_service, mock_settings):
        """Test getting processed file path."""
        path = document_service._get_processed_path("test.jpg")
        assert "processed" in str(path)
        assert "test.pdf" in str(path)  # Extension changed to .pdf
