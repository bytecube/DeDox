"""Job worker - processes document jobs through the pipeline."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from uuid import UUID

from dedox.core.config import get_settings
from dedox.core.exceptions import DedoxError
from dedox.db import get_database
from dedox.db.repositories.document_repository import DocumentRepository
from dedox.db.repositories.job_repository import JobRepository
from dedox.models.document import DocumentStatus
from dedox.models.job import JobStatus, JobStage
from dedox.pipeline.orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)


class JobWorker:
    """Background worker for processing document jobs."""
    
    def __init__(self):
        self.orchestrator = None  # Initialized lazily when we have db connection
    
    async def _ensure_orchestrator(self):
        """Ensure the orchestrator is initialized with database."""
        if self.orchestrator is None:
            db = await get_database()
            self.orchestrator = PipelineOrchestrator(db)
    
    async def process_job(self, job_id: str) -> None:
        """Process a single job.
        
        Args:
            job_id: ID of the job to process
        """
        db = await get_database()
        job_repo = JobRepository(db)
        doc_repo = DocumentRepository(db)
        
        # Get job
        job = await job_repo.get_by_id(UUID(job_id))
        if not job:
            logger.error(f"Job not found: {job_id}")
            return
        
        # Check if already processed or cancelled
        if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED):
            logger.info(f"Job already in terminal state: {job.status}")
            return
        
        # Get document
        document = await doc_repo.get_by_id(job.document_id)
        if not document:
            logger.error(f"Document not found: {job.document_id}")
            await job_repo.update_status(job_id, JobStatus.FAILED, "Document not found")
            return
        
        logger.info(f"Starting job {job_id} for document {document.id}")
        
        try:
            # Ensure orchestrator is initialized
            await self._ensure_orchestrator()
            
            # Use orchestrator to process document - it handles everything
            await self.orchestrator.process_document(document, job)
            
            logger.info(f"Job {job_id} completed")
                
        except Exception as e:
            logger.exception(f"Job {job_id} failed with exception: {e}")
            await job_repo.update_status(job_id, JobStatus.FAILED, str(e))
            await doc_repo.update_by_id(str(document.id), {"status": DocumentStatus.FAILED.value})
    
    async def run_worker_loop(self, poll_interval: float = 5.0) -> None:
        """Run the worker loop, processing pending jobs.
        
        Args:
            poll_interval: Seconds between queue checks
        """
        logger.info("Starting job worker loop")
        
        while True:
            try:
                db = await get_database()
                job_repo = JobRepository(db)
                
                # Get next pending job
                pending_jobs = await job_repo.get_pending_jobs(limit=1)
                job = pending_jobs[0] if pending_jobs else None
                
                if job:
                    await self.process_job(str(job.id))
                else:
                    # No pending jobs, wait before checking again
                    await asyncio.sleep(poll_interval)
                    
            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                break
            except Exception as e:
                logger.exception(f"Worker loop error: {e}")
                await asyncio.sleep(poll_interval)
    
    async def process_all_pending(self, max_concurrent: int = 3) -> int:
        """Process all pending jobs with concurrency limit.
        
        Args:
            max_concurrent: Maximum concurrent jobs
            
        Returns:
            Number of jobs processed
        """
        db = await get_database()
        job_repo = JobRepository(db)
        
        # Get all pending jobs
        pending_jobs = await job_repo.get_jobs_by_status(JobStatus.QUEUED)
        
        if not pending_jobs:
            return 0
        
        processed = 0
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def process_with_limit(job_id: str):
            nonlocal processed
            async with semaphore:
                await self.process_job(job_id)
                processed += 1
        
        tasks = [process_with_limit(str(job.id)) for job in pending_jobs]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        return processed


async def start_worker():
    """Start the background job worker."""
    # Register pipeline processors
    from dedox.pipeline.processors import register_all_processors
    register_all_processors()
    logger.info("Pipeline processors registered")

    # Initialize Paperless connection (auto-fetch token if needed)
    from dedox.services.paperless_service import init_paperless
    paperless_ok = await init_paperless()
    if paperless_ok:
        logger.info("Paperless-ngx integration initialized")
    else:
        logger.warning("Paperless-ngx integration not available")

    worker = JobWorker()
    await worker.run_worker_loop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    asyncio.run(start_worker())
