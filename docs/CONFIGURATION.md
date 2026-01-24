# DeDox Configuration Guide

This document describes all configuration options for DeDox.

## Configuration Files

Configuration is loaded from YAML files in the `config/` directory:

| File | Purpose |
|------|---------|
| `settings.yaml` | Main application settings |
| `metadata_fields.yaml` | LLM extraction field definitions |
| `document_types.yaml` | Document type classifications |
| `urgency_rules.yaml` | Document urgency calculation rules |

## Environment Variables

Environment variables override YAML configuration using the `${VAR_NAME:default}` syntax.

### DeDox Core Settings

```bash
# JWT secret for authentication (generate a secure random string)
DEDOX_JWT_SECRET=your-secure-secret-here

# DeDox admin user (auto-created on first startup if no users exist)
# Password must be at least 8 characters
DEDOX_ADMIN_EMAIL=admin@example.com
DEDOX_ADMIN_PASSWORD=changeme123

# Custom config directory (default: ./config)
DEDOX_CONFIG_DIR=/path/to/config
```

### Paperless-ngx Integration

```bash
# Paperless-ngx URL
DEDOX_PAPERLESS_URL=http://paperless:8000

# Authentication (Option 1: Direct API token)
DEDOX_PAPERLESS_TOKEN=your-paperless-api-token

# Authentication (Option 2: Admin credentials for auto-generation)
# Leave DEDOX_PAPERLESS_TOKEN empty to use these instead
PAPERLESS_ADMIN_USER=admin
PAPERLESS_ADMIN_PASSWORD=your-admin-password

# Webhook signature secret
DEDOX_WEBHOOK_SECRET=your-webhook-secret
```

### Open WebUI Integration

```bash
# Authentication (Option 1: Direct API key)
DEDOX_OPENWEBUI_API_KEY=your-api-key

# Authentication (Option 2: Admin credentials for auto-generation)
# Leave DEDOX_OPENWEBUI_API_KEY empty to use these instead
OPENWEBUI_ADMIN_EMAIL=admin@example.com
OPENWEBUI_ADMIN_PASSWORD=your-admin-password

# Open WebUI secret key (for bundled Open WebUI)
OPENWEBUI_SECRET_KEY=your-secret-key

# Open WebUI frontend port
OPENWEBUI_PORT=3000
```

### Paperless-ngx (Bundled)

These are only needed if using the bundled Paperless-ngx in docker-compose:

```bash
# Database password for Paperless PostgreSQL
POSTGRES_PASSWORD=paperless

# Paperless secret key
PAPERLESS_SECRET_KEY=your-paperless-secret
```

## Settings Reference

### Server Settings

```yaml
server:
  host: "0.0.0.0"           # Bind address
  port: 8000                # Port number
  workers: 1                # Number of Uvicorn workers
  debug: false              # Debug mode
  cors_origins:             # Allowed CORS origins
    - "http://localhost:3000"
    - "http://localhost:8080"
```

### Storage Settings

```yaml
storage:
  base_path: "./data"       # Base directory for all storage
  originals_dir: "uploads"  # Original uploaded files
  processed_dir: "processed" # Processed files (PDFs)
  vectors_dir: "vectors"    # Vector database (if using file-based)
  max_file_size_mb: 50      # Maximum upload size in MB
```

### Database Settings

```yaml
database:
  path: "./data/dedox.db"   # SQLite database path
  wal_mode: true            # Enable WAL mode for better concurrency
```

### Authentication Settings

```yaml
auth:
  jwt_secret: ""            # JWT signing secret (use env var)
  jwt_algorithm: "HS256"    # JWT algorithm
  token_expire_hours: 24    # Token expiration time
  allow_registration: true  # Allow new user registration
```

### OCR Settings

```yaml
ocr:
  languages:                # Tesseract language codes
    - "deu"                 # German
    - "eng"                 # English
  tesseract_path: "tesseract" # Path to Tesseract binary
  dpi: 300                  # Resolution for OCR
  min_confidence: 0.6       # Minimum confidence threshold
```

### LLM Settings

```yaml
llm:
  ollama_url: "http://ollama:11434"  # Ollama API URL
  model: "qwen2.5:14b"      # Model name
  timeout_seconds: 120      # Request timeout
  max_retries: 3            # Retry count on failure
  temperature: 0.1          # Model temperature (lower = more deterministic)
```

### Paperless-ngx Settings

