"""Document routes."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from dedox.api.deps import CurrentUser
from dedox.db import get_database
from dedox.db.repositories.document_repository import DocumentRepository
from dedox.db.repositories.job_repository import JobRepository
from dedox.models.document import DocumentStatus
from dedox.services.document_service import DocumentService

logger = logging.getLogger(__name__)

router = APIRouter()


class DocumentResponse(BaseModel):
    """Document response."""
    id: str
    filename: str
    original_filename: str
    content_type: str
    status: str
    file_hash: str | None = None
    file_size: int | None = None
    paperless_id: int | None = None
    ocr_confidence: float | None = None
    created_at: datetime
    processed_at: datetime | None = None


class DocumentListResponse(BaseModel):
    """Document list response."""
    documents: list[DocumentResponse]
    total: int
    page: int
    page_size: int


class JobResponse(BaseModel):
    """Job response."""
    id: str
    document_id: str
    status: str
    current_stage: str
    progress: int
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
):
    """List documents with pagination."""
    db = await get_database()
    repo = DocumentRepository(db)

    # Build filters
    filters = {"user_id": str(current_user.id)}

    if status_filter:
        try:
            DocumentStatus(status_filter)
            filters["status"] = status_filter
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status_filter}",
            )

    # Get documents
    documents, total = await repo.list_with_pagination(
        page=page,
        page_size=page_size,
        **filters,
    )
    
    return DocumentListResponse(
        documents=[
            DocumentResponse(
                id=str(doc.id),
                filename=doc.filename,
                original_filename=doc.original_filename,
                content_type=doc.content_type,
                status=doc.status.value,
                file_hash=doc.file_hash,
                file_size=doc.file_size,
                paperless_id=doc.paperless_id,
                ocr_confidence=doc.ocr_confidence,
                created_at=doc.created_at,
                processed_at=doc.processed_at,
            )
            for doc in documents
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: str, current_user: CurrentUser):
    """Get a document by ID."""
    db = await get_database()
    repo = DocumentRepository(db)
    
    document = await repo.get_by_id(document_id)
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    
    # Check ownership
    if str(document.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    return DocumentResponse(
        id=str(document.id),
        filename=document.filename,
        original_filename=document.original_filename,
        content_type=document.content_type,
        status=document.status.value,
        file_hash=document.file_hash,
        file_size=document.file_size,
        paperless_id=document.paperless_id,
        ocr_confidence=document.ocr_confidence,
        created_at=document.created_at,
        processed_at=document.processed_at,
    )


@router.get("/{document_id}/metadata")
async def get_document_metadata(document_id: str, current_user: CurrentUser):
    """Get extracted metadata for a document."""
    db = await get_database()
    repo = DocumentRepository(db)
    
    document = await repo.get_by_id(document_id)
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    
    if str(document.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    metadata = await repo.get_metadata(document_id)
    
    return {
        "document_id": document_id,
        "metadata": metadata,
    }


@router.put("/{document_id}/metadata")
async def update_document_metadata(
    document_id: str,
    metadata: dict,
    current_user: CurrentUser,
):
    """Update document metadata (for review corrections)."""
    db = await get_database()
    repo = DocumentRepository(db)
    
    document = await repo.get_by_id(document_id)
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    
    if str(document.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    # Update metadata
    await repo.update_metadata(document_id, metadata)

    return {"message": "Metadata updated"}


@router.get("/{document_id}/job", response_model=JobResponse)
async def get_document_job(document_id: str, current_user: CurrentUser):
    """Get the processing job for a document."""
    db = await get_database()
    doc_repo = DocumentRepository(db)
    job_repo = JobRepository(db)
    
    document = await doc_repo.get_by_id(document_id)
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    
    if str(document.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    job = await job_repo.get_by_document_id(document_id)
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    
    return JobResponse(
        id=str(job.id),
        document_id=str(job.document_id),
        status=job.status.value,
        current_stage=job.current_stage.value,
        progress=job.progress,
        error_message=job.error_message,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


@router.post("/{document_id}/reprocess")
async def reprocess_document(document_id: str, current_user: CurrentUser):
    """Trigger reprocessing of a document."""
    db = await get_database()
    repo = DocumentRepository(db)
    
    document = await repo.get_by_id(document_id)
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    
    if str(document.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    # Create new processing job
    service = DocumentService()
    job = await service.reprocess_document(document)
    
    return {
        "message": "Reprocessing started",
        "job_id": str(job.id),
    }


@router.delete("/{document_id}")
async def delete_document(document_id: str, current_user: CurrentUser):
    """Delete a document and its files."""
    db = await get_database()
    repo = DocumentRepository(db)
    
    document = await repo.get_by_id(document_id)
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    
    if str(document.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    # Delete from database and files
    service = DocumentService()
    await service.delete_document(document)
    
    return {"message": "Document deleted"}
