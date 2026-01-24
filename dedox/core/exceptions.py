"""
Custom exceptions for DeDox application.
"""

from typing import Any


class DedoxError(Exception):
    """Base exception for all DeDox errors."""
    
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(DedoxError):
    """Configuration-related errors."""
    pass


class ValidationError(DedoxError):
    """Input validation errors."""
    pass


class ProcessingError(DedoxError):
    """Document processing errors."""
    pass


class OCRError(ProcessingError):
    """OCR-specific errors."""
    pass


class LLMError(ProcessingError):
    """LLM extraction errors."""
    pass


class PaperlessError(DedoxError):
    """Paperless-ngx API errors."""
    
    def __init__(self, message: str, status_code: int | None = None, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.status_code = status_code


class PaperlessConnectionError(PaperlessError):
    """Paperless-ngx connection errors."""
    pass


class PaperlessAPIError(PaperlessError):
    """Paperless-ngx API response errors."""
    pass


class StorageError(DedoxError):
    """File storage errors."""
    pass


class AuthenticationError(DedoxError):
    """Authentication-related errors."""
    pass


class AuthorizationError(DedoxError):
    """Authorization-related errors."""
    pass


class JobNotFoundError(DedoxError):
    """Job not found error."""
    
    def __init__(self, job_id: str):
        super().__init__(f"Job not found: {job_id}", {"job_id": job_id})
        self.job_id = job_id


class DocumentNotFoundError(DedoxError):
    """Document not found error."""
    
    def __init__(self, document_id: str):
        super().__init__(f"Document not found: {document_id}", {"document_id": document_id})
        self.document_id = document_id
