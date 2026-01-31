"""
LLM extractor processor for metadata extraction.

Uses Ollama for local LLM-based extraction of document metadata.
Supports structured JSON output for reliable field extraction.

Architecture:
    This module is organized into three main components:

    1. **ConfidenceEstimator**: Calculates confidence scores for extracted values
       based on field type and value characteristics.

    2. **UrgencyCalculator**: Evaluates urgency rules against extracted metadata
       to determine document priority (critical, high, medium, low).

    3. **LLMExtractor**: The main processor that orchestrates extraction using
       Ollama, delegates to the above components, and handles sender matching.
"""

import json
import logging
import re
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

import httpx

from dedox.core.config import get_settings, get_metadata_fields, get_urgency_rules
from dedox.core.image_utils import encode_image_for_vl
from dedox.core.exceptions import LLMError
from dedox.models.job import JobStage
from dedox.models.metadata import ExtractedMetadata
from dedox.pipeline.base import BaseProcessor, ProcessorContext, ProcessorResult
from dedox.pipeline.processors.sender_matcher import SenderMatcher

logger = logging.getLogger(__name__)


class ConfidenceEstimator:
    """Estimates confidence scores for extracted metadata values.

    Confidence scoring is based on field type and value characteristics:

    - **Enum fields** (0.9): High confidence when value matches allowed options
    - **Date fields** (0.85): High confidence for valid YYYY-MM-DD format
    - **Decimal fields** (0.8): High confidence for numeric values
    - **Boolean fields** (0.85): High confidence (binary choice)
    - **String fields** (0.5-0.75): Variable based on value length

    The scores are heuristic-based since we cannot directly measure LLM certainty.
    Longer string values generally indicate more specific extraction.

    Example:
        >>> estimator = ConfidenceEstimator()
        >>> estimator.estimate("invoice", "enum", ["invoice", "letter", "contract"])
        0.9
        >>> estimator.estimate("2024-01-15", "date", None)
        0.85
        >>> estimator.estimate(None, "string", None)
        0.0
    """

    # Confidence scores by field type
    ENUM_MATCH_CONFIDENCE = 0.9
    ENUM_NO_MATCH_CONFIDENCE = 0.5
    DATE_VALID_CONFIDENCE = 0.85
    DATE_INVALID_CONFIDENCE = 0.5
    DECIMAL_CONFIDENCE = 0.8
    BOOLEAN_CONFIDENCE = 0.85
    STRING_SHORT_CONFIDENCE = 0.5
    STRING_LONG_CONFIDENCE = 0.75
    STRING_LENGTH_THRESHOLD = 3

    def estimate(
        self,
        value: Any,
        field_type: str,
        allowed_values: list[str] | None
    ) -> float:
        """Estimate confidence score for an extracted value.

        Args:
            value: The extracted value (can be None)
            field_type: Type of the field (enum, date, decimal, boolean, string, etc.)
            allowed_values: For enum fields, the list of valid options

        Returns:
            Confidence score between 0.0 and 1.0
        """
        if value is None:
            return 0.0

        if field_type == "enum" and allowed_values:
            return self._estimate_enum_confidence(value, allowed_values)
        elif field_type == "date":
            return self._estimate_date_confidence(value)
        elif field_type == "decimal":
            return self._estimate_decimal_confidence(value)
        elif field_type == "boolean":
            return self.BOOLEAN_CONFIDENCE
        else:
            return self._estimate_string_confidence(value)

    def _estimate_enum_confidence(self, value: Any, allowed_values: list[str]) -> float:
        """Estimate confidence for enum field values."""
        if value in allowed_values:
            return self.ENUM_MATCH_CONFIDENCE
        return self.ENUM_NO_MATCH_CONFIDENCE

    def _estimate_date_confidence(self, value: Any) -> float:
        """Estimate confidence for date field values."""
        if re.match(r'\d{4}-\d{2}-\d{2}', str(value)):
            return self.DATE_VALID_CONFIDENCE
        return self.DATE_INVALID_CONFIDENCE

    def _estimate_decimal_confidence(self, value: Any) -> float:
        """Estimate confidence for decimal field values."""
        if isinstance(value, (int, float)):
            return self.DECIMAL_CONFIDENCE
        return self.ENUM_NO_MATCH_CONFIDENCE

    def _estimate_string_confidence(self, value: Any) -> float:
        """Estimate confidence for string field values based on length."""
        if len(str(value)) > self.STRING_LENGTH_THRESHOLD:
            return self.STRING_LONG_CONFIDENCE
        return self.STRING_SHORT_CONFIDENCE


