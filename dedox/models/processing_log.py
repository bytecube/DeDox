"""Processing log model for detailed job logging."""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


class LogLevel(str, Enum):
    """Log level for processing logs."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ProcessingLog(BaseModel):
    """A single log entry for document processing."""

    id: UUID = Field(default_factory=uuid4)
    job_id: UUID
    timestamp: datetime = Field(default_factory=_utcnow)
    level: LogLevel = LogLevel.INFO
    stage: Optional[str] = None
    message: str
    details: Optional[dict] = None

    class Config:
        use_enum_values = True


class ProcessingLogCreate(BaseModel):
    """Schema for creating a processing log entry."""

    job_id: UUID
    level: LogLevel = LogLevel.INFO
    stage: Optional[str] = None
    message: str
    details: Optional[dict] = None
