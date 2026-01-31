"""
Finalizer processor - completes the pipeline.

Updates Paperless-ngx metadata, removes processing tags,
marks document for review if needed.

Supports both upload-originated and webhook-originated documents.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

import httpx

from dedox.core.config import get_settings
from dedox.core.exceptions import PaperlessConnectionError, PaperlessAPIError
from dedox.db import get_database
from dedox.models.document import DocumentStatus
from dedox.models.job import JobStage
from dedox.pipeline.base import BaseProcessor, ProcessorContext, ProcessorResult
from dedox.services.paperless_webhook_service import PaperlessWebhookService

logger = logging.getLogger(__name__)


class Finalizer(BaseProcessor):
    """Finalizes processing and updates Paperless-ngx.

    This processor:
    - Updates Paperless-ngx document metadata
    - Removes "Processing..." tag
    - Adds "Needs Review" tag if confidence is low
    - Updates local database status
    """

    @property
    def stage(self) -> JobStage:
        return JobStage.FINALIZATION

    def can_process(self, context: ProcessorContext) -> bool:
        """Check if we can finalize."""
        # We should always be able to finalize at this point
        return True
    
    async def process(self, context: ProcessorContext) -> ProcessorResult:
        """Finalize the document processing."""
        start_time = _utcnow()

        try:
            settings = get_settings()
            results = {}

            # Update Paperless if we have a document ID
            if context.paperless_id:
                paperless_result = await self._update_paperless_webhook(context)
                results["paperless_updated"] = paperless_result

            # Update local document status
            await self._update_document_status(context)
            results["document_updated"] = True

            return ProcessorResult.ok(
                stage=self.stage,
                message="Document processing finalized",
                data=results,
                processing_time_ms=self._measure_time(start_time),
            )

        except Exception as e:
            logger.exception(f"Finalization failed: {e}")
            return ProcessorResult.fail(
                stage=self.stage,
                error=str(e),
            )

    async def _update_paperless_webhook(self, context: ProcessorContext) -> dict[str, Any]:
        """Update Paperless document for webhook-originated documents.

        Uses PaperlessWebhookService to:
        - Update metadata as custom fields
        - Remove processing tag
        - Add enhanced tag
        """
        settings = get_settings()
        webhook_service = PaperlessWebhookService()

        # Prepare title from metadata
        title = None
        if context.metadata:
            if context.metadata.get("subject"):
                title = context.metadata["subject"]
            elif context.metadata.get("document_type") and context.metadata.get("sender"):
                title = f"{context.metadata['document_type']} - {context.metadata['sender']}"

            if title and len(title) > 128:
                title = title[:125] + "..."

        # Sync OCR text to Paperless content field
        # This updates the searchable text in Paperless with our extracted text
        if context.ocr_text:
            content_updated = await webhook_service.update_document_content(
                paperless_id=context.paperless_id,
                content=context.ocr_text
            )
            if content_updated:
                logger.info(
                    f"Synced {len(context.ocr_text)} chars of OCR text to Paperless"
                )
            else:
                logger.warning(
                    f"Failed to sync OCR text to Paperless for document {context.paperless_id}"
                )

        # Finalize the document in Paperless
        await webhook_service.finalize_document_processing(
            paperless_id=context.paperless_id,
            metadata=context.metadata or {},
            success=True,
            title=title,
        )

        # Handle correspondent and document type via standard Paperless API
        async with httpx.AsyncClient(
            base_url=settings.paperless.url,
            timeout=30.0,
        ) as client:
            headers = {"Authorization": f"Token {settings.paperless.api_token}"}
            update_data = {}

            if context.metadata:
                # Set correspondent
                if context.metadata.get("sender"):
                    correspondent_id = await self._get_or_create_correspondent(
                        client, headers, context.metadata["sender"]
                    )
                    if correspondent_id:
                        update_data["correspondent"] = correspondent_id

                # Set document type
                if context.metadata.get("document_type"):
                    doc_type_id = await self._get_or_create_document_type(
                        client, headers, context.metadata["document_type"]
                    )
                    if doc_type_id:
                        update_data["document_type"] = doc_type_id

                # Set created date
                if context.metadata.get("document_date"):
                    doc_date = context.metadata["document_date"]
                    if hasattr(doc_date, 'strftime'):
                        update_data["created"] = doc_date.strftime("%Y-%m-%d")
                    elif isinstance(doc_date, str):
                        parsed_date = self._parse_date_string(doc_date)
                        if parsed_date:
                            update_data["created"] = parsed_date

            if update_data:
                response = await client.patch(
                    f"/api/documents/{context.paperless_id}/",
                    headers=headers,
                    json=update_data,
                )
                if response.status_code != 200:
                    logger.warning(f"Failed to update correspondent/type: {response.text}")

        logger.info(f"Finalized webhook document {context.paperless_id} in Paperless")
        return {
            "document_id": context.paperless_id,
            "source": "paperless_webhook",
            "success": True,
        }

    def _sanitize_name(self, name: str) -> str:
        """Sanitize a name extracted by LLM.

        Handles cases where LLM outputs verbose text instead of just the name.
        """
        if not name:
            return name

        # Remove common LLM verbose patterns
        patterns_to_remove = [
            r'^The sender or issuer of this document is[:\s]*',
            r'^The sender is[:\s]*',
            r'^The correspondent is[:\s]*',
            r'^The document type is[:\s]*',
            r'^\*+\s*',  # Remove leading asterisks (markdown list items)
            r'\n.*$',  # Remove everything after first line
            r'\([^)]*\)$',  # Remove trailing parenthetical (e.g., "(phone: ...)")
        ]

        result = name.strip()

        for pattern in patterns_to_remove:
            result = re.sub(pattern, '', result, flags=re.IGNORECASE | re.DOTALL).strip()

        # If result is now empty or just "UNKNOWN", return None behavior
        if not result or result.upper() == "UNKNOWN":
            return ""

        # Truncate to 128 chars (Paperless limit)
        if len(result) > 128:
            result = result[:125] + "..."

        return result

    async def _get_or_create_correspondent(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        name: str
    ) -> int | None:
        """Get or create a correspondent in Paperless."""
        # Sanitize and truncate name
        name = self._sanitize_name(name)
        if not name:
            return None

        try:
            # Search for existing
            response = await client.get(
                "/api/correspondents/",
                headers=headers,
                params={"name__iexact": name},
            )

            if response.status_code == 200:
                results = response.json().get("results", [])
                if results:
                    return results[0]["id"]

            # Create new
            response = await client.post(
                "/api/correspondents/",
                headers=headers,
                json={"name": name},
            )
            
            if response.status_code == 201:
                return response.json()["id"]
            
            logger.warning(f"Could not create correspondent {name}: {response.text}")
            return None
            
        except Exception as e:
            logger.warning(f"Error with correspondent {name}: {e}")
            return None
    
    async def _get_or_create_document_type(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        name: str
    ) -> int | None:
        """Get or create a document type in Paperless."""
        # Sanitize and truncate name
        name = self._sanitize_name(name)
        if not name:
            return None

        try:
            # Search for existing
            response = await client.get(
                "/api/document_types/",
                headers=headers,
                params={"name__iexact": name},
            )

            if response.status_code == 200:
                results = response.json().get("results", [])
                if results:
                    return results[0]["id"]

            # Create new
            response = await client.post(
                "/api/document_types/",
                headers=headers,
                json={"name": name},
            )
            
            if response.status_code == 201:
                return response.json()["id"]
            
            logger.warning(f"Could not create document type {name}: {response.text}")
            return None
            
        except Exception as e:
            logger.warning(f"Error with document type {name}: {e}")
            return None
    
    async def _update_tags(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        context: ProcessorContext
    ) -> list[int] | None:
        """Update document tags - remove processing, add enhanced/review as needed."""
        settings = get_settings()

        try:
            # Get current tags
            response = await client.get(
                f"/api/documents/{context.paperless_id}/",
                headers=headers,
            )

            if response.status_code != 200:
                return None

            current_tags = response.json().get("tags", [])

            # Find and remove processing tag
            processing_tag_id = None
            response = await client.get(
                "/api/tags/",
                headers=headers,
                params={"name__iexact": settings.paperless.processing_tag},
            )

            if response.status_code == 200:
                results = response.json().get("results", [])
                if results:
                    processing_tag_id = results[0]["id"]

            new_tags = [t for t in current_tags if t != processing_tag_id]

            # Add enhanced tag to indicate successful DeDox processing
            enhanced_tag_id = await self._get_or_create_tag(
                client, headers, settings.paperless.enhanced_tag
            )
            if enhanced_tag_id and enhanced_tag_id not in new_tags:
                new_tags.append(enhanced_tag_id)

            # Add "Needs Review" tag if OCR confidence is low or critical fields are missing
            needs_review = self._should_tag_for_review(context, settings)
            if needs_review:
                review_tag_id = await self._get_or_create_tag(
                    client, headers, settings.paperless.review_tag
                )
                if review_tag_id and review_tag_id not in new_tags:
                    new_tags.append(review_tag_id)

            # Add urgency tag if high/critical
            if context.metadata:
                urgency = context.metadata.get("urgency", "low")
                if urgency in ("critical", "high"):
                    urgency_tag_id = await self._get_or_create_tag(
                        client, headers, f"Urgency: {urgency.capitalize()}"
                    )
                    if urgency_tag_id and urgency_tag_id not in new_tags:
                        new_tags.append(urgency_tag_id)

            return new_tags

        except Exception as e:
            logger.warning(f"Error updating tags: {e}")
            return None
    
    async def _get_or_create_tag(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        name: str
    ) -> int | None:
        """Get or create a tag in Paperless."""
        # Truncate name to 128 chars (Paperless limit)
        if len(name) > 128:
            name = name[:125] + "..."

        try:
            response = await client.get(
                "/api/tags/",
                headers=headers,
                params={"name__iexact": name},
            )
            
            if response.status_code == 200:
                results = response.json().get("results", [])
                if results:
                    return results[0]["id"]
            
            # Create new
            response = await client.post(
                "/api/tags/",
                headers=headers,
                json={"name": name},
            )
            
            if response.status_code == 201:
                return response.json()["id"]
            
            return None
            
        except Exception as e:
            logger.warning(f"Error with tag {name}: {e}")
            return None
    
    def _parse_date_string(self, date_str: str) -> str | None:
        """Parse a date string and return in YYYY-MM-DD format.

        Supports common formats: YYYY-MM-DD, DD.MM.YYYY, DD/MM/YYYY, MM/DD/YYYY
        """
        # Already in correct format?
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            return date_str

        # Try common formats
        formats = [
            ("%d.%m.%Y", r'^\d{1,2}\.\d{1,2}\.\d{4}$'),
            ("%d/%m/%Y", r'^\d{1,2}/\d{1,2}/\d{4}$'),
            ("%m/%d/%Y", r'^\d{1,2}/\d{1,2}/\d{4}$'),
            ("%Y/%m/%d", r'^\d{4}/\d{1,2}/\d{1,2}$'),
        ]

        for fmt, pattern in formats:
            if re.match(pattern, date_str):
                try:
                    parsed = datetime.strptime(date_str, fmt)
                    return parsed.strftime("%Y-%m-%d")
                except ValueError:
                    continue

        logger.warning(f"Could not parse date: {date_str}")
        return None

    def _should_tag_for_review(self, context: ProcessorContext, settings) -> bool:
        """Check if document should be tagged for review in Paperless.

        Only adds the review tag - no internal review queue functionality.
        Tags are added when:
        - OCR confidence is below threshold
        - Critical metadata fields (document_type, sender) are missing
        """
        # Check OCR confidence
        if context.ocr_confidence is not None:
            if context.ocr_confidence < settings.ocr.min_confidence:
                logger.info(f"Tagging for review: low OCR confidence ({context.ocr_confidence})")
                return True

        # Check for missing critical fields
        critical_fields = ["document_type", "sender"]
        if context.metadata:
            for field in critical_fields:
                if not context.metadata.get(field):
                    logger.info(f"Tagging for review: missing critical field '{field}'")
                    return True

        return False

    async def _update_document_status(self, context: ProcessorContext) -> None:
        """Update the local document status."""
        db = await get_database()
        
        await db.update(
            "documents",
            {
                "status": DocumentStatus.COMPLETED.value,
                "ocr_confidence": context.ocr_confidence,
                "paperless_id": context.paperless_id,
                "processed_at": _utcnow().isoformat(),
            },
            "id = ?",
            (str(context.document.id),)
        )

        logger.info(f"Updated document {context.document.id} status to COMPLETED")
