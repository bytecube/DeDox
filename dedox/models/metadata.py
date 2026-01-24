"""
Metadata model definitions for extracted document information.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MetadataConfidence(BaseModel):
    """Confidence scores for extracted metadata fields."""
    field_name: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    raw_response: str | None = None
    
    class Config:
        from_attributes = True


class ExtractedMetadata(BaseModel):
    """Extracted metadata from a document."""
    document_id: UUID

    # Core fields
    document_type: str | None = None
    sender: str | None = None
    recipient: str | None = None
    subject: str | None = None

    # Dates
    document_date: date | None = None
    due_date: date | None = None
    validity_end_date: date | None = None  # Expiration date for contracts, warranties, IDs

    # Financial
    total_amount: float | None = None
    currency: str | None = None

    # Identifiers
    reference_number: str | None = None
    account_number: str | None = None  # Persistent customer/policy number

    # Analysis
    language: str | None = None
    urgency: str | None = None  # low, medium, high, critical
    action_required: bool = False
    tax_relevant: bool = False  # Whether relevant for tax filing
    retention_period: str | None = None  # permanent, 10_years, 6_years, 3_years, 1_year

    # Summary and keywords
    summary: str | None = None
    keywords: list[str] = Field(default_factory=list)
    
    # Confidence tracking
    confidence_scores: dict[str, float] = Field(default_factory=dict)
    overall_confidence: float = 0.0
    
    # Additional custom fields (from configurable extraction)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    
    # Extraction metadata
    extracted_at: datetime = Field(default_factory=datetime.utcnow)
    llm_model: str | None = None
    extraction_time_ms: int | None = None
    
    class Config:
        from_attributes = True
    
    def calculate_overall_confidence(self) -> float:
        """Calculate overall confidence as weighted average."""
        if not self.confidence_scores:
            return 0.0
        
        # Weight required fields higher
        required_fields = {"document_type", "sender", "document_date"}
        
        total_weight = 0.0
        weighted_sum = 0.0
        
        for field, confidence in self.confidence_scores.items():
            weight = 2.0 if field in required_fields else 1.0
            weighted_sum += confidence * weight
            total_weight += weight
        
        self.overall_confidence = weighted_sum / total_weight if total_weight > 0 else 0.0
        return self.overall_confidence
    
    def to_paperless_metadata(self) -> dict[str, Any]:
        """Convert to Paperless-ngx metadata format."""
        return {
            "document_type": self.document_type,
            "correspondent": self.sender,
            "title": self.subject,
            "created": self.document_date.isoformat() if self.document_date else None,
            "tags": self._get_tags(),
            "custom_fields": self._get_custom_fields(),
        }
    
    def _get_tags(self) -> list[str]:
        """Get tags to apply to the document."""
        tags = list(self.keywords)

        if self.urgency:
            tags.append(f"urgency:{self.urgency}")

        if self.action_required:
            tags.append("action-required")

        if self.tax_relevant:
            tags.append("tax-relevant")

        return tags
    
    def _get_custom_fields(self) -> dict[str, Any]:
        """Get custom fields for Paperless-ngx."""
        fields = {}

        if self.due_date:
            fields["Due Date"] = self.due_date.isoformat()

        if self.validity_end_date:
            fields["Valid Until"] = self.validity_end_date.isoformat()

        if self.total_amount is not None:
            amount_str = f"{self.currency or 'EUR'}{self.total_amount:.2f}"
            fields["Amount"] = amount_str

        if self.reference_number:
            fields["Reference"] = self.reference_number

        if self.account_number:
            fields["Account Number"] = self.account_number

        if self.recipient:
            fields["Recipient"] = self.recipient

        if self.summary:
            fields["Summary"] = self.summary

        if self.language:
            fields["Language"] = self.language

        if self.retention_period:
            fields["Retention Period"] = self.retention_period

        # Add any additional custom fields
        fields.update(self.custom_fields)

        return fields
