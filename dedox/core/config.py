"""
Configuration management for DeDox.

Loads and validates configuration from YAML files with environment variable support.
"""

import os
import re
import secrets
import warnings
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings


def _resolve_env_vars(value: Any) -> Any:
    """Resolve environment variable references in configuration values.
    
    Supports format: ${VAR_NAME:default_value} or ${VAR_NAME}
    """
    if isinstance(value, str):
        pattern = r'\$\{([^}:]+)(?::([^}]*))?\}'
        
        def replacer(match):
            var_name = match.group(1)
            default = match.group(2) if match.group(2) is not None else ""
            return os.environ.get(var_name, default)
        
        return re.sub(pattern, replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_yaml_config(path: Path) -> dict:
    """Load a YAML configuration file with environment variable resolution."""
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    
    with open(path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    return _resolve_env_vars(config)


# --- Settings Models ---

class ServerSettings(BaseModel):
    """Server configuration."""
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    debug: bool = False
    # Default to localhost only; configure explicitly for production
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8000"]
    # Hostname used for webhook URL construction (container/service name)
    service_hostname: str = "dedox"


class AuthSettings(BaseModel):
    """Authentication configuration."""
    secret_key: str = ""
    algorithm: str = "HS256"
    access_token_expire_hours: int = 24
    refresh_token_expire_days: int = 7
    allow_registration: bool = False  # Disabled by default for security
    token_expire_hours: int = 24

    _INSECURE_DEFAULTS = {"change-me-in-production", "secret", "changeme", ""}

    @model_validator(mode="after")
    def validate_secret_key(self) -> "AuthSettings":
        """Ensure secret_key is set to a secure value."""
        if self.secret_key in self._INSECURE_DEFAULTS:
            # Check environment variable
            env_secret = os.environ.get("DEDOX_JWT_SECRET")
            if env_secret and env_secret not in self._INSECURE_DEFAULTS:
                self.secret_key = env_secret
            else:
                # Generate a secure random secret
                self.secret_key = secrets.token_urlsafe(32)
                warnings.warn(
                    "JWT secret_key not configured! A random key was generated. "
                    "Set DEDOX_JWT_SECRET or auth.secret_key in settings.yaml for "
                    "persistent sessions across restarts.",
                    UserWarning,
                    stacklevel=2,
                )
        return self

    @property
    def jwt_secret(self) -> str:
        """Alias for secret_key for JWT operations."""
        return self.secret_key

    @property
    def jwt_algorithm(self) -> str:
        """Alias for algorithm for JWT operations."""
        return self.algorithm


class StorageSettings(BaseModel):
    """Storage paths configuration."""
    base_path: str = "/data"
    upload_path: str = "/data/uploads"
    processed_path: str = "/data/processed"
    vector_db_path: str = "/data/vectors/dedox.db"
    
    @property
    def originals_dir(self) -> str:
        """Directory name for original files (relative to base_path)."""
        return "originals"
    
    @property
    def processed_dir(self) -> str:
        """Directory name for processed files (relative to base_path)."""
        return "processed"
    
    @property
    def max_file_size_mb(self) -> int:
        """Alias for processing.max_file_size_mb for backward compatibility."""
        return 50  # Default, will be overridden by processing settings


class ProcessingSettings(BaseModel):
    """Document processing configuration."""
    max_file_size_mb: int = 50
    supported_formats: list[str] = ["jpg", "jpeg", "png", "pdf", "tiff", "tif"]
    default_language: str = "de"
    supported_languages: list[str] = ["de", "en"]
    # Correspondent cache TTL in seconds
    correspondent_cache_ttl: int = 300
    # Max correspondents to fetch for matching
    max_correspondents: int = 200
    # Default pagination limit for API responses
    pagination_limit: int = 100


class OCRSettings(BaseModel):
    """OCR engine configuration."""
    engine: str = "tesseract"
    languages: list[str] = ["deu", "eng"]
    confidence_threshold: int = 60
    psm: int = 3
    tesseract_path: str = "/usr/bin/tesseract"
    dpi: int = 300

    @property
    def min_confidence(self) -> int:
        """Alias for confidence_threshold."""
        return self.confidence_threshold


class ImageProcessingSettings(BaseModel):
    """Image processing configuration for document enhancement."""
    # Gaussian blur kernel size (must be odd)
    gaussian_blur_kernel: int = 5
    # Canny edge detection thresholds
    canny_threshold_low: int = 75
    canny_threshold_high: int = 200
    # Alternative Canny thresholds for line detection
    canny_line_threshold_low: int = 50
    canny_line_threshold_high: int = 150
    # Hough line detection parameters
    hough_min_line_length: int = 100
    hough_max_line_gap: int = 10


class LLMSettings(BaseModel):
    """LLM provider configuration."""
    provider: str = "ollama"
    base_url: str = "http://ollama:11434"
    model: str = "qwen2.5:14b"
    # Increased timeout to handle initial model loading and complex extractions
    timeout_seconds: int = 600
    temperature: float = 0.1
    max_retries: int = 3
    # Context window size (num_ctx for Ollama)
    context_window: int = 16384
    # Max characters of OCR text to send to LLM
    ocr_text_limit: int = 8000

    @property
    def ollama_url(self) -> str:
        """Alias for base_url."""
        return self.base_url


class TagColorSettings(BaseModel):
    """Color settings for Paperless tags."""
    default: str = "#808080"      # Gray
    processing: str = "#FFA500"   # Orange
    enhanced: str = "#28A745"     # Green
    error: str = "#DC3545"        # Red
    review: str = "#FFC107"       # Yellow
    reprocess: str = "#9C27B0"    # Purple
    pending: str = "#FFA500"      # Orange


class WebhookSettings(BaseModel):
    """Webhook configuration for receiving events from external systems."""
    enabled: bool = True
    secret: str = ""  # HMAC secret for webhook signature verification
    auto_create_custom_fields: bool = True  # Auto-create Paperless custom fields if missing
    auto_setup_workflow: bool = False  # Auto-create Paperless workflow on startup


class OpenWebUISettings(BaseModel):
    """Open WebUI integration configuration."""
    enabled: bool = True
    base_url: str = "http://open-webui:8080"
    frontend_port: int = 3000
    api_key: str = ""
    admin_email: str = ""  # Admin email for automatic API key generation
    admin_password: str = ""  # Admin password for automatic API key generation
    auto_generate_api_key: bool = True  # Generate API key automatically if not provided
    knowledge_base_id: str = "dedox-documents"
    auto_create_knowledge_base: bool = True
    timeout_seconds: int = 60
    wait_for_processing: bool = True
    max_processing_wait: int = 300
    # Polling interval for status checks (seconds)
    poll_interval: int = 5
    # Wait time for file processing (seconds)
    file_processing_wait: int = 10


class PaperlessSettings(BaseModel):
    """Paperless-ngx integration configuration."""
    base_url: str = "http://paperless:8000"
    api_token: str = ""
    # Admin credentials for automatic API token generation (if api_token not provided)
    admin_user: str = "admin"
    admin_password: str = ""
    auto_generate_token: bool = True  # Auto-generate token using admin credentials if api_token empty
    verify_ssl: bool = False
    timeout_seconds: int = 30
    # API version for Accept header (e.g., "application/json; version=9")
    api_version: int = 9
    # Timeout for downloading documents (seconds)
    document_download_timeout: int = 120
    # Timeout for connection tests (seconds)
    connection_test_timeout: int = 10
    processing_tag: str = "dedox:processing"
    enhanced_tag: str = "dedox:enhanced"
    error_tag: str = "dedox:error"
    review_tag: str = "dedox:needs-review"
    duplicate_tag: str = "dedox:potential-duplicate"
    reprocess_tag: str = "dedox:reprocess"  # Tag to trigger reprocessing
    default_correspondent: str = ""
    webhook: WebhookSettings = Field(default_factory=WebhookSettings)
    tag_colors: TagColorSettings = Field(default_factory=TagColorSettings)

    @property
    def url(self) -> str:
        """Alias for base_url."""
        return self.base_url


class DatabaseSettings(BaseModel):
    """Database configuration."""
    path: str = "/data/dedox.db"
    wal_mode: bool = True


class Settings(BaseModel):
    """Main application settings."""
    server: ServerSettings = Field(default_factory=ServerSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    processing: ProcessingSettings = Field(default_factory=ProcessingSettings)
    ocr: OCRSettings = Field(default_factory=OCRSettings)
    image_processing: ImageProcessingSettings = Field(default_factory=ImageProcessingSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    paperless: PaperlessSettings = Field(default_factory=PaperlessSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    openwebui: OpenWebUISettings = Field(default_factory=OpenWebUISettings)
    
    @classmethod
    def load(cls, config_dir: Path | None = None) -> "Settings":
        """Load settings from configuration file."""
        if config_dir is None:
            config_dir = Path(os.environ.get("DEDOX_CONFIG_DIR", "/app/config"))
        
        settings_path = config_dir / "settings.yaml"
        
        if settings_path.exists():
            config = load_yaml_config(settings_path)
            return cls(**config)
        
        return cls()


# --- Metadata Fields Models ---

class PaperlessMapping(BaseModel):
    """Mapping to Paperless-ngx fields."""
    type: str  # document_type, correspondent, custom_field, tag, title, created_date
    auto_create: bool = False
    field_name: str | None = None
    field_type: str | None = None
    tag_name: str | None = None
    apply_if_true: bool | None = None


class MetadataField(BaseModel):
    """A configurable metadata extraction field."""
    name: str
    type: str  # enum, string, date, decimal, boolean, text, array
    description: str = ""
    values: list[str] | None = None  # For enum types
    prompt: str
    required: bool = False
    paperless_mapping: PaperlessMapping | None = None


class MetadataFieldsConfig(BaseModel):
    """Metadata fields configuration."""
    fields: list[MetadataField]
    
    @classmethod
    def load(cls, config_dir: Path | None = None) -> "MetadataFieldsConfig":
        """Load metadata fields from configuration file."""
        if config_dir is None:
            config_dir = Path(os.environ.get("DEDOX_CONFIG_DIR", "/app/config"))
        
        path = config_dir / "metadata_fields.yaml"
        
        if path.exists():
            config = load_yaml_config(path)
            return cls(**config)
        
        return cls(fields=[])
    
    def get_field(self, name: str) -> MetadataField | None:
        """Get a field by name."""
        for field in self.fields:
            if field.name == name:
                return field
        return None


# --- Document Types Models ---

class DocumentType(BaseModel):
    """A document type definition."""
    id: str
    name: str
    name_de: str = ""
    description: str = ""
    keywords: list[str] = []
    default_urgency: str = "low"


class DocumentTypesConfig(BaseModel):
    """Document types configuration."""
    document_types: list[DocumentType]
    
    @classmethod
    def load(cls, config_dir: Path | None = None) -> "DocumentTypesConfig":
        """Load document types from configuration file."""
        if config_dir is None:
            config_dir = Path(os.environ.get("DEDOX_CONFIG_DIR", "/app/config"))
        
        path = config_dir / "document_types.yaml"
        
        if path.exists():
            config = load_yaml_config(path)
            return cls(**config)
        
        return cls(document_types=[])
    
    def get_type(self, type_id: str) -> DocumentType | None:
        """Get a document type by ID."""
        for doc_type in self.document_types:
            if doc_type.id == type_id:
                return doc_type
        return None


# --- Urgency Rules Models ---

class UrgencyLevel(BaseModel):
    """An urgency level definition."""
    id: str
    name: str
    name_de: str = ""
    color: str = "#000000"
    priority: int = 0


class UrgencyCondition(BaseModel):
    """A condition for urgency rule evaluation."""
    type: str  # due_date_within_days, keywords_any, document_type, has_due_date, field_equals, always
    value: Any
    field: str | None = None


class UrgencyRule(BaseModel):
    """An urgency calculation rule."""
    name: str
    description: str = ""
    urgency: str
    conditions: list[UrgencyCondition]


class UrgencyRulesConfig(BaseModel):
    """Urgency rules configuration."""
    levels: list[UrgencyLevel]
    rules: list[UrgencyRule]
    
    @classmethod
    def load(cls, config_dir: Path | None = None) -> "UrgencyRulesConfig":
        """Load urgency rules from configuration file."""
        if config_dir is None:
            config_dir = Path(os.environ.get("DEDOX_CONFIG_DIR", "/app/config"))
        
        path = config_dir / "urgency_rules.yaml"
        
        if path.exists():
            config = load_yaml_config(path)
            return cls(**config)
        
        return cls(levels=[], rules=[])
    
    def get_level(self, level_id: str) -> UrgencyLevel | None:
        """Get an urgency level by ID."""
        for level in self.levels:
            if level.id == level_id:
                return level
        return None


# --- Global Config Instance ---

_settings: Settings | None = None
_metadata_fields: MetadataFieldsConfig | None = None
_document_types: DocumentTypesConfig | None = None
_urgency_rules: UrgencyRulesConfig | None = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def get_metadata_fields() -> MetadataFieldsConfig:
    """Get the global metadata fields configuration."""
    global _metadata_fields
    if _metadata_fields is None:
        _metadata_fields = MetadataFieldsConfig.load()
    return _metadata_fields


def get_document_types() -> DocumentTypesConfig:
    """Get the global document types configuration."""
    global _document_types
    if _document_types is None:
        _document_types = DocumentTypesConfig.load()
    return _document_types


def get_urgency_rules() -> UrgencyRulesConfig:
    """Get the global urgency rules configuration."""
    global _urgency_rules
    if _urgency_rules is None:
        _urgency_rules = UrgencyRulesConfig.load()
    return _urgency_rules


def reload_config(config_dir: Path | None = None) -> None:
    """Reload all configuration from files."""
    global _settings, _metadata_fields, _document_types, _urgency_rules
    _settings = Settings.load(config_dir)
    _metadata_fields = MetadataFieldsConfig.load(config_dir)
    _document_types = DocumentTypesConfig.load(config_dir)
    _urgency_rules = UrgencyRulesConfig.load(config_dir)