class UrgencyCalculator:
    """Calculates document urgency based on configurable rules.

    Urgency is determined by evaluating rules from config/urgency_rules.yaml
    against extracted metadata. Rules are evaluated in order; the first
    matching rule determines the urgency level.

    Urgency Levels:
        - **critical**: Requires immediate attention (e.g., due in < 3 days)
        - **high**: Requires action soon (e.g., due in < 7 days)
        - **medium**: Standard priority
        - **low**: No urgency (default)

    Condition Types:
        - ``always``: Always matches
        - ``due_date_within_days``: Due date within N days from today
        - ``has_due_date``: Document has/doesn't have a due date
        - ``document_type``: Document type equals specified value
        - ``keywords_any``: OCR text contains any of specified keywords
        - ``field_equals``: Specific field equals specified value

    Example:
        >>> calculator = UrgencyCalculator()
        >>> metadata = {"due_date": "2024-01-20", "document_type": "invoice"}
        >>> calculator.calculate(metadata)  # If due_date is within 3 days
        'critical'
    """

    DEFAULT_URGENCY = "low"

    def __init__(self):
        """Initialize the urgency calculator."""
        self._date_parser = DateParser()

    def calculate(self, metadata: dict[str, Any]) -> str:
        """Calculate urgency level for document metadata.

        Args:
            metadata: Dictionary of extracted metadata fields

        Returns:
            Urgency level string: 'critical', 'high', 'medium', or 'low'
        """
        urgency_config = get_urgency_rules()

        for rule in urgency_config.rules:
            if self._evaluate_rule(rule, metadata):
                return rule.urgency

        return self.DEFAULT_URGENCY

    def _evaluate_rule(self, rule, metadata: dict[str, Any]) -> bool:
        """Evaluate a single urgency rule.

        A rule matches if ALL its conditions are satisfied (AND logic).

        Args:
            rule: Rule object with conditions list and urgency level
            metadata: Document metadata to evaluate against

        Returns:
            True if all conditions match, False otherwise
        """
        for condition in rule.conditions:
            if not self._evaluate_condition(condition, metadata):
                return False
        return True

    def _evaluate_condition(self, condition, metadata: dict[str, Any]) -> bool:
        """Evaluate a single condition against metadata.

        Args:
            condition: Condition object with type, value, and optional field
            metadata: Document metadata to evaluate against

        Returns:
            True if condition is satisfied, False otherwise
        """
        cond_type = condition.type
        value = condition.value

        if cond_type == "always":
            return True

        elif cond_type == "due_date_within_days":
            return self._check_due_date_within_days(metadata, value)

        elif cond_type == "has_due_date":
            has_date = metadata.get("due_date") is not None
            return has_date == value

        elif cond_type == "document_type":
            return metadata.get("document_type") == value

        elif cond_type == "keywords_any":
            return self._check_keywords_any(metadata, value)

        elif cond_type == "field_equals":
            field = condition.field
            return metadata.get(field) == value

        return False

    def _check_due_date_within_days(self, metadata: dict[str, Any], days: int) -> bool:
        """Check if due date is within specified number of days."""
        due_date = self._date_parser.parse(metadata.get("due_date"))
        if due_date:
            days_until = (due_date - date.today()).days
            return days_until <= days
        return False

    def _check_keywords_any(self, metadata: dict[str, Any], keywords: list[str]) -> bool:
        """Check if OCR text contains any of the specified keywords."""
        ocr_text = metadata.get("_ocr_text", "").lower()
        return any(kw.lower() in ocr_text for kw in keywords)


class DateParser:
    """Parses date values from various formats.

    Supports common date formats:
        - ISO format: YYYY-MM-DD
        - German format: DD.MM.YYYY
        - European format: DD/MM/YYYY
        - US format: MM/DD/YYYY

    Example:
        >>> parser = DateParser()
        >>> parser.parse("2024-01-15")
        datetime.date(2024, 1, 15)
        >>> parser.parse("15.01.2024")
        datetime.date(2024, 1, 15)
    """

    DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y"]

    def parse(self, value: Any) -> date | None:
        """Parse a date value from various formats.

        Args:
            value: Date value (string, date object, or None)

        Returns:
            Parsed date object or None if parsing fails
        """
        if value is None:
            return None

        if isinstance(value, date):
            return value

        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(str(value), fmt).date()
            except ValueError:
                continue

        return None

