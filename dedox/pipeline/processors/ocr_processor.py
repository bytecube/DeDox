"""
OCR processor for text extraction.

Uses Tesseract for German and English text extraction.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

import pytesseract
from PIL import Image

from dedox.core.config import get_settings
from dedox.models.job import JobStage
from dedox.pipeline.base import BaseProcessor, ProcessorContext, ProcessorResult

logger = logging.getLogger(__name__)


class OCRProcessor(BaseProcessor):
    """Processor for OCR text extraction.
    
    Uses Tesseract to extract text from processed images,
    with support for German and English languages.
    """
    
    @property
    def stage(self) -> JobStage:
        return JobStage.OCR
    
    def can_process(self, context: ProcessorContext) -> bool:
        """Check if we can process this document.

        Skips OCR when a Vision-Language model is configured, as the VL model
        will extract text directly from the image during metadata extraction.
        """
        # Skip OCR when VL model is active (VL model handles text extraction)
        settings = get_settings()
        if settings.llm.is_vision_model and settings.llm.skip_ocr_for_vl:
            logger.info("Skipping OCR: Vision-Language model will extract text")
            return False

        # Need a processed image file
        file_path = context.processed_file_path or context.original_file_path
        if not file_path:
            return False

        path = Path(file_path)
        return path.exists()
    
    async def process(self, context: ProcessorContext) -> ProcessorResult:
        """Extract text using OCR."""
        start_time = _utcnow()

        try:
            settings = get_settings()

            # Always perform our own OCR for consistent results
            # Use processed file if available, otherwise original
            file_path = context.processed_file_path or context.original_file_path
            path = Path(file_path)
            
            # Configure Tesseract
            lang_string = "+".join(settings.ocr.languages)  # e.g., "deu+eng"
            
            # OCR configuration
            custom_config = f"--psm {settings.ocr.psm}"
            
            # Handle multi-page TIFF
            if path.suffix.lower() in [".tiff", ".tif"]:
                text, confidence, language = await self._process_multipage_tiff(
                    path, lang_string, custom_config
                )
            else:
                text, confidence, language = await self._process_single_image(
                    path, lang_string, custom_config
                )
            
            # Check confidence threshold
            if confidence < settings.ocr.confidence_threshold:
                context.add_warning(
                    f"OCR confidence ({confidence:.1f}%) below threshold "
                    f"({settings.ocr.confidence_threshold}%)"
                )
            
            # Update context
            context.ocr_text = text
            context.ocr_confidence = confidence
            context.ocr_language = language

            # Also update document directly
            context.document.ocr_text = text
            context.document.ocr_confidence = confidence
            context.document.ocr_language = language

            # Log the full OCR text for comparison with Paperless OCR
            logger.info("=" * 80)
            logger.info(f"DEDOX OCR TEXT (Document: {context.document.original_filename})")
            logger.info(f"Language: {language}, Confidence: {confidence:.1f}%, Length: {len(text)} chars")
            logger.info("=" * 80)
            logger.info(f"\n{text}\n")
            logger.info("=" * 80)
            
            return ProcessorResult.ok(
                stage=self.stage,
                message=f"OCR completed: {len(text)} characters, {confidence:.1f}% confidence",
                data={
                    "text_length": len(text),
                    "confidence": confidence,
                    "language": language,
                },
                processing_time_ms=self._measure_time(start_time),
            )
            
        except Exception as e:
            logger.exception(f"OCR failed: {e}")
            return ProcessorResult.fail(
                stage=self.stage,
                error=str(e),
            )
    
    async def _process_single_image(
        self,
        path: Path,
        lang: str,
        config: str
    ) -> tuple[str, float, str]:
        """Process a single image file."""
        # Open image
        image = Image.open(path)
        
        # Get OCR data with confidence
        data = pytesseract.image_to_data(
            image, lang=lang, config=config, output_type=pytesseract.Output.DICT
        )
        
        # Extract text
        text = pytesseract.image_to_string(image, lang=lang, config=config)
        
        # Calculate confidence
        confidences = [
            int(conf) for conf in data["conf"]
            if conf != "-1" and str(conf).isdigit()
        ]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0
        
        # Detect dominant language
        detected_lang = self._detect_language(text)
        
        return text.strip(), avg_confidence, detected_lang
    
    async def _process_multipage_tiff(
        self,
        path: Path,
        lang: str,
        config: str
    ) -> tuple[str, float, str]:
        """Process a multi-page TIFF file."""
        image = Image.open(path)
        
        all_text = []
        all_confidences = []
        
        # Process each page
        page_num = 0
        while True:
            try:
                image.seek(page_num)
                
                # Get OCR data
                data = pytesseract.image_to_data(
                    image, lang=lang, config=config, output_type=pytesseract.Output.DICT
                )
                
                # Extract text
                text = pytesseract.image_to_string(image, lang=lang, config=config)
                all_text.append(text.strip())
                
                # Calculate confidence
                confidences = [
                    int(conf) for conf in data["conf"]
                    if conf != "-1" and str(conf).isdigit()
                ]
                if confidences:
                    all_confidences.extend(confidences)
                
                page_num += 1
                
            except EOFError:
                break
        
        # Combine all pages
        combined_text = "\n\n--- Page Break ---\n\n".join(all_text)
        avg_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0
        detected_lang = self._detect_language(combined_text)
        
        logger.info(f"Processed {page_num} pages from TIFF")
        
        return combined_text, avg_confidence, detected_lang
    
    def _detect_language(self, text: str) -> str:
        """Simple language detection based on common words."""
        text_lower = text.lower()
        
        # German indicators
        german_words = [
            "der", "die", "das", "und", "ist", "von", "mit", "für",
            "den", "dem", "ein", "eine", "einer", "nicht", "sich",
            "auf", "als", "auch", "nach", "wird", "bei", "haben",
            "sehr", "geehrte", "rechnung", "datum", "betrag"
        ]
        
        # English indicators
        english_words = [
            "the", "and", "is", "of", "to", "in", "for", "on",
            "with", "as", "by", "this", "that", "from", "have",
            "not", "are", "was", "been", "dear", "invoice", "date"
        ]
        
        german_count = sum(1 for word in german_words if f" {word} " in f" {text_lower} ")
        english_count = sum(1 for word in english_words if f" {word} " in f" {text_lower} ")
        
        if german_count > english_count:
            return "de"
        elif english_count > german_count:
            return "en"
        else:
            # Default to German if unclear
            return "de"
