"""Image encoding utilities for Vision-Language model support.

Provides functions to encode images for VL model input, handling resize,
format conversion, and base64 encoding.
"""

import base64
import io
import logging
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)


def encode_image_for_vl(
    image_path: str | Path,
    max_size: int = 1568,
    quality: int = 85
) -> Optional[str]:
    """Encode an image to base64 for Vision-Language model input.

    Handles image loading, resizing (if needed), format conversion to JPEG,
    and base64 encoding for use with VL models like qwen3-vl.

    Args:
        image_path: Path to the image file (supports PNG, JPEG, TIFF, etc.)
        max_size: Maximum dimension (width or height) in pixels. Images larger
            than this will be resized while maintaining aspect ratio.
        quality: JPEG compression quality (1-100). Higher values mean better
            quality but larger encoded size.

    Returns:
        Base64-encoded string of the image ready for VL model input,
        or None if encoding fails.

    Example:
        >>> encoded = encode_image_for_vl("/path/to/document.png")
        >>> if encoded:
        ...     # Use in Ollama VL request
        ...     messages = [{"role": "user", "content": "...", "images": [encoded]}]
    """
    try:
        path = Path(image_path)
        if not path.exists():
            logger.warning(f"Image file not found: {image_path}")
            return None

        with Image.open(path) as img:
            original_size = f"{img.width}x{img.height}"
            original_mode = img.mode

            # Convert to RGB if necessary (handles RGBA, P, L modes, etc.)
            if img.mode not in ("RGB",):
                img = img.convert("RGB")
                logger.debug(f"Converted image from {original_mode} to RGB")

            # Resize if necessary, maintaining aspect ratio
            resized = False
            if img.width > max_size or img.height > max_size:
                img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                resized = True

            # Encode to JPEG bytes
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=quality, optimize=True)
            image_bytes = buffer.getvalue()

            # Base64 encode
            encoded = base64.b64encode(image_bytes).decode("utf-8")

            logger.info(
                f"Encoded image: {path.name}, "
                f"original={original_size}, "
                f"final={img.width}x{img.height}, "
                f"resized={resized}, "
                f"encoded_size={len(encoded)} chars"
            )

            return encoded

    except Exception as e:
        logger.error(f"Failed to encode image {image_path}: {e}")
        return None


def encode_images_for_vl(
    image_paths: list[str | Path],
    max_size: int = 1568,
    quality: int = 85
) -> list[str]:
    """Encode multiple images to base64 for Vision-Language model input.

    Useful for multi-page documents where each page is a separate image.

    Args:
        image_paths: List of paths to image files
        max_size: Maximum dimension for each image
        quality: JPEG compression quality

    Returns:
        List of base64-encoded strings. Failed images are skipped.

    Example:
        >>> pages = ["/path/page1.png", "/path/page2.png"]
        >>> encoded = encode_images_for_vl(pages)
    """
    encoded_images = []
    for path in image_paths:
        encoded = encode_image_for_vl(path, max_size=max_size, quality=quality)
        if encoded:
            encoded_images.append(encoded)
    return encoded_images


def get_image_dimensions(image_path: str | Path) -> tuple[int, int] | None:
    """Get the dimensions of an image without fully loading it.

    Args:
        image_path: Path to the image file

    Returns:
        Tuple of (width, height) or None if reading fails
    """
    try:
        with Image.open(image_path) as img:
            return img.width, img.height
    except Exception as e:
        logger.error(f"Failed to get dimensions for {image_path}: {e}")
        return None
