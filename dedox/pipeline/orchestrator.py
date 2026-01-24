"""
Pipeline orchestrator for coordinating document processing.

The orchestrator manages the execution of processors in sequence,
handling errors and updating job status.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

from dedox.core.config import get_settings
from dedox.db.database import Database
from dedox.db.repositories import DocumentRepository, JobRepository
from dedox.db.repositories.processing_log_repository import ProcessingLogRepository
from dedox.models.document import Document, DocumentStatus
from dedox.models.job import Job, JobCreate, JobStage, JobStatus
from dedox.models.processing_log import LogLevel
from dedox.pipeline.base import BaseProcessor, ProcessorContext, ProcessorResult
from dedox.pipeline.registry import ProcessorRegistry

logger = logging.getLogger(__name__)


async def _update_paperless_tags_on_failure(paperless_id: int | None, error_message: str) -> None:
    """Update Paperless tags when pipeline fails.

    Removes processing tag and adds error tag.

    Args:
        paperless_id: The Paperless document ID (if known)
        error_message: The error message to log
    """
    if not paperless_id:
        return

    try:
        from dedox.services.paperless_webhook_service import PaperlessWebhookService
        settings = get_settings()
        webhook_service = PaperlessWebhookService()

        # Remove processing tag
        await webhook_service.remove_tag_from_document(
            paperless_id,
            settings.paperless.processing_tag
        )

        # Add error tag
        await webhook_service.add_tag_to_document(
            paperless_id,
            settings.paperless.error_tag
        )

        logger.info(f"Updated Paperless tags for failed document {paperless_id}")
    except Exception as e:
        logger.warning(f"Failed to update Paperless tags for document {paperless_id}: {e}")


class PipelineOrchestrator:
    """Orchestrates the document processing pipeline.

    Manages the execution of registered processors in sequence, handling
    errors and updating job/document status as processing progresses.

    Pipeline Flow:
        1. IMAGE_PROCESSING - Edge detection, perspective correction
        2. OCR - Text extraction via Tesseract
        3. PAPERLESS_UPLOAD - Upload to Paperless (skipped for webhook docs)
        4. METADATA_EXTRACTION - LLM-based field extraction
        5. SENDER_MATCHING - Correspondent deduplication (merged with extraction)
        6. FINALIZATION - Update Paperless metadata, add tags

    State Machines:

        Job States::

            QUEUED ──────► PROCESSING ──────► COMPLETED
                                │
                                ▼
                              FAILED

        Document States::

            PENDING ─────► PROCESSING ─────► COMPLETED
                                │
                                ▼
                              FAILED

    Error Recovery Strategy:

        The pipeline uses a fail-fast approach where any processor failure
        terminates the entire pipeline. Recovery varies by processor type:

        +---------------------+------------------+------------------------+
        | Processor           | Failure Impact   | Recovery Action        |
        +---------------------+------------------+------------------------+
        | IMAGE_PROCESSING    | Non-critical     | Logs warning, original |
        |                     |                  | image used for OCR     |
        +---------------------+------------------+------------------------+
        | OCR                 | Critical         | Job fails, error tag   |
        |                     |                  | added to Paperless     |
        +---------------------+------------------+------------------------+
        | PAPERLESS_UPLOAD    | Skippable        | Skipped for webhook    |
        |                     |                  | docs (already in PL)   |
        +---------------------+------------------+------------------------+
        | METADATA_EXTRACTION | Critical         | Job fails, error tag   |
        |                     |                  | added to Paperless     |
        +---------------------+------------------+------------------------+
        | SENDER_MATCHING     | Non-critical     | Uses raw sender name   |
        |                     |                  | if matching fails      |
        +---------------------+------------------+------------------------+
        | FINALIZATION        | Critical         | Job fails, error tag   |
        |                     |                  | added to Paperless     |
        +---------------------+------------------+------------------------+

        On any failure:
        1. ``dedox:processing`` tag removed from Paperless document
        2. ``dedox:error`` tag added to Paperless document
        3. Job status set to FAILED
        4. Document status set to FAILED
        5. Error details logged to processing_logs table

        Recovery Options:
        - Add ``dedox:reprocess`` tag in Paperless to retry processing
        - Check job logs via ``/api/jobs/{job_id}/logs`` endpoint
        - Manually fix document and remove error tag
    """
    
    def __init__(
        self,
        db: Database,
        registry: ProcessorRegistry | None = None
    ):
        self.db = db
        self.registry = registry or ProcessorRegistry.get_instance()
        self.doc_repo = DocumentRepository(db)
        self.job_repo = JobRepository(db)
        self.log_repo = ProcessingLogRepository(db)

        # Callbacks for progress updates
        self._on_stage_start: Callable[[Job, JobStage], None] | None = None
        self._on_stage_complete: Callable[[Job, JobStage, ProcessorResult], None] | None = None
        self._on_job_complete: Callable[[Job], None] | None = None

    async def _log(
        self,
        job: Job,
        message: str,
        level: LogLevel = LogLevel.INFO,
        stage: str | None = None,
        details: dict | None = None,
    ) -> None:
        """Write a log entry for a job."""
        try:
            await self.log_repo.create(
                job_id=job.id,
                message=message,
                level=level,
                stage=stage,
                details=details,
            )
        except Exception as e:
            logger.warning(f"Failed to write processing log: {e}")
    
    def on_stage_start(self, callback: Callable[[Job, JobStage], None]) -> None:
        """Set callback for stage start events."""
        self._on_stage_start = callback
    
    def on_stage_complete(self, callback: Callable[[Job, JobStage, ProcessorResult], None]) -> None:
        """Set callback for stage complete events."""
        self._on_stage_complete = callback
    
    def on_job_complete(self, callback: Callable[[Job], None]) -> None:
        """Set callback for job complete events."""
        self._on_job_complete = callback
    
    async def create_job(self, document: Document) -> Job:
        """Create a new processing job for a document.
        
        Args:
            document: The document to process.
            
        Returns:
            The created job.
        """
        job_create = JobCreate(document_id=document.id)
        job = await self.job_repo.create(job_create)
        logger.info(f"Created job {job.id} for document {document.id}")
        return job
    
    async def process_document(self, document: Document, job: Job) -> Job:
        """Process a document through the pipeline.
        
        Args:
            document: The document to process.
            job: The job tracking progress.
            
        Returns:
            The updated job after processing.
        """
        logger.info(f"Starting pipeline for document {document.id}, job {job.id}")
        await self._log(job, f"Starting pipeline for document {document.id}")

        # Create processing context
        context = ProcessorContext(
            document=document,
            job=job,
            original_file_path=document.original_path,
        )

        # For webhook-sourced documents, paperless_id is already set
        if document.paperless_id:
            context.paperless_id = document.paperless_id
            logger.info(f"Document has existing paperless_id: {document.paperless_id}")
            await self._log(job, f"Document already in Paperless (ID: {document.paperless_id})")
        
        # Get ordered processors
        processors = self.registry.get_ordered_processors()
        
        if not processors:
            logger.warning("No processors registered!")
            job.mark_failed("No processors registered")
            await self.job_repo.update(job)
            return job
        
        # Execute processors in order
        for processor_class in processors:
            processor = processor_class()

            # Check if processor can handle this context
            if not processor.can_process(context):
                logger.info(f"Skipping {processor.name}: cannot process context")
                job.skip_stage(processor.stage, f"Skipped: {processor.name} cannot process context")
                await self._log(
                    job,
                    f"Skipping {processor.name}: not applicable for this document",
                    level=LogLevel.INFO,
                    stage=processor.stage.value
                )
                await self.job_repo.update(job)
                continue
            
            # Start stage
            job.start_stage(processor.stage, f"Starting {processor.name}")
            await self.job_repo.update(job)
            await self._log(
                job,
                f"Starting {processor.name}",
                level=LogLevel.INFO,
                stage=processor.stage.value
            )

            if self._on_stage_start:
                self._on_stage_start(job, processor.stage)

            try:
                # Execute processor
                logger.info(f"Executing {processor.name}")
                result = await processor.process(context)

                # Handle result
                if result.success:
                    job.complete_stage(result.message)
                    logger.info(f"{processor.name} completed: {result.message}")
                    await self._log(
                        job,
                        f"{processor.name} completed: {result.message}",
                        level=LogLevel.INFO,
                        stage=processor.stage.value
                    )
                else:
                    job.fail_stage(result.error or "Unknown error")
                    logger.error(f"{processor.name} failed: {result.error}")
                    await self._log(
                        job,
                        f"{processor.name} failed: {result.error}",
                        level=LogLevel.ERROR,
                        stage=processor.stage.value
                    )

                    # Stop pipeline on failure
                    job.mark_failed(result.error or "Processing failed")
                    await self.job_repo.update(job)
                    await self._update_document_status(document, DocumentStatus.FAILED)

                    # Update Paperless tags on failure
                    await _update_paperless_tags_on_failure(
                        context.paperless_id or document.paperless_id,
                        result.error or "Processing failed"
                    )

                    if self._on_stage_complete:
                        self._on_stage_complete(job, processor.stage, result)

                    return job
                
                # Update job
                await self.job_repo.update(job)
                
                if self._on_stage_complete:
                    self._on_stage_complete(job, processor.stage, result)
                
            except Exception as e:
                error_msg = f"{processor.name} error: {str(e)}"
                logger.exception(error_msg)
                await self._log(
                    job,
                    f"{processor.name} exception: {str(e)}",
                    level=LogLevel.ERROR,
                    stage=processor.stage.value,
                    details={"exception_type": type(e).__name__}
                )

                job.fail_stage(error_msg)
                job.mark_failed(error_msg)
                await self.job_repo.update(job)
                await self._update_document_status(document, DocumentStatus.FAILED)

                # Update Paperless tags on failure
                await _update_paperless_tags_on_failure(
                    context.paperless_id or document.paperless_id,
                    error_msg
                )

                return job
            
            finally:
                # Always cleanup
                try:
                    await processor.cleanup(context)
                except Exception as e:
                    logger.warning(f"Cleanup error in {processor.name}: {e}")
        
        # All processors completed successfully
        job.mark_completed(result={
            "paperless_id": context.paperless_id,
            "ocr_confidence": context.ocr_confidence,
            "metadata": context.metadata,
        })
        await self.job_repo.update(job)
        await self._log(
            job,
            f"Pipeline completed successfully for document {document.id}",
            level=LogLevel.INFO,
            stage="completed"
        )
        
        # Update document status
        document.paperless_id = context.paperless_id
        document.ocr_text = context.ocr_text
        document.ocr_confidence = context.ocr_confidence
        document.ocr_language = context.ocr_language
        document.processed_path = context.processed_file_path
        document.metadata = context.metadata
        document.metadata_confidence = context.metadata_confidence
        
        # Determine final status
        if context.has_errors():
            document.mark_failed(context.errors[0] if context.errors else "Processing error")
        else:
            document.mark_completed()
        
        await self.doc_repo.update(document)
        
        if self._on_job_complete:
            self._on_job_complete(job)
        
        logger.info(f"Pipeline completed for job {job.id}")
        return job
    
    async def process_async(self, document: Document) -> Job:
        """Start async processing for a document.
        
        Creates a job and starts processing in the background.
        
        Args:
            document: The document to process.
            
        Returns:
            The created job (processing continues in background).
        """
        job = await self.create_job(document)
        
        # Start processing in background
        asyncio.create_task(self._process_background(document, job))
        
        return job
    
    async def _process_background(self, document: Document, job: Job) -> None:
        """Background processing task."""
        try:
            await self.process_document(document, job)
        except Exception as e:
            logger.exception(f"Background processing failed for job {job.id}: {e}")
            job.mark_failed(str(e))
            await self.job_repo.update(job)
    
    async def retry_job(self, job: Job) -> Job:
        """Retry a failed job.
        
        Args:
            job: The job to retry.
            
        Returns:
            The updated job.
        """
        if not job.can_retry():
            logger.warning(f"Job {job.id} cannot be retried (max retries reached)")
            return job
        
        # Get document
        document = await self.doc_repo.get_by_id(job.document_id)
        if not document:
            logger.error(f"Document {job.document_id} not found for retry")
            return job
        
        # Reset job state
        job.retry_count += 1
        job.status = JobStatus.QUEUED
        job.current_stage = JobStage.PENDING
        job.progress_percent = 0
        job.stages = []
        job.completed_at = None
        job.errors = []
        job.result = {}
        
        await self.job_repo.update(job)
        
        # Process again
        return await self.process_document(document, job)
    
    async def _update_document_status(
        self,
        document: Document,
        status: DocumentStatus
    ) -> None:
        """Update document status in database."""
        document.status = status
        document.updated_at = _utcnow()
        await self.doc_repo.update(document)
    
