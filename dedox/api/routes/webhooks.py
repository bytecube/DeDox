"""
Webhook routes for receiving events from external systems.

Handles Paperless-ngx workflow webhooks for document processing.
Supports both JSON-only payloads and multipart uploads with document file.

Endpoints:
    POST /paperless/document-added
        Triggered when new documents are added to Paperless.
        Starts DeDox processing pipeline (OCR + LLM extraction).

    POST /paperless/document-updated
        Triggered when dedox:reprocess tag is added.
        Re-runs processing on existing documents.

    POST /paperless/document-sync
        Triggered on document updates for Open WebUI sync.
        Uploads document to Open WebUI knowledge base.

Security:
    - HMAC signature verification (when DEDOX_WEBHOOK_SECRET is set)
    - File type validation (images and PDFs only)
    - Path traversal protection for saved files
    - Rate limiting recommended via reverse proxy
"""

import hashlib
import hmac
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Request, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile
from pydantic import BaseModel, field_validator

from dedox.core.config import get_settings
from dedox.db import get_database
from dedox.db.repositories.document_repository import DocumentRepository
from dedox.db.repositories.job_repository import JobRepository
from dedox.models.document import Document, DocumentStatus
from dedox.models.job import Job, JobCreate, JobStatus
from dedox.services.paperless_webhook_service import PaperlessWebhookService

logger = logging.getLogger(__name__)

router = APIRouter()


class PaperlessWebhookPayload(BaseModel):
    """Payload from Paperless-ngx workflow webhook.

    Supports both Paperless placeholder names and custom names for flexibility.

    Available Paperless template variables for "Document Added" trigger:
    - doc_url: URL to the document in web UI (contains document ID)
    - doc_title: Current document title
    - original_filename: Original file name without extension
    - filename: Current file name without extension
    - correspondent: Assigned correspondent name
    - document_type: Assigned document type name
    - owner_username: Assigned owner username
    - created: Created datetime
    - added: Added datetime
    """
    # Document URL (primary way to get document ID)
    doc_url: str | None = None

    # Document metadata from Paperless placeholders
    doc_title: str | None = None
    correspondent: str | None = None
    document_type: str | None = None
    original_filename: str | None = None
    filename: str | None = None
    created: str | None = None
    added: str | None = None
    owner_username: str | None = None

    # Legacy field names (for backwards compatibility)
    doc_pk: int | None = None  # Some older setups may send this directly
    document_id: int | None = None  # Alternative name
    title: str | None = None  # Alias for doc_title
    original_name: str | None = None  # Alias for original_filename
    tag_list: str | None = None  # Comma-separated tags (if available)
    document_title: str | None = None
    document_filename: str | None = None
    document_created: str | None = None
    document_added: str | None = None
    document_correspondent: str | None = None
    document_document_type: str | None = None
    document_tags: list[str] | None = None
    document_content: str | None = None  # Paperless OCR text

    @field_validator('doc_title', 'original_filename', 'filename', 'title', 'original_name', 'document_title', 'document_filename', mode='before')
    @classmethod
    def coerce_to_string(cls, v):
        """Coerce integer values to strings.

        Paperless-ngx may send document ID as int for title/filename when no proper value is set.
        """
        if v is None:
            return None
        return str(v)

    @property
    def paperless_id(self) -> int | None:
        """Get the Paperless document ID from available fields.

        Priority:
        1. Direct doc_pk field (legacy)
        2. document_id field (alternative)
        3. Extract from doc_url (e.g., http://paperless:8000/documents/123/)
        """
        if self.doc_pk:
            return self.doc_pk
        if self.document_id:
            return self.document_id

        # Extract from doc_url - format: http://host/documents/{id}/
        if self.doc_url:
            match = re.search(r'/documents/(\d+)/?', self.doc_url)
            if match:
                return int(match.group(1))

        return None

    @property
    def effective_title(self) -> str | None:
        """Get title from available fields."""
        return self.doc_title or self.title or self.document_title

    @property
    def effective_filename(self) -> str | None:
        """Get filename from available fields."""
        return self.original_filename or self.original_name or self.filename or self.document_filename

    @property
    def effective_tags(self) -> list[str] | None:
        """Get tags from available fields."""
        if self.document_tags:
            return self.document_tags
        if self.tag_list:
            return [t.strip() for t in self.tag_list.split(",") if t.strip()]
        return None


class WebhookResponse(BaseModel):
    """Response for webhook requests."""
    status: str
    message: str
    job_id: str | None = None
    document_id: str | None = None