```yaml
paperless:
  base_url: "http://paperless:8000"  # Paperless-ngx URL

  # Authentication - either set api_token directly or use admin credentials for auto-generation
  api_token: ""             # API token (use env var DEDOX_PAPERLESS_TOKEN)
  admin_user: "admin"       # Admin username for auto token generation
  admin_password: ""        # Admin password (use env var PAPERLESS_ADMIN_PASSWORD)
  auto_generate_token: true # Auto-generate token using admin credentials if api_token is empty

  verify_ssl: false         # Verify SSL certificates
  timeout_seconds: 30       # API request timeout

  # Tags for document processing state
  processing_tag: "dedox:processing"    # Tag during processing
  enhanced_tag: "dedox:enhanced"        # Tag after successful enhancement
  error_tag: "dedox:error"              # Tag on processing error
  review_tag: "dedox:needs-review"      # Tag for low-confidence extractions
  duplicate_tag: "dedox:potential-duplicate"  # Tag for potential duplicates
  reprocess_tag: "dedox:reprocess"      # Add this tag to trigger reprocessing

  default_correspondent: ""  # Default correspondent name

  webhook:
    enabled: true           # Enable webhook receiver
    secret: ""              # HMAC secret for verification (use env var DEDOX_WEBHOOK_SECRET)
    auto_create_custom_fields: true  # Auto-create Paperless custom fields
    auto_setup_workflow: true        # Auto-setup Paperless workflows on startup
```

### Open WebUI Settings

```yaml
openwebui:
  enabled: true             # Enable Open WebUI document sync
  base_url: "http://open-webui:8080"  # Open WebUI API URL (internal)
  frontend_port: 3000       # Frontend port for external access

  # Authentication - either set api_key directly or use admin credentials for auto-generation
  api_key: ""               # API key (use env var DEDOX_OPENWEBUI_API_KEY)
  admin_email: "admin@example.com"     # Admin email for auto API key generation
  admin_password: ""        # Admin password (use env var OPENWEBUI_ADMIN_PASSWORD)
  auto_generate_api_key: true  # Auto-generate API key using admin credentials

  # Knowledge base configuration
  knowledge_base_id: ""     # Leave empty for auto-creation (or use env var DEDOX_OPENWEBUI_KB_ID)
  auto_create_knowledge_base: true  # Auto-create knowledge base if it doesn't exist (recommended)

  timeout_seconds: 300      # API request timeout (increased for file processing)
  wait_for_processing: false # Wait for file processing before adding to knowledge base
  max_processing_wait: 300  # Max wait time for file processing (seconds)
```

### Processing Settings

```yaml
processing:
  max_file_size_mb: 50      # Maximum file size
  allowed_formats:          # Allowed MIME types
    - "image/jpeg"
    - "image/png"
    - "image/tiff"
    - "application/pdf"
  languages:                # Document languages
    - "de"
    - "en"
```

## Metadata Fields Configuration

The `metadata_fields.yaml` file defines fields extracted by the LLM:

```yaml
fields:
  - name: document_type
    type: enum
    description: "Classification of the document"
    values:
      - invoice
      - receipt
      - contract
      - letter
      - official_notice
      - bank_statement
      - insurance_document
      - medical_record
      - tax_document
      - warranty
      - manual
      - certificate
      - id_document
      - other
    prompt: "Classify this document into one of the following types..."

  - name: sender
    type: string
    description: "Organization or person who sent/issued the document"
    prompt: "Extract the sender or issuer of this document..."

  - name: document_date
    type: date
    description: "Date the document was created"
    prompt: "Extract the date when this document was created..."

  # ... more fields
```

### Available Field Types

| Type | Description | Example |
|------|-------------|---------|
| `string` | Free text | Sender name |
| `date` | Date value (YYYY-MM-DD) | Document date |
| `number` | Numeric value | Total amount |
| `enum` | Fixed set of values | Document type |
| `boolean` | True/false | Action required |
| `array` | List of strings | Keywords |

## Document Types Configuration

The `document_types.yaml` file defines document classifications:

```yaml
types:
  - id: invoice
    name: "Invoice"
    description: "Bill for goods or services"
    keywords:
      - "invoice"
      - "rechnung"
      - "bill"
    default_urgency: medium

  - id: contract
    name: "Contract"
    description: "Legal agreement"
    keywords:
      - "contract"
      - "vertrag"
      - "agreement"
    default_urgency: high

  # ... more types
```

## Urgency Rules Configuration

The `urgency_rules.yaml` file defines priority calculation:

