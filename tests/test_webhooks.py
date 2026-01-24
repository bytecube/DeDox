"""
Tests for Paperless-ngx webhook integration.
"""

import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient


class TestWebhookSignatureVerification:
    """Tests for webhook signature verification."""

    def test_verify_signature_no_secret_configured(self):
        """Should pass verification when no secret is configured."""
        from dedox.api.routes.webhooks import verify_webhook_signature

        result = verify_webhook_signature(
            payload=b'{"test": "data"}',
            signature="sha256=invalid",
            secret=""  # No secret configured
        )
        assert result is True

    def test_verify_signature_valid(self):
        """Should verify a valid HMAC signature."""
        from dedox.api.routes.webhooks import verify_webhook_signature

        secret = "test-secret-key"
        payload = b'{"document_id": 123}'

        # Calculate correct signature
        expected_sig = hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()

        result = verify_webhook_signature(
            payload=payload,
            signature=f"sha256={expected_sig}",
            secret=secret
        )
        assert result is True

    def test_verify_signature_invalid(self):
        """Should reject an invalid signature."""
        from dedox.api.routes.webhooks import verify_webhook_signature

        result = verify_webhook_signature(
            payload=b'{"document_id": 123}',
            signature="sha256=invalid",
            secret="test-secret"
        )
        assert result is False

    def test_verify_signature_missing_when_required(self):
        """Should reject when signature is missing but secret is configured."""
        from dedox.api.routes.webhooks import verify_webhook_signature

        result = verify_webhook_signature(
            payload=b'{"document_id": 123}',
            signature=None,
            secret="test-secret"
        )
        assert result is False


class TestWebhookPayload:
    """Tests for webhook payload parsing."""

    def test_parse_minimal_payload(self):
        """Should parse payload with only required fields."""
        from dedox.api.routes.webhooks import PaperlessWebhookPayload

        payload = PaperlessWebhookPayload(document_id=123)
        assert payload.document_id == 123
        assert payload.document_title is None
        assert payload.document_tags is None

    def test_parse_full_payload(self):
        """Should parse payload with all fields."""
        from dedox.api.routes.webhooks import PaperlessWebhookPayload

        payload = PaperlessWebhookPayload(
            document_id=123,
            document_title="Test Document",
            document_filename="test.pdf",
            document_created="2024-01-15",
            document_added="2024-01-15T10:30:00Z",
            document_correspondent="ACME Corp",
            document_correspondent_id=5,
            document_document_type="Invoice",
            document_document_type_id=3,
            document_tags=["important", "finance"],
            document_content="This is the OCR text from Paperless..."
        )

        assert payload.document_id == 123
        assert payload.document_title == "Test Document"
        assert payload.document_tags == ["important", "finance"]
        assert payload.document_content == "This is the OCR text from Paperless..."