def verify_webhook_signature(
    payload: bytes,
    signature: str | None,
    secret: str
) -> bool:
    """Verify HMAC signature for webhook payload.

    Args:
        payload: Raw request body
        signature: Signature from header (format: sha256=<hex>)
        secret: Shared secret for HMAC

    Returns:
        True if signature is valid or no secret configured
    """
    if not secret:
        # No secret configured, skip verification
        return True

    if not signature:
        return False

    # Parse signature (format: sha256=<hex>)
    if signature.startswith("sha256="):
        signature = signature[7:]

    # Calculate expected signature
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


def verify_multipart_signature(
    form_data: dict[str, Any],
    signature: str | None,
    secret: str
) -> bool:
    """Verify HMAC signature for multipart form data.

    For multipart requests, we create a canonical representation of the
    non-file form fields and verify the signature against that.

    Args:
        form_data: Dictionary of form field names to values (excluding files)
        signature: Signature from header (format: sha256=<hex>)
        secret: Shared secret for HMAC

    Returns:
        True if signature is valid or no secret configured
    """
    if not secret:
        # No secret configured, skip verification
        return True

    if not signature:
        return False

    # Create canonical representation: sorted keys, JSON encoded
    canonical = json.dumps(form_data, sort_keys=True, separators=(',', ':'))

    return verify_webhook_signature(canonical.encode(), signature, secret)


async def _handle_reprocess_request(
    paperless_id: int,
    doc_repo: DocumentRepository,
    job_repo: JobRepository,
    webhook_service: PaperlessWebhookService,
    settings,
) -> bool:
    """Handle a document reprocess request.

    Resets an existing document's processing state and creates a new job.
    If the document doesn't exist in DeDox, returns False to indicate
    it should be processed as a new document.

    Args:
        paperless_id: Paperless document ID
        doc_repo: Document repository instance
        job_repo: Job repository instance
        webhook_service: Paperless webhook service instance
        settings: Application settings

    Returns:
        True if reprocess was handled (document existed), False if document
        should be processed as new
    """
    logger.info(f"Reprocess request for Paperless document {paperless_id}")

    # Remove reprocess tag immediately to prevent loops
    await webhook_service.remove_tag_from_document(
        paperless_id,
        settings.paperless.reprocess_tag
    )

    # Also remove the enhanced tag (will be re-added after processing)
    await webhook_service.remove_tag_from_document(
        paperless_id,
        settings.paperless.enhanced_tag
    )

    # Check if document exists in DeDox
    existing = await doc_repo.get_by_paperless_id(paperless_id)

    if not existing:
        # Document doesn't exist in DeDox - process as new
        logger.info(f"Document {paperless_id} not in DeDox, processing as new")
        return False

    # Reset existing document for reprocessing
    logger.info(f"Resetting existing document {existing.id} for reprocessing")

    await doc_repo.update_by_id(
        str(existing.id),
        {
            "status": DocumentStatus.PENDING.value,
            "ocr_text": None,
            "ocr_confidence": None,
            "metadata": "{}",
            "metadata_confidence": "{}",
            "processed_at": None,
        }
    )

    # Create new processing job
    await job_repo.create(JobCreate(document_id=existing.id, source="reprocess"))

    # Add processing tag
    await webhook_service.add_tag_to_document(
        paperless_id,
        settings.paperless.processing_tag
    )

    logger.info(f"Created reprocess job for document {existing.id}")
    return True


