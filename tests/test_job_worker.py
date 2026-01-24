"""Tests for the JobWorker service."""

import asyncio
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from uuid import uuid4

from dedox.services.job_worker import JobWorker, start_worker
from dedox.models.job import Job, JobStatus, JobStage
from dedox.models.document import Document, DocumentStatus


@pytest.fixture
def job_worker():
    """Create a JobWorker instance."""
    return JobWorker()


@pytest.fixture
def mock_document():
    """Create a mock document."""
    return Document(
        id=uuid4(),
        filename="test.pdf",
        original_filename="test_document.pdf",
        content_type="application/pdf",
        file_size=1000,
        status=DocumentStatus.PENDING,
        user_id=str(uuid4()),
    )


@pytest.fixture
def mock_job(mock_document):
    """Create a mock job."""
    return Job(
        id=uuid4(),
        document_id=mock_document.id,
        status=JobStatus.QUEUED,
        current_stage=JobStage.PENDING,
        progress=0,
    )


class TestJobWorkerProcessJob:
    """Tests for the process_job method."""

    @pytest.mark.asyncio
    async def test_process_job_not_found(self, job_worker, test_db, mock_settings):
        """Test processing when job is not found."""
        async def mock_get_db():
            return test_db

        with patch('dedox.db.get_database', mock_get_db):
            # Should not raise, just log error
            await job_worker.process_job(str(uuid4()))

    @pytest.mark.asyncio
    async def test_process_job_already_completed(self, job_worker, test_db, mock_settings, mock_job):
        """Test processing when job is already completed."""
        async def mock_get_db():
            return test_db

        mock_job.status = JobStatus.COMPLETED

        mock_job_repo = MagicMock()
        mock_job_repo.get_by_id = AsyncMock(return_value=mock_job)

        with patch('dedox.db.get_database', mock_get_db):
            with patch('dedox.services.job_worker.JobRepository', return_value=mock_job_repo):
                await job_worker.process_job(str(mock_job.id))

        # Job should not be processed if already completed
        mock_job_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_job_document_not_found(self, job_worker, test_db, mock_settings, mock_job):
        """Test processing when document is not found."""
        async def mock_get_db():
            return test_db

        mock_job_repo = MagicMock()
        mock_job_repo.get_by_id = AsyncMock(return_value=mock_job)
        mock_job_repo.update_status = AsyncMock()

        mock_doc_repo = MagicMock()
        mock_doc_repo.get_by_id = AsyncMock(return_value=None)

        with patch('dedox.db.get_database', mock_get_db):
            with patch('dedox.services.job_worker.JobRepository', return_value=mock_job_repo):
                with patch('dedox.services.job_worker.DocumentRepository', return_value=mock_doc_repo):
                    await job_worker.process_job(str(mock_job.id))

        # Should mark job as failed
        mock_job_repo.update_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_job_success(self, job_worker, test_db, mock_settings, mock_job, mock_document):
        """Test successful job processing."""
        async def mock_get_db():
            return test_db

        mock_job_repo = MagicMock()
        mock_job_repo.get_by_id = AsyncMock(return_value=mock_job)

        mock_doc_repo = MagicMock()
        mock_doc_repo.get_by_id = AsyncMock(return_value=mock_document)

        mock_orchestrator = MagicMock()
        mock_orchestrator.process_document = AsyncMock()

        with patch('dedox.db.get_database', mock_get_db):
            with patch('dedox.services.job_worker.JobRepository', return_value=mock_job_repo):
                with patch('dedox.services.job_worker.DocumentRepository', return_value=mock_doc_repo):
                    with patch.object(job_worker, 'orchestrator', mock_orchestrator):
                        job_worker.orchestrator = mock_orchestrator
                        await job_worker.process_job(str(mock_job.id))

        mock_orchestrator.process_document.assert_called_once()


