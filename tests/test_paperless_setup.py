"""
Tests for Paperless-ngx automated setup service.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class TestPaperlessSetupService:
    """Tests for PaperlessSetupService."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.paperless.base_url = "http://paperless:8000"
        settings.paperless.api_token = "test-token"
        settings.paperless.verify_ssl = False
        settings.paperless.timeout_seconds = 30
        settings.paperless.webhook.enabled = True
        settings.paperless.webhook.auto_setup_workflow = False
        settings.server.port = 8000
        return settings

    @pytest.mark.asyncio
    async def test_check_connectivity_success(self, mock_settings):
        """Should return connected status when API is reachable."""
        from dedox.services.paperless_setup_service import PaperlessSetupService

        with patch("dedox.services.paperless_setup_service.get_settings", return_value=mock_settings):
            with patch("dedox.services.paperless_setup_service.PaperlessService.get_token", return_value="test-token"):
                service = PaperlessSetupService()

                mock_response = MagicMock()
                mock_response.status_code = 200

                # check_connectivity creates its own httpx.AsyncClient, so mock that
                mock_client_instance = AsyncMock()
                mock_client_instance.get = AsyncMock(return_value=mock_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock()

                with patch("dedox.services.paperless_setup_service.httpx.AsyncClient", return_value=mock_client_instance):
                    result = await service.check_connectivity()

        assert result["connected"] is True
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_check_connectivity_auth_failure(self, mock_settings):
        """Should return auth error when token is invalid."""
        from dedox.services.paperless_setup_service import PaperlessSetupService

        with patch("dedox.services.paperless_setup_service.get_settings", return_value=mock_settings):
            with patch("dedox.services.paperless_setup_service.PaperlessService.get_token", return_value="test-token"):
                service = PaperlessSetupService()

                mock_response = MagicMock()
                mock_response.status_code = 401

                # check_connectivity creates its own httpx.AsyncClient, so mock that
                mock_client_instance = AsyncMock()
                mock_client_instance.get = AsyncMock(return_value=mock_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock()

                with patch("dedox.services.paperless_setup_service.httpx.AsyncClient", return_value=mock_client_instance):
                    result = await service.check_connectivity()

        assert result["connected"] is False
        assert "Authentication failed" in result["error"]

    @pytest.mark.asyncio
    async def test_check_workflow_exists_not_found(self, mock_settings):
        """Should return exists=False when workflow doesn't exist."""
        from dedox.services.paperless_setup_service import PaperlessSetupService

        with patch("dedox.services.paperless_setup_service.get_settings", return_value=mock_settings):
            service = PaperlessSetupService()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"results": []}

            with patch.object(service, "_get_client") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.get = AsyncMock(return_value=mock_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock()
                mock_client.return_value = mock_client_instance

                result = await service.check_workflow_exists()

        assert result["exists"] is False

    @pytest.mark.asyncio
    async def test_check_workflow_exists_found(self, mock_settings):
        """Should return workflow details when workflow exists."""
        from dedox.services.paperless_setup_service import PaperlessSetupService, DEDOX_WORKFLOW_NAME

        with patch("dedox.services.paperless_setup_service.get_settings", return_value=mock_settings):
            service = PaperlessSetupService()

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "results": [
                    {"id": 42, "name": DEDOX_WORKFLOW_NAME, "triggers": [1], "actions": [2]}
                ]
            }

            with patch.object(service, "_get_client") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.get = AsyncMock(return_value=mock_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock()
                mock_client.return_value = mock_client_instance

                result = await service.check_workflow_exists()

        assert result["exists"] is True
        assert result["workflow_id"] == 42

    @pytest.mark.asyncio
    async def test_setup_workflow_creates_all_resources(self, mock_settings):
        """Should create workflow with inline trigger and action."""
        from dedox.services.paperless_setup_service import PaperlessSetupService

        with patch("dedox.services.paperless_setup_service.get_settings", return_value=mock_settings):
            with patch("dedox.services.paperless_setup_service.PaperlessService.get_token", return_value="test-token"):
                service = PaperlessSetupService()

                # Mock connectivity check response (for httpx.AsyncClient in check_connectivity)
                connectivity_response = MagicMock()
                connectivity_response.status_code = 200

                # Mock workflow existence check (not found)
                workflow_list_response = MagicMock()
                workflow_list_response.status_code = 200
                workflow_list_response.json.return_value = {"results": []}

                # Mock tags list (empty)
                tags_response = MagicMock()
                tags_response.status_code = 200
                tags_response.json.return_value = {"results": []}

                # Mock workflow creation (inline trigger/action)
                workflow_response = MagicMock()
                workflow_response.status_code = 201
                workflow_response.json.return_value = {"id": 30}

                # Mock for check_connectivity's direct httpx.AsyncClient usage
                connectivity_client = AsyncMock()
                connectivity_client.get = AsyncMock(return_value=connectivity_response)
                connectivity_client.__aenter__ = AsyncMock(return_value=connectivity_client)
                connectivity_client.__aexit__ = AsyncMock()

                # Mock for _get_client usage (workflow check, tags, workflow creation)
                api_client = AsyncMock()
                api_client.get = AsyncMock(side_effect=[
                    workflow_list_response, tags_response
                ])
                api_client.post = AsyncMock(return_value=workflow_response)
                api_client.__aenter__ = AsyncMock(return_value=api_client)
                api_client.__aexit__ = AsyncMock()

                with patch("dedox.services.paperless_setup_service.httpx.AsyncClient", return_value=connectivity_client):
                    with patch.object(service, "_get_client", return_value=api_client):
                        result = await service.setup_dedox_workflow()

        assert result["success"] is True
        assert result["workflow_id"] == 30
        assert "webhook_url" in result

    @pytest.mark.asyncio
    async def test_setup_workflow_already_exists(self, mock_settings):
        """Should return already_exists when workflow exists and force=False."""
        from dedox.services.paperless_setup_service import PaperlessSetupService, DEDOX_WORKFLOW_NAME

        with patch("dedox.services.paperless_setup_service.get_settings", return_value=mock_settings):
            with patch("dedox.services.paperless_setup_service.PaperlessService.get_token", return_value="test-token"):
                service = PaperlessSetupService()

                # Mock connectivity check
                connectivity_response = MagicMock()
                connectivity_response.status_code = 200

                # Mock workflow existence check (found)
                workflow_list_response = MagicMock()
                workflow_list_response.status_code = 200
                workflow_list_response.json.return_value = {
                    "results": [{"id": 42, "name": DEDOX_WORKFLOW_NAME}]
                }

                # Mock for check_connectivity's direct httpx.AsyncClient usage
                connectivity_client = AsyncMock()
                connectivity_client.get = AsyncMock(return_value=connectivity_response)
                connectivity_client.__aenter__ = AsyncMock(return_value=connectivity_client)
                connectivity_client.__aexit__ = AsyncMock()

                # Mock for _get_client usage (workflow check)
                api_client = AsyncMock()
                api_client.get = AsyncMock(return_value=workflow_list_response)
                api_client.__aenter__ = AsyncMock(return_value=api_client)
                api_client.__aexit__ = AsyncMock()

                with patch("dedox.services.paperless_setup_service.httpx.AsyncClient", return_value=connectivity_client):
                    with patch.object(service, "_get_client", return_value=api_client):
                        result = await service.setup_dedox_workflow(force=False)

        assert result["success"] is True
        assert result["already_exists"] is True
        assert result["workflow_id"] == 42

    @pytest.mark.asyncio
    async def test_remove_workflow_success(self, mock_settings):
        """Should remove workflow, triggers, and actions."""
        from dedox.services.paperless_setup_service import PaperlessSetupService, DEDOX_WORKFLOW_NAME

        with patch("dedox.services.paperless_setup_service.get_settings", return_value=mock_settings):
            service = PaperlessSetupService()

            # Mock workflow existence check
            workflow_list_response = MagicMock()
            workflow_list_response.status_code = 200
            workflow_list_response.json.return_value = {
                "results": [{
                    "id": 42,
                    "name": DEDOX_WORKFLOW_NAME,
                    "triggers": [10],
                    "actions": [20]
                }]
            }

            # Mock delete responses
            delete_response = MagicMock()
            delete_response.status_code = 204

            with patch.object(service, "_get_client") as mock_client:
                mock_client_instance = AsyncMock()
                mock_client_instance.get = AsyncMock(return_value=workflow_list_response)
                mock_client_instance.delete = AsyncMock(return_value=delete_response)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock()
                mock_client.return_value = mock_client_instance

                result = await service.remove_dedox_workflow()

        assert result["success"] is True
        assert result["removed_workflow_id"] == 42
        assert 10 in result["removed_trigger_ids"]
        assert 20 in result["removed_action_ids"]

    @pytest.mark.asyncio
    async def test_get_status(self, mock_settings):
        """Should return full integration status."""
        from dedox.services.paperless_setup_service import PaperlessSetupService, DEDOX_WORKFLOW_NAME

        with patch("dedox.services.paperless_setup_service.get_settings", return_value=mock_settings):
            with patch("dedox.services.paperless_setup_service.PaperlessService.get_token", return_value="test-token"):
                service = PaperlessSetupService()

                # Mock connectivity check
                connectivity_response = MagicMock()
                connectivity_response.status_code = 200

                # Mock workflow existence check
                workflow_list_response = MagicMock()
                workflow_list_response.status_code = 200
                workflow_list_response.json.return_value = {
                    "results": [{"id": 42, "name": DEDOX_WORKFLOW_NAME}]
                }

                # Mock for check_connectivity's direct httpx.AsyncClient usage
                connectivity_client = AsyncMock()
                connectivity_client.get = AsyncMock(return_value=connectivity_response)
                connectivity_client.__aenter__ = AsyncMock(return_value=connectivity_client)
                connectivity_client.__aexit__ = AsyncMock()

                # Mock for _get_client usage (workflow check)
                api_client = AsyncMock()
                api_client.get = AsyncMock(return_value=workflow_list_response)
                api_client.__aenter__ = AsyncMock(return_value=api_client)
                api_client.__aexit__ = AsyncMock()

                with patch("dedox.services.paperless_setup_service.httpx.AsyncClient", return_value=connectivity_client):
                    with patch.object(service, "_get_client", return_value=api_client):
                        result = await service.get_status()

        assert result["paperless_connected"] is True
        assert result["workflow_configured"] is True
        assert result["workflow_id"] == 42
        assert "dedox_webhook_url" in result


class TestAdminEndpoints:
    """Tests for admin API endpoints."""

    @pytest_asyncio.fixture
    async def setup_db(self, test_db, mock_settings):
        """Setup database and mock global getter."""
        async def mock_get_database():
            return test_db
        with patch("dedox.db.get_database", mock_get_database):
            yield test_db

    @pytest.fixture
    def app(self, mock_settings):
        """Create test app."""
        from dedox.api.app import create_app
        return create_app()

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        from fastapi.testclient import TestClient
        return TestClient(app)

    @pytest.fixture
    def admin_headers(self, admin_token):
        """Create auth headers with admin token."""
        return {"Authorization": f"Bearer {admin_token}"}

    @pytest.mark.asyncio
    async def test_get_paperless_status_no_token(self, client, setup_db, admin_user, admin_headers):
        """Should return error when Paperless not configured."""
        with patch("dedox.api.routes.admin.get_settings") as mock_settings:
            mock_settings.return_value.paperless.api_token = ""
            mock_settings.return_value.paperless.webhook.enabled = True

            response = client.get("/api/admin/paperless-status", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["paperless_connected"] is False
        assert "not configured" in data["error"]

    @pytest.mark.asyncio
    async def test_setup_paperless_no_token(self, client, setup_db, admin_user, admin_headers):
        """Should return 503 when Paperless not configured."""
        with patch("dedox.api.routes.admin.get_settings") as mock_settings:
            mock_settings.return_value.paperless.api_token = ""

            response = client.post("/api/admin/setup-paperless", headers=admin_headers)

        assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_setup_paperless_success(self, client, setup_db, admin_user, admin_headers):
        """Should create workflow successfully."""
        with patch("dedox.api.routes.admin.get_settings") as mock_settings:
            mock_settings.return_value.paperless.api_token = "test-token"

            with patch("dedox.api.routes.admin.PaperlessSetupService") as mock_service:
                mock_instance = AsyncMock()
                mock_instance.setup_dedox_workflow = AsyncMock(return_value={
                    "success": True,
                    "workflow_id": 42,
                    "trigger_id": 10,
                    "action_id": 20,
                    "webhook_url": "http://dedox:8000/api/webhooks/paperless/document-added",
                    "message": "Successfully created workflow",
                })
                mock_service.return_value = mock_instance

                response = client.post("/api/admin/setup-paperless", headers=admin_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["workflow_id"] == 42

    def test_admin_endpoint_requires_auth(self, client, mock_settings):
        """Should return 401 when not authenticated."""
        response = client.get("/api/admin/paperless-status")
        assert response.status_code == 401


class TestCLI:
    """Tests for CLI commands."""

    def test_setup_paperless_check(self):
        """Test setup-paperless --check command."""
        from dedox.cli import main
        import sys

        with patch.object(sys, "argv", ["dedox", "setup-paperless", "--check"]):
            with patch("dedox.cli._setup_paperless_async") as mock_async:
                mock_async.return_value = None

                # Import and patch argparse behavior
                with patch("dedox.cli.asyncio.run") as mock_run:
                    try:
                        main()
                    except SystemExit:
                        pass  # CLI may call sys.exit

    def test_setup_paperless_force(self):
        """Test setup-paperless --force flag parsing."""
        from dedox.cli import main
        import sys

        with patch.object(sys, "argv", ["dedox", "setup-paperless", "--force"]):
            with patch("dedox.cli.asyncio.run") as mock_run:
                try:
                    main()
                except SystemExit:
                    pass

                # Verify asyncio.run was called
                assert mock_run.called