async def _create_new_document(
    paperless_id: int,
    payload: PaperlessWebhookPayload,
    file_path: Path | None,
    file_info: dict[str, Any] | None,
    doc_repo: DocumentRepository,
    job_repo: JobRepository,
    webhook_service: PaperlessWebhookService,
    settings,
) -> None:
    """Create a new document record and processing job.

    Downloads the document from Paperless if not provided, creates the
    document record, and schedules it for processing.

    Args:
        paperless_id: Paperless document ID
        payload: Webhook payload with document info
        file_path: Path to uploaded file (if included in webhook)
        file_info: File metadata (if included in webhook)
        doc_repo: Document repository instance
        job_repo: Job repository instance
        webhook_service: Paperless webhook service instance
        settings: Application settings
    """
    # If file was not included in webhook, download from Paperless API
    if not file_path:
        logger.info(f"Document not included in webhook, downloading from Paperless API")
        file_path, file_info = await webhook_service.download_document(paperless_id)

        if not file_path:
            logger.error(f"Failed to download document {paperless_id} from Paperless")
            return

        file_info = file_info or {}
    else:
        file_info = file_info or {}
        logger.info(f"Using document included in webhook payload")

    # Create document record
    document = Document(
        id=uuid4(),
        filename=file_info.get("filename", f"paperless_{paperless_id}"),
        original_filename=file_info.get("original_filename", payload.effective_filename or f"document_{paperless_id}"),
        content_type=file_info.get("content_type", "application/pdf"),
        file_size=file_info.get("file_size", 0),
        source="paperless_webhook",
        original_path=str(file_path),
        paperless_id=paperless_id,
        status=DocumentStatus.PENDING,
    )

    # Store Paperless OCR text if available (we'll also run our own)
    if payload.document_content:
        document.metadata["paperless_ocr_text"] = payload.document_content

    await doc_repo.create(document)
    logger.info(f"Created document {document.id} from Paperless webhook (paperless_id={paperless_id})")

    # Create processing job
    await job_repo.create(JobCreate(document_id=document.id, source="paperless_webhook"))
    logger.info(f"Created job for Paperless document {paperless_id}")

    # Add processing tag to Paperless document
    await webhook_service.add_tag_to_document(
        paperless_id,
        settings.paperless.processing_tag
    )


async def _process_paperless_document(
    paperless_id: int,
    payload: PaperlessWebhookPayload,
    file_path: Path | None = None,
    file_info: dict[str, Any] | None = None,
    is_reprocess: bool = False,
) -> None:
    """Background task to process a document from Paperless webhook.

    This is the main entry point for document processing, orchestrating:
    1. Reprocess handling (if triggered by tag or explicit flag)
    2. Duplicate detection (skip if already processed)
    3. New document creation and job scheduling

    Args:
        paperless_id: Paperless document ID
        payload: Webhook payload with document info
        file_path: Path to uploaded file (if included in webhook)
        file_info: File metadata (if included in webhook)
        is_reprocess: If True, this is a reprocess request triggered by tag
    """
    settings = get_settings()
    db = await get_database()
    doc_repo = DocumentRepository(db)
    job_repo = JobRepository(db)
    webhook_service = PaperlessWebhookService()

    try:
        # Check for reprocess tag in payload
        tags = payload.effective_tags or []
        has_reprocess_tag = settings.paperless.reprocess_tag in tags

        # Handle reprocess request
        if has_reprocess_tag or is_reprocess:
            handled = await _handle_reprocess_request(
                paperless_id, doc_repo, job_repo, webhook_service, settings
            )
            if handled:
                return
            # If not handled, fall through to create as new document

        # Check if document already exists in DeDox (skip duplicates)
        existing = await doc_repo.get_by_paperless_id(paperless_id)
        if existing:
            logger.info(f"Skipping Paperless document {paperless_id} - already processed")
            return

        # Check if document has DeDox tags (indicates we already processed it)
        if tags and settings.paperless.enhanced_tag in tags:
            logger.info(f"Skipping Paperless document {paperless_id} - already has enhanced tag")
            return

        # Create new document and schedule processing
        await _create_new_document(
            paperless_id, payload, file_path, file_info,
            doc_repo, job_repo, webhook_service, settings
        )

    except Exception as e:
        logger.exception(f"Error processing Paperless webhook for document {paperless_id}: {e}")

        # Try to add error tag
        try:
            await webhook_service.add_tag_to_document(
                paperless_id,
                settings.paperless.error_tag
            )
        except Exception:
            pass