class TestJobWorkerRunWorkerLoop:
    """Tests for the run_worker_loop method."""

    @pytest.mark.asyncio
    async def test_run_worker_loop_processes_jobs(self, job_worker, test_db, mock_settings, mock_job):
        """Test that worker loop processes pending jobs."""
        async def mock_get_db():
            return test_db

        mock_job_repo = MagicMock()
        mock_job_repo.get_pending_jobs = AsyncMock(side_effect=[
            [mock_job],  # First call returns a job
            [],  # Second call returns empty
        ])

        job_worker.process_job = AsyncMock()

        with patch('dedox.db.get_database', mock_get_db):
            with patch('dedox.services.job_worker.JobRepository', return_value=mock_job_repo):
                # Run worker loop with a task that cancels after first iteration
                async def run_with_cancel():
                    task = asyncio.create_task(job_worker.run_worker_loop(poll_interval=0.1))
                    await asyncio.sleep(0.2)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                await run_with_cancel()

        # Verify process_job was called
        assert job_worker.process_job.called

    @pytest.mark.asyncio
    async def test_run_worker_loop_handles_cancellation(self, job_worker, test_db, mock_settings):
        """Test that worker loop handles cancellation gracefully."""
        async def mock_get_db():
            return test_db

        mock_job_repo = MagicMock()
        mock_job_repo.get_pending_jobs = AsyncMock(return_value=[])

        with patch('dedox.db.get_database', mock_get_db):
            with patch('dedox.services.job_worker.JobRepository', return_value=mock_job_repo):
                task = asyncio.create_task(job_worker.run_worker_loop(poll_interval=0.1))
                await asyncio.sleep(0.15)  # Give time for at least one iteration
                task.cancel()

                # Task should be cancelled - may or may not raise depending on implementation
                try:
                    await task
                except asyncio.CancelledError:
                    pass  # Expected behavior
                # Test passes if we get here without other exceptions


class TestJobWorkerProcessAllPending:
    """Tests for the process_all_pending method."""

    @pytest.mark.asyncio
    async def test_process_all_pending_empty_queue(self, job_worker, test_db, mock_settings):
        """Test processing when queue is empty."""
        async def mock_get_db():
            return test_db

        mock_job_repo = MagicMock()
        mock_job_repo.get_jobs_by_status = AsyncMock(return_value=[])

        with patch('dedox.db.get_database', mock_get_db):
            with patch('dedox.services.job_worker.JobRepository', return_value=mock_job_repo):
                count = await job_worker.process_all_pending()

        assert count == 0

    @pytest.mark.asyncio
    async def test_process_all_pending_with_jobs(self, job_worker, test_db, mock_settings, mock_job):
        """Test processing multiple pending jobs."""
        async def mock_get_db():
            return test_db

        mock_jobs = [mock_job]

        mock_job_repo = MagicMock()
        mock_job_repo.get_jobs_by_status = AsyncMock(return_value=mock_jobs)

        job_worker.process_job = AsyncMock()

        with patch('dedox.db.get_database', mock_get_db):
            with patch('dedox.services.job_worker.JobRepository', return_value=mock_job_repo):
                count = await job_worker.process_all_pending(max_concurrent=3)

        assert count == 1
        job_worker.process_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_all_pending_respects_concurrency(self, job_worker, test_db, mock_settings):
        """Test that concurrency limit is respected."""
        # Create multiple mock jobs
        mock_jobs = [
            Job(
                id=uuid4(),
                document_id=uuid4(),
                status=JobStatus.QUEUED,
                current_stage=JobStage.PENDING,
                progress=0,
            )
            for _ in range(5)
        ]

        async def mock_get_db():
            return test_db

        mock_job_repo = MagicMock()
        mock_job_repo.get_jobs_by_status = AsyncMock(return_value=mock_jobs)

        concurrent_count = 0
        max_concurrent_seen = 0

        async def track_concurrency(job_id):
            nonlocal concurrent_count, max_concurrent_seen
            concurrent_count += 1
            max_concurrent_seen = max(max_concurrent_seen, concurrent_count)
            await asyncio.sleep(0.05)
            concurrent_count -= 1

        job_worker.process_job = track_concurrency

        with patch('dedox.db.get_database', mock_get_db):
            with patch('dedox.services.job_worker.JobRepository', return_value=mock_job_repo):
                await job_worker.process_all_pending(max_concurrent=2)

        # Should not exceed max_concurrent
        assert max_concurrent_seen <= 2


class TestStartWorker:
    """Tests for the start_worker function."""

    @pytest.mark.asyncio
    async def test_start_worker_registers_processors(self, mock_settings):
        """Test that start_worker registers all processors."""
        with patch('dedox.pipeline.processors.register_all_processors') as mock_register:
            with patch('dedox.services.paperless_service.init_paperless', new_callable=AsyncMock, return_value=True):
                # Create a task and cancel it quickly
                with patch.object(JobWorker, 'run_worker_loop', new_callable=AsyncMock) as mock_loop:
                    mock_loop.side_effect = asyncio.CancelledError()
                    try:
                        await start_worker()
                    except asyncio.CancelledError:
                        pass

        mock_register.assert_called_once()
