"""Document service - handles document operations and pipeline triggering."""

import logging
from pathlib import Path

from dedox.core.config import get_settings
from dedox.db import get_database
from dedox.db.repositories.document_repository import DocumentRepository
from dedox.db.repositories.job_repository import JobRepository
from dedox.models.document import Document, DocumentStatus
from dedox.models.job import Job, JobCreate

logger = logging.getLogger(__name__)


class DocumentService:
    """Service for document management operations."""

    async def reprocess_document(self, document: Document) -> Job:
        """Reprocess an existing document.
        
        Args:
            document: Document to reprocess
            
        Returns:
            New processing job
        """
        db = await get_database()
        doc_repo = DocumentRepository(db)
        job_repo = JobRepository(db)
        
        # Update document status
        await doc_repo.update_by_id(
            str(document.id),
            {
                "status": DocumentStatus.PENDING.value,
            }
        )
        
        # Create new job
        job_create = JobCreate(document_id=document.id)
        job = await job_repo.create(job_create)
        
        # Queue for processing
        await self._queue_job(job)
        
        return job
    
    async def delete_document(self, document: Document) -> None:
        """Delete a document and all associated data.
        
        Args:
            document: Document to delete
        """
        db = await get_database()
        doc_repo = DocumentRepository(db)
        
        # Delete files
        original_path = self._get_original_path(document.filename)
        processed_path = self._get_processed_path(document.filename)
        
        if original_path.exists():
            original_path.unlink()
            logger.info(f"Deleted original: {original_path}")
        
        if processed_path.exists():
            processed_path.unlink()
            logger.info(f"Deleted processed: {processed_path}")

        # Delete jobs
        await db.delete("jobs", "document_id = ?", (str(document.id),))
        
        # Delete document
        await doc_repo.delete(str(document.id))
        
        logger.info(f"Deleted document: {document.id}")
    
    def _get_original_path(self, filename: str) -> Path:
        """Get the path for original files."""
        settings = get_settings()
        return Path(settings.storage.base_path) / settings.storage.originals_dir / filename
    
    def _get_processed_path(self, filename: str) -> Path:
        """Get the path for processed files."""
        settings = get_settings()
        # Change extension to .pdf for processed files
        stem = Path(filename).stem
        return Path(settings.storage.base_path) / settings.storage.processed_dir / f"{stem}.pdf"
    
    async def _queue_job(self, job: Job) -> None:
        """Queue a job for background processing.
        
        For simplicity, we use an in-process task queue.
        In production, this could use Celery, RQ, or similar.
        """
        import asyncio
        from dedox.services.job_worker import JobWorker
        
        # Start processing in background
        worker = JobWorker()
        asyncio.create_task(worker.process_job(str(job.id)))
        logger.info(f"Queued job for processing: {job.id}")
    
    async def get_document_with_metadata(self, document_id: str) -> dict:
        """Get document with all metadata.
        
        Args:
            document_id: Document ID
            
        Returns:
            Document data with metadata
        """
        db = await get_database()
        doc_repo = DocumentRepository(db)
        
        document = await doc_repo.get_by_id(document_id)
        if not document:
            return None
        
        metadata = await doc_repo.get_metadata(document_id)
        
        return {
            "document": document,
            "metadata": metadata,
        }