class TestPaperlessWebhookService:
    """Tests for PaperlessWebhookService."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.paperless.base_url = "http://paperless:8000"
        settings.paperless.api_token = "test-token"
        settings.paperless.verify_ssl = False
        settings.paperless.timeout_seconds = 30
        settings.paperless.processing_tag = "dedox:processing"
        settings.paperless.enhanced_tag = "dedox:enhanced"
        settings.paperless.error_tag = "dedox:error"
        settings.paperless.webhook.auto_create_custom_fields = True
        settings.storage.upload_path = "/tmp/uploads"
        return settings

    @pytest.mark.asyncio
    async def test_get_or_create_tag_existing(self, mock_settings):
        """Should return existing tag ID."""
        from dedox.services.paperless_webhook_service import PaperlessWebhookService

        with patch("dedox.services.paperless_webhook_service.get_settings", return_value=mock_settings):
            service = PaperlessWebhookService()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"results": [{"id": 42, "name": "test-tag"}]}

            with patch.object(service, "_get_client") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.get = AsyncMock(return_value=mock_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock()
                mock_client.return_value = mock_client_instance

                tag_id = await service.get_or_create_tag("test-tag")
                assert tag_id == 42

    @pytest.mark.asyncio
    async def test_get_or_create_tag_create_new(self, mock_settings):
        """Should create new tag if not exists."""
        from dedox.services.paperless_webhook_service import PaperlessWebhookService

        with patch("dedox.services.paperless_webhook_service.get_settings", return_value=mock_settings):
            service = PaperlessWebhookService()

            # Mock search response (no results)
            search_response = MagicMock()
            search_response.status_code = 200
            search_response.json.return_value = {"results": []}

            # Mock create response
            create_response = MagicMock()
            create_response.status_code = 201
            create_response.json.return_value = {"id": 99, "name": "new-tag"}

            with patch.object(service, "_get_client") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.get = AsyncMock(return_value=search_response)
                mock_client_instance.post = AsyncMock(return_value=create_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock()
                mock_client.return_value = mock_client_instance

                tag_id = await service.get_or_create_tag("new-tag")
                assert tag_id == 99

    @pytest.mark.asyncio
    async def test_add_tag_to_document(self, mock_settings):
        """Should add tag to document."""
        from dedox.services.paperless_webhook_service import PaperlessWebhookService

        with patch("dedox.services.paperless_webhook_service.get_settings", return_value=mock_settings):
            service = PaperlessWebhookService()
            service._tag_cache["test-tag"] = 42

            # Mock get document response
            get_response = MagicMock()
            get_response.status_code = 200
            get_response.json.return_value = {"tags": [1, 2, 3]}

            # Mock patch response
            patch_response = MagicMock()
            patch_response.status_code = 200

            with patch.object(service, "_get_client") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.get = AsyncMock(return_value=get_response)
                mock_client_instance.patch = AsyncMock(return_value=patch_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock()
                mock_client.return_value = mock_client_instance

                result = await service.add_tag_to_document(123, "test-tag")
                assert result is True


class TestWebhookEndpoint:
    """Tests for the webhook endpoint."""

    @pytest.fixture
    def app(self):
        """Create test app."""
        from dedox.api.app import create_app
        return create_app()

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return TestClient(app)

    def test_webhook_health_endpoint(self, client):
        """Should return webhook health status."""
        with patch("dedox.api.routes.webhooks.get_settings") as mock_settings:
            mock_settings.return_value.paperless.webhook.enabled = True
            mock_settings.return_value.paperless.webhook.secret = "test-secret"
            mock_settings.return_value.paperless.api_token = "test-token"

            response = client.get("/api/webhooks/paperless/health")
            assert response.status_code == 200

            data = response.json()
            assert data["status"] == "ok"
            assert "webhooks_enabled" in data

    def test_webhook_disabled_returns_503(self, client):
        """Should return 503 when webhooks are disabled."""
        with patch("dedox.api.routes.webhooks.get_settings") as mock_settings:
            mock_settings.return_value.paperless.webhook.enabled = False

            response = client.post(
                "/api/webhooks/paperless/document-added",
                json={"document_id": 123}
            )
            assert response.status_code == 503

    def test_webhook_invalid_signature_returns_401(self, client):
        """Should return 401 for invalid signature when secret is configured."""
        with patch("dedox.api.routes.webhooks.get_settings") as mock_settings:
            mock_settings.return_value.paperless.webhook.enabled = True
            mock_settings.return_value.paperless.webhook.secret = "test-secret"

            response = client.post(
                "/api/webhooks/paperless/document-added",
                json={"document_id": 123},
                headers={"X-Webhook-Signature": "sha256=invalid"}
            )
            assert response.status_code == 401

    def test_webhook_valid_request_accepted(self, client):
        """Should accept valid webhook request."""
        with patch("dedox.api.routes.webhooks.get_settings") as mock_settings:
            mock_settings.return_value.paperless.webhook.enabled = True
            mock_settings.return_value.paperless.webhook.secret = ""  # No signature required

            payload = {"document_id": 123, "document_title": "Test"}

            # Mock the background task processing
            with patch("dedox.api.routes.webhooks._process_paperless_document"):
                response = client.post(
                    "/api/webhooks/paperless/document-added",
                    json=payload
                )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "accepted"
            assert "123" in data["message"]


class TestPipelineWebhookIntegration:
    """Tests for pipeline handling of webhook documents."""

    @pytest.mark.asyncio
    async def test_paperless_archiver_skips_webhook_documents(self):
        """Paperless archiver should skip documents from webhook."""
        from dedox.pipeline.processors.paperless_archiver import PaperlessArchiver
        from dedox.pipeline.base import ProcessorContext
        from dedox.models.document import Document
        from dedox.models.job import Job

        doc = Document(
            id=uuid4(),
            filename="test.pdf",
            original_filename="test.pdf",
            content_type="application/pdf",
            file_size=1000,
            source="paperless_webhook",  # Webhook source
            paperless_id=123,
        )

        job = Job(id=uuid4(), document_id=doc.id)
        context = ProcessorContext(document=doc, job=job)

        processor = PaperlessArchiver()

        with patch("dedox.pipeline.processors.paperless_archiver.get_settings") as mock:
            mock.return_value.paperless.base_url = "http://paperless:8000"
            mock.return_value.paperless.api_token = "test-token"

            can_process = processor.can_process(context)

        assert can_process is False
        assert context.paperless_id == 123  # Should be set from document

    @pytest.mark.asyncio
    async def test_paperless_archiver_processes_upload_documents(self):
        """Paperless archiver should process upload documents."""
        from dedox.pipeline.processors.paperless_archiver import PaperlessArchiver
        from dedox.pipeline.base import ProcessorContext
        from dedox.models.document import Document
        from dedox.models.job import Job
        import tempfile
        import os

        # Create a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(b"test content")
            temp_path = f.name

        try:
            doc = Document(
                id=uuid4(),
                filename="test.pdf",
                original_filename="test.pdf",
                content_type="application/pdf",
                file_size=1000,
                source="upload",  # Upload source
                original_path=temp_path,
            )

            job = Job(id=uuid4(), document_id=doc.id)
            context = ProcessorContext(
                document=doc,
                job=job,
                original_file_path=temp_path
            )

            processor = PaperlessArchiver()

            with patch("dedox.pipeline.processors.paperless_archiver.get_settings") as mock:
                mock.return_value.paperless.base_url = "http://paperless:8000"
                mock.return_value.paperless.api_token = "test-token"

                can_process = processor.can_process(context)

            assert can_process is True
        finally:
            os.unlink(temp_path)


class TestFinalizerWebhookHandling:
    """Tests for finalizer handling of webhook documents."""

    @pytest.mark.asyncio
    async def test_finalizer_uses_webhook_service_for_webhook_docs(self):
        """Finalizer should use webhook service for webhook-sourced documents."""
        from dedox.pipeline.processors.finalizer import Finalizer
        from dedox.pipeline.base import ProcessorContext
        from dedox.models.document import Document
        from dedox.models.job import Job

        doc = Document(
            id=uuid4(),
            filename="test.pdf",
            original_filename="test.pdf",
            content_type="application/pdf",
            file_size=1000,
            source="paperless_webhook",
            paperless_id=123,
        )

        job = Job(id=uuid4(), document_id=doc.id)
        context = ProcessorContext(
            document=doc,
            job=job,
            paperless_id=123,
            metadata={"document_type": "Invoice", "sender": "ACME"},
        )

        processor = Finalizer()

        with patch.object(processor, "_update_paperless_webhook", new_callable=AsyncMock) as mock_webhook:
            with patch.object(processor, "_update_document_status", new_callable=AsyncMock):
                mock_webhook.return_value = {"document_id": 123, "success": True}

                result = await processor.process(context)

        assert result.success is True
        mock_webhook.assert_called_once()

