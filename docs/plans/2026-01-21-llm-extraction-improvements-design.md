# LLM Extraction Improvements Design

## Overview

Improve document metadata extraction by implementing schema-constrained structured output with a dedicated system prompt. This addresses accuracy, consistency, missing fields, and performance issues.

## Problem Statement

Current implementation:
- Uses Ollama's `/api/generate` with `"format": "json"` (simple JSON mode)
- Builds a JSON schema but doesn't pass it to Ollama
- No system prompt - all instructions mixed in user message
- Uses "UNKNOWN" sentinel strings instead of null
- Verbose prompts repeated for each extraction

## Solution: Schema-Constrained Structured Output

### 1. System Prompt

Add a dedicated system prompt establishing extraction rules:

```
You are a document metadata extraction assistant. Your task is to extract structured information from OCR text of scanned documents.

Rules:
1. Extract ONLY information explicitly stated in the document
2. Do not infer or guess values that aren't clearly present
3. For dates, convert to YYYY-MM-DD format regardless of input format
4. For monetary amounts, extract only the numeric value without currency symbols
5. If a field's value cannot be determined with confidence, use null
6. For enum fields, choose the closest matching option or use null if none fit
7. Respond with valid JSON only - no explanations or commentary

Common document patterns:
- Letterheads typically contain sender information at the top
- "Betreff:", "Subject:", "Re:" indicate the document subject
- "Fällig am:", "Due date:", "Zahlbar bis:" indicate payment deadlines
- Look for totals at the bottom of invoices ("Summe", "Total", "Gesamtbetrag")
```

### 2. API Change: Generate → Chat

Switch from `/api/generate` to `/api/chat`:

```python
response = await client.post(
    f"{settings.llm.base_url}/api/chat",
    json={
        "model": settings.llm.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "stream": False,
        "format": json_schema,  # Actual schema object, not "json" string
        "options": {
            "temperature": settings.llm.temperature,
        }
    }
)
```

Key change: Pass actual JSON schema to `format` parameter for grammar-constrained generation.

### 3. Enhanced JSON Schema

Improve schema for better extraction:

- Use nullable types: `"type": ["string", "null"]` for optional fields
- Merge extraction hints into field descriptions
- Add `additionalProperties: false` to prevent hallucinated fields
- Remove "UNKNOWN" from patterns - use `null` instead
- Boolean fields always required (default false, not null)

Example:
```python
{
    "type": "object",
    "properties": {
        "sender": {
            "type": ["string", "null"],
            "description": "Company or person who sent/issued the document. Look for letterheads, 'From:' fields, signatures."
        },
        "document_date": {
            "type": ["string", "null"],
            "description": "Document creation/issue date in YYYY-MM-DD format",
            "pattern": "^\\d{4}-\\d{2}-\\d{2}$"
        },
        "total_amount": {
            "type": ["number", "null"],
            "description": "Total monetary amount as a number. Look for 'Total:', 'Summe:', 'Gesamtbetrag:'"
        },
        "action_required": {
            "type": "boolean",
            "description": "True if document requires action: payment due, response needed, signature required"
        }
    },
    "required": ["document_type", "language", "action_required"],
    "additionalProperties": false
}
```

### 4. Simplified User Prompt

With system prompt and schema handling instructions, user prompt becomes minimal:

```
Extract metadata from this document:

---
{ocr_text}
---
```

## Implementation Changes

### File: dedox/pipeline/processors/llm_extractor.py

1. **Add system prompt constant** (~line 28)
   - New `EXTRACTION_SYSTEM_PROMPT` constant

2. **Modify `_build_json_schema()`** (lines 234-267)
   - Add nullable types for optional fields
   - Merge extraction hints from field prompts into descriptions
   - Add `additionalProperties: false`
   - Remove "UNKNOWN" from enum values and patterns

3. **Replace `_call_ollama_json()` with `_call_ollama_chat()`** (lines 295-350)
   - Switch endpoint from `/api/generate` to `/api/chat`
   - Add system message to messages array
   - Pass schema object to `format` parameter
   - Update response parsing: `message.content` instead of `response`

4. **Simplify `_build_structured_prompt()`** (lines 269-293)
   - Remove field descriptions (now in schema)
   - Just wrap OCR text with minimal context

5. **Update `_clean_extracted_value()`** (lines 352-409)
   - Handle `null` instead of "UNKNOWN" strings
   - Simplify validation since schema enforces types

### Files Unchanged

- `config/metadata_fields.yaml` - field definitions stay the same
- Database schema - no structural changes
- API endpoints - extraction interface unchanged

## Expected Benefits

1. **Accuracy**: System prompt provides clear extraction guidelines
2. **Consistency**: Grammar-constrained output guarantees valid JSON matching schema
3. **Missing fields**: Better descriptions help model find values; null handling is cleaner
4. **Performance**: Fewer retries due to malformed responses; schema constraints reduce validation overhead

## References

- [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [Ollama Chat API](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [Qwen 2.5 Function Calling](https://qwen.readthedocs.io/en/latest/framework/function_call.html)
