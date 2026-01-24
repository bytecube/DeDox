"""
Paperless-ngx archiver processor.

Uploads processed documents to Paperless-ngx with initial "Processing..." tag.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

import httpx

from dedox.core.config import get_settings
from dedox.core.exceptions import PaperlessError
from dedox.models.job import JobStage
from dedox.pipeline.base import BaseProcessor, ProcessorContext, ProcessorResult

logger = logging.getLogger(__name__)


class PaperlessArchiver(BaseProcessor):
    """Processor for uploading documents to Paperless-ngx.
    
    Uploads the processed document with a "Processing..." tag,
    which will be updated later after metadata extraction.
    """
    
    @property
    def stage(self) -> JobStage:
        return JobStage.PAPERLESS_UPLOAD
    
    def can_process(self, context: ProcessorContext) -> bool:
        """Check if we can upload to Paperless."""
        settings = get_settings()

        # Skip upload if document already has a paperless_id (already in Paperless)
        if context.document.paperless_id:
            logger.info(f"Skipping Paperless upload - document already in Paperless (ID: {context.document.paperless_id})")
            # Ensure paperless_id is set in context from document
            context.paperless_id = context.document.paperless_id
            return False

        # Skip upload for webhook-sourced documents (already in Paperless)
        if context.document.source == "paperless_webhook":
            logger.info("Skipping Paperless upload - document originated from webhook")
            # Ensure paperless_id is set in context from document
            if context.document.paperless_id:
                context.paperless_id = context.document.paperless_id
            return False

        # Need Paperless configuration
        if not settings.paperless.base_url or not settings.paperless.api_token:
            logger.warning("Paperless-ngx not configured, skipping upload")
            return False

        # Need a file to upload
        file_path = context.processed_file_path or context.original_file_path
        if not file_path or not Path(file_path).exists():
            return False

        return True
    
    async def process(self, context: ProcessorContext) -> ProcessorResult:
        """Upload document to Paperless-ngx."""
        start_time = _utcnow()
        
        try:
            settings = get_settings()
            
            # Get file to upload (prefer processed, fallback to original)
            file_path = context.processed_file_path or context.original_file_path
            path = Path(file_path)
            
            # Ensure processing tag exists
            tag_id = await self._ensure_tag_exists(
                settings.paperless.processing_tag,
                settings
            )
            
            # Upload document
            paperless_task_id = await self._upload_document(
                path,
                context,
                tag_id,
                settings
            )
            
            # Wait for consumption and get document ID
            paperless_id = await self._wait_for_consumption(
                paperless_task_id,
                settings
            )
            
            # Update context
            context.paperless_id = paperless_id
            context.paperless_task_id = paperless_task_id
            context.document.paperless_id = paperless_id
            context.document.paperless_task_id = paperless_task_id
            
            return ProcessorResult.ok(
                stage=self.stage,
                message=f"Uploaded to Paperless-ngx (ID: {paperless_id})",
                data={
                    "paperless_id": paperless_id,
                    "paperless_task_id": paperless_task_id,
                },
                processing_time_ms=self._measure_time(start_time),
            )
            
        except PaperlessError as e:
            logger.error(f"Paperless upload failed: {e}")
            return ProcessorResult.fail(
                stage=self.stage,
                error=str(e),
            )
        except Exception as e:
            logger.exception(f"Paperless upload failed: {e}")
            return ProcessorResult.fail(
                stage=self.stage,
                error=str(e),
            )
    
    async def _ensure_tag_exists(self, tag_name: str, settings) -> int:
        """Ensure a tag exists in Paperless-ngx, create if needed."""
        async with httpx.AsyncClient(
            base_url=settings.paperless.base_url,
            headers=self._get_headers(settings),
            verify=settings.paperless.verify_ssl,
            timeout=settings.paperless.timeout_seconds,
        ) as client:
            # Search for existing tag
            response = await client.get(
                "/api/tags/",
                params={"name__iexact": tag_name}
            )
            
            if response.status_code != 200:
                raise PaperlessError(
                    f"Failed to search tags: {response.text}",
                    status_code=response.status_code
                )
            
            data = response.json()
            if data.get("results"):
                return data["results"][0]["id"]
            
            # Create tag if not exists
            response = await client.post(
                "/api/tags/",
                json={"name": tag_name, "color": settings.paperless.tag_colors.processing}
            )
            
            if response.status_code not in [200, 201]:
                raise PaperlessError(
                    f"Failed to create tag: {response.text}",
                    status_code=response.status_code
                )
            
            return response.json()["id"]
    
    async def _upload_document(
        self,
        file_path: Path,
        context: ProcessorContext,
        tag_id: int,
        settings
    ) -> str:
        """Upload document to Paperless-ngx."""
        async with httpx.AsyncClient(
            base_url=settings.paperless.base_url,
            headers=self._get_headers(settings),
            verify=settings.paperless.verify_ssl,
            timeout=60,  # Longer timeout for uploads
        ) as client:
            # Prepare multipart form data
            with open(file_path, "rb") as f:
                files = {
                    "document": (
                        context.document.original_filename,
                        f,
                        context.document.content_type
                    )
                }
                
                # Form data
                data = {
                    "tags": str(tag_id),
                }
                
                # Add title if we have OCR text (extract first line)
                if context.ocr_text:
                    first_line = context.ocr_text.split("\n")[0][:100].strip()
                    if first_line:
                        data["title"] = first_line
                
                response = await client.post(
                    "/api/documents/post_document/",
                    files=files,
                    data=data,
                )
            
            if response.status_code != 200:
                raise PaperlessError(
                    f"Failed to upload document: {response.text}",
                    status_code=response.status_code
                )
            
            # Response contains task UUID
            task_id = response.text.strip().strip('"')
            logger.info(f"Document uploaded, task ID: {task_id}")
            
            return task_id
    
    async def _wait_for_consumption(
        self,
        task_id: str,
        settings,
        max_wait_seconds: int = 120,
        poll_interval: int = 2
    ) -> int:
        """Wait for Paperless-ngx to consume the document."""
        import asyncio
        
        async with httpx.AsyncClient(
            base_url=settings.paperless.base_url,
            headers=self._get_headers(settings),
            verify=settings.paperless.verify_ssl,
            timeout=settings.paperless.timeout_seconds,
        ) as client:
            waited = 0
            
            while waited < max_wait_seconds:
                response = await client.get(
                    "/api/tasks/",
                    params={"task_id": task_id}
                )
                
                if response.status_code != 200:
                    raise PaperlessError(
                        f"Failed to check task status: {response.text}",
                        status_code=response.status_code
                    )
                
                tasks = response.json()
                
                if tasks:
                    task = tasks[0]
                    status = task.get("status")
                    
                    if status == "SUCCESS":
                        # Get document ID from task result
                        related_doc = task.get("related_document")
                        if related_doc:
                            logger.info(f"Document consumed, ID: {related_doc}")
                            return related_doc
                        
                        raise PaperlessError(
                            "Task succeeded but no document ID returned"
                        )
                    
                    elif status == "FAILURE":
                        error = task.get("result", "Unknown error")
                        raise PaperlessError(f"Consumption failed: {error}")
                
                # Wait and retry
                await asyncio.sleep(poll_interval)
                waited += poll_interval
            
            raise PaperlessError(
                f"Timeout waiting for document consumption after {max_wait_seconds}s"
            )
    
    def _get_headers(self, settings) -> dict[str, str]:
        """Get HTTP headers for Paperless-ngx API."""
        return {
            "Authorization": f"Token {settings.paperless.api_token}",
            "Accept": f"application/json; version={settings.paperless.api_version}",
        }
