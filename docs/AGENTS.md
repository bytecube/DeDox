# AI Agents and LLM Integration

This document describes the AI/LLM components used in DeDox for intelligent document processing.

## Overview

DeDox uses local LLM models via Ollama to extract structured metadata from OCR text. This ensures:
- Complete privacy (no data leaves your network)
- Offline operation capability
- Customizable extraction via prompt engineering

## LLM Extractor

**File**: `dedox/pipeline/processors/llm_extractor.py`

The LLMExtractor is the core AI component that transforms raw OCR text into structured metadata.

### Extraction Strategy

The extractor uses a two-phase approach:

1. **Structured Batch Extraction** (Primary)
   - Sends all configured fields to the LLM in a single request
   - Uses JSON mode for structured output
   - More efficient for most documents

2. **Individual Field Extraction** (Fallback)
   - Falls back to extracting fields one-by-one if batch fails
   - More robust for complex or unusual documents
   - Slower but handles edge cases better

### Configurable Fields

Fields are defined in `config/metadata_fields.yaml`:

```yaml
fields:
  - name: document_type
    type: enum
    options: [invoice, letter, contract, receipt, statement, notification, form, report, other]
    prompt: "What type of document is this?"

  - name: sender
    type: string
    prompt: "Who is the sender/issuer of this document?"

  - name: document_date
    type: date
    prompt: "What is the document date?"
```

### Confidence Scoring

Each extracted field receives a confidence score (0.0 - 1.0) based on:
- Field type (enums get higher confidence)
- Value validation (dates, amounts)
- Text length and completeness

```python
# Confidence heuristics by type
- enum fields: 0.90 (constrained options)
- date fields: 0.85 (format validation)
- decimal fields: 0.85 (numeric validation)
- string fields: 0.60-0.75 (based on length)
```

### System Prompt

The extractor uses a detailed system prompt that includes:
- OCR error tolerance guidelines
- Language awareness (German/English)
- Document pattern recognition (sender priority, date formats)
- Summary and keyword extraction guidelines

See `EXTRACTION_SYSTEM_PROMPT` in `llm_extractor.py` for the full prompt.

## Sender Matcher

**File**: `dedox/pipeline/processors/sender_matcher.py`

The SenderMatcher agent deduplicates correspondents by matching extracted sender names against existing Paperless correspondents.

### Matching Logic

1. **Exact Match** - Check for exact name match (case-insensitive)
2. **LLM Fuzzy Match** - Use LLM to find semantic matches
   - "Deutsche Telekom AG" matches "Telekom Deutschland"
   - "Dr. Max Mustermann" matches "Max Mustermann"

### Correspondent Caching

To reduce API calls, correspondents are cached:
```python
# Cache TTL: 5 minutes
_correspondents_cache: list[dict] = []
_cache_timestamp: float = 0
CACHE_TTL = 300  # seconds
```

## Open WebUI Integration

**File**: `dedox/services/openwebui_sync_service.py`

Documents are synced to Open WebUI for RAG (Retrieval-Augmented Generation) capabilities.

### Sync Workflow

1. Document is processed by DeDox pipeline
2. On finalization, document is uploaded to Open WebUI knowledge base
3. Users can query documents via Open WebUI chat interface

### Knowledge Base Management

- Auto-creates knowledge base on first sync
- Manages file uploads with metadata
- Handles document updates and deletions

## Urgency Calculation

**File**: `config/urgency_rules.yaml`

Documents are assigned urgency levels based on configurable rules:

```yaml
rules:
  - name: due_date_critical
    condition: "days_until_due < 3"
    urgency: critical

  - name: due_date_high
    condition: "days_until_due < 7"
    urgency: high
```

### Urgency Levels

- **critical** - Requires immediate action (< 3 days)
- **high** - Requires action soon (< 7 days)
- **medium** - Normal priority
- **low** - No urgency

## Model Configuration

### Default Model

```yaml
llm:
  ollama_url: "http://ollama:11434"
  model: "qwen2.5:14b"
  timeout_seconds: 120
  temperature: 0.1  # Low temperature for consistent extraction
```

### Recommended Models

| Model | Size | Use Case |
|-------|------|----------|
| qwen2.5:14b | 14B | Best accuracy (default) |
| qwen2.5:7b | 7B | Faster, lower memory |
| llama3.2:3b | 3B | Minimal resources |

### Hardware Requirements

- **Minimum**: 8GB RAM (for 7B models)
- **Recommended**: 16GB+ RAM (for 14B models)
- **GPU**: Optional but significantly improves speed

## Prompt Engineering Tips

When customizing extraction:

1. **Be Specific** - "Extract the invoice number" > "Find numbers"
2. **Provide Context** - Include expected formats
3. **Handle Nulls** - Specify what to return if not found
4. **Language Aware** - Note if German patterns expected

### Example Custom Field

```yaml
- name: contract_end_date
  type: date
  prompt: |
    Find the contract end date or renewal date.
    Look for: "Vertragslaufzeit", "endet am", "valid until"
    Format: YYYY-MM-DD
    Return null if not found.
```

## Debugging AI Extraction

### Enable Debug Logging

```yaml
server:
  debug: true
```

### Check Extraction Results

```bash
# Get job details with extraction results
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/jobs/{job_id}
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| Empty extractions | OCR text too short | Check OCR quality |
| Wrong field types | Invalid format | Adjust prompt |
| Timeouts | Large documents | Increase timeout |
| Low confidence | Ambiguous content | Add more context to prompt |

## Performance Optimization

1. **Batch Processing** - Use structured extraction for efficiency
2. **Caching** - Correspondent cache reduces API calls
3. **Temperature** - Low temperature (0.1) for consistent results
4. **Timeouts** - Set appropriate timeouts for your hardware
