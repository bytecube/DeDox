# DeDox Feature Requests & Issues

This document contains structured feature requests formatted for GitHub Issues.
Copy each section when creating issues in the repository.

---

## Table of Contents

1. [Good First Issues](#good-first-issues)
2. [Pipeline & Processing](#pipeline--processing)
3. [API Enhancements](#api-enhancements)
4. [Integrations](#integrations)
5. [User Experience](#user-experience)
6. [Performance & Scalability](#performance--scalability)
7. [Security](#security)
8. [Documentation](#documentation)
9. [Testing & Quality](#testing--quality)

---

## Good First Issues

### Issue: Add request timeout configuration to all HTTP clients

**Labels:** `good first issue`, `enhancement`, `code-quality`

**Description:**
HTTP client timeouts are currently hardcoded across multiple services. These should be unified and made configurable.

**Current Behavior:**
- Timeouts scattered: 30s in some places, 10s in others, 300s for Open WebUI
- No central configuration

**Desired Behavior:**
- Single `http_client.py` utility with configurable defaults
- Timeout settings in `settings.yaml`

**Files to modify:**
- Create `dedox/utils/http_client.py`
- Update `dedox/services/paperless_service.py`
- Update `dedox/services/openwebui_sync_service.py`
- Update `dedox/pipeline/llm_extractor.py`

**Acceptance Criteria:**
- [ ] Centralized HTTP client factory function
- [ ] Configurable timeout in settings.yaml
- [ ] All services use the shared client
- [ ] Tests pass

---

### Issue: Add structured logging with correlation IDs

**Labels:** `good first issue`, `enhancement`, `observability`

**Description:**
Add correlation IDs to log messages to track document processing across services.

**Current Behavior:**
- Basic logging without request/job correlation
- Difficult to trace a single document's processing journey

**Desired Behavior:**
- Each document processing job gets a unique correlation ID
- All log messages include the correlation ID
- Logs can be filtered by correlation ID

**Acceptance Criteria:**
- [ ] Correlation ID generated at job creation
- [ ] ID passed through all pipeline stages
- [ ] Structured JSON logging option
- [ ] Documentation updated

---

### Issue: Add health check endpoint for external services

**Labels:** `good first issue`, `enhancement`, `operations`

**Description:**
Extend `/health` endpoint to report connectivity status of Paperless-ngx, Ollama, and Open WebUI.

**Current Behavior:**
- Basic health check only verifies DeDox is running

**Desired Behavior:**
```json
{
  "status": "healthy",
  "services": {
    "paperless": {"status": "connected", "latency_ms": 45},
    "ollama": {"status": "connected", "model": "qwen2.5:14b"},
    "openwebui": {"status": "connected"}
  }
}
```

**Acceptance Criteria:**
- [ ] Health check tests all external service connections
- [ ] Returns degraded status if optional services unavailable
- [ ] Returns unhealthy if required services unavailable
- [ ] Response includes latency measurements

---

### Issue: Add document processing statistics endpoint

**Labels:** `good first issue`, `enhancement`, `api`

**Description:**
Create an API endpoint that returns processing statistics.

**Endpoint:** `GET /api/stats`

**Response:**
```json
{
  "total_documents": 1234,
  "documents_by_status": {
    "completed": 1200,
    "processing": 5,
    "failed": 29
  },
  "documents_by_type": {
    "invoice": 450,
    "contract": 200,
    "letter": 300
  },
  "avg_processing_time_seconds": 12.5,
  "avg_confidence_score": 0.87
}
```

**Acceptance Criteria:**
- [ ] Endpoint returns accurate statistics
- [ ] Statistics cached for performance (5 min TTL)
- [ ] Filterable by date range
- [ ] Documentation added

---

## Pipeline & Processing

### Issue: Add duplicate document detection

**Labels:** `enhancement`, `feature`, `pipeline`

**Description:**
Detect and flag potentially duplicate documents before processing to avoid redundant work and storage.

**Proposed Solution:**
1. Calculate file hash (SHA-256) during upload
2. Store hash in database
3. Check for existing hash before processing
4. If duplicate found:
   - Tag document with `dedox:potential-duplicate`
   - Link to original document
   - Skip processing (configurable)

**Configuration:**
```yaml
processing:
  duplicate_detection:
    enabled: true
    action: "tag"  # tag, skip, or ask
    similarity_threshold: 0.95  # for fuzzy matching
```

**Acceptance Criteria:**
- [ ] Exact duplicate detection via hash
- [ ] Optional fuzzy duplicate detection via OCR text similarity
- [ ] Configurable action (tag, skip, prompt review)
- [ ] API endpoint to manually mark as not-duplicate
- [ ] Tests with duplicate and similar documents

---

### Issue: Add document validation processor

**Labels:** `enhancement`, `feature`, `pipeline`

**Description:**
Add a pipeline stage that validates extracted metadata against configurable business rules.

**Use Cases:**
- Invoice amount must be positive
- Due date must be after document date
- Contract must have both parties identified
- Tax documents must have tax year

**Configuration:**
```yaml
# config/validation_rules.yaml
rules:
  - document_type: invoice
    validations:
      - field: total_amount
        rule: greater_than
        value: 0
        severity: error
      - field: due_date
        rule: after_field
        reference: document_date
        severity: warning
```

**Acceptance Criteria:**
- [ ] Configurable validation rules per document type
- [ ] Support for: required, regex, range, date comparisons, cross-field
- [ ] Validation failures flagged for review
- [ ] Severity levels: error (blocks), warning (flags)
- [ ] Validation report in metadata

---

### Issue: Support multiple LLM providers

**Labels:** `enhancement`, `feature`, `llm`

**Description:**
Allow switching between different LLM providers beyond Ollama.

**Providers to support:**
- Ollama (current)
- OpenAI API
- Anthropic Claude API
- Azure OpenAI
- Local llama.cpp

**Configuration:**
```yaml
llm:
  provider: "openai"  # ollama, openai, anthropic, azure, llamacpp
  api_key: "${LLM_API_KEY}"
  model: "gpt-4o"
  # Provider-specific options
  openai:
    organization: ""
  anthropic:
    version: "2024-01-01"
```

**Acceptance Criteria:**
- [ ] Abstract LLM interface
- [ ] Provider implementations
- [ ] Graceful fallback if provider unavailable
- [ ] Cost tracking per provider
- [ ] Documentation for each provider

---

### Issue: Add OCR quality assessment

**Labels:** `enhancement`, `feature`, `pipeline`

**Description:**
Assess OCR quality before metadata extraction and suggest improvements.

**Features:**
- Calculate average confidence score
- Detect common issues (skew, blur, low resolution)
- Suggest image preprocessing
- Flag for manual review if quality below threshold

**Output:**
```json
{
  "ocr_quality": {
    "overall_score": 0.72,
    "issues": ["low_contrast", "slight_skew"],
    "suggestions": ["increase_contrast", "deskew"],
    "confidence_by_region": {...}
  }
}
```

**Acceptance Criteria:**
- [ ] Quality score calculation
- [ ] Issue detection (skew, blur, contrast, resolution)
- [ ] Configurable quality threshold
- [ ] Auto-retry with preprocessing (optional)
- [ ] Quality metrics stored with document

---

### Issue: Support batch document processing

**Labels:** `enhancement`, `feature`, `api`

**Description:**
Allow uploading and processing multiple documents in a single request.

**Endpoint:** `POST /api/documents/batch`

**Features:**
- Upload multiple files (multipart)
- Process as single job with sub-tasks
- Progress tracking per document
- Bulk metadata application
- ZIP file upload support

**Acceptance Criteria:**
- [ ] Batch upload endpoint
- [ ] ZIP file extraction
- [ ] Progress tracking per document
- [ ] Batch job status endpoint
- [ ] Configurable batch size limit
- [ ] Error handling (partial failures)

---

### Issue: Add document reprocessing with modified prompts

**Labels:** `enhancement`, `feature`, `pipeline`

**Description:**
Allow reprocessing documents with custom or modified extraction prompts.

**Use Case:**
- Initial extraction missed important field
- User wants to try different prompt
- Testing prompt improvements

**Endpoint:** `POST /api/documents/{id}/reprocess`

**Request:**
```json
{
  "stages": ["llm_extraction"],
  "custom_prompts": {
    "sender": "Extract the company name that issued this invoice..."
  },
  "preserve_existing": true
}
```

**Acceptance Criteria:**
- [ ] Selective stage reprocessing
- [ ] Custom prompt override
- [ ] Option to preserve or replace existing metadata
- [ ] Comparison view (old vs new extraction)
- [ ] Prompt effectiveness tracking

---

## API Enhancements

### Issue: Add WebSocket support for real-time progress

**Labels:** `enhancement`, `feature`, `api`

**Description:**
Implement WebSocket endpoints for real-time job progress updates instead of polling.

**Endpoint:** `WS /api/ws/jobs/{job_id}`

**Events:**
```json
{"event": "stage_started", "stage": "ocr", "timestamp": "..."}
{"event": "stage_progress", "stage": "ocr", "progress": 50}
{"event": "stage_completed", "stage": "ocr", "duration_ms": 2500}
{"event": "job_completed", "status": "success", "document_id": "..."}
```

**Acceptance Criteria:**
- [ ] WebSocket endpoint for job progress
- [ ] Events for stage start/progress/complete
- [ ] Error event with details
- [ ] Connection management (heartbeat, reconnect)
- [ ] Fallback to polling for clients without WS

---

### Issue: Add document export functionality

**Labels:** `enhancement`, `feature`, `api`

**Description:**
Export documents with their metadata in various formats.

**Endpoint:** `GET /api/documents/export`

**Formats:**
- JSON (metadata only)
- CSV (tabular metadata)
- PDF (document with metadata cover page)
- ZIP (documents + metadata files)

**Query Parameters:**
- `format`: json, csv, pdf, zip
- `ids`: comma-separated document IDs (or all)
- `include_ocr_text`: boolean
- `date_from`, `date_to`: filter by date range

**Acceptance Criteria:**
- [ ] Export single document
- [ ] Bulk export with filters
- [ ] All format options
- [ ] Streaming for large exports
- [ ] Export job for very large datasets

---

### Issue: Add saved searches and filters

**Labels:** `enhancement`, `feature`, `api`

**Description:**
Allow users to save and reuse search queries and filter combinations.

**Endpoints:**
- `POST /api/saved-searches` - Create saved search
- `GET /api/saved-searches` - List saved searches
- `GET /api/saved-searches/{id}/execute` - Run saved search

**Model:**
```json
{
  "id": "uuid",
  "name": "Unpaid Invoices",
  "query": {
    "document_type": "invoice",
    "payment_status": "unpaid",
    "due_date_before": "today"
  },
  "notify_on_match": true
}
```

**Acceptance Criteria:**
- [ ] CRUD for saved searches
- [ ] Execute saved search
- [ ] Optional notification on new matches
- [ ] Share searches between users
- [ ] Pre-built system searches (needs review, high urgency, etc.)

---

### Issue: Add metadata revision history

**Labels:** `enhancement`, `feature`, `data-management`

**Description:**
Track all changes to document metadata with ability to view history and revert.

**Features:**
- Automatic versioning on metadata change
- View revision history
- Compare revisions
- Revert to previous version
- Track who made changes

**Endpoint:** `GET /api/documents/{id}/revisions`

**Response:**
```json
{
  "revisions": [
    {
      "revision": 3,
      "timestamp": "2024-01-15T10:30:00Z",
      "changed_by": "user@example.com",
      "changes": {
        "sender": {"old": "ACME Inc", "new": "ACME Corporation"}
      }
    }
  ]
}
```

**Acceptance Criteria:**
- [ ] Automatic revision creation on update
- [ ] Revision history endpoint
- [ ] Diff between revisions
- [ ] Revert endpoint
- [ ] Configurable retention (keep last N revisions)

---

### Issue: Add full-text search on OCR content

**Labels:** `enhancement`, `feature`, `search`

**Description:**
Enable searching within the OCR-extracted text content of documents.

**Features:**
- Full-text search index on ocr_text
- Highlight matching text in results
- Search within specific document types
- Combine with metadata filters

**Endpoint:** `GET /api/documents/search`

**Query:**
```json
{
  "text_query": "payment overdue",
  "document_type": "invoice",
  "highlight": true
}
```

**Technical Notes:**
- SQLite FTS5 for full-text indexing
- Consider PostgreSQL migration for production scale

**Acceptance Criteria:**
- [ ] Full-text index on OCR content
- [ ] Search endpoint with text query
- [ ] Result highlighting
- [ ] Combined text + metadata search
- [ ] Pagination for results

---

## Integrations

### Issue: Add email document ingestion

**Labels:** `enhancement`, `feature`, `integration`

**Description:**
Process documents received via email attachments.

**Features:**
- IMAP/POP3 mailbox monitoring
- Extract PDF/image attachments
- Use email metadata (sender, subject, date)
- Auto-categorize by sender domain
- Mark emails as processed

**Configuration:**
```yaml
email:
  enabled: true
  protocol: imap
  server: imap.example.com
  username: "${EMAIL_USER}"
  password: "${EMAIL_PASSWORD}"
  folder: "INBOX"
  poll_interval_seconds: 300
  mark_as_read: true
  move_to_folder: "Processed"
```

**Acceptance Criteria:**
- [ ] IMAP connection and monitoring
- [ ] Attachment extraction
- [ ] Email metadata mapping
- [ ] Duplicate email detection
- [ ] Error handling for malformed emails

---

### Issue: Add cloud storage sync (S3/GCS/Azure Blob)

**Labels:** `enhancement`, `feature`, `integration`

**Description:**
Sync documents from cloud storage providers.

**Providers:**
- AWS S3
- Google Cloud Storage
- Azure Blob Storage
- MinIO (S3-compatible)

**Features:**
- Watch bucket/container for new files
- Download and process new documents
- Optional upload of processed metadata
- Sync status tracking

**Configuration:**
```yaml
cloud_storage:
  provider: s3
  bucket: my-documents
  prefix: inbox/
  aws_access_key: "${AWS_ACCESS_KEY}"
  aws_secret_key: "${AWS_SECRET_KEY}"
  region: eu-west-1
  poll_interval_seconds: 60
```

**Acceptance Criteria:**
- [ ] S3 integration
- [ ] GCS integration
- [ ] Azure Blob integration
- [ ] New file detection
- [ ] Processed file tracking
- [ ] Error handling and retry

---

### Issue: Add Slack/Discord notifications

**Labels:** `enhancement`, `feature`, `integration`

**Description:**
Send notifications to Slack or Discord for document events.

**Events:**
- Document processing complete
- Document needs review
- High urgency document detected
- Processing error
- Daily summary

**Configuration:**
```yaml
notifications:
  slack:
    enabled: true
    webhook_url: "${SLACK_WEBHOOK_URL}"
    channel: "#documents"
    events:
      - needs_review
      - high_urgency
      - error
```

**Acceptance Criteria:**
- [ ] Slack webhook integration
- [ ] Discord webhook integration
- [ ] Configurable events
- [ ] Rich message formatting
- [ ] Rate limiting to prevent spam

---

### Issue: Two-way sync with Paperless-ngx

**Labels:** `enhancement`, `feature`, `integration`

**Description:**
Sync metadata changes made in Paperless-ngx back to DeDox.

**Current State:**
- DeDox → Paperless only
- Manual edits in Paperless not reflected in DeDox

**Proposed:**
- Periodic sync of Paperless metadata
- Webhook on Paperless document update
- Conflict resolution strategy
- Audit trail of sync changes

**Acceptance Criteria:**
- [ ] Detect changes made in Paperless
- [ ] Update DeDox metadata from Paperless
- [ ] Conflict detection (both modified)
- [ ] Configurable conflict resolution
- [ ] Sync status tracking

---

### Issue: Add Open WebUI knowledge base management

**Labels:** `enhancement`, `feature`, `integration`

**Description:**
Manage Open WebUI knowledge bases from DeDox.

**Features:**
- List all knowledge bases
- Create/delete knowledge bases
- Move documents between KBs
- View KB statistics
- Manage KB permissions

**Endpoints:**
- `GET /api/openwebui/knowledge-bases`
- `POST /api/openwebui/knowledge-bases`
- `DELETE /api/openwebui/knowledge-bases/{id}`
- `POST /api/openwebui/knowledge-bases/{id}/documents`

**Acceptance Criteria:**
- [ ] KB listing endpoint
- [ ] KB creation/deletion
- [ ] Document assignment to KB
- [ ] Permission management
- [ ] Sync status per KB

---

## User Experience

### Issue: Add document review dashboard

**Labels:** `enhancement`, `feature`, `ui`

**Description:**
Create a dedicated interface for reviewing documents that need attention.

**Features:**
- Queue of documents needing review
- Side-by-side document view and metadata editor
- Quick actions (approve, reject, flag)
- Keyboard shortcuts for efficiency
- Bulk approval/rejection

**Review Reasons:**
- Low confidence extraction
- Missing required fields
- Potential duplicate
- Validation errors

**Acceptance Criteria:**
- [ ] Review queue endpoint
- [ ] Review UI page
- [ ] Document preview
- [ ] Inline metadata editing
- [ ] Keyboard navigation
- [ ] Review history

---

### Issue: Add document preview generation

**Labels:** `enhancement`, `feature`, `ui`

**Description:**
Generate and serve document previews/thumbnails.

**Features:**
- Thumbnail generation for list views
- Full preview for detail views
- PDF page previews
- Image optimization for web

**Endpoints:**
- `GET /api/documents/{id}/thumbnail`
- `GET /api/documents/{id}/preview`
- `GET /api/documents/{id}/preview/page/{page}`

**Acceptance Criteria:**
- [ ] Thumbnail generation on upload
- [ ] Preview endpoint with caching
- [ ] PDF multi-page preview
- [ ] Image format optimization
- [ ] Lazy loading support

---

### Issue: Add processing timeline visualization

**Labels:** `enhancement`, `feature`, `ui`

**Description:**
Show a visual timeline of document processing stages.

**Features:**
- Stage-by-stage breakdown
- Duration per stage
- Error points highlighted
- Retry indicators
- Expandable stage details

**API Response:**
```json
{
  "timeline": [
    {"stage": "upload", "started": "...", "completed": "...", "duration_ms": 150},
    {"stage": "ocr", "started": "...", "completed": "...", "duration_ms": 2500},
    {"stage": "llm_extraction", "started": "...", "completed": "...", "duration_ms": 8000, "retries": 1}
  ]
}
```

**Acceptance Criteria:**
- [ ] Timeline data in job response
- [ ] Visual timeline component
- [ ] Stage detail expansion
- [ ] Error visualization
- [ ] Performance comparison across documents

---

### Issue: Add bulk metadata editing

**Labels:** `enhancement`, `feature`, `ui`

**Description:**
Allow editing metadata for multiple documents at once.

**Use Cases:**
- Set same correspondent for multiple documents
- Bulk tag assignment
- Batch date correction
- Mass category change

**Endpoint:** `PATCH /api/documents/batch`

**Request:**
```json
{
  "document_ids": ["id1", "id2", "id3"],
  "updates": {
    "correspondent": "ACME Inc",
    "tags": {"add": ["reviewed"], "remove": ["pending"]}
  }
}
```

**Acceptance Criteria:**
- [ ] Batch update endpoint
- [ ] Partial updates (only specified fields)
- [ ] Add/remove for array fields
- [ ] Validation across all documents
- [ ] Rollback on partial failure

---

## Performance & Scalability

### Issue: Add Redis-based task queue

**Labels:** `enhancement`, `feature`, `infrastructure`

**Description:**
Replace the in-memory task queue with Redis for reliability and scalability.

**Benefits:**
- Job persistence across restarts
- Distributed worker support
- Priority queues
- Dead letter queue for failed jobs
- Job scheduling

**Configuration:**
```yaml
queue:
  backend: redis  # memory, redis
  redis_url: "${REDIS_URL:redis://localhost:6379}"
  default_queue: dedox
  worker_count: 4
```

**Acceptance Criteria:**
- [ ] Redis queue implementation
- [ ] Job persistence
- [ ] Multiple worker support
- [ ] Priority queue support
- [ ] Failed job handling
- [ ] Backward compatible with memory queue

---

### Issue: Add response caching layer

**Labels:** `enhancement`, `feature`, `performance`

**Description:**
Cache frequently accessed data to reduce database load.

**Cache Targets:**
- Document metadata (short TTL)
- Statistics/counts (medium TTL)
- Configuration data (long TTL)
- LLM responses (content-based)

**Configuration:**
```yaml
cache:
  backend: redis  # memory, redis
  ttl_seconds:
    documents: 60
    statistics: 300
    config: 3600
```

**Acceptance Criteria:**
- [ ] Cache abstraction layer
- [ ] Memory and Redis backends
- [ ] Configurable TTL per cache type
- [ ] Cache invalidation on update
- [ ] Cache hit/miss metrics

---

### Issue: Add LLM response caching

**Labels:** `enhancement`, `feature`, `performance`

**Description:**
Cache LLM extraction results to avoid redundant API calls.

**Strategy:**
- Hash OCR text + prompt
- Cache extraction results
- Configurable TTL
- Invalidate on prompt change

**Benefits:**
- Faster reprocessing
- Reduced LLM costs
- Faster testing/development

**Acceptance Criteria:**
- [ ] Cache key generation (text hash + prompt hash)
- [ ] Cache storage (file or Redis)
- [ ] TTL configuration
- [ ] Cache hit logging
- [ ] Manual cache clear option

---

### Issue: Add database query optimization

**Labels:** `enhancement`, `performance`, `database`

**Description:**
Optimize database queries and add proper indexing.

**Improvements:**
- Add indexes on frequently queried fields
- Implement query pagination
- Add query result limits
- Optimize JOIN queries
- Consider read replicas for scale

**Indexes to add:**
- `documents.paperless_id`
- `documents.status`
- `documents.created_at`
- `documents.document_type` (metadata)
- `jobs.status`
- `jobs.created_at`

**Acceptance Criteria:**
- [ ] Index migration script
- [ ] Query pagination for list endpoints
- [ ] Query explain analysis
- [ ] Performance benchmarks before/after

---

## Security

### Issue: Add API rate limiting

**Labels:** `enhancement`, `security`

**Description:**
Implement rate limiting to prevent API abuse.

**Configuration:**
```yaml
security:
  rate_limiting:
    enabled: true
    requests_per_minute: 60
    burst: 10
    by: ip  # ip, user, api_key
```

**Acceptance Criteria:**
- [ ] Rate limit middleware
- [ ] Configurable limits
- [ ] Rate limit headers in response
- [ ] Different limits per endpoint
- [ ] Whitelist for internal services

---

### Issue: Add file upload security scanning

**Labels:** `enhancement`, `security`

**Description:**
Validate and scan uploaded files for security.

**Checks:**
- File type validation (magic bytes, not just extension)
- Maximum file size
- Malware scanning (ClamAV integration)
- Image bomb detection
- PDF bomb detection

**Configuration:**
```yaml
security:
  upload:
    max_size_mb: 50
    allowed_types:
      - application/pdf
      - image/jpeg
      - image/png
      - image/tiff
    malware_scan: true
    clamav_socket: /var/run/clamav/clamd.sock
```

**Acceptance Criteria:**
- [ ] Magic byte validation
- [ ] Size limit enforcement
- [ ] Optional ClamAV integration
- [ ] Decompression bomb protection
- [ ] Detailed rejection reasons

---

### Issue: Add document encryption at rest

**Labels:** `enhancement`, `security`

**Description:**
Encrypt sensitive documents stored on disk.

**Features:**
- AES-256 encryption
- Key management
- Selective encryption (by document type)
- Encrypted backup support

**Configuration:**
```yaml
security:
  encryption:
    enabled: true
    algorithm: aes-256-gcm
    key_source: env  # env, vault, kms
    key_env_var: DEDOX_ENCRYPTION_KEY
```

**Acceptance Criteria:**
- [ ] Encryption on write
- [ ] Decryption on read
- [ ] Key rotation support
- [ ] Vault/KMS integration option
- [ ] Migration tool for existing documents

---

### Issue: Add comprehensive audit logging

**Labels:** `enhancement`, `security`, `compliance`

**Description:**
Log all security-relevant actions for compliance and debugging.

**Events to log:**
- Authentication attempts
- Document access
- Metadata changes
- Configuration changes
- API key usage
- Admin actions

**Log Format:**
```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "event": "document.accessed",
  "user": "user@example.com",
  "document_id": "uuid",
  "ip_address": "192.168.1.100",
  "user_agent": "..."
}
```

**Acceptance Criteria:**
- [ ] Audit log middleware
- [ ] Configurable event types
- [ ] Structured JSON logging
- [ ] Log rotation
- [ ] Log export endpoint (admin only)

---

## Documentation

### Issue: Add API documentation with OpenAPI/Swagger

**Labels:** `documentation`, `good first issue`

**Description:**
Generate and serve interactive API documentation.

**Features:**
- OpenAPI 3.0 specification
- Swagger UI at `/docs`
- ReDoc at `/redoc`
- Example requests/responses
- Authentication documentation

**Acceptance Criteria:**
- [ ] OpenAPI spec generation from routes
- [ ] Swagger UI endpoint
- [ ] Request/response examples
- [ ] Authentication section
- [ ] Error response documentation

---

### Issue: Create video tutorials

**Labels:** `documentation`, `help wanted`

**Description:**
Create video tutorials for common use cases.

**Topics:**
1. Installation and first run
2. Configuring Paperless-ngx integration
3. Customizing metadata extraction
4. Setting up Open WebUI sync
5. Troubleshooting common issues

**Acceptance Criteria:**
- [ ] Script/outline for each video
- [ ] Recorded videos
- [ ] YouTube playlist
- [ ] Links in README

---

### Issue: Add troubleshooting guide

**Labels:** `documentation`

**Description:**
Create a comprehensive troubleshooting guide for common issues.

**Topics:**
- Connection issues (Paperless, Ollama, Open WebUI)
- OCR quality problems
- Extraction accuracy issues
- Performance problems
- Docker/container issues
- Authentication problems

**Format:**
```markdown
## Problem: OCR quality is poor

### Symptoms
- Low confidence scores
- Garbled text

### Causes
- Poor image quality
- Wrong language setting
- Skewed documents

### Solutions
1. Check image resolution (minimum 300 DPI)
2. Verify language settings in config
3. Enable image preprocessing
```

**Acceptance Criteria:**
- [ ] Common issues documented
- [ ] Clear symptom descriptions
- [ ] Step-by-step solutions
- [ ] Links to relevant config options

---

## Testing & Quality

### Issue: Add integration test suite

**Labels:** `testing`, `quality`

**Description:**
Create comprehensive integration tests for the full pipeline.

**Test Scenarios:**
- Full document processing flow
- Paperless-ngx webhook handling
- Open WebUI sync
- Error recovery
- Concurrent processing

**Requirements:**
- Docker-compose test environment
- Test fixtures (sample documents)
- Mocked external services option
- CI/CD integration

**Acceptance Criteria:**
- [ ] Integration test framework
- [ ] Test docker-compose
- [ ] Sample document fixtures
- [ ] CI workflow for integration tests
- [ ] Coverage reporting

---

### Issue: Add performance benchmarks

**Labels:** `testing`, `performance`

**Description:**
Create automated performance benchmarks.

**Metrics:**
- Documents per minute throughput
- Average processing time by stage
- Memory usage under load
- API response times
- Database query times

**Features:**
- Benchmark CLI command
- Historical tracking
- Regression detection
- CI integration

**Acceptance Criteria:**
- [ ] Benchmark test suite
- [ ] Metric collection
- [ ] Result storage/comparison
- [ ] Performance regression CI check
- [ ] Benchmark documentation

---

### Issue: Add extraction accuracy testing

**Labels:** `testing`, `quality`, `ml`

**Description:**
Test extraction accuracy against a labeled dataset.

**Features:**
- Ground truth dataset format
- Accuracy metrics (precision, recall, F1)
- Per-field metrics
- Regression tracking
- A/B testing for prompts

**Dataset Format:**
```json
{
  "document": "path/to/document.pdf",
  "expected": {
    "document_type": "invoice",
    "sender": "ACME Corporation",
    "total_amount": 1234.56
  }
}
```

**Acceptance Criteria:**
- [ ] Test dataset format specification
- [ ] Accuracy calculation
- [ ] Per-field metrics
- [ ] CI integration
- [ ] Accuracy trend reporting

---

## Infrastructure

### Issue: Add Kubernetes deployment manifests

**Labels:** `infrastructure`, `deployment`

**Description:**
Provide Kubernetes manifests for production deployment.

**Components:**
- Deployment for API server
- Deployment for workers
- ConfigMap for settings
- Secret for credentials
- Service for API
- Ingress for external access
- HPA for autoscaling

**Acceptance Criteria:**
- [ ] K8s deployment manifests
- [ ] Helm chart (optional)
- [ ] Documentation for K8s deployment
- [ ] Health/readiness probes
- [ ] Resource limits

---

### Issue: Add Prometheus metrics endpoint

**Labels:** `infrastructure`, `monitoring`

**Description:**
Expose Prometheus-compatible metrics for monitoring.

**Metrics:**
- `dedox_documents_total` (counter)
- `dedox_processing_duration_seconds` (histogram)
- `dedox_queue_size` (gauge)
- `dedox_extraction_confidence` (histogram)
- `dedox_errors_total` (counter by type)

**Endpoint:** `GET /metrics`

**Acceptance Criteria:**
- [ ] Prometheus client integration
- [ ] Core metrics defined
- [ ] `/metrics` endpoint
- [ ] Grafana dashboard template
- [ ] Documentation

---

### Issue: Add backup and restore functionality

**Labels:** `infrastructure`, `operations`

**Description:**
Implement backup and restore for DeDox data.

**Features:**
- Database backup
- Document files backup
- Configuration backup
- Scheduled backups
- Restore procedure

**Commands:**
```bash
dedox backup --output /path/to/backup.tar.gz
dedox restore --input /path/to/backup.tar.gz
```

**Acceptance Criteria:**
- [ ] Backup CLI command
- [ ] Restore CLI command
- [ ] Scheduled backup option
- [ ] Incremental backup support
- [ ] Documentation

---

## Labels Reference

Use these labels when creating issues:

| Label | Description |
|-------|-------------|
| `good first issue` | Good for newcomers |
| `help wanted` | Extra attention needed |
| `enhancement` | New feature or improvement |
| `bug` | Something isn't working |
| `documentation` | Documentation improvements |
| `security` | Security-related |
| `performance` | Performance improvements |
| `testing` | Testing improvements |
| `infrastructure` | Deployment/ops related |
| `api` | API changes |
| `pipeline` | Processing pipeline |
| `integration` | External integrations |
| `ui` | User interface |
| `code-quality` | Code cleanup/refactoring |

---

## Priority Guidelines

**P0 - Critical:** Security vulnerabilities, data loss risks
**P1 - High:** Core functionality, blocking issues
**P2 - Medium:** Important features, significant improvements
**P3 - Low:** Nice to have, minor improvements
