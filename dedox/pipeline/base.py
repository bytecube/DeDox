"""
Base processor class for pipeline stages.

All pipeline processors should inherit from BaseProcessor and implement
the process() method.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

from dedox.models.document import Document
from dedox.models.job import Job, JobStage


@dataclass
class ProcessorContext:
    """Context passed through the processing pipeline.
    
    Contains the document, job, and accumulated results from previous stages.
    """
    document: Document
    job: Job
    
    # Accumulated data from previous stages
    data: dict[str, Any] = field(default_factory=dict)
    
    # File paths
    original_file_path: str | None = None
    processed_file_path: str | None = None
    archive_file_path: str | None = None  # Higher quality version for archival
    
    # OCR results
    ocr_text: str | None = None
    ocr_confidence: float | None = None
    ocr_language: str | None = None
    
    # Extracted metadata
    metadata: dict[str, Any] = field(default_factory=dict)
    metadata_confidence: dict[str, float] = field(default_factory=dict)
    
    # Paperless integration
    paperless_id: int | None = None
    paperless_task_id: str | None = None
    
    # Embeddings
    embeddings: list[list[float]] = field(default_factory=list)
    
    # Error tracking
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    
    def add_error(self, error: str) -> None:
        """Add an error message."""
        self.errors.append(error)
    
    def add_warning(self, warning: str) -> None:
        """Add a warning message."""
        self.warnings.append(warning)
    
    def has_errors(self) -> bool:
        """Check if there are any errors."""
        return len(self.errors) > 0


@dataclass
class ProcessorResult:
    """Result from a processor stage."""
    success: bool
    stage: JobStage
    message: str | None = None
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    processing_time_ms: int = 0
    
    @classmethod
    def ok(
        cls,
        stage: JobStage,
        message: str | None = None,
        data: dict[str, Any] | None = None,
        processing_time_ms: int = 0
    ) -> "ProcessorResult":
        """Create a successful result."""
        return cls(
            success=True,
            stage=stage,
            message=message,
            data=data or {},
            processing_time_ms=processing_time_ms,
        )
    
    @classmethod
    def fail(
        cls,
        stage: JobStage,
        error: str,
        data: dict[str, Any] | None = None
    ) -> "ProcessorResult":
        """Create a failed result."""
        return cls(
            success=False,
            stage=stage,
            error=error,
            data=data or {},
        )


class BaseProcessor(ABC):
    """Abstract base class for all pipeline processors.
    
    Subclasses must implement:
    - stage: The JobStage this processor handles
    - process(): The main processing logic
    
    Optionally override:
    - can_process(): Check if processor can handle the context
    - cleanup(): Cleanup resources after processing
    """
    
    @property
    @abstractmethod
    def stage(self) -> JobStage:
        """The pipeline stage this processor handles."""
        pass
    
    @property
    def name(self) -> str:
        """Human-readable name of the processor."""
        return self.__class__.__name__
    
    @property
    def description(self) -> str:
        """Description of what this processor does."""
        return self.__doc__ or ""
    
    def can_process(self, context: ProcessorContext) -> bool:
        """Check if this processor can handle the given context.
        
        Override this to add custom validation logic.
        Default implementation always returns True.
        """
        return True
    
    @abstractmethod
    async def process(self, context: ProcessorContext) -> ProcessorResult:
        """Process the document.
        
        Args:
            context: The processing context with document, job, and accumulated data.
            
        Returns:
            ProcessorResult indicating success or failure.
        """
        pass
    
    async def cleanup(self, context: ProcessorContext) -> None:
        """Cleanup any resources after processing.
        
        Override this to clean up temporary files, connections, etc.
        Called regardless of success or failure.
        """
        pass
    
    def _measure_time(self, start: datetime) -> int:
        """Calculate processing time in milliseconds."""
        delta = _utcnow() - start
        return int(delta.total_seconds() * 1000)
