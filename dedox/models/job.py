"""
Job model definitions for tracking document processing.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    """Job processing status."""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REVIEW_REQUIRED = "review_required"
    CANCELLED = "cancelled"


class JobStage(str, Enum):
    """Current processing stage."""
    PENDING = "pending"
    IMAGE_PROCESSING = "image_processing"
    OCR = "ocr"
    PAPERLESS_UPLOAD = "paperless_upload"
    METADATA_EXTRACTION = "metadata_extraction"
    FINALIZATION = "finalization"
    COMPLETED = "completed"
    FAILED = "failed"


class JobCreate(BaseModel):
    """Schema for creating a new job."""
    document_id: UUID
    source: str = "upload"
    
    class Config:
        from_attributes = True


class JobProgress(BaseModel):
    """Progress information for a job stage."""
    stage: JobStage
    started_at: datetime | None = None
    completed_at: datetime | None = None
    message: str | None = None
    error: str | None = None
    
    class Config:
        from_attributes = True


class Job(BaseModel):
    """Job model representing a document processing job."""
    id: UUID = Field(default_factory=uuid4)
    document_id: UUID

    # Status tracking
    status: JobStatus = JobStatus.QUEUED
    current_stage: JobStage = JobStage.PENDING
    progress_percent: int = 0

    # Stage history
    stages: list[JobProgress] = Field(default_factory=list)
    skipped_stages: list[str] = Field(default_factory=list)  # Stages that were skipped

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Results
    result: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)

    # Retry tracking
    retry_count: int = 0
    max_retries: int = 3
    
    class Config:
        from_attributes = True
    
    def start_stage(self, stage: JobStage, message: str | None = None) -> None:
        """Start a new processing stage."""
        self.current_stage = stage
        self.status = JobStatus.PROCESSING
        self.updated_at = datetime.utcnow()
        
        if self.started_at is None:
            self.started_at = datetime.utcnow()
        
        # Calculate progress percentage based on stage
        stage_progress = {
            JobStage.PENDING: 0,
            JobStage.IMAGE_PROCESSING: 20,
            JobStage.OCR: 40,
            JobStage.PAPERLESS_UPLOAD: 55,
            JobStage.METADATA_EXTRACTION: 75,
            JobStage.FINALIZATION: 90,
            JobStage.COMPLETED: 100,
        }
        self.progress_percent = stage_progress.get(stage, 0)
        
        # Add to stage history
        self.stages.append(JobProgress(
            stage=stage,
            started_at=datetime.utcnow(),
            message=message
        ))
    
    def complete_stage(self, message: str | None = None) -> None:
        """Complete the current processing stage."""
        if self.stages:
            self.stages[-1].completed_at = datetime.utcnow()
            if message:
                self.stages[-1].message = message
        self.updated_at = datetime.utcnow()
    
    def fail_stage(self, error: str) -> None:
        """Mark current stage as failed."""
        if self.stages:
            self.stages[-1].completed_at = datetime.utcnow()
            self.stages[-1].error = error
        self.errors.append(error)
        self.updated_at = datetime.utcnow()

    def skip_stage(self, stage: JobStage, reason: str | None = None) -> None:
        """Mark a stage as skipped."""
        self.skipped_stages.append(stage.value)
        self.updated_at = datetime.utcnow()
    
    def mark_completed(self, result: dict[str, Any] | None = None) -> None:
        """Mark job as completed."""
        self.status = JobStatus.COMPLETED
        self.current_stage = JobStage.COMPLETED
        self.progress_percent = 100
        self.completed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        if result:
            self.result = result
    
    def mark_failed(self, error: str) -> None:
        """Mark job as failed."""
        self.status = JobStatus.FAILED
        self.current_stage = JobStage.FAILED
        self.completed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.errors.append(error)
    
    def mark_review_required(self, reason: str) -> None:
        """Mark job as requiring review."""
        self.status = JobStatus.REVIEW_REQUIRED
        self.completed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.result["review_reason"] = reason
    
    def can_retry(self) -> bool:
        """Check if job can be retried."""
        return self.retry_count < self.max_retries
    
    @property
    def progress(self) -> int:
        """Alias for progress_percent."""
        return self.progress_percent
    
    @property
    def error_message(self) -> str | None:
        """Get the latest error message."""
        return self.errors[-1] if self.errors else None
    
    @property
    def stages_completed(self) -> list[JobStage]:
        """Get list of completed stages."""
        return [s.stage for s in self.stages if s.completed_at is not None]
    
    @property
    def processing_times(self) -> dict[str, float]:
        """Get processing times for each stage in milliseconds."""
        times = {}
        for s in self.stages:
            if s.started_at and s.completed_at:
                duration_ms = (s.completed_at - s.started_at).total_seconds() * 1000
                times[s.stage.value] = duration_ms
        return times


class JobResponse(BaseModel):
    """API response for job status."""
    job_id: UUID
    status: JobStatus
    stage: JobStage
    progress: int
    message: str | None = None
    result: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    
    class Config:
        from_attributes = True
    
    @classmethod
    def from_job(cls, job: Job) -> "JobResponse":
        """Create response from job model."""
        message = None
        if job.stages:
            message = job.stages[-1].message
        
        return cls(
            job_id=job.id,
            status=job.status,
            stage=job.current_stage,
            progress=job.progress_percent,
            message=message,
            result=job.result if job.status == JobStatus.COMPLETED else None,
            errors=job.errors,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )
