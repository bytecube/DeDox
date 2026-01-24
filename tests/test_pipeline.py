"""Tests for the pipeline processors."""

import pytest
import pytest_asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from dedox.models.document import Document, DocumentStatus
from dedox.models.job import Job, JobStatus, JobStage
from dedox.pipeline.base import ProcessorContext, ProcessorResult


@pytest.fixture
def mock_document():
    """Create a mock document."""
    return Document(
        id=uuid4(),
        filename="test.jpg",
        original_filename="test.jpg",
        content_type="image/jpeg",
        file_size=1024,
        status=DocumentStatus.PENDING,
    )


@pytest.fixture
def mock_job(mock_document):
    """Create a mock job."""
    return Job(
        document_id=mock_document.id,
        status=JobStatus.PROCESSING,
        current_stage=JobStage.PENDING,
    )


class TestProcessorContext:
    """Tests for ProcessorContext."""

    def test_create_context(self, mock_document, mock_job):
        """Test creating a processor context."""
        context = ProcessorContext(
            document=mock_document,
            job=mock_job,
        )

        assert context.document == mock_document
        assert context.job == mock_job
        assert context.ocr_text is None
        assert context.metadata == {}

    def test_context_updates(self, mock_document, mock_job):
        """Test updating context during processing."""
        context = ProcessorContext(
            document=mock_document,
            job=mock_job,
        )

        # Update fields as processors would
        context.processed_file_path = "/tmp/processed.pdf"
        context.ocr_text = "Extracted text"
        context.ocr_confidence = 0.95
        context.metadata = {"sender": "Test Company"}
        context.paperless_id = 123

        assert context.processed_file_path == "/tmp/processed.pdf"
        assert context.ocr_text == "Extracted text"
        assert context.ocr_confidence == 0.95
        assert context.metadata["sender"] == "Test Company"
        assert context.paperless_id == 123


class TestProcessorResult:
    """Tests for ProcessorResult."""

    def test_success_result(self):
        """Test creating a success result."""
        result = ProcessorResult.ok(
            stage=JobStage.IMAGE_PROCESSING,
            message="Image processed successfully",
            data={"width": 800, "height": 600},
            processing_time_ms=150,
        )

        assert result.success is True
        assert result.stage == JobStage.IMAGE_PROCESSING
        assert result.message == "Image processed successfully"
        assert result.data["width"] == 800
        assert result.processing_time_ms == 150
        assert result.error is None

    def test_failure_result(self):
        """Test creating a failure result."""
        result = ProcessorResult.fail(
            stage=JobStage.OCR,
            error="Tesseract not found",
        )

        assert result.success is False
        assert result.stage == JobStage.OCR
        assert result.error == "Tesseract not found"


class TestImageProcessor:
    """Tests for ImageProcessor."""

    @pytest.fixture
    def processor(self):
        """Create image processor."""
        from dedox.pipeline.processors.image_processor import ImageProcessor
        return ImageProcessor()

    def test_can_process_with_original_path(self, processor, mock_document, mock_job, temp_dir):
        """Test can_process with original path set."""
        context = ProcessorContext(
            document=mock_document,
            job=mock_job,
        )
        context.original_file_path = str(temp_dir / "test.jpg")

        # Create a dummy file
        with open(context.original_file_path, 'wb') as f:
            f.write(b"fake image data")

        assert processor.can_process(context) is True

    def test_can_process_without_original_path(self, processor, mock_document, mock_job):
        """Test can_process without original path."""
        context = ProcessorContext(
            document=mock_document,
            job=mock_job,
        )
        context.original_file_path = None

        assert processor.can_process(context) is False

    @pytest.mark.asyncio
    async def test_process_image(self, processor, sample_image, mock_settings, mock_document, mock_job, temp_dir):
        """Test processing an image."""
        # Write sample image to file
        image_path = temp_dir / "test_image.jpg"
        with open(image_path, 'wb') as f:
            f.write(sample_image)

        context = ProcessorContext(
            document=mock_document,
            job=mock_job,
        )
        context.original_file_path = str(image_path)

        result = await processor.process(context)

        assert result.success is True
        assert result.stage == JobStage.IMAGE_PROCESSING
        assert context.processed_file_path is not None