```yaml
rules:
  # Due date proximity
  - condition:
      field: due_date
      operator: within_days
      value: 3
    urgency: critical

  - condition:
      field: due_date
      operator: within_days
      value: 7
    urgency: high

  # Document type
  - condition:
      field: document_type
      operator: equals
      value: invoice
    urgency: medium

  # Keywords
  - condition:
      field: keywords
      operator: contains
      value: "mahnung"
    urgency: high
```

### Urgency Levels

| Level | Priority | Description |
|-------|----------|-------------|
| `critical` | 1 | Immediate attention required |
| `high` | 2 | Action needed soon |
| `medium` | 3 | Standard priority |
| `low` | 4 | No rush |

## Docker Configuration

### Full Stack (docker-compose.yml)

Includes DeDox, Paperless-ngx, Ollama, Open WebUI, PostgreSQL, and Redis.

Key environment variables:
```yaml
services:
  dedox:
    environment:
      - DEDOX_CONFIG_DIR=/app/config
      - DEDOX_JWT_SECRET=${DEDOX_JWT_SECRET}
      # Paperless auto-authentication
      - DEDOX_PAPERLESS_URL=http://paperless:8000
      - PAPERLESS_ADMIN_USER=${PAPERLESS_ADMIN_USER:-admin}
      - PAPERLESS_ADMIN_PASSWORD=${PAPERLESS_ADMIN_PASSWORD:-admin}
      # Open WebUI auto-authentication
      - OPENWEBUI_ADMIN_EMAIL=${OPENWEBUI_ADMIN_EMAIL:-admin@example.com}
      - OPENWEBUI_ADMIN_PASSWORD=${OPENWEBUI_ADMIN_PASSWORD:-admin}
```

### Minimal Stack (docker-compose.minimal.yml)

DeDox, Ollama, and Open WebUI only. Requires external Paperless-ngx.

Key environment variables:
```yaml
services:
  dedox:
    environment:
      # For external Paperless, either provide token directly:
      - DEDOX_PAPERLESS_URL=http://your-paperless:8000
      - DEDOX_PAPERLESS_TOKEN=${DEDOX_PAPERLESS_TOKEN}
      # Or provide admin credentials for auto-generation:
      - PAPERLESS_ADMIN_USER=${PAPERLESS_ADMIN_USER}
      - PAPERLESS_ADMIN_PASSWORD=${PAPERLESS_ADMIN_PASSWORD}
```

### Zero-Configuration Startup

Both docker-compose files are configured for zero-manual-setup:

1. **DeDox Admin User**: On first startup (when no users exist), DeDox creates an admin user using `DEDOX_ADMIN_EMAIL` and `DEDOX_ADMIN_PASSWORD`. If no password is set, a random one is generated and logged.

2. **Paperless-ngx**: If no `DEDOX_PAPERLESS_TOKEN` is provided, DeDox automatically generates an API token using `PAPERLESS_ADMIN_USER` and `PAPERLESS_ADMIN_PASSWORD`

3. **Open WebUI Admin User**: On first startup, Open WebUI automatically creates an admin user using `OPENWEBUI_ADMIN_EMAIL` and `OPENWEBUI_ADMIN_PASSWORD` (passed as `WEBUI_ADMIN_EMAIL` and `WEBUI_ADMIN_PASSWORD` to the container)

4. **Open WebUI API Key**: If no `DEDOX_OPENWEBUI_API_KEY` is provided, DeDox automatically generates one by logging in with `OPENWEBUI_ADMIN_EMAIL` and `OPENWEBUI_ADMIN_PASSWORD`

5. **Knowledge Base**: If no `DEDOX_OPENWEBUI_KB_ID` is provided, DeDox automatically creates a "DeDox Documents" knowledge base in Open WebUI (if `auto_create_knowledge_base: true`)

6. **Workflows**: On startup, DeDox auto-creates the necessary Paperless-ngx workflows for document processing (if `auto_setup_workflow: true`)

## Example Configurations

### Development

```yaml
server:
  debug: true
  cors_origins:
    - "*"

auth:
  allow_registration: true

llm:
  model: "qwen2.5:7b"  # Smaller model for faster dev
```

### Production

```yaml
server:
  debug: false
  workers: 4
  cors_origins:
    - "https://your-domain.com"

auth:
  allow_registration: false
  token_expire_hours: 8

paperless:
  webhook:
    secret: "your-secure-webhook-secret"
```
