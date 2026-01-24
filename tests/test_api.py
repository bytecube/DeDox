"""Tests for API routes."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient


class TestHealthRoutes:
    """Tests for health check routes."""
    
    @pytest.fixture
    def client(self, mock_settings):
        """Create test client."""
        from dedox.api.app import create_app
        
        app = create_app()
        return TestClient(app)
    
    def test_health_check(self, client):
        """Test basic health check."""
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "dedox"
    
    def test_root_endpoint(self, client):
        """Test root endpoint redirects to login."""
        # Root endpoint redirects to login for unauthenticated users
        response = client.get("/", follow_redirects=False)

        # Should redirect to login page
        assert response.status_code == 302
        assert "/login" in response.headers.get("location", "")


class TestAuthRoutes:
    """Tests for authentication routes."""
    
    @pytest_asyncio.fixture
    async def setup_db(self, test_db, mock_settings):
        """Setup database and mock global getter."""
        async def mock_get_database():
            return test_db
        with patch("dedox.db.get_database", mock_get_database):
            yield test_db
    
    @pytest.fixture
    def client(self, mock_settings):
        """Create test client."""
        from dedox.api.app import create_app
        
        app = create_app()
        return TestClient(app)
    
    @pytest.mark.asyncio
    async def test_register_user(self, client, setup_db):
        """Test user registration."""
        response = client.post(
            "/api/auth/register",
            json={
                "username": "newuser",
                "email": "new@example.com",
                "password": "password123",
            }
        )
        
        # Registration might be disabled or succeed
        assert response.status_code in [201, 403]
    
    @pytest.mark.asyncio
    async def test_login(self, client, setup_db, test_user):
        """Test user login."""
        response = client.post(
            "/api/auth/login",
            json={
                "username": test_user.username,  # Use actual username from fixture
                "password": "testpassword123",
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
    
    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client, setup_db, test_user):
        """Test login with wrong password."""
        response = client.post(
            "/api/auth/login",
            json={
                "username": "testuser",
                "password": "wrongpassword",
            }
        )
        
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_get_current_user(self, client, setup_db, test_user, auth_token):
        """Test getting current user info."""
        response = client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {auth_token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["username"] == test_user.username


class TestDocumentRoutes:
    """Tests for document routes."""

    @pytest_asyncio.fixture
    async def setup_db(self, test_db, mock_settings):
        """Setup database and mock global getter."""
        async def mock_get_database():
            return test_db
        with patch("dedox.db.get_database", mock_get_database):
            yield test_db
    
    @pytest.fixture
    def client(self, mock_settings):
        """Create test client."""
        from dedox.api.app import create_app
        
        app = create_app()
        return TestClient(app)
    
    @pytest.mark.asyncio
    async def test_list_documents_unauthorized(self, client):
        """Test listing documents without auth."""
        response = client.get("/api/documents")

        assert response.status_code == 401


class TestJobRoutes:
    """Tests for job routes."""

    @pytest_asyncio.fixture
    async def setup_db(self, test_db, mock_settings):
        """Setup database and mock global getter."""
        async def mock_get_database():
            return test_db
        with patch("dedox.db.get_database", mock_get_database):
            yield test_db
    
    @pytest.fixture
    def client(self, mock_settings):
        """Create test client."""
        from dedox.api.app import create_app
        
        app = create_app()
        return TestClient(app)
    
    @pytest.mark.asyncio
    async def test_list_jobs_unauthorized(self, client):
        """Test listing jobs without auth."""
        response = client.get("/api/jobs")
        
        assert response.status_code == 401
    
    @pytest.mark.asyncio
    async def test_list_jobs_empty(self, client, setup_db, auth_token):
        """Test listing jobs when empty."""
        response = client.get(
            "/api/jobs",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["jobs"] == []
        assert data["total"] == 0


class TestSearchRoutes:
    """Tests for search routes."""

    @pytest_asyncio.fixture
    async def setup_db(self, test_db, mock_settings):
        """Setup database and mock global getter."""
        async def mock_get_database():
            return test_db
        with patch("dedox.db.get_database", mock_get_database):
            yield test_db
    
    @pytest.fixture
    def client(self, mock_settings):
        """Create test client."""
        from dedox.api.app import create_app
        
        app = create_app()
        return TestClient(app)
    
    @pytest.mark.asyncio
    async def test_get_recent_documents(self, client, setup_db, auth_token):
        """Test getting recent documents."""
        response = client.get(
            "/api/search/recent",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "documents" in data


class TestConfigRoutes:
    """Tests for configuration routes."""
    
    @pytest.fixture
    def client(self, mock_settings):
        """Create test client."""
        from dedox.api.app import create_app
        
        app = create_app()
        return TestClient(app)
    
    @pytest_asyncio.fixture
    async def setup_db(self, test_db, mock_settings):
        """Setup database and mock global getter."""
        async def mock_get_database():
            return test_db
        with patch("dedox.db.get_database", mock_get_database):
            yield test_db
    
    @pytest.mark.asyncio
    async def test_get_metadata_fields(self, client, setup_db, auth_token):
        """Test getting metadata fields config."""
        response = client.get(
            "/api/config/metadata-fields",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "fields" in data
    
    @pytest.mark.asyncio
    async def test_get_document_types(self, client, setup_db, auth_token):
        """Test getting document types config."""
        response = client.get(
            "/api/config/document-types",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "document_types" in data
    
    @pytest.mark.asyncio
    async def test_get_public_settings(self, client, setup_db, auth_token):
        """Test getting public settings."""
        response = client.get(
            "/api/config/settings",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "ocr" in data
        assert "llm" in data
    
    @pytest.mark.asyncio
    async def test_get_full_settings_requires_admin(self, client, setup_db, auth_token):
        """Test getting full settings requires admin."""
        response = client.get(
            "/api/config/settings/full",
            headers={"Authorization": f"Bearer {auth_token}"}
        )
        
        # Should be forbidden for regular user
        assert response.status_code == 403
    
    @pytest.mark.asyncio
    async def test_get_full_settings_as_admin(self, client, setup_db, admin_token):
        """Test getting full settings as admin."""
        response = client.get(
            "/api/config/settings/full",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "server" in data
        assert "storage" in data
