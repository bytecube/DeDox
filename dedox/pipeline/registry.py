"""
Processor registry for managing pipeline processors.

The registry allows dynamic registration and retrieval of processors
by their stage, enabling extensibility.
"""

import logging
from typing import Type

from dedox.models.job import JobStage
from dedox.pipeline.base import BaseProcessor

logger = logging.getLogger(__name__)


class ProcessorRegistry:
    """Registry for pipeline processors.
    
    Processors are registered by their stage and can be retrieved
    in order for pipeline execution.
    """
    
    _instance: "ProcessorRegistry | None" = None
    
    def __init__(self):
        self._processors: dict[JobStage, Type[BaseProcessor]] = {}
        self._stage_order: list[JobStage] = [
            JobStage.IMAGE_PROCESSING,
            JobStage.OCR,
            JobStage.PAPERLESS_UPLOAD,
            JobStage.METADATA_EXTRACTION,
            JobStage.FINALIZATION,
        ]
    
    @classmethod
    def get_instance(cls) -> "ProcessorRegistry":
        """Get the singleton registry instance."""
        if cls._instance is None:
            cls._instance = ProcessorRegistry()
        return cls._instance
    
    def register(self, processor_class: Type[BaseProcessor]) -> None:
        """Register a processor class.
        
        Args:
            processor_class: The processor class to register.
        """
        # Create a temporary instance to get the stage
        # This works because stage is a property
        temp_instance = processor_class.__new__(processor_class)
        stage = processor_class.stage.fget(temp_instance)
        
        if stage in self._processors:
            logger.warning(
                f"Overwriting processor for stage {stage}: "
                f"{self._processors[stage].__name__} -> {processor_class.__name__}"
            )
        
        self._processors[stage] = processor_class
        logger.info(f"Registered processor: {processor_class.__name__} for stage {stage}")
    
    def get_processor(self, stage: JobStage) -> Type[BaseProcessor] | None:
        """Get the processor class for a stage.
        
        Args:
            stage: The pipeline stage.
            
        Returns:
            The processor class or None if not registered.
        """
        return self._processors.get(stage)
    
    def get_ordered_processors(self) -> list[Type[BaseProcessor]]:
        """Get all registered processors in execution order.
        
        Returns:
            List of processor classes in stage order.
        """
        processors = []
        for stage in self._stage_order:
            if stage in self._processors:
                processors.append(self._processors[stage])
        return processors
    
    def get_stages(self) -> list[JobStage]:
        """Get registered stages in execution order.
        
        Returns:
            List of stages that have registered processors.
        """
        return [
            stage for stage in self._stage_order
            if stage in self._processors
        ]
    
    def is_registered(self, stage: JobStage) -> bool:
        """Check if a processor is registered for a stage.
        
        Args:
            stage: The pipeline stage.
            
        Returns:
            True if a processor is registered.
        """
        return stage in self._processors
    
    def unregister(self, stage: JobStage) -> None:
        """Unregister a processor.
        
        Args:
            stage: The stage to unregister.
        """
        if stage in self._processors:
            del self._processors[stage]
            logger.info(f"Unregistered processor for stage {stage}")
    
    def clear(self) -> None:
        """Clear all registered processors."""
        self._processors.clear()
        logger.info("Cleared all registered processors")


def register_processor(processor_class: Type[BaseProcessor]) -> Type[BaseProcessor]:
    """Decorator to register a processor class.
    
    Usage:
        @register_processor
        class MyProcessor(BaseProcessor):
            ...
    """
    ProcessorRegistry.get_instance().register(processor_class)
    return processor_class
