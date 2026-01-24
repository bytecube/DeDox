"""
Document model definitions.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DocumentStatus(str, Enum):
    """Document processing status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentCreate(BaseModel):
    """Schema for creating a new document."""
    filename: str
    content_type: str
    source: str = "paperless_webhook"  # Documents come from Paperless-ngx webhooks
    file_size: int
    
    class Config:
        from_attributes = True


class Document(BaseModel):
    """Document model representing a scanned/uploaded document."""
    id: UUID = Field(default_factory=uuid4)
    filename: str
    original_filename: str
    content_type: str
    file_size: int
    source: str = "paperless_webhook"

    # Storage paths
    original_path: str | None = None
    processed_path: str | None = None

    # OCR results
    ocr_text: str | None = None
    ocr_confidence: float | None = None
    ocr_language: str | None = None

    # Extracted metadata (JSON stored as dict)
    metadata: dict | None = Field(default_factory=dict)
    metadata_confidence: dict | None = Field(default_factory=dict)
    
    # File hashes for duplicate detection
    file_hash: str | None = None
    content_hash: str | None = None
    
    # Paperless integration
    paperless_id: int | None = None
    paperless_task_id: str | None = None
    
    # Status and timestamps
    status: DocumentStatus = DocumentStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    processed_at: datetime | None = None
    
    class Config:
        from_attributes = True
    
    def mark_completed(self) -> None:
        """Mark document as completed."""
        self.status = DocumentStatus.COMPLETED
        self.processed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
    
    def mark_failed(self, reason: str | None = None) -> None:
        """Mark document as failed."""
        self.status = DocumentStatus.FAILED
        self.updated_at = datetime.utcnow()


class DocumentResponse(BaseModel):
    """API response for document."""
    id: UUID
    filename: str
    status: DocumentStatus
    source: str
    created_at: datetime
    paperless_id: int | None = None

    class Config:
        from_attributes = True