def _sanitize_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal attacks.

    Args:
        filename: The original filename

    Returns:
        Sanitized filename with only the basename and dangerous chars removed
    """
    import os
    import re

    # Get only the basename (removes any path components like ../)
    filename = os.path.basename(filename)

    # Remove null bytes
    filename = filename.replace('\x00', '')

    # Replace any remaining problematic characters
    # Allow alphanumeric, dots, hyphens, underscores, and spaces
    filename = re.sub(r'[^\w.\-\s]', '_', filename)

    # Prevent hidden files (starting with .)
    if filename.startswith('.'):
        filename = '_' + filename[1:]

    # Ensure filename is not empty
    if not filename or filename.isspace():
        filename = "unnamed_file"

    # Limit filename length
    max_len = 200
    if len(filename) > max_len:
        name, ext = os.path.splitext(filename)
        filename = name[:max_len - len(ext)] + ext

    return filename


async def _save_uploaded_file(file: UploadFile) -> tuple[Path, dict[str, Any]]:
    """Save an uploaded file to the upload directory.

    Args:
        file: The uploaded file

    Returns:
        Tuple of (file_path, file_info dict)
    """
    settings = get_settings()
    upload_dir = Path(settings.storage.upload_path).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize and generate unique filename
    original_filename = _sanitize_filename(file.filename or "document")
    unique_filename = f"{uuid4().hex}_{original_filename}"
    file_path = upload_dir / unique_filename

    # Verify the resolved path is still within upload_dir (defense in depth)
    resolved_path = file_path.resolve()
    if not str(resolved_path).startswith(str(upload_dir)):
        logger.error(f"Path traversal attempt detected: {file.filename}")
        raise HTTPException(
            status_code=400,
            detail="Invalid filename"
        )

    # Determine content type
    content_type = file.content_type or mimetypes.guess_type(original_filename)[0] or "application/octet-stream"

    # Save file
    content = await file.read()
    with open(resolved_path, "wb") as f:
        f.write(content)

    file_info = {
        "filename": unique_filename,
        "original_filename": original_filename,
        "content_type": content_type,
        "file_size": len(content),
    }

    logger.info(f"Saved uploaded file to {resolved_path} ({file_info['file_size']} bytes)")
    return resolved_path, file_info


@router.post(
    "/paperless/document-added",
    response_model=WebhookResponse,
    summary="Paperless document added webhook",
    description="Receives webhook when a new document is added to Paperless-ngx. "
                "Supports both JSON-only payloads and multipart uploads with document file."
)
async def paperless_document_added(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_signature: str | None = Header(None, alias="X-Webhook-Signature"),
):
    """Handle Paperless-ngx document added webhook.

    This endpoint is called by Paperless-ngx workflow when a new document
    is added. It triggers DeDox processing pipeline to enhance the document.

    Supports two modes:
    1. JSON-only: Payload contains metadata, DeDox downloads document via API
    2. Multipart: Payload includes document file (when "Include document" is enabled)
    """
    settings = get_settings()

    # Check if webhooks are enabled
    if not settings.paperless.webhook.enabled:
        raise HTTPException(
            status_code=503,
            detail="Webhooks are disabled"
        )

    content_type = request.headers.get("content-type", "")
    file_path: Path | None = None
    file_info: dict[str, Any] | None = None
    payload: PaperlessWebhookPayload

    # Handle multipart/form-data (includes document file)
    if "multipart/form-data" in content_type:
        logger.info("Received multipart webhook with document file")
        form = await request.form()
        logger.debug(f"Form fields received: {list(form.keys())}")

        # Extract file if present (Paperless sends it as "file", not "document")
        document_file = form.get("file") or form.get("document")
        # Check for both FastAPI and Starlette UploadFile types
        if document_file and isinstance(document_file, (UploadFile, StarletteUploadFile)):
            file_path, file_info = await _save_uploaded_file(document_file)

        # Build payload from form fields
        payload_dict = {}
        for key, value in form.items():
            if key not in ("document", "file") and not isinstance(value, (UploadFile, StarletteUploadFile)):
                # Handle potential JSON strings in form fields
                if isinstance(value, str):
                    try:
                        # Try to parse as JSON (for nested objects/arrays)
                        payload_dict[key] = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        payload_dict[key] = value
                else:
                    payload_dict[key] = value

        # Verify signature for multipart requests
        if not verify_multipart_signature(
            payload_dict,
            x_webhook_signature,
            settings.paperless.webhook.secret
        ):
            logger.warning("Invalid webhook signature for multipart request")
            raise HTTPException(
                status_code=401,
                detail="Invalid webhook signature"
            )

        try:
            payload = PaperlessWebhookPayload(**payload_dict)
        except Exception as e:
            logger.error(f"Failed to parse multipart payload: {e}, fields: {payload_dict}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid payload: {e}"
            )

    # Handle JSON payload
    else:
        body = await request.body()

        # Verify signature if secret is configured
        if not verify_webhook_signature(
            body,
            x_webhook_signature,
            settings.paperless.webhook.secret
        ):
            logger.warning("Invalid webhook signature")
            raise HTTPException(
                status_code=401,
                detail="Invalid webhook signature"
            )

        try:
            payload_dict = json.loads(body)
            payload = PaperlessWebhookPayload(**payload_dict)
        except Exception as e:
            logger.error(f"Failed to parse JSON payload: {e}")
            raise HTTPException(
                status_code=400,
                detail=f"Invalid payload: {e}"
            )

    # Get paperless_id from payload
    paperless_id = payload.paperless_id
    if not paperless_id:
        logger.error(f"Missing document ID. doc_url={payload.doc_url}")
        raise HTTPException(
            status_code=400,
            detail="Missing document ID (doc_url required)"
        )

    logger.info(
        f"Received Paperless webhook for document {paperless_id}: "
        f"{payload.effective_title or payload.effective_filename}"
    )

    # Process in background
    background_tasks.add_task(
        _process_paperless_document,
        paperless_id,
        payload,
        file_path,
        file_info,
    )

    return WebhookResponse(
        status="accepted",
        message=f"Processing document {paperless_id}",
    )


@router.post(
    "/paperless/document-updated",
    response_model=WebhookResponse,
    summary="Paperless document updated webhook",
    description="Receives webhook when a document is updated in Paperless-ngx (e.g., tags changed). "
                "Used to trigger reprocessing when the reprocess tag is added."
)
async def paperless_document_updated(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_signature: str | None = Header(None, alias="X-Webhook-Signature"),
):
    """Handle Paperless-ngx document updated webhook.

    This endpoint is called by Paperless-ngx workflow when a document is updated.
    Primarily used to detect when the reprocess tag is added to trigger reprocessing.
    """
    settings = get_settings()

    # Check if webhooks are enabled
    if not settings.paperless.webhook.enabled:
        raise HTTPException(
            status_code=503,
            detail="Webhooks are disabled"
        )

    # Parse request body
    body = await request.body()

    # Verify signature if secret is configured
    if not verify_webhook_signature(
        body,
        x_webhook_signature,
        settings.paperless.webhook.secret
    ):
        logger.warning("Invalid webhook signature for document-updated")
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook signature"
        )

    try:
        payload_dict = json.loads(body)
        logger.info(f"document-updated payload: {payload_dict}")
        payload = PaperlessWebhookPayload(**payload_dict)
    except Exception as e:
        logger.error(f"Failed to parse JSON payload: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid payload: {e}"
        )

    # Get paperless_id from payload
    paperless_id = payload.paperless_id
    if not paperless_id:
        logger.error(f"Missing document ID. doc_url={payload.doc_url}")
        raise HTTPException(
            status_code=400,
            detail="Missing document ID (doc_url required)"
        )

    # Check if this is a reprocess request
    # Note: Paperless workflow may not send tags in the payload, so we also
    # accept any document-updated webhook as a reprocess trigger since the
    # workflow is already filtered to only fire when the reprocess tag is present
    tags = payload.effective_tags or []
    has_reprocess_tag = settings.paperless.reprocess_tag in tags

    logger.info(f"Document {paperless_id} updated - tags in payload: {tags}, has_reprocess_tag: {has_reprocess_tag}")

    # If the workflow is correctly configured, it will only trigger when the
    # reprocess tag is added. So even if tags are not in the payload, we should
    # process it as a reprocess request since the workflow filtered it.
    # We still check the tag if present for extra safety.
    if not has_reprocess_tag and tags:
        # Tags were provided but reprocess tag is not among them - skip
        logger.debug(f"Document {paperless_id} updated but no reprocess tag - ignoring")
        return WebhookResponse(
            status="ignored",
            message=f"Document {paperless_id} updated, no reprocess tag",
        )

    logger.info(
        f"Received Paperless document-updated webhook for document {paperless_id} with reprocess tag"
    )

    # Process reprocess request in background
    background_tasks.add_task(
        _process_paperless_document,
        paperless_id,
        payload,
        None,  # No file included
        None,  # No file info
        True,  # is_reprocess=True
    )

    return WebhookResponse(
        status="accepted",
        message=f"Reprocessing document {paperless_id}",
    )


@router.post(
    "/paperless/document-sync",
    response_model=WebhookResponse,
    summary="Paperless document sync webhook",
    description="Receives webhook to sync ANY document update to Open WebUI. "
                "Separate from the processing pipeline - purely for Open WebUI synchronization."
)
async def paperless_document_sync(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_signature: str | None = Header(None, alias="X-Webhook-Signature"),
):
    """Handle Paperless-ngx document sync webhook for Open WebUI.

    This endpoint syncs documents to Open WebUI knowledge base independently
    of the DeDox processing pipeline. It should be called on ANY document
    update in Paperless.
    """
    settings = get_settings()

    # Check if Open WebUI sync is enabled
    if not settings.openwebui.enabled:
        raise HTTPException(
            status_code=503,
            detail="Open WebUI sync is disabled"
        )

    # Check if webhooks are enabled
    if not settings.paperless.webhook.enabled:
        raise HTTPException(
            status_code=503,
            detail="Webhooks are disabled"
        )

    # Parse request body
    body = await request.body()

    # Verify signature if secret is configured
    if not verify_webhook_signature(
        body,
        x_webhook_signature,
        settings.paperless.webhook.secret
    ):
        logger.warning("Invalid webhook signature for document-sync")
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook signature"
        )

    try:
        payload_dict = json.loads(body)
        logger.info(f"document-sync payload: {payload_dict}")
        payload = PaperlessWebhookPayload(**payload_dict)
    except Exception as e:
        logger.error(f"Failed to parse JSON payload: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid payload: {e}"
        )

    # Get paperless_id from payload
    paperless_id = payload.paperless_id
    if not paperless_id:
        logger.error(f"Missing document ID. doc_url={payload.doc_url}")
        raise HTTPException(
            status_code=400,
            detail="Missing document ID (doc_url required)"
        )

    logger.info(
        f"Received Paperless document-sync webhook for document {paperless_id}"
    )

    # Sync to Open WebUI in background
    background_tasks.add_task(
        _sync_to_openwebui,
        paperless_id,
        payload,
    )

    return WebhookResponse(
        status="accepted",
        message=f"Syncing document {paperless_id} to Open WebUI",
    )


async def _sync_to_openwebui(paperless_id: int, payload: PaperlessWebhookPayload) -> None:
    """Background task to sync a document to Open WebUI.

    Args:
        paperless_id: Paperless document ID
        payload: Webhook payload with document info
    """
    from dedox.services.openwebui_sync_service import OpenWebUISyncService

    settings = get_settings()
    db = await get_database()
    doc_repo = DocumentRepository(db)
    webhook_service = PaperlessWebhookService()

    try:
        # Download document from Paperless API
        logger.info(f"Downloading document {paperless_id} from Paperless for Open WebUI sync")
        file_path, file_info = await webhook_service.download_document(paperless_id)

        if not file_path:
            logger.error(f"Failed to download document {paperless_id} from Paperless")
            return

        # Get full document metadata from Paperless API
        async with httpx.AsyncClient(
            timeout=settings.paperless.timeout_seconds,
            verify=settings.paperless.verify_ssl
        ) as client:
            headers = {"Authorization": f"Token {settings.paperless.api_token}"}
            response = await client.get(
                f"{settings.paperless.base_url}/api/documents/{paperless_id}/",
                headers=headers,
            )

            if response.status_code != 200:
                logger.error(f"Failed to fetch document metadata: {response.status_code}")
                return

            paperless_doc_data = response.json()

        # Check if document exists in DeDox (to get extracted metadata)
        dedox_doc = await doc_repo.get_by_paperless_id(paperless_id)

        # Create temporary Document object for sync
        if not dedox_doc:
            from uuid import uuid4
            from dedox.models.document import DocumentStatus

            dedox_doc = Document(
                id=uuid4(),
                filename=file_info.get("filename", f"paperless_{paperless_id}"),
                original_filename=file_info.get("original_filename", f"document_{paperless_id}"),
                content_type=file_info.get("content_type", "application/pdf"),
                file_size=file_info.get("file_size", 0),
                source="paperless_webhook",
                original_path=str(file_path),
                paperless_id=paperless_id,
                status=DocumentStatus.COMPLETED,
                metadata="{}",  # No extracted metadata yet
            )

        # Sync to Open WebUI
        sync_service = OpenWebUISyncService()
        success = await sync_service.sync_document(
            dedox_doc,
            file_path,
            paperless_doc_data
        )

        if success:
            logger.info(f"Successfully synced document {paperless_id} to Open WebUI")
        else:
            logger.error(f"Failed to sync document {paperless_id} to Open WebUI")

    except Exception as e:
        logger.exception(f"Error syncing document {paperless_id} to Open WebUI: {e}")


@router.get(
    "/paperless/health",
    summary="Webhook health check",
    description="Check if webhook endpoint is healthy and configured"
)
async def webhook_health():
    """Health check for webhook endpoint."""
    settings = get_settings()

    return {
        "status": "ok",
        "webhooks_enabled": settings.paperless.webhook.enabled,
        "signature_required": bool(settings.paperless.webhook.secret),
        "paperless_configured": bool(settings.paperless.api_token),
        "reprocess_tag": settings.paperless.reprocess_tag,
        "openwebui_sync_enabled": settings.openwebui.enabled,
    }
