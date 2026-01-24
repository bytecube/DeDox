"""Pipeline processors module."""

from dedox.pipeline.processors.image_processor import ImageProcessor
from dedox.pipeline.processors.ocr_processor import OCRProcessor
from dedox.pipeline.processors.paperless_archiver import PaperlessArchiver
from dedox.pipeline.processors.llm_extractor import LLMExtractor
from dedox.pipeline.processors.finalizer import Finalizer

__all__ = [
    "ImageProcessor",
    "OCRProcessor",
    "PaperlessArchiver",
    "LLMExtractor",
    "Finalizer",
]


def register_all_processors() -> None:
    """Register all default processors with the registry."""
    from dedox.pipeline.registry import ProcessorRegistry

    registry = ProcessorRegistry.get_instance()
    registry.register(ImageProcessor)
    registry.register(OCRProcessor)
    registry.register(PaperlessArchiver)
    registry.register(LLMExtractor)
    registry.register(Finalizer)
