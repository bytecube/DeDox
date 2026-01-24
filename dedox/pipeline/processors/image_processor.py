"""
Image processor for document preprocessing.

Handles edge detection, perspective correction, deskewing, and image enhancement.
"""

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

import cv2
import numpy as np
from PIL import Image

from dedox.core.config import get_settings
from dedox.models.job import JobStage
from dedox.pipeline.base import BaseProcessor, ProcessorContext, ProcessorResult

logger = logging.getLogger(__name__)


class ImageProcessor(BaseProcessor):
    """Processor for image preprocessing and enhancement.
    
    Performs:
    - Edge detection for document boundaries
    - Perspective correction (deskewing)
    - Image enhancement for better OCR
    - File hash calculation for duplicate detection
    """
    
    @property
    def stage(self) -> JobStage:
        return JobStage.IMAGE_PROCESSING
    
    def can_process(self, context: ProcessorContext) -> bool:
        """Check if we can process this document."""
        if not context.original_file_path:
            return False
        
        # Check if file exists
        path = Path(context.original_file_path)
        if not path.exists():
            return False
        
        # Check if it's an image or PDF
        content_type = context.document.content_type.lower()
        return content_type.startswith("image/") or content_type == "application/pdf"
    
    async def process(self, context: ProcessorContext) -> ProcessorResult:
        """Process the image."""
        start_time = _utcnow()
        
        try:
            original_path = Path(context.original_file_path)
            settings = get_settings()
            
            # Calculate file hash
            file_hash = self._calculate_hash(original_path)
            context.document.file_hash = file_hash
            context.data["file_hash"] = file_hash
            
            # Handle PDF vs image
            if context.document.content_type == "application/pdf":
                # For PDFs, convert first page to image for processing
                processed_path = await self._process_pdf(original_path, settings)
            else:
                # Process image
                processed_path = await self._process_image(original_path, settings)
            
            context.processed_file_path = str(processed_path)
            context.data["processed_path"] = str(processed_path)
            
            return ProcessorResult.ok(
                stage=self.stage,
                message=f"Image processed: {processed_path.name}",
                data={"processed_path": str(processed_path), "file_hash": file_hash},
                processing_time_ms=self._measure_time(start_time),
            )
            
        except Exception as e:
            logger.exception(f"Image processing failed: {e}")
            return ProcessorResult.fail(
                stage=self.stage,
                error=str(e),
            )
    
    async def _process_image(self, input_path: Path, settings: Any) -> Path:
        """Process a single image file."""
        # Read image
        img = cv2.imread(str(input_path))
        if img is None:
            raise ValueError(f"Failed to read image: {input_path}")
        
        # Convert to grayscale for processing
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Detect document edges and correct perspective
        processed = self._detect_and_correct_perspective(img, gray)
        
        # Enhance image for OCR
        processed = self._enhance_for_ocr(processed)
        
        # Create output path
        processed_dir = Path(settings.storage.processed_path)
        processed_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = processed_dir / f"processed_{input_path.stem}.png"

        # Use PIL for saving to ensure proper file flushing
        # cv2.imwrite can leave files truncated in some cases
        pil_image = Image.fromarray(processed)
        pil_image.save(str(output_path), format="PNG")

        logger.info(f"Processed image saved: {output_path}")
        return output_path
    
    async def _process_pdf(self, input_path: Path, settings: Any) -> Path:
        """Process a PDF file by converting pages to images."""
        try:
            from pdf2image import convert_from_path
            
            # Convert PDF pages to images
            images = convert_from_path(str(input_path), dpi=settings.ocr.dpi)
            
            if not images:
                raise ValueError("PDF contains no pages")
            
            processed_dir = Path(settings.storage.processed_path)
            processed_dir.mkdir(parents=True, exist_ok=True)
            
            # Process each page
            processed_images = []
            for i, page_img in enumerate(images):
                # Convert PIL to OpenCV format
                img = cv2.cvtColor(np.array(page_img), cv2.COLOR_RGB2BGR)
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                
                # Detect and correct perspective
                processed = self._detect_and_correct_perspective(img, gray)
                processed = self._enhance_for_ocr(processed)
                processed_images.append(processed)
            
            # If single page, save as image
            if len(processed_images) == 1:
                output_path = processed_dir / f"processed_{input_path.stem}.png"
                # Use PIL for saving to ensure proper file flushing
                pil_image = Image.fromarray(processed_images[0])
                pil_image.save(str(output_path), format="PNG")
            else:
                # Multi-page: save as multi-page TIFF
                output_path = processed_dir / f"processed_{input_path.stem}.tiff"
                pil_images = [
                    Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                    for img in processed_images
                ]
                pil_images[0].save(
                    str(output_path),
                    save_all=True,
                    append_images=pil_images[1:],
                    compression="tiff_deflate"
                )
            
            logger.info(f"Processed PDF saved: {output_path}")
            return output_path
            
        except ImportError:
            logger.warning("pdf2image not available, using original PDF")
            return input_path
    
    def _detect_and_correct_perspective(
        self,
        img: np.ndarray,
        gray: np.ndarray
    ) -> np.ndarray:
        """Detect document edges and correct perspective.

        Only applies perspective correction if a document boundary is clearly
        detected covering a significant portion of the image.
        """
        img_height, img_width = img.shape[:2]
        img_area = img_height * img_width
        settings = get_settings()
        img_settings = settings.image_processing

        # Apply Gaussian blur
        kernel_size = img_settings.gaussian_blur_kernel
        blurred = cv2.GaussianBlur(gray, (kernel_size, kernel_size), 0)

        # Edge detection
        edges = cv2.Canny(blurred, img_settings.canny_threshold_low, img_settings.canny_threshold_high)

        # Find contours
        contours, _ = cv2.findContours(
            edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        if not contours:
            logger.info("No contours found, returning original image")
            return img

        # Get the largest contour
        largest_contour = max(contours, key=cv2.contourArea)
        contour_area = cv2.contourArea(largest_contour)

        # Only consider perspective correction if contour covers at least 30% of image
        # This prevents cropping to small elements like stamps or logos
        min_area_ratio = 0.30
        if contour_area < img_area * min_area_ratio:
            logger.info(
                f"Largest contour too small ({contour_area / img_area:.1%} of image), "
                "skipping perspective correction"
            )
            return self._deskew(img, gray)

        # Approximate the contour to a polygon
        epsilon = 0.02 * cv2.arcLength(largest_contour, True)
        approx = cv2.approxPolyDP(largest_contour, epsilon, True)

        # If we found a quadrilateral, correct perspective
        if len(approx) == 4:
            result = self._four_point_transform(img, approx.reshape(4, 2))
            # Sanity check: result should be at least 50% of original size
            result_area = result.shape[0] * result.shape[1]
            if result_area < img_area * 0.5:
                logger.warning(
                    f"Perspective correction result too small ({result_area / img_area:.1%}), "
                    "using deskewed original instead"
                )
                return self._deskew(img, gray)
            return result

        # Otherwise, just deskew
        return self._deskew(img, gray)
    
    def _four_point_transform(
        self,
        img: np.ndarray,
        pts: np.ndarray
    ) -> np.ndarray:
        """Apply perspective transform to straighten document."""
        # Order points: top-left, top-right, bottom-right, bottom-left
        rect = self._order_points(pts)
        (tl, tr, br, bl) = rect
        
        # Compute width of new image
        width_a = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        width_b = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        max_width = max(int(width_a), int(width_b))
        
        # Compute height of new image
        height_a = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        height_b = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        max_height = max(int(height_a), int(height_b))
        
        # Destination points
        dst = np.array([
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1]
        ], dtype="float32")
        
        # Compute perspective transform matrix and apply
        matrix = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(img, matrix, (max_width, max_height))
        
        return warped
    
    def _order_points(self, pts: np.ndarray) -> np.ndarray:
        """Order points in: top-left, top-right, bottom-right, bottom-left."""
        rect = np.zeros((4, 2), dtype="float32")
        
        # Top-left has smallest sum, bottom-right has largest sum
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        
        # Top-right has smallest difference, bottom-left has largest
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        
        return rect
    
    def _deskew(self, img: np.ndarray, gray: np.ndarray) -> np.ndarray:
        """Deskew image based on detected text angle."""
        settings = get_settings()
        img_settings = settings.image_processing

        # Detect skew angle using Hough transform
        edges = cv2.Canny(gray, img_settings.canny_line_threshold_low, img_settings.canny_line_threshold_high, apertureSize=3)
        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 180, 100,
            minLineLength=img_settings.hough_min_line_length, maxLineGap=img_settings.hough_max_line_gap
        )
        
        if lines is None:
            return img
        
        # Calculate angles
        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            angle = np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi
            if abs(angle) < 45:  # Filter out near-vertical lines
                angles.append(angle)
        
        if not angles:
            return img
        
        # Median angle
        median_angle = np.median(angles)
        
        if abs(median_angle) < 0.5:  # Skip if angle is tiny
            return img
        
        # Rotate image
        (h, w) = img.shape[:2]
        center = (w // 2, h // 2)
        matrix = cv2.getRotationMatrix2D(center, median_angle, 1.0)
        rotated = cv2.warpAffine(
            img, matrix, (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE
        )
        
        logger.info(f"Deskewed image by {median_angle:.2f} degrees")
        return rotated
    
    def _enhance_for_ocr(self, img: np.ndarray) -> np.ndarray:
        """Enhance image for better OCR results.

        Uses gentle enhancement to avoid destroying text.
        Tesseract handles binarization internally, so we just prepare
        a clean grayscale image.
        """
        # Convert to grayscale if needed
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img.copy()

        # Light denoising - be gentle to preserve text
        denoised = cv2.fastNlMeansDenoising(gray, None, h=5, templateWindowSize=7, searchWindowSize=21)

        # Increase contrast using CLAHE (Contrast Limited Adaptive Histogram Equalization)
        # This helps with faded text without destroying it
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(denoised)

        return enhanced
    
    def _calculate_hash(self, file_path: Path) -> str:
        """Calculate SHA-256 hash of file."""
        sha256_hash = hashlib.sha256()
        
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        
        return sha256_hash.hexdigest()
