"""Job routes for processing status and management."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)
from pydantic import BaseModel

from dedox.api.deps import CurrentUser, AdminUser
from dedox.db import get_database
from dedox.db.repositories.job_repository import JobRepository
from dedox.models.job import JobStatus, JobStage

logger = logging.getLogger(__name__)

router = APIRouter()


class JobResponse(BaseModel):
    """Job response."""
    id: str
    document_id: str
    status: str
    current_stage: str
    progress: int
    error_message: str | None = None
    stages_completed: list[str]
    stages_skipped: list[str] = []
    processing_times: dict
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Document info for display
    document_filename: str | None = None
    paperless_id: int | None = None


class JobListResponse(BaseModel):
    """Job list response."""
    jobs: list[JobResponse]
    total: int
    page: int
    page_size: int


@router.get("", response_model=JobListResponse)
async def list_jobs(
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
):
    """List processing jobs with pagination."""
    db = await get_database()
    repo = JobRepository(db)
    
    # Build filters
    filters = {}
    
    if status_filter:
        try:
            JobStatus(status_filter)
            filters["status"] = status_filter
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status_filter}",
            )
    
    # Get jobs for user's documents
    jobs, total = await repo.list_for_user(
        user_id=str(current_user.id),
        page=page,
        page_size=page_size,
        **filters,
    )

    # Fetch document info for each job
    from dedox.db.repositories.document_repository import DocumentRepository
    doc_repo = DocumentRepository(db)

    job_responses = []
    for job in jobs:
        document = await doc_repo.get_by_id(str(job.document_id))
        job_responses.append(
            JobResponse(
                id=str(job.id),
                document_id=str(job.document_id),
                status=job.status.value,
                current_stage=job.current_stage.value,
                progress=job.progress,
                error_message=job.error_message,
                stages_completed=[s.value for s in job.stages_completed],
                stages_skipped=job.skipped_stages or [],
                processing_times=job.processing_times or {},
                created_at=job.created_at,
                started_at=job.started_at,
                completed_at=job.completed_at,
                document_filename=document.original_filename if document else None,
                paperless_id=document.paperless_id if document else None,
            )
        )

    return JobListResponse(
        jobs=job_responses,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, current_user: CurrentUser):
    """Get a job by ID."""
    db = await get_database()
    repo = JobRepository(db)
    
    job = await repo.get_by_id(job_id)
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    
    # Verify ownership through document
    from dedox.db.repositories.document_repository import DocumentRepository
    doc_repo = DocumentRepository(db)
    document = await doc_repo.get_by_id(str(job.document_id))
    
    if not document or str(document.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    return JobResponse(
        id=str(job.id),
        document_id=str(job.document_id),
        status=job.status.value,
        current_stage=job.current_stage.value,
        progress=job.progress,
        error_message=job.error_message,
        stages_completed=[s.value for s in job.stages_completed],
        stages_skipped=job.skipped_stages or [],
        processing_times=job.processing_times or {},
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@router.get("/{job_id}/progress")
async def get_job_progress(job_id: str, current_user: CurrentUser):
    """Get detailed job progress (for polling)."""
    db = await get_database()
    repo = JobRepository(db)
    
    job = await repo.get_by_id(job_id)
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    
    # Verify ownership
    from dedox.db.repositories.document_repository import DocumentRepository
    doc_repo = DocumentRepository(db)
    document = await doc_repo.get_by_id(str(job.document_id))
    
    if not document or str(document.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    # Calculate stage progress
    all_stages = [s for s in JobStage]
    completed_count = len(job.stages_completed)
    
    return {
        "job_id": job_id,
        "status": job.status.value,
        "current_stage": job.current_stage.value,
        "progress_percent": job.progress,
        "stages": {
            stage.value: {
                "completed": stage in job.stages_completed,
                "current": stage == job.current_stage,
                "time_ms": job.processing_times.get(stage.value) if job.processing_times else None,
            }
            for stage in all_stages
        },
        "error": job.error_message,
        "is_complete": job.status in (JobStatus.COMPLETED, JobStatus.FAILED),
    }


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, current_user: CurrentUser):
    """Cancel a running job."""
    db = await get_database()
    repo = JobRepository(db)
    
    job = await repo.get_by_id(job_id)
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    
    # Verify ownership
    from dedox.db.repositories.document_repository import DocumentRepository
    doc_repo = DocumentRepository(db)
    document = await doc_repo.get_by_id(str(job.document_id))
    
    if not document or str(document.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    # Check if cancellable
    if job.status not in (JobStatus.QUEUED, JobStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel job in {job.status.value} status",
        )
    
    # Cancel job
    await repo.update_status(job_id, JobStatus.CANCELLED)
    
    return {"message": "Job cancelled", "job_id": job_id}


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, current_user: CurrentUser):
    """Retry a failed job."""
    db = await get_database()
    repo = JobRepository(db)
    
    job = await repo.get_by_id(job_id)
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    
    # Verify ownership
    from dedox.db.repositories.document_repository import DocumentRepository
    doc_repo = DocumentRepository(db)
    document = await doc_repo.get_by_id(str(job.document_id))
    
    if not document or str(document.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )
    
    # Check if retriable
    if job.status != JobStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only failed jobs can be retried",
        )
    
    # Reset and requeue
    from dedox.services.document_service import DocumentService
    service = DocumentService()
    new_job = await service.reprocess_document(document)
    
    return {"message": "Job requeued", "new_job_id": str(new_job.id)}


@router.get("/stats/summary")
async def get_job_stats(current_user: CurrentUser):
    """Get job statistics summary."""
    db = await get_database()
    repo = JobRepository(db)
    
    stats = await repo.get_stats_for_user(str(current_user.id))
    
    return stats


@router.get("/admin/queue")
async def get_queue_status(admin: AdminUser):
    """Get queue status (admin only)."""
    db = await get_database()
    repo = JobRepository(db)

    pending = await repo.count_by_status_single(JobStatus.QUEUED)
    running = await repo.count_by_status_single(JobStatus.PROCESSING)

    # Get oldest pending job
    oldest = await repo.get_oldest_pending()

    return {
        "pending_count": pending,
        "running_count": running,
        "oldest_pending_age_seconds": (
            (_utcnow() - oldest.created_at).total_seconds()
            if oldest else 0
        ),
    }


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: str,
    current_user: CurrentUser,
    level: str | None = Query(None, description="Minimum log level (DEBUG, INFO, WARNING, ERROR)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get processing logs for a job."""
    db = await get_database()
    repo = JobRepository(db)

    job = await repo.get_by_id(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    # Note: Document ownership check removed since this is a Paperless-ngx companion app
    # where documents don't have individual user ownership. All authenticated users
    # can view all job logs.

    # Get logs
    from dedox.db.repositories.processing_log_repository import ProcessingLogRepository
    from dedox.models.processing_log import LogLevel as LogLevelEnum

    log_repo = ProcessingLogRepository(db)

    # Parse level filter
    level_filter = None
    if level:
        try:
            level_filter = LogLevelEnum(level.upper())
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid log level: {level}",
            )

    logs, total = await log_repo.get_by_job_id(
        job_id=job.id,
        level=level_filter,
        limit=limit,
        offset=offset,
    )

    return {
        "job_id": job_id,
        "logs": [
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat(),
                "level": log.level,
                "stage": log.stage,
                "message": log.message,
                "details": log.details,
            }
            for log in logs
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
