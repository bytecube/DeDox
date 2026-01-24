# Fix Metadata Tags & Remove Direct Upload Functionality

**Date**: 2026-01-22
**Status**: Implemented

## Problem Statement

1. **Boolean metadata fields not sent as tags**: Fields like `tax_relevant` and `action_required` are configured to map to Paperless tags (via `paperless_mapping.type: "tag"`), but the `update_document_metadata()` method only handles `custom_field` mappings. These boolean flags are extracted but never applied as actual tags.

2. **Dual processing paths**: The finalizer has two code paths - one for webhook-originated documents and one for direct uploads. The upload path only appends metadata as text notes, not as actual custom fields.

3. **Unused upload functionality**: Direct uploads to DeDox are no longer needed since all documents come from Paperless-ngx webhooks.

## Solution

### Part 1: Add Tag Handling to Webhook Service

**File**: `dedox/services/paperless_webhook_service.py`

Modify `update_document_metadata()` to handle `tag` type mappings:

```python
async def update_document_metadata(
    self,
    paperless_id: int,
    metadata: dict[str, Any],
    title: str | None = None,
    correspondent_id: int | None = None,
    document_type_id: int | None = None,
) -> bool:
    # ... existing code ...

    metadata_fields = get_metadata_fields()
    custom_fields = []
    tags_to_add = []  # NEW: Track tags to add

    for field_name, value in metadata.items():
        field_config = metadata_fields.get_field(field_name)

        if not field_config or not field_config.paperless_mapping:
            continue

        mapping = field_config.paperless_mapping

        if mapping.type == "custom_field":
            # ... existing custom field handling ...

        # NEW: Handle tag mappings
        elif mapping.type == "tag":
            if mapping.apply_if_true and value is True:
                tag_name = mapping.tag_name or field_name
                tags_to_add.append(tag_name)

    # ... existing update logic ...

    # NEW: Apply tags after document update
    for tag_name in tags_to_add:
        await self.add_tag_to_document(paperless_id, tag_name)
```

### Part 2: Remove Direct Upload Functionality

#### Files to Modify

| File | Action | Details |
|------|--------|---------|
| `dedox/api/routes/documents.py` | Remove lines 63-159 | Delete `upload_document()` and `upload_batch()` endpoints |
| `dedox/services/document_service.py` | Remove lines 26-94 | Delete `create_document_and_job()` method |
| `dedox/pipeline/processors/finalizer.py` | Remove lines 182-275 | Delete `_update_paperless()` method (upload path) |
| `dedox/pipeline/processors/finalizer.py` | Simplify lines 61-70 | Remove source branching, always use webhook path |
| `dedox/core/config.py` | Remove line 178 | Delete `skip_dedox_originated` setting |
| `dedox/models/document.py` | Update | Change source default to `"paperless_webhook"` |
| `dedox/db/database.py` | Update | Change source column default |

#### Files to Review for Test Updates

| File | Action |
|------|--------|
| `tests/test_api.py` | Remove upload tests (lines 117-174) |
| `tests/test_document_service.py` | Remove `TestCreateDocumentAndJob` class |

## Implementation Order

1. **Add tag handling** to `PaperlessWebhookService.update_document_metadata()`
2. **Remove upload endpoints** from `documents.py`
3. **Remove upload service method** from `document_service.py`
4. **Simplify finalizer** - remove upload path and source branching
5. **Update config** - remove `skip_dedox_originated`
6. **Update models and schema** - change source defaults
7. **Clean up imports** - remove unused `File`, `Form`, `UploadFile`
8. **Update tests** - remove upload-specific tests

## Affected Metadata Fields

After this fix, these fields will be properly handled:

| Field | Type | Mapping | Current Status | After Fix |
|-------|------|---------|----------------|-----------|
| `tax_relevant` | boolean | tag: "tax-relevant" | Not applied | Applied as tag |
| `action_required` | boolean | tag: "action-required" | Not applied | Applied as tag |
| All custom fields | various | custom_field | Working via webhook | No change |

## Verification

After implementation:
1. Process a document with `tax_relevant: true` → should have "tax-relevant" tag in Paperless
2. Process a document with `action_required: true` → should have "action-required" tag in Paperless
3. Upload endpoints should return 404
4. All existing webhook processing should continue working