# System prompt for metadata extraction
EXTRACTION_SYSTEM_PROMPT = """You are a document metadata extraction assistant for a personal document management system. Extract structured information from OCR text of scanned documents with high precision.

CORE PRINCIPLES:
1. ACCURACY OVER COMPLETENESS: Only extract information explicitly stated. Use null for anything uncertain.
2. OCR TOLERANCE: Account for common OCR errors (0/O, l/1/I confusion, missing spaces).
3. LANGUAGE AWARENESS: Documents may be in German or English. Recognize patterns in both.

EXTRACTION RULES:
1. Extract ONLY information clearly visible in the document text
2. Never infer, guess, or hallucinate values not present
3. If OCR quality makes a value ambiguous, use null rather than guessing
4. For dates: Convert any format to YYYY-MM-DD
   - German: DD.MM.YYYY, DD.MM.YY
   - US: MM/DD/YYYY
   - ISO: YYYY-MM-DD
5. For monetary amounts: Extract numeric value only (e.g., 1234.56)
6. For enum fields: Select closest valid option, or null if none fit
7. Ignore marketing text, advertisements, and boilerplate footers

DOCUMENT PATTERNS:

Sender (priority order):
- Letterhead logo/name at top
- Return address (Absender)
- Company name in signature block
- Bank details owner (last resort)

Document Date (priority order):
- "Datum:", "Date:" labels
- Date in header (top right)
- Reference line date
- Signature date (last resort)

Amounts:
- Use final totals: "Gesamtbetrag", "Total", "Summe", "Endbetrag"
- IGNORE: Subtotals, tax-only amounts, individual line items

Reference Numbers:
- "Rechnungsnummer", "Invoice No."
- "Aktenzeichen", "Reference"
- "Vertragsnummer", "Policy No."

Subject/Title:
- Extract the ACTUAL subject or title from the document
- Look for: "Betreff:", "Subject:", "Re:", "Ihr Schreiben:", "Concerning:"
- For invoices: Extract service/product description (e.g., "Mobilfunkrechnung Januar 2024")
- For official documents: Extract the formal subject line
- Do NOT concatenate metadata fields to create a title

Summary:
- ALWAYS provide a brief summary (max 128 characters)
- Describe the main purpose and key facts of the document
- Use the document's language (German or English)
- Include key dates, amounts, or deadlines if present
- Never return empty, null, "UNKNOWN", or "N/A" for summary

Keywords:
- Extract 3-5 unique keywords to help find this document
- Use specific product/service names, unique identifiers, specific topics
- Avoid generic terms already captured elsewhere (sender name, document type)

OUTPUT: Valid JSON only. No explanations or markdown."""


# System prompt for Vision-Language model extraction (combined OCR + metadata)
VL_EXTRACTION_SYSTEM_PROMPT = """/no_think

You are a document processing assistant with vision capabilities. Analyze the document image and perform two tasks:

1. **TEXT EXTRACTION (OCR)**: Extract ALL readable text from the document image.
   - Use Markdown formatting for better structure:
     - Tables: Use markdown table format with `| col | col |` and separator row
     - Headers/Sections: Use `## Section Name` for major sections
     - Lists: Use `- item` for bullet points, `1. item` for numbered lists
   - Preserve original language (German or English)
   - Include all text: headers, body, footers, stamps, handwriting if readable

   Example table formatting:
   | Pos | Artikel | Menge | Preis |
   |-----|---------|-------|-------|
   | 1   | Widget  | 5     | 10,00 |

2. **METADATA EXTRACTION**: Extract ALL of the following fields from the document:

   **CRITICAL FIELDS (always extract if visible):**
   - **sender**: Company/person who sent the document. Look for:
     - Letterhead/logo at top
     - Return address (Absender)
     - Signature block company name
     Example: "Deutsche Telekom AG", "Finanzamt München"

   - **subject**: The actual subject line or title as written in the document. Look for:
     - "Betreff:", "Subject:", "Re:" lines
     - Bold/large headers after greeting
     - Document title (e.g., "Rechnung Nr. 12345")
     Example: "Mobilfunkrechnung Januar 2024", "Mahnung - offene Rechnung"

   - **summary**: Write a brief 1-2 sentence summary of the document's main content.
     Always provide a summary based on what you see!
     Example: "Monatliche Telefonrechnung über 45,99 EUR, zahlbar bis 15.02.2024"

   **Other fields**: document_type, recipient, document_date, due_date, total_amount, currency, reference_number, etc.

CORE PRINCIPLES:
1. ALWAYS extract sender, subject, and summary if ANY information is visible
2. For sender: Use company/organization name, not individual employee names
3. For subject: Use the ACTUAL text from the document, don't generate one
4. For summary: Always write something - describe what the document is about
5. For other fields: Use null only if truly not present

LANGUAGE: Documents may be in German or English. Keep original language for sender/subject.

OUTPUT: Valid JSON with ocr_text containing markdown-formatted text and ALL metadata fields."""


