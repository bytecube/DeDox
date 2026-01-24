# DeDox

> Self-hosted, privacy-first document ingestion and processing service

## Project Overview

DeDox (**de**+**dox** = "detoxify your documents") is a document processing pipeline that:
- Receives documents from Paperless-ngx via webhooks
- Extracts text via OCR (Tesseract)
- Extracts metadata via local LLM (Ollama)
- Updates Paperless-ngx with enriched metadata
- Syncs documents to Open WebUI for RAG-powered search

## Tech Stack

- **Backend**: Python 3.10+, FastAPI, Uvicorn
- **Database**: SQLite with aiosqlite (async)
- **OCR**: Tesseract (German/English)
- **LLM**: Ollama (Qwen 2.5 14B default)
- **Document Archive**: Paperless-ngx
- **RAG Interface**: Open WebUI
- **Authentication**: JWT tokens + API keys

## Directory Structure

```
dedox/
├── api/                    # FastAPI routes and app
│   ├── app.py             # Application factory & lifespan
│   ├── deps.py            # Dependency injection
│   └── routes/            # Route handlers
├── pipeline/              # Document processing pipeline
│   ├── orchestrator.py    # Pipeline execution engine
│   ├── base.py           # BaseProcessor interface
│   └── processors/        # Processing stages
│       ├── image_processor.py
│       ├── ocr_processor.py
│       ├── llm_extractor.py
│       ├── sender_matcher.py
│       ├── paperless_archiver.py
│       └── finalizer.py
├── services/              # Business logic
│   ├── document_service.py
│   ├── paperless_service.py
│   ├── paperless_webhook_service.py
│   ├── paperless_setup_service.py
│   └── openwebui_sync_service.py
├── db/                    # Database layer
│   ├── database.py
│   └── repositories/
├── models/                # Pydantic models
├── core/                  # Config & exceptions
└── ui/                    # Web dashboard
```

## Configuration Files

Located in `config/`:
- `settings.yaml` - Main application settings
- `metadata_fields.yaml` - LLM extraction field definitions
- `document_types.yaml` - Document type classifications
- `urgency_rules.yaml` - Urgency calculation rules

## Development Commands

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn dedox.api.app:app --reload --port 8000

# Run tests
pytest tests/ -v

# Run type checking
mypy dedox/ --ignore-missing-imports

# Run with Docker
docker-compose up -d
```

## Key Architectural Decisions

1. **Webhook-first Architecture**: Documents flow from Paperless-ngx via webhooks, not direct uploads
2. **Local LLM**: All AI processing happens locally via Ollama for privacy
3. **Pipeline Pattern**: Processing stages are modular and can be skipped/customized
4. **Async Throughout**: All I/O operations use async/await for performance
5. **Paperless as Source of Truth**: DeDox enriches metadata, Paperless stores documents

## Processing Pipeline

1. **IMAGE_PROCESSING** - Edge detection, perspective correction
2. **OCR** - Tesseract text extraction
3. **PAPERLESS_UPLOAD** - Upload to Paperless (if not from webhook)
4. **METADATA_EXTRACTION** - LLM-based field extraction
5. **SENDER_MATCHING** - Correspondent deduplication
6. **FINALIZATION** - Update Paperless metadata, add tags

## Environment Variables

Key variables (see `.env.example`):
- `DEDOX_JWT_SECRET` - JWT signing secret
- `DEDOX_PAPERLESS_URL` - Paperless-ngx URL
- `DEDOX_PAPERLESS_TOKEN` - Paperless API token
- `DEDOX_WEBHOOK_SECRET` - Webhook HMAC secret
- `DEDOX_OPENWEBUI_API_KEY` - Open WebUI API key

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_pipeline.py -v

# Run with coverage
pytest tests/ --cov=dedox --cov-report=html
```

## API Documentation

When running locally:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Common Tasks

### Adding a New Extraction Field

1. Add field definition to `config/metadata_fields.yaml`
2. Map to Paperless custom field in `paperless_webhook_service.py`
3. Update `ExtractedMetadata` model if needed

### Adding a New Pipeline Processor

1. Create class extending `BaseProcessor` in `pipeline/processors/`
2. Implement `stage`, `can_process()`, and `process()` methods
3. Register in `pipeline/processors/__init__.py`

### Debugging Pipeline Issues

1. Check job logs in `/api/jobs/{job_id}/logs`
2. Enable debug mode in `settings.yaml`
3. View processing logs in `processing_logs` table
