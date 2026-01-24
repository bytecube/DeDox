"""Repository for processing log operations."""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID, uuid4

from dedox.db.database import Database
from dedox.models.processing_log import ProcessingLog, LogLevel

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


class ProcessingLogRepository:
    """Repository for processing log CRUD operations."""

    def __init__(self, db: Database):
        self.db = db

    async def ensure_table(self) -> None:
        """Create the processing_logs table if it doesn't exist."""
        await self.db.execute("""
            CREATE TABLE IF NOT EXISTS processing_logs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'INFO',
                stage TEXT,
                message TEXT NOT NULL,
                details TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            )
        """)
        # Create indexes for efficient querying
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_processing_logs_job_id
            ON processing_logs(job_id)
        """)
        await self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_processing_logs_timestamp
            ON processing_logs(timestamp)
        """)

    async def create(
        self,
        job_id: UUID,
        message: str,
        level: LogLevel = LogLevel.INFO,
        stage: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> ProcessingLog:
        """Create a new processing log entry."""
        log_entry = ProcessingLog(
            id=uuid4(),
            job_id=job_id,
            timestamp=_utcnow(),
            level=level,
            stage=stage,
            message=message,
            details=details,
        )

        await self.db.execute(
            """
            INSERT INTO processing_logs (id, job_id, timestamp, level, stage, message, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(log_entry.id),
                str(log_entry.job_id),
                log_entry.timestamp.isoformat(),
                log_entry.level,
                log_entry.stage,
                log_entry.message,
                json.dumps(log_entry.details) if log_entry.details else None,
            ),
        )

        return log_entry

    async def get_by_job_id(
        self,
        job_id: UUID,
        level: Optional[LogLevel] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ProcessingLog], int]:
        """Get all log entries for a job with optional level filtering."""
        # Build query
        query = "SELECT * FROM processing_logs WHERE job_id = ?"
        count_query = "SELECT COUNT(*) as cnt FROM processing_logs WHERE job_id = ?"
        params = [str(job_id)]

        if level:
            # Include this level and higher severity
            level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
            # Handle both string and LogLevel enum
            level_str = level.value if hasattr(level, 'value') else str(level)
            min_level = level_order.get(level_str, 1)
            level_conditions = [
                k for k, v in level_order.items() if v >= min_level
            ]
            placeholders = ",".join(["?" for _ in level_conditions])
            query += f" AND level IN ({placeholders})"
            count_query += f" AND level IN ({placeholders})"
            params.extend(level_conditions)

        # Get total count
        count_row = await self.db.fetch_one(count_query, tuple(params))
        total = count_row["cnt"] if count_row else 0

        # Add ordering and pagination
        query += " ORDER BY timestamp ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = await self.db.fetch_all(query, tuple(params))

        logs = []
        for row in rows:
            logs.append(self._row_to_model(row))

        return logs, total

    async def get_latest_by_job_id(
        self, job_id: UUID, limit: int = 50
    ) -> list[ProcessingLog]:
        """Get the most recent log entries for a job."""
        rows = await self.db.fetch_all(
            """
            SELECT * FROM processing_logs
            WHERE job_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (str(job_id), limit),
        )

        logs = [self._row_to_model(row) for row in rows]
        # Reverse to get chronological order
        return list(reversed(logs))

    async def delete_by_job_id(self, job_id: UUID) -> int:
        """Delete all log entries for a job."""
        result = await self.db.execute(
            "DELETE FROM processing_logs WHERE job_id = ?",
            (str(job_id),),
        )
        return result.rowcount if result else 0

    async def delete_old_logs(self, days: int = 30) -> int:
        """Delete log entries older than specified days."""
        cutoff = _utcnow() - timedelta(days=days)
        result = await self.db.execute(
            "DELETE FROM processing_logs WHERE timestamp < ?",
            (cutoff.isoformat(),),
        )
        return result.rowcount if result else 0

    async def count_by_job_id(self, job_id: UUID) -> int:
        """Count log entries for a job."""
        count_row = await self.db.fetch_one(
            "SELECT COUNT(*) as cnt FROM processing_logs WHERE job_id = ?",
            (str(job_id),),
        )
        return count_row["cnt"] if count_row else 0

    def _row_to_model(self, row: dict) -> ProcessingLog:
        """Convert a database row to a ProcessingLog model."""
        details = None
        if row.get("details"):
            try:
                details = json.loads(row["details"])
            except json.JSONDecodeError:
                details = None

        return ProcessingLog(
            id=UUID(row["id"]),
            job_id=UUID(row["job_id"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            level=LogLevel(row["level"]),
            stage=row.get("stage"),
            message=row["message"],
            details=details,
        )