class LLMExtractor(BaseProcessor):
    """Processor for LLM-based metadata extraction.

    Uses Ollama to extract configurable metadata fields from OCR text.
    This is the main orchestrator that coordinates:

    1. **Extraction**: Calls Ollama with structured JSON schema
    2. **Confidence Scoring**: Delegates to ConfidenceEstimator
    3. **Urgency Calculation**: Delegates to UrgencyCalculator
    4. **Sender Matching**: Deduplicates correspondents via SenderMatcher

    Processing Flow:
        1. Build JSON schema from configured metadata fields
        2. Send OCR text + schema to Ollama Chat API
        3. Parse and validate the structured response
        4. Estimate confidence for each extracted field
        5. Match sender against existing Paperless correspondents
        6. Calculate document urgency based on rules
        7. Return extracted metadata with confidence scores

    Fallback Strategy:
        If batch extraction fails, falls back to per-field extraction
        which is slower but more robust for edge cases.

    Configuration:
        - Fields: config/metadata_fields.yaml
        - Urgency rules: config/urgency_rules.yaml
        - LLM settings: config/settings.yaml (llm section)
    """

    def __init__(self):
        """Initialize the LLM extractor with helper components."""
        super().__init__()
        self._sender_matcher: SenderMatcher | None = None
        self._confidence_estimator = ConfidenceEstimator()
        self._urgency_calculator = UrgencyCalculator()
        self._date_parser = DateParser()

    @property
    def sender_matcher(self) -> SenderMatcher:
        """Lazy initialization of sender matcher."""
        if self._sender_matcher is None:
            self._sender_matcher = SenderMatcher(self._call_ollama)
        return self._sender_matcher

    @property
    def stage(self) -> JobStage:
        return JobStage.METADATA_EXTRACTION

    def can_process(self, context: ProcessorContext) -> bool:
        """Check if we can extract metadata.

        For VL models: Need processed image path (text will be extracted from image)
        For text-only models: Need OCR text from previous stage
        """
        settings = get_settings()

        if settings.llm.is_vision_model and settings.llm.skip_ocr_for_vl:
            # VL mode: need processed image
            image_path = context.processed_file_path or context.original_file_path
            if not image_path or not Path(image_path).exists():
                logger.warning("No image available for VL metadata extraction")
                return False
            return True
        else:
            # Text-only mode: need OCR text
            if not context.ocr_text:
                logger.warning("No OCR text available for metadata extraction")
                return False
            return True

    async def process(self, context: ProcessorContext) -> ProcessorResult:
        """Extract metadata using LLM with structured JSON output.

        For VL models: Extracts text AND metadata from image in single call.
        For text-only models: Uses OCR text from previous stage.
        """
        start_time = _utcnow()

        try:
            settings = get_settings()
            metadata_config = get_metadata_fields()

            # Determine processing mode
            if settings.llm.is_vision_model and settings.llm.skip_ocr_for_vl:
                # VL mode: extract text + metadata from image
                logger.info("Using VL model for combined text and metadata extraction")
                image_path = context.processed_file_path or context.original_file_path
                additional_pages = context.data.get("additional_page_images", [])
                extracted, confidence_scores, ocr_text = await self._extract_with_vision(
                    metadata_config.fields,
                    image_path,
                    settings,
                    additional_pages=additional_pages
                )
                # Store extracted text in context for downstream use
                context.ocr_text = ocr_text
                context.ocr_confidence = 95.0  # VL models generally have high accuracy
                context.ocr_language = extracted.get("language") or "de"
                # Also update document
                context.document.ocr_text = ocr_text
                context.document.ocr_confidence = 95.0
                context.document.ocr_language = context.ocr_language
            else:
                # Text-only mode: use OCR text from previous stage
                logger.info("Using text-only model for metadata extraction")
                extracted, confidence_scores = await self._extract_all_fields_structured(
                    metadata_config.fields,
                    context.ocr_text,
                    settings
                )

            # Calculate urgency using dedicated calculator
            urgency = self._urgency_calculator.calculate(extracted)
            extracted["urgency"] = urgency
            
            # Create metadata object
            metadata = ExtractedMetadata(
                document_id=context.document.id,
                document_type=extracted.get("document_type"),
                sender=extracted.get("sender"),
                recipient=extracted.get("recipient"),
                subject=extracted.get("subject"),
                document_date=self._date_parser.parse(extracted.get("document_date")),
                due_date=self._date_parser.parse(extracted.get("due_date")),
                validity_end_date=self._date_parser.parse(extracted.get("validity_end_date")),
                total_amount=self._parse_decimal(extracted.get("total_amount")),
                currency=extracted.get("currency"),
                reference_number=extracted.get("reference_number"),
                account_number=extracted.get("account_number"),
                language=extracted.get("language") or context.ocr_language,
                urgency=urgency,
                action_required=extracted.get("action_required", False),
                tax_relevant=extracted.get("tax_relevant", False),
                retention_period=extracted.get("retention_period"),
                summary=extracted.get("summary"),
                keywords=self._parse_keywords(extracted.get("keywords")),
                confidence_scores=confidence_scores,
                llm_model=settings.llm.model,
                extraction_time_ms=self._measure_time(start_time),
            )
            
            metadata.calculate_overall_confidence()
            
            # Update context
            context.metadata = extracted
            context.metadata_confidence = confidence_scores
            context.document.metadata = extracted
            context.document.metadata_confidence = confidence_scores

            return ProcessorResult.ok(
                stage=self.stage,
                message=f"Extracted {len(extracted)} metadata fields",
                data={
                    "fields_extracted": len(extracted),
                    "overall_confidence": metadata.overall_confidence,
                    "metadata": extracted,
                },
                processing_time_ms=self._measure_time(start_time),
            )
            
        except LLMError as e:
            logger.error(f"LLM extraction failed: {e}")
            return ProcessorResult.fail(
                stage=self.stage,
                error=str(e),
            )
        except Exception as e:
            logger.exception(f"Metadata extraction failed: {e}")
            return ProcessorResult.fail(
                stage=self.stage,
                error=str(e),
            )
    
    async def _extract_field(
        self,
        field_name: str,
        prompt_template: str,
        field_type: str,
        allowed_values: list[str] | None,
        ocr_text: str,
        settings
    ) -> tuple[Any, float]:
        """Extract a single field using the LLM."""
        # Build prompt
        prompt = prompt_template.format(
            ocr_text=ocr_text[:settings.llm.ocr_text_limit],  # Limit text length for context window
            values=", ".join(allowed_values) if allowed_values else "",
        )
        
        # Call Ollama
        response = await self._call_ollama(prompt, settings)
        
        # Parse response based on type
        value = self._parse_response(response, field_type, allowed_values)
        
        # Estimate confidence based on response quality
        confidence = self._confidence_estimator.estimate(value, field_type, allowed_values)
        
        logger.debug(f"Extracted {field_name}: {value} (confidence: {confidence:.2f})")

        return value, confidence

    async def _extract_all_fields_structured(
        self,
        fields: list,
        ocr_text: str,
        settings
    ) -> tuple[dict[str, Any], dict[str, float]]:
        """Extract all fields in a single call using structured JSON output.

        This is more efficient and produces cleaner results than per-field extraction.
        """
        logger.info(f"Starting structured extraction for {len(fields)} fields, OCR text length: {len(ocr_text)} chars")

        # Build JSON schema for structured output
        json_schema = self._build_json_schema(fields)

        # Log schema details for debugging
        schema_json = json.dumps(json_schema)
        logger.info(f"Generated JSON schema: {len(schema_json)} chars, {len(json_schema.get('properties', {}))} properties")
        logger.debug(f"Full JSON schema: {schema_json[:2000]}...")

        # Build the extraction prompt
        prompt = self._build_structured_prompt(fields, ocr_text, settings)
        logger.debug(f"Extraction prompt ({len(prompt)} chars): {prompt[:500]}...")

        # Call Ollama Chat API with schema-constrained output
        try:
            response_json = await self._call_ollama_chat(prompt, json_schema, settings)

            # Parse and validate the response
            extracted = {}
            confidence_scores = {}

            for field in fields:
                field_name = field.name
                raw_value = response_json.get(field_name)

                # Clean and validate the value
                value = self._clean_extracted_value(raw_value, field.type, field.values)

                if value is not None:
                    extracted[field_name] = value
                    confidence_scores[field_name] = self._confidence_estimator.estimate(
                        value, field.type, field.values
                    )

            # Match sender against existing correspondents to avoid duplicates
            if extracted.get("sender"):
                matched_sender = await self.sender_matcher.match_sender(
                    extracted["sender"],
                    settings
                )
                if matched_sender != extracted["sender"]:
                    logger.info(f"Sender matched: '{extracted['sender']}' -> '{matched_sender}'")
                extracted["sender"] = matched_sender

            logger.info(f"Structured extraction completed: {len(extracted)} fields")
            logger.info(f"Extracted metadata: {extracted}")
            return extracted, confidence_scores

        except Exception as e:
            logger.warning(f"Structured extraction failed, falling back to per-field: {e}")
            # Fall back to per-field extraction
            return await self._extract_fields_individually(fields, ocr_text, settings)

    async def _extract_with_vision(
        self,
        fields: list,
        image_path: str,
        settings,
        additional_pages: list[str] | None = None
    ) -> tuple[dict[str, Any], dict[str, float], str]:
        """Extract text and metadata from document image(s) using VL model.

        This method performs combined OCR and metadata extraction in a single
        call to the VL model, which can directly read the document image(s).

        For multi-page documents (PDFs), all pages are sent to the VL model
        and text is extracted from each page.

        Args:
            fields: List of metadata fields to extract
            image_path: Path to the primary document image
            settings: Application settings
            additional_pages: Optional list of paths to additional page images

        Returns:
            Tuple of (metadata dict, confidence dict, extracted OCR text)
        """
        # Collect all image paths
        all_image_paths = [image_path]
        if additional_pages:
            all_image_paths.extend(additional_pages)

        logger.info(f"VL extraction from {len(all_image_paths)} image(s): {image_path}")

        # Encode all images for VL model
        encoded_images = []
        for img_path in all_image_paths:
            image_base64 = encode_image_for_vl(
                img_path,
                max_size=settings.llm.max_image_size_pixels,
                quality=settings.llm.image_quality
            )
            if image_base64:
                encoded_images.append(image_base64)
            else:
                logger.warning(f"Failed to encode image: {img_path}")

        if not encoded_images:
            raise LLMError(f"Failed to encode any images from: {image_path}")

        # Build JSON schema that includes ocr_text field
        json_schema = self._build_vl_json_schema(fields)

        # Build prompt for VL extraction (adjust for multi-page)
        if len(encoded_images) > 1:
            prompt = f"""Analyze these {len(encoded_images)} document page images and extract:
1. The complete text content from ALL pages combined (ocr_text field)
2. Structured metadata fields

Read ALL text visible in ALL pages carefully. Combine text from all pages in reading order."""
        else:
            prompt = """Analyze this document image and extract:
1. The complete text content (ocr_text field)
2. Structured metadata fields

Read ALL text visible in the document carefully."""

        # Call VL model with all images
        response_json = await self._call_ollama_chat_vl(
            prompt,
            json_schema,
            encoded_images,
            settings
        )

        # Extract OCR text
        ocr_text = response_json.get("ocr_text", "")
        if not ocr_text:
            logger.warning("VL model returned empty ocr_text")
            ocr_text = ""

        logger.info(f"VL extracted {len(ocr_text)} characters of text")

        # Debug: Log raw response keys and critical fields
        logger.debug(f"VL response keys: {list(response_json.keys())}")
        for critical_field in ["sender", "subject", "summary"]:
            raw_val = response_json.get(critical_field)
            logger.info(f"VL raw '{critical_field}': {repr(raw_val)[:100]}")

        # Process metadata fields
        extracted = {}
        confidence_scores = {}

        for field in fields:
            field_name = field.name
            raw_value = response_json.get(field_name)

            value = self._clean_extracted_value(raw_value, field.type, field.values)

            if value is not None:
                extracted[field_name] = value
                confidence_scores[field_name] = self._confidence_estimator.estimate(
                    value, field.type, field.values
                )

        # Match sender against existing correspondents
        if extracted.get("sender"):
            matched_sender = await self.sender_matcher.match_sender(
                extracted["sender"],
                settings
            )
            if matched_sender != extracted["sender"]:
                logger.info(f"Sender matched: '{extracted['sender']}' -> '{matched_sender}'")
            extracted["sender"] = matched_sender

        logger.info(f"VL extraction completed: {len(extracted)} fields, {len(ocr_text)} chars text")
        return extracted, confidence_scores, ocr_text

    def _build_vl_json_schema(self, fields: list) -> dict:
        """Build JSON schema for VL extraction including ocr_text field."""
        # Start with base schema from regular extraction
        schema = self._build_json_schema(fields)

        # Add ocr_text field for extracted text
        schema["properties"]["ocr_text"] = {
            "type": "string",
            "description": "Complete text content extracted from the document image"
        }

        # Make ocr_text required
        if "ocr_text" not in schema.get("required", []):
            schema.setdefault("required", []).append("ocr_text")

        return schema

    async def _call_ollama_chat_vl(
        self,
        user_prompt: str,
        json_schema: dict,
        images_base64: list[str],
        settings
    ) -> dict:
        """Call Ollama Chat API with VL model and image input.

        Args:
            user_prompt: The prompt for the model
            json_schema: JSON schema for structured output
            images_base64: List of base64-encoded images (supports multi-page docs)
            settings: Application settings

        Returns:
            Parsed JSON response from the model
        """
        schema_size = len(json.dumps(json_schema))
        prompt_size = len(user_prompt)
        total_image_size = sum(len(img) for img in images_base64)

        logger.info(
            f"Ollama VL chat request: model={settings.llm.model}, "
            f"prompt={prompt_size} chars, "
            f"schema={schema_size} chars, "
            f"images={len(images_base64)}, "
            f"total_image_size={total_image_size} chars"
        )

        # Use VL system prompt
        system_prompt = VL_EXTRACTION_SYSTEM_PROMPT if settings.llm.disable_thinking else VL_EXTRACTION_SYSTEM_PROMPT.replace("/no_think\n\n", "")

        async with httpx.AsyncClient(
            timeout=settings.llm.timeout_seconds
        ) as client:
            for attempt in range(settings.llm.max_retries):
                try:
                    logger.info(f"Sending Ollama VL request (attempt {attempt + 1}/{settings.llm.max_retries})...")

                    response = await client.post(
                        f"{settings.llm.base_url}/api/chat",
                        json={
                            "model": settings.llm.model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {
                                    "role": "user",
                                    "content": user_prompt,
                                    "images": images_base64
                                }
                            ],
                            "stream": False,
                            "format": json_schema,
                            "options": {
                                "temperature": settings.llm.temperature,
                                "num_ctx": settings.llm.context_window,
                            }
                        }
                    )

                    if response.status_code != 200:
                        raise LLMError(
                            f"Ollama VL API error: {response.status_code} - {response.text}"
                        )

                    result = response.json()
                    response_text = result.get("message", {}).get("content", "").strip()

                    logger.info(f"Raw VL response length: {len(response_text)} chars")

                    # Parse JSON response
                    try:
                        parsed = json.loads(response_text)
                        return parsed
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse VL JSON response: {e}")
                        # Try to extract JSON
                        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                        if json_match:
                            return json.loads(json_match.group())
                        raise LLMError(f"Invalid JSON response from VL model: {response_text[:200]}")

                except httpx.TimeoutException:
                    if attempt < settings.llm.max_retries - 1:
                        logger.warning(f"Ollama VL timeout, retrying ({attempt + 1})")
                        continue
                    raise LLMError("Ollama VL request timed out")

                except httpx.ConnectError:
                    raise LLMError(
                        f"Cannot connect to Ollama at {settings.llm.base_url}"
                    )

        raise LLMError("Max retries exceeded for VL request")

    async def _extract_fields_individually(
        self,
        fields: list,
        ocr_text: str,
        settings
    ) -> tuple[dict[str, Any], dict[str, float]]:
        """Fall back to extracting fields one by one."""
        extracted = {}
        confidence_scores = {}

        for field in fields:
            try:
                value, confidence = await self._extract_field(
                    field.name,
                    field.prompt,
                    field.type,
                    field.values,
                    ocr_text,
                    settings
                )

                if value is not None:
                    extracted[field.name] = value
                    confidence_scores[field.name] = confidence

            except Exception as e:
                logger.warning(f"Failed to extract field {field.name}: {e}")

        # Match sender against existing correspondents to avoid duplicates
        if extracted.get("sender"):
            matched_sender = await self.sender_matcher.match_sender(
                extracted["sender"],
                settings
            )
            if matched_sender != extracted["sender"]:
                logger.info(f"Sender matched: '{extracted['sender']}' -> '{matched_sender}'")
            extracted["sender"] = matched_sender

        logger.info(f"Individual extraction completed: {len(extracted)} fields")
        logger.info(f"Extracted metadata: {extracted}")
        return extracted, confidence_scores

    def _build_json_schema(self, fields: list) -> dict:
        """Build a JSON schema for structured output with nullable types."""
        properties = {}
        required = []

        for field in fields:
            # Use short description only - system prompt handles detailed instructions
            description = field.description or field.name

            if field.type == "enum" and field.values:
                # For enums, always include the values in the enum list
                # Add empty string as a "null" option for optional fields
                enum_values = list(field.values)
                if not field.required:
                    enum_values.append("")  # Empty string as null alternative
                field_schema = {
                    "type": "string",
                    "enum": enum_values,
                    "description": description
                }
            elif field.type == "boolean":
                # Booleans are always required (default false, not null)
                field_schema = {
                    "type": "boolean",
                    "description": description
                }
                if field.name not in required:
                    required.append(field.name)
            elif field.type == "decimal":
                # Use number type - LLM can return 0 or omit for "no value"
                field_schema = {"type": "number", "description": description}
            elif field.type == "date":
                # Dates as strings - empty string means no date
                date_desc = f"{description}. Format: YYYY-MM-DD or empty string if not found"
                field_schema = {
                    "type": "string",
                    "description": date_desc
                }
            elif field.type == "array":
                # Arrays - empty array means no values
                field_schema = {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": description
                }
            else:  # string, text
                # Strings - empty string means no value
                field_schema = {"type": "string", "description": description}

            properties[field.name] = field_schema

            if field.required and field.name not in required:
                required.append(field.name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    def _build_structured_prompt(self, fields: list, ocr_text: str, settings) -> str:
        """Build a simplified prompt for structured extraction.

        The system prompt and JSON schema handle field definitions,
        so this just provides the document content.
        """
        return f"""Extract metadata from this document:

---
{ocr_text[:settings.llm.ocr_text_limit]}
---"""

    async def _call_ollama_chat(
        self,
        user_prompt: str,
        json_schema: dict,
        settings
    ) -> dict:
        """Call Ollama Chat API with schema-constrained JSON output.

        Uses /api/chat instead of /api/generate for better instruction following.
        Passes the actual JSON schema to the format parameter for grammar-constrained generation.
        """
        # Log request details for debugging
        schema_size = len(json.dumps(json_schema))
        prompt_size = len(user_prompt)
        system_prompt_size = len(EXTRACTION_SYSTEM_PROMPT)
        total_size = schema_size + prompt_size + system_prompt_size

        logger.info(
            f"Ollama chat request: model={settings.llm.model}, "
            f"system_prompt={system_prompt_size} chars, "
            f"user_prompt={prompt_size} chars, "
            f"schema={schema_size} chars, "
            f"total={total_size} chars, "
            f"num_ctx={settings.llm.context_window}, timeout={settings.llm.timeout_seconds}s"
        )
        logger.debug(f"JSON schema properties: {list(json_schema.get('properties', {}).keys())}")

        async with httpx.AsyncClient(
            timeout=settings.llm.timeout_seconds
        ) as client:
            for attempt in range(settings.llm.max_retries):
                try:
                    logger.info(f"Sending Ollama chat request (attempt {attempt + 1}/{settings.llm.max_retries})...")
                    response = await client.post(
                        f"{settings.llm.base_url}/api/chat",
                        json={
                            "model": settings.llm.model,
                            "messages": [
                                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                                {"role": "user", "content": user_prompt}
                            ],
                            "stream": False,
                            "format": json_schema,  # Schema-constrained output
                            "options": {
                                "temperature": settings.llm.temperature,
                                "num_ctx": settings.llm.context_window,
                            }
                        }
                    )

                    if response.status_code != 200:
                        raise LLMError(
                            f"Ollama API error: {response.status_code} - {response.text}"
                        )

                    result = response.json()
                    # Chat API returns message.content instead of response
                    response_text = result.get("message", {}).get("content", "").strip()

                    logger.info(f"Raw LLM response: {response_text}")

                    # Parse JSON response
                    try:
                        parsed = json.loads(response_text)
                        logger.info(f"Parsed LLM response: {parsed}")
                        return parsed
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON response: {e}")
                        # Try to extract JSON from the response (fallback)
                        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                        if json_match:
                            return json.loads(json_match.group())
                        raise LLMError(f"Invalid JSON response: {response_text[:200]}")

                except httpx.TimeoutException:
                    if attempt < settings.llm.max_retries - 1:
                        logger.warning(f"Ollama timeout, retrying ({attempt + 1})")
                        continue
                    raise LLMError("Ollama request timed out")

                except httpx.ConnectError:
                    raise LLMError(
                        f"Cannot connect to Ollama at {settings.llm.base_url}"
                    )

        raise LLMError("Max retries exceeded")

    def _clean_extracted_value(
        self,
        value: Any,
        field_type: str,
        allowed_values: list[str] | None
    ) -> Any:
        """Clean and validate an extracted value.

        With schema-constrained output, values should already be well-formed.
        This handles edge cases and legacy "UNKNOWN" strings for compatibility.
        """
        # Handle null values (schema uses null for missing fields)
        if value is None:
            return None

        # Handle legacy "UNKNOWN" strings (for backward compatibility)
        if isinstance(value, str):
            value = value.strip()
            if value.upper() in ["UNKNOWN", "NONE", "N/A", "NOT FOUND", ""] or value == "null":
                return None

        if field_type == "enum" and allowed_values:
            if isinstance(value, str):
                # Case-insensitive match
                value_lower = value.lower()
                for allowed in allowed_values:
                    if allowed.lower() == value_lower:
                        return allowed
            return None

        elif field_type == "boolean":
            # Schema should provide actual boolean, but handle strings for robustness
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ["true", "yes", "1", "ja"]
            return False  # Default to false for booleans

        elif field_type == "decimal":
            # Schema should provide number, but handle strings for robustness
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    cleaned = re.sub(r'[^\d.,\-]', '', value)
                    cleaned = cleaned.replace(",", ".")
                    return float(cleaned)
                except ValueError:
                    return None
            return None

        elif field_type == "date":
            if isinstance(value, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', value):
                return value
            return None

        elif field_type == "array":
            # Schema should provide array, but handle strings for robustness
            if isinstance(value, list):
                return [str(v).strip() for v in value if v]
            if isinstance(value, str):
                return [v.strip() for v in value.split(",") if v.strip()]
            return None

        else:  # string, text
            return str(value) if value else None

    async def _call_ollama(self, prompt: str, settings) -> str:
        """Call Ollama API."""
        async with httpx.AsyncClient(
            timeout=settings.llm.timeout_seconds
        ) as client:
            for attempt in range(settings.llm.max_retries):
                try:
                    response = await client.post(
                        f"{settings.llm.base_url}/api/generate",
                        json={
                            "model": settings.llm.model,
                            "prompt": prompt,
                            "stream": False,
                            "options": {
                                "temperature": settings.llm.temperature,
                                "num_ctx": settings.llm.context_window,
                            }
                        }
                    )
                    
                    if response.status_code != 200:
                        raise LLMError(
                            f"Ollama API error: {response.status_code} - {response.text}"
                        )
                    
                    result = response.json()
                    return result.get("response", "").strip()
                    
                except httpx.TimeoutException:
                    if attempt < settings.llm.max_retries - 1:
                        logger.warning(f"Ollama timeout, retrying ({attempt + 1})")
                        continue
                    raise LLMError("Ollama request timed out")
                
                except httpx.ConnectError:
                    raise LLMError(
                        f"Cannot connect to Ollama at {settings.llm.base_url}"
                    )
        
        raise LLMError("Max retries exceeded")
    
    def _parse_response(
        self,
        response: str,
        field_type: str,
        allowed_values: list[str] | None
    ) -> Any:
        """Parse LLM response based on field type."""
        response = response.strip()
        
        # Handle "not found" responses
        if response.upper() in ["UNKNOWN", "NONE", "N/A", "NOT FOUND", ""]:
            return None
        
        if field_type == "enum" and allowed_values:
            # Find matching value (case-insensitive)
            response_lower = response.lower()
            for value in allowed_values:
                if value.lower() == response_lower:
                    return value
            # Try partial match
            for value in allowed_values:
                if value.lower() in response_lower or response_lower in value.lower():
                    return value
            return None
        
        elif field_type == "boolean":
            return response.lower() in ["true", "yes", "1", "ja"]
        
        elif field_type == "date":
            return response  # Will be parsed later
        
        elif field_type == "decimal":
            # Extract number from response
            match = re.search(r'[\d.,]+', response.replace(",", "."))
            if match:
                try:
                    return float(match.group().replace(",", "."))
                except ValueError:
                    return None
            return None
        
        elif field_type == "array":
            # Split by commas
            return response
        
        else:  # string, text
            return response if response else None
    
    def _parse_decimal(self, value: Any) -> float | None:
        """Parse a decimal value."""
        if value is None:
            return None
        
        if isinstance(value, (int, float)):
            return float(value)
        
        try:
            # Remove currency symbols and parse
            cleaned = re.sub(r'[^\d.,\-]', '', str(value))
            cleaned = cleaned.replace(",", ".")
            return float(cleaned)
        except ValueError:
            return None
    
    def _parse_keywords(self, value: Any) -> list[str]:
        """Parse keywords from string."""
        if value is None:
            return []
        
        if isinstance(value, list):
            return value
        
        # Split by comma and clean
        keywords = [kw.strip() for kw in str(value).split(",")]
        return [kw for kw in keywords if kw]
