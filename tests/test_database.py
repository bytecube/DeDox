"""Tests for the database layer."""

import pytest
import pytest_asyncio
from datetime import datetime
from uuid import uuid4

from dedox.db.database import Database
from dedox.db.repositories.user_repository import UserRepository
from dedox.db.repositories.document_repository import DocumentRepository
from dedox.db.repositories.job_repository import JobRepository
from dedox.models.user import UserCreate, UserRole
from dedox.models.document import DocumentCreate, DocumentStatus
from dedox.models.job import JobCreate, JobStatus, JobStage


class TestDatabase:
    """Tests for the Database class."""
    
    @pytest_asyncio.fixture
    async def db(self, temp_dir):
        """Create a test database."""
        db_path = temp_dir / "test_database.db"
        database = Database(str(db_path))
        await database.connect()
        await database.init_schema()
        yield database
        await database.disconnect()
    
    @pytest.mark.asyncio
    async def test_connect_disconnect(self, temp_dir):
        """Test database connection lifecycle."""
        db_path = temp_dir / "test_connect.db"
        db = Database(str(db_path))
        
        await db.connect()
        assert db._connection is not None
        
        await db.disconnect()
        assert db._connection is None
    
    @pytest.mark.asyncio
    async def test_insert_and_fetch(self, db):
        """Test basic insert and fetch operations."""
        # Insert
        data = {
            "id": str(uuid4()),
            "username": "testuser_fetch",
            "email": "test_fetch@example.com",
            "hashed_password": "hash",
            "role": "user",
            "is_active": True,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        await db.insert("users", data)
        
        # Fetch
        row = await db.fetch_one(
            "SELECT * FROM users WHERE username = ?",
            ("testuser_fetch",)
        )

        assert row is not None
        assert row["username"] == "testuser_fetch"
        assert row["email"] == "test_fetch@example.com"
    
    @pytest.mark.asyncio
    async def test_update(self, db):
        """Test update operation."""
        user_id = str(uuid4())

        # Insert
        await db.insert("users", {
            "id": user_id,
            "username": "updatetest",
            "email": "update@example.com",
            "hashed_password": "hash",
            "role": "user",
            "is_active": True,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        })
        
        # Update
        await db.update(
            "users",
            {"email": "updated@example.com"},
            "id = ?",
            (user_id,)
        )
        
        # Verify
        row = await db.fetch_one(
            "SELECT email FROM users WHERE id = ?",
            (user_id,)
        )
        assert row["email"] == "updated@example.com"
    
    @pytest.mark.asyncio
    async def test_delete(self, db):
        """Test delete operation."""
        user_id = str(uuid4())

        # Insert
        await db.insert("users", {
            "id": user_id,
            "username": "deletetest",
            "email": "delete@example.com",
            "hashed_password": "hash",
            "role": "user",
            "is_active": True,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        })
        
        # Delete
        await db.delete("users", "id = ?", (user_id,))
        
        # Verify
        row = await db.fetch_one(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        )
        assert row is None


class TestUserRepository:
    """Tests for UserRepository."""
    
    @pytest_asyncio.fixture
    async def repo(self, test_db):
        """Create user repository."""
        return UserRepository(test_db)
    
    @pytest.mark.asyncio
    async def test_create_user(self, repo):
        """Test user creation."""
        from uuid import uuid4
        unique_id = str(uuid4())[:8]
        user_create = UserCreate(
            username=f"newuser_{unique_id}",
            email=f"new_{unique_id}@example.com",
            password="password123",
            role=UserRole.USER,
        )

        user = await repo.create(user_create)

        assert user is not None
        assert user.username == f"newuser_{unique_id}"
        assert user.email == f"new_{unique_id}@example.com"
        assert user.role == UserRole.USER
    
    @pytest.mark.asyncio
    async def test_verify_password(self, repo):
        """Test password verification."""
        # Create user
        user_create = UserCreate(
            username="passtest",
            email="pass@example.com",
            password="correctpassword",
            role=UserRole.USER,
        )
        await repo.create(user_create)
        
        # Verify correct password
        user = await repo.verify_password("passtest", "correctpassword")
        assert user is not None
        
        # Verify wrong password
        user = await repo.verify_password("passtest", "wrongpassword")
        assert user is None
    
    @pytest.mark.asyncio
    async def test_get_by_username(self, repo):
        """Test getting user by username."""
        user_create = UserCreate(
            username="findme",
            email="find@example.com",
            password="password",
            role=UserRole.USER,
        )
        await repo.create(user_create)
        
        user = await repo.get_by_username("findme")
        assert user is not None
        assert user.username == "findme"
        
        user = await repo.get_by_username("nonexistent")
        assert user is None


class TestDocumentRepository:
    """Tests for DocumentRepository."""
    
    @pytest_asyncio.fixture
    async def repo(self, test_db):
        """Create document repository."""
        return DocumentRepository(test_db)
    
    @pytest.mark.asyncio
    async def test_create_document(self, repo, temp_dir):
        """Test document creation."""
        doc_create = DocumentCreate(
            filename="test.jpg",
            content_type="image/jpeg",
            file_size=1024,
        )

        doc = await repo.create(doc_create, str(temp_dir / "test.jpg"))

        assert doc is not None
        assert doc.filename == "test.jpg"
        assert doc.status == DocumentStatus.PENDING
    
    @pytest.mark.asyncio
    async def test_get_by_hash(self, repo, temp_dir):
        """Test getting document by hash (via update after create)."""
        from dedox.models.document import Document

        # Create document first
        doc_create = DocumentCreate(
            filename="hashtest.jpg",
            content_type="image/jpeg",
            file_size=1024,
        )
        doc = await repo.create(doc_create, str(temp_dir / "hashtest.jpg"))

        # Update with file hash (this would normally happen during processing)
        doc.file_hash = "unique_hash_123"
        await repo.update(doc)

        # Now test get_by_hash
        fetched = await repo.get_by_hash("unique_hash_123")
        assert fetched is not None
        assert fetched.file_hash == "unique_hash_123"
    
    @pytest.mark.asyncio
    async def test_update_metadata(self, repo, temp_dir):
        """Test metadata update via document update."""
        doc_create = DocumentCreate(
            filename="metadata.jpg",
            content_type="image/jpeg",
            file_size=1024,
        )
        doc = await repo.create(doc_create, str(temp_dir / "metadata.jpg"))

        # Update metadata through the document model
        doc.metadata = {
            "document_type": "invoice",
            "sender": "Test Company",
            "total_amount": 100.50,
        }
        await repo.update(doc)

        # Fetch and verify
        fetched = await repo.get_by_id(doc.id)
        assert fetched is not None
        assert fetched.metadata["document_type"] == "invoice"
        assert fetched.metadata["sender"] == "Test Company"


class TestJobRepository:
    """Tests for JobRepository."""
    
    @pytest_asyncio.fixture
    async def repo(self, test_db):
        """Create job repository."""
        return JobRepository(test_db)
    
    @pytest_asyncio.fixture
    async def test_document(self, test_db, test_user):
        """Create a test document."""
        doc_repo = DocumentRepository(test_db)
        doc_create = DocumentCreate(
            filename="job_test.jpg",
            original_filename="job.jpg",
            content_type="image/jpeg",
            file_hash="job_hash_123",
            file_size=1024,
            user_id=str(test_user.id),
        )
        return await doc_repo.create(doc_create, "/tmp/test/job_test.jpg")
    
    @pytest.mark.asyncio
    async def test_create_job(self, repo, test_document):
        """Test job creation."""
        job_create = JobCreate(document_id=test_document.id)

        job = await repo.create(job_create)

        assert job is not None
        assert job.status == JobStatus.QUEUED
        assert job.current_stage == JobStage.PENDING
    
    @pytest.mark.asyncio
    async def test_update_status(self, repo, test_document):
        """Test job status update."""
        job_create = JobCreate(document_id=test_document.id)
        job = await repo.create(job_create)
        
        await repo.update_status(str(job.id), JobStatus.PROCESSING)
        
        updated = await repo.get_by_id(str(job.id))
        assert updated.status == JobStatus.PROCESSING
    
    @pytest.mark.asyncio
    async def test_get_pending_jobs(self, repo, test_document):
        """Test getting pending jobs."""
        # Create multiple jobs
        for i in range(3):
            await repo.create(JobCreate(document_id=test_document.id))

        # Get pending jobs
        jobs = await repo.get_pending_jobs(limit=10)
        assert len(jobs) >= 3
        assert jobs[0].status == JobStatus.QUEUED