class TestOCRProcessor:
    """Tests for OCRProcessor."""

    @pytest.fixture
    def processor(self):
        """Create OCR processor."""
        from dedox.pipeline.processors.ocr_processor import OCRProcessor
        return OCRProcessor()

    def test_can_process_with_processed_path(self, processor, mock_document, mock_job, temp_dir):
        """Test can_process with processed path."""
        # Create the file so it exists (can_process checks file existence)
        pdf_path = temp_dir / "processed.pdf"
        with open(pdf_path, 'wb') as f:
            f.write(b"fake pdf data")

        context = ProcessorContext(
            document=mock_document,
            job=mock_job,
        )
        context.processed_file_path = str(pdf_path)

        assert processor.can_process(context) is True

    @pytest.mark.asyncio
    @patch("dedox.pipeline.processors.ocr_processor.pytesseract")
    async def test_process_ocr(self, mock_tesseract, processor, sample_image, mock_settings, mock_document, mock_job, temp_dir):
        """Test OCR processing with mocked Tesseract."""
        # Mock tesseract functions
        mock_tesseract.image_to_string.return_value = "This is a test"
        mock_tesseract.Output.DICT = "dict"
        mock_tesseract.image_to_data.return_value = {
            "text": ["This", "is", "a", "test"],
            "conf": [95, 90, 85, 92],
        }

        # Write sample image to file (using real image bytes)
        image_path = temp_dir / "test_ocr.jpg"
        with open(image_path, 'wb') as f:
            f.write(sample_image)

        context = ProcessorContext(
            document=mock_document,
            job=mock_job,
        )
        context.processed_file_path = str(image_path)

        result = await processor.process(context)

        assert result.success is True
        assert context.ocr_text == "This is a test"


class TestLLMExtractor:
    """Tests for LLMExtractor."""

    @pytest.fixture
    def processor(self):
        """Create LLM extractor."""
        from dedox.pipeline.processors.llm_extractor import LLMExtractor
        return LLMExtractor()

    def test_can_process_with_text(self, processor, mock_document, mock_job):
        """Test can_process with OCR text."""
        context = ProcessorContext(
            document=mock_document,
            job=mock_job,
        )
        context.ocr_text = "This is extracted text from a document."

        assert processor.can_process(context) is True

    def test_can_process_without_text(self, processor, mock_document, mock_job):
        """Test can_process without OCR text."""
        context = ProcessorContext(
            document=mock_document,
            job=mock_job,
        )
        context.ocr_text = None

        assert processor.can_process(context) is False

    @pytest.mark.asyncio
    @patch("httpx.AsyncClient")
    async def test_process_extraction(self, mock_client_class, processor, mock_settings, mock_document, mock_job):
        """Test metadata extraction with mocked Ollama Chat API."""
        # Mock the httpx client
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Mock Chat API response format (message.content instead of response)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": '{"document_type": "invoice", "sender": "Test Corp", "total_amount": 100.00, "language": "en", "action_required": false}'
            }
        }
        mock_client.post.return_value = mock_response

        context = ProcessorContext(
            document=mock_document,
            job=mock_job,
        )
        context.ocr_text = "Invoice from Test Corp. Total: $100.00"

        result = await processor.process(context)

        # The actual result depends on the mock response
        assert result.stage == JobStage.METADATA_EXTRACTION


class TestPipelineOrchestrator:
    """Tests for PipelineOrchestrator."""

    @pytest.fixture
    def orchestrator(self, test_db):
        """Create pipeline orchestrator."""
        from dedox.pipeline.orchestrator import PipelineOrchestrator
        return PipelineOrchestrator(db=test_db)

    @pytest.mark.asyncio
    async def test_orchestrator_initialization(self, orchestrator):
        """Test orchestrator is properly initialized."""
        assert orchestrator.db is not None

    @pytest.mark.asyncio
    async def test_orchestrator_has_stages(self, orchestrator):
        """Test that orchestrator has defined stages."""
        # Orchestrator should have a way to process documents through stages
        assert hasattr(orchestrator, 'process_document')
