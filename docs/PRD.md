# DeDox - Product Requirements Document

**Version:** 1.0  
**Date:** January 17, 2026  
**Status:** Implementation In Progress

## 1. Overview

DeDox is a self-hosted document ingestion service that:
- Accepts documents via phone camera, uploads, or (future) email
- Performs edge detection, alignment, and OCR
- Uses local LLMs to extract configurable metadata
- Creates semantic embeddings for natural language search
- Archives to Paperless-ngx with full metadata

### Key Principles
- **100% Local/Offline**: No cloud dependencies
- **Privacy-First**: All processing on-premises
- **Extensible**: Configurable pipeline and metadata fields

## 2. Processing Pipeline (Option A)

```
┌──────────┐   ┌──────────┐   ┌─────────┐   ┌──────────┐   ┌──────────┐
│  Ingest  │──▶│ Process  │──▶│   OCR   │──▶│ Upload   │──▶│ Extract  │
│  (Store) │   │ (Align)  │   │         │   │ Paperless│   │ Metadata │
└──────────┘   └──────────┘   └─────────┘   └──────────┘   └──────────┘
     │                                            │              │
     ▼                                            ▼              ▼
 Return                                      Tag with       Update
 Job ID                                   "Processing..."   Metadata
```

### Pipeline Stages

1. **Ingest (Sync)**: Store original, return job_id immediately
2. **Image Processing (Async)**: Edge detection, perspective correction
3. **OCR (Async)**: Tesseract text extraction
4. **Paperless Upload (Async)**: Upload processed image with "Processing..." tag
5. **Metadata Extraction (Async)**: LLM-based extraction
6. **Embedding Generation (Async)**: Create vectors
7. **Finalize (Async)**: Update Paperless metadata, set review flags

## 3. Metadata Fields

| Field | Type | Paperless Mapping |
|-------|------|-------------------|
| document_type | enum | Document Type |
| document_date | date | Created Date |
| sender | string | Correspondent |
| recipient | string | Custom Field |
| due_date | date | Custom Field |
| total_amount | decimal | Custom Field |
| reference_number | string | Custom Field |
| urgency | enum | Tag |
| sentiment | enum | Custom Field |
| summary | text | Custom Field |

## 4. Languages

- German (deu)
- English (eng)

## 5. Technology Stack

- **API**: FastAPI
- **OCR**: Tesseract
- **LLM**: Ollama (local)
- **Embeddings**: sentence-transformers
- **Vector Store**: SQLite with vectors
- **Archive**: Paperless-ngx v2.20.4
