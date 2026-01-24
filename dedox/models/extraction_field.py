"""Extraction field models for testing extraction prompts."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FieldType(str, Enum):
    """Supported field types for extraction."""
    STRING = "string"
    TEXT = "text"
    DATE = "date"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    ENUM = "enum"
    ARRAY = "array"


class TestExtractionRequest(BaseModel):
    """Request schema for testing extraction on sample text."""

    prompt: str = Field(..., description="Extraction prompt to test")
    field_type: FieldType = Field(default=FieldType.STRING)
    enum_values: Optional[list[str]] = None
    sample_text: str = Field(..., description="Sample document text to extract from")


class TestExtractionResponse(BaseModel):
    """Response schema for extraction test results."""

    extracted_value: Optional[str] = None
    confidence: float = 0.0
    raw_response: str = ""
    success: bool = True
    error: Optional[str] = None
