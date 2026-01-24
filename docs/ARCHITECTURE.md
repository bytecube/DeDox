# DeDox Architecture

This document describes the architecture of DeDox.

## System Overview

DeDox is a document processing service that captures, processes, extracts metadata, and archives documents to Paperless-ngx.

```
┌─────────────────────────────────────────────────────────────────┐
│                         DeDox Service                            │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   Web UI    │  │   REST API  │  │   Webhook Receiver      │  │
│  │  (FastAPI)  │  │  (FastAPI)  │  │   (Paperless Events)    │  │
│  └──────┬──────┘  └──────┬──────┘  └───────────┬─────────────┘  │
│         │                │                      │                 │
│  ┌──────┴────────────────┴──────────────────────┴─────────────┐ │
│  │                     Service Layer                           │ │
│  │  DocumentService | PaperlessService | PaperlessWebhook     │ │
│  └────────────────────────────┬───────────────────────────────┘ │
│                               │                                   │
│  ┌────────────────────────────┴───────────────────────────────┐ │
│  │                   Pipeline Orchestrator                     │ │
│  │  Ingest → Image → OCR → Upload → LLM → Embedding → Final   │ │
│  └────────────────────────────┬───────────────────────────────┘ │
│                               │                                   │
│  ┌────────────────────────────┴───────────────────────────────┐ │
│  │                      Data Layer                             │ │
│  │            SQLite Database | File Storage                   │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│   Ollama    │      │ Paperless   │      │  Tesseract  │
│    (LLM)    │      │    -ngx     │      │    (OCR)    │
└─────────────┘      └─────────────┘      └─────────────┘
```

## Layers

### API Layer

Located in `dedox/api/`, the API layer handles HTTP requests.

**Components:**
- `app.py`: FastAPI application factory and initialization
- `deps.py`: Dependency injection (authentication, database)
- `routes/`: API endpoint handlers

**Key Routes:**
| Route | Purpose |
|-------|---------|
| `/api/auth/*` | Authentication and user management |
| `/api/documents/*` | Document CRUD operations |
| `/api/jobs/*` | Job status and management |
| `/api/search/*` | Semantic and metadata search |
| `/api/config/*` | Configuration endpoints |
| `/api/webhooks/*` | External webhook receivers |
| `/health` | Health check endpoints |

### Service Layer

Located in `dedox/services/`, contains business logic.

**Services:**
- `DocumentService`: Document lifecycle management
- `PaperlessService`: Paperless-ngx API integration
- `PaperlessWebhookService`: Webhook event handling
- `PaperlessSetupService`: Automated Paperless configuration
- `JobWorker`: Background job processing

### Pipeline Layer

Located in `dedox/pipeline/`, handles document processing.

**Components:**
- `base.py`: Base processor interface and context
- `orchestrator.py`: Pipeline execution engine
- `registry.py`: Processor registration and management
- `processors/`: Individual pipeline stages

**Pipeline Stages:**

```
1. INGEST
   └─→ Store original file, generate document ID

2. IMAGE_PROCESSING
   └─→ Edge detection, perspective correction, enhancement

3. OCR
   └─→ Text extraction with Tesseract (German/English)

4. PAPERLESS_UPLOAD (for uploads only)
   └─→ Initial archival with "Processing..." tag

5. METADATA_EXTRACTION
   └─→ LLM-based field extraction (14 fields)

6. EMBEDDING_GENERATION
   └─→ Vector embeddings for semantic search

7. FINALIZATION
   └─→ Update Paperless metadata, cleanup
```

### Data Layer

**Database:** SQLite with async support (aiosqlite)

**Tables:**
- `users`: User accounts and authentication
- `api_keys`: API key management
- `documents`: Document records and metadata
- `jobs`: Processing job tracking
- `embeddings`: Vector embeddings for search
- `settings`: Runtime configuration

**Repositories:** (`dedox/db/repositories/`)
- `UserRepository`: User data access
- `DocumentRepository`: Document data access
- `JobRepository`: Job data access

## Processing Flows

### Upload Flow

```
User Upload
    │
    ▼
┌───────────────┐
│ DocumentService│
│  • Save file   │
│  • Create doc  │
│  • Create job  │
└───────┬───────┘
        │
        ▼
┌───────────────┐
│  JobWorker    │
│  • Pick job   │
│  • Run pipeline│
└───────┬───────┘
        │
        ▼
┌───────────────────────────────────────┐
│           Pipeline Stages             │
│ Ingest → Image → OCR → Upload → LLM  │
│ → Embedding → Finalization           │
└───────────────────────────────────────┘
        │
        ▼
┌───────────────┐
│  Paperless    │
│  (archived)   │
└───────────────┘
```

### Webhook Flow (Pull-based)

```
Paperless Document Added
    │
    ▼
┌───────────────────┐
│ Webhook Endpoint  │
│  • Verify signature│
│  • Parse payload  │
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│ WebhookService    │
│  • Download doc   │
│  • Create record  │
│  • Queue job      │
└────────┬──────────┘
         │
         ▼
┌───────────────────────────────────────┐
│           Pipeline Stages             │
│ Ingest → Image → OCR → LLM →         │
│ Embedding → Finalization (skip upload)│
└───────────────────────────────────────┘
         │
         ▼
┌───────────────────┐
│  Paperless        │
│  (metadata updated)│
└───────────────────┘
```

## External Integrations

### Paperless-ngx

- **Protocol**: REST API with token authentication
- **Operations**: Document upload, metadata update, tag management
- **Custom Fields**: Recipient, Due Date, Amount, Summary, etc.

### Ollama (LLM)

- **Protocol**: REST API (OpenAI-compatible)
- **Model**: Qwen 2.5 14B (configurable)
- **Usage**: Metadata extraction from OCR text

### Tesseract OCR

- **Languages**: German, English (configurable)
- **Output**: Text with confidence scores

## Configuration

Configuration is loaded from YAML files in `config/`:

- `settings.yaml`: Main application settings
- `metadata_fields.yaml`: LLM extraction prompts
- `document_types.yaml`: Document classification
- `urgency_rules.yaml`: Priority calculation

Environment variables can override settings. See `dedox/core/config.py` for details.

## Security

### Authentication

- **JWT Tokens**: For web UI and API access
- **API Keys**: For programmatic access
- **Cookie-based**: For web UI sessions

### Data Protection

- All data stored locally (no cloud dependencies)
- File storage with user isolation
- Secure token management

## Scalability Considerations

Current design is optimized for single-instance deployment. For scaling:

- **Job Queue**: Replace in-process queue with Redis/RabbitMQ
- **Database**: Migrate to PostgreSQL for concurrent access
- **Storage**: Use object storage (S3-compatible) for files
- **Workers**: Run multiple worker instances
