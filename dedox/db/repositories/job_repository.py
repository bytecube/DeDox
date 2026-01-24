"""
Repository for Job operations.
"""

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

from dedox.db.database import Database
from dedox.models.job import Job, JobCreate, JobStatus, JobStage, JobProgress


class JobRepository:
    """Repository for Job CRUD operations."""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def create(self, job_create: JobCreate) -> Job:
        """Create a new job."""
        job = Job(
            document_id=job_create.document_id,
        )
        
        data = {
            "id": str(job.id),
            "document_id": str(job.document_id),
            "status": job.status.value,
            "current_stage": job.current_stage.value,
            "progress_percent": job.progress_percent,
            "stages": json.dumps([s.model_dump() for s in job.stages]),
            "skipped_stages": json.dumps(job.skipped_stages),
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "result": json.dumps(job.result),
            "errors": json.dumps(job.errors),
            "retry_count": job.retry_count,
            "max_retries": job.max_retries,
        }

        await self.db.insert("jobs", data)
        return job
    
    async def get_by_id(self, job_id: UUID) -> Job | None:
        """Get a job by ID."""
        row = await self.db.fetch_one(
            "SELECT * FROM jobs WHERE id = ?",
            (str(job_id),)
        )
        
        if not row:
            return None
        
        return self._row_to_job(row)
    
    async def get_by_document_id(self, document_id: UUID) -> Job | None:
        """Get the latest job for a document."""
        row = await self.db.fetch_one(
            "SELECT * FROM jobs WHERE document_id = ? ORDER BY created_at DESC LIMIT 1",
            (str(document_id),)
        )
        
        if not row:
            return None
        
        return self._row_to_job(row)
    
    async def get_pending_jobs(self, limit: int = 10) -> list[Job]:
        """Get pending jobs for processing."""
        rows = await self.db.fetch_all(
            """
            SELECT * FROM jobs 
            WHERE status IN ('queued', 'processing')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,)
        )
        
        return [self._row_to_job(row) for row in rows]
    
    async def get_jobs_by_status(
        self,
        status: JobStatus,
        limit: int = 100,
        offset: int = 0
    ) -> list[Job]:
        """Get jobs by status with pagination."""
        rows = await self.db.fetch_all(
            """
            SELECT * FROM jobs 
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (status.value, limit, offset)
        )
        
        return [self._row_to_job(row) for row in rows]
    
    async def update(self, job: Job) -> Job:
        """Update a job."""
        job.updated_at = _utcnow()
        
        data = {
            "status": job.status.value,
            "current_stage": job.current_stage.value,
            "progress_percent": job.progress_percent,
            "stages": json.dumps([
                {
                    "stage": s.stage.value,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                    "message": s.message,
                    "error": s.error,
                }
                for s in job.stages
            ]),
            "skipped_stages": json.dumps(job.skipped_stages),
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "updated_at": job.updated_at.isoformat(),
            "result": json.dumps(job.result),
            "errors": json.dumps(job.errors),
            "retry_count": job.retry_count,
        }

        await self.db.update("jobs", data, "id = ?", (str(job.id),))
        return job
    
    async def delete(self, job_id: UUID) -> bool:
        """Delete a job."""
        count = await self.db.delete("jobs", "id = ?", (str(job_id),))
        return count > 0
    
    async def count_by_status(self) -> dict[str, int]:
        """Count jobs by status."""
        rows = await self.db.fetch_all(
            "SELECT status, COUNT(*) as count FROM jobs GROUP BY status"
        )
        return {row["status"]: row["count"] for row in rows}
    
    async def list_for_user(
        self,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        **kwargs,
    ) -> tuple[list[Job], int]:
        """List jobs with pagination (currently returns all jobs, user filtering can be added later)."""
        conditions = []
        params: list[Any] = []
        
        if status:
            conditions.append("status = ?")
            params.append(status)
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Get total count
        count_row = await self.db.fetch_one(
            f"SELECT COUNT(*) as count FROM jobs WHERE {where_clause}",
            tuple(params) if params else None
        )
        total = count_row["count"] if count_row else 0
        
        # Get paginated results
        offset = (page - 1) * page_size
        rows = await self.db.fetch_all(
            f"""
            SELECT * FROM jobs 
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (page_size, offset) if params else (page_size, offset)
        )
        
        jobs = [self._row_to_job(row) for row in rows]
        return jobs, total
    
    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        error_message: str | None = None,
    ) -> Job | None:
        """Update a job's status."""
        job = await self.get_by_id(UUID(job_id))
        if not job:
            return None
        
        job.status = status
        job.updated_at = _utcnow()
        
        if status == JobStatus.PROCESSING and not job.started_at:
            job.started_at = _utcnow()
        elif status in (JobStatus.COMPLETED, JobStatus.FAILED):
            job.completed_at = _utcnow()
        
        if error_message:
            job.errors.append(error_message)
        
        return await self.update(job)

    async def get_stats_for_user(self, user_id: str) -> dict[str, Any]:
        """Get job statistics for a user.

        Currently returns stats for all jobs (user filtering can be added later).
        """
        from datetime import date

        # Get today's start timestamp
        today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)

        # Total completed
        total_completed_row = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM jobs WHERE status = ?",
            (JobStatus.COMPLETED.value,)
        )
        total_completed = total_completed_row["count"] if total_completed_row else 0

        # Completed today
        completed_today_row = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM jobs WHERE status = ? AND completed_at >= ?",
            (JobStatus.COMPLETED.value, today_start.isoformat())
        )
        completed_today = completed_today_row["count"] if completed_today_row else 0

        # Total failed
        total_failed_row = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM jobs WHERE status = ?",
            (JobStatus.FAILED.value,)
        )
        total_failed = total_failed_row["count"] if total_failed_row else 0

        # Average processing time (for completed jobs with both started_at and completed_at)
        avg_time_row = await self.db.fetch_one(
            """
            SELECT AVG(
                (julianday(completed_at) - julianday(started_at)) * 86400
            ) as avg_seconds
            FROM jobs
            WHERE status = ? AND started_at IS NOT NULL AND completed_at IS NOT NULL
            """,
            (JobStatus.COMPLETED.value,)
        )
        avg_processing_time = avg_time_row["avg_seconds"] if avg_time_row and avg_time_row["avg_seconds"] else None

        # Get average confidence from completed documents
        from dedox.db.repositories.document_repository import DocumentRepository
        doc_repo = DocumentRepository(self.db)

        # Get all completed documents and calculate average confidence
        avg_confidence = None
        try:
            confidence_row = await self.db.fetch_one(
                """
                SELECT AVG(ocr_confidence) as avg_conf
                FROM documents
                WHERE status = 'completed' AND ocr_confidence IS NOT NULL
                """
            )
            if confidence_row and confidence_row["avg_conf"]:
                avg_confidence = confidence_row["avg_conf"]
        except Exception:
            pass

        return {
            "total_completed": total_completed,
            "completed_today": completed_today,
            "total_failed": total_failed,
            "avg_processing_time_seconds": round(avg_processing_time, 1) if avg_processing_time else None,
            "avg_confidence": round(avg_confidence, 2) if avg_confidence else None,
        }

    async def get_oldest_pending(self) -> Job | None:
        """Get the oldest pending (queued) job."""
        row = await self.db.fetch_one(
            """
            SELECT * FROM jobs
            WHERE status = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (JobStatus.QUEUED.value,)
        )

        if not row:
            return None

        return self._row_to_job(row)

    async def count_by_status_single(self, status: JobStatus) -> int:
        """Count jobs with a specific status."""
        row = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM jobs WHERE status = ?",
            (status.value,)
        )
        return row["count"] if row else 0

    def _row_to_job(self, row: dict[str, Any]) -> Job:
        """Convert a database row to a Job model."""
        stages_data = json.loads(row.get("stages", "[]"))
        stages = []
        for s in stages_data:
            stages.append(JobProgress(
                stage=JobStage(s["stage"]),
                started_at=datetime.fromisoformat(s["started_at"]) if s.get("started_at") else None,
                completed_at=datetime.fromisoformat(s["completed_at"]) if s.get("completed_at") else None,
                message=s.get("message"),
                error=s.get("error"),
            ))
        
        # Parse skipped_stages (may not exist in older records)
        skipped_stages = []
        if row.get("skipped_stages"):
            try:
                skipped_stages = json.loads(row["skipped_stages"])
            except (json.JSONDecodeError, TypeError):
                skipped_stages = []

        return Job(
            id=UUID(row["id"]),
            document_id=UUID(row["document_id"]),
            status=JobStatus(row["status"]),
            current_stage=JobStage(row["current_stage"]),
            progress_percent=row["progress_percent"],
            stages=stages,
            skipped_stages=skipped_stages,
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row.get("started_at") else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row.get("completed_at") else None,
            updated_at=datetime.fromisoformat(row["updated_at"]),
            result=json.loads(row.get("result", "{}")),
            errors=json.loads(row.get("errors", "[]")),
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
        )
