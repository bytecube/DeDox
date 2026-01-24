"""Configuration routes for runtime config management."""

import logging
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from dedox.api.deps import CurrentUser, AdminUser
from dedox.core.config import get_settings, get_metadata_fields, get_document_types, get_urgency_rules
from dedox.models.extraction_field import (
    TestExtractionRequest,
    TestExtractionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class MetadataFieldConfig(BaseModel):
    """Metadata field configuration."""
    name: str
    type: str
    description: str
    prompt_template: str
    enabled: bool = True


class DocumentTypeConfig(BaseModel):
    """Document type configuration."""
    id: str
    name: str
    description: str
    keywords: list[str]
    default_tags: list[str]


@router.get("/metadata-fields")
async def get_metadata_fields_config(current_user: CurrentUser):
    """Get configured metadata extraction fields."""
    fields = get_metadata_fields()
    return {"fields": fields}


@router.get("/document-types")
async def get_document_types_config(current_user: CurrentUser):
    """Get configured document types."""
    types = get_document_types()
    return {"document_types": types}


@router.get("/urgency-rules")
async def get_urgency_rules_config(current_user: CurrentUser):
    """Get urgency calculation rules."""
    rules = get_urgency_rules()
    return {"rules": rules}


@router.get("/settings")
async def get_public_settings(current_user: CurrentUser):
    """Get public/non-sensitive settings."""
    settings = get_settings()
    
    return {
        "ocr": {
            "languages": settings.ocr.languages,
            "min_confidence": settings.ocr.min_confidence,
        },
        "llm": {
            "model": settings.llm.model,
        },
        "storage": {
            "max_file_size_mb": settings.storage.max_file_size_mb,
        },
    }


@router.get("/settings/full")
async def get_full_settings(admin: AdminUser):
    """Get full settings (admin only, excludes secrets)."""
    settings = get_settings()
    
    return {
        "server": {
            "host": settings.server.host,
            "port": settings.server.port,
            "debug": settings.server.debug,
            "cors_origins": settings.server.cors_origins,
        },
        "storage": {
            "base_path": settings.storage.base_path,
            "originals_dir": settings.storage.originals_dir,
            "processed_dir": settings.storage.processed_dir,
            "max_file_size_mb": settings.storage.max_file_size_mb,
        },
        "ocr": {
            "languages": settings.ocr.languages,
            "tesseract_path": settings.ocr.tesseract_path,
            "dpi": settings.ocr.dpi,
            "min_confidence": settings.ocr.min_confidence,
        },
        "llm": {
            "ollama_url": settings.llm.ollama_url,
            "model": settings.llm.model,
            "timeout_seconds": settings.llm.timeout_seconds,
            "max_retries": settings.llm.max_retries,
        },
        "paperless": {
            "url": settings.paperless.url,
            "processing_tag": settings.paperless.processing_tag,
            "default_correspondent": settings.paperless.default_correspondent,
        },
        "auth": {
            "token_expire_hours": settings.auth.token_expire_hours,
            "allow_registration": settings.auth.allow_registration,
        },
    }


@router.put("/settings")
async def update_settings(updates: dict[str, Any], admin: AdminUser):
    """Update runtime settings (admin only).
    
    Note: Some settings require a restart to take effect.
    """
    # For now, we store runtime settings in the database
    from dedox.db import get_database
    
    db = await get_database()
    
    for key, value in updates.items():
        await db.execute("""
            INSERT OR REPLACE INTO settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
        """, (key, yaml.dump(value)))
    
    return {
        "message": "Settings updated",
        "updated_keys": list(updates.keys()),
        "note": "Some settings may require a restart",
    }


@router.get("/status")
async def get_system_status(current_user: CurrentUser):
    """Get system status information."""
    settings = get_settings()
    
    status_info = {
        "services": {},
        "storage": {},
    }
    
    # Check storage
    import os
    base_path = settings.storage.base_path
    if os.path.exists(base_path):
        total, used, free = os.statvfs(base_path)[:3]
        # Calculate actual values
        block_size = os.statvfs(base_path).f_frsize
        status_info["storage"] = {
            "base_path": base_path,
            "free_gb": round((free * block_size) / (1024**3), 2),
            "used_gb": round(((total - free) * block_size) / (1024**3), 2),
            "total_gb": round((total * block_size) / (1024**3), 2),
        }
    
    # Check services
    import httpx
    from dedox.services.paperless_service import PaperlessService

    # Paperless
    try:
        # Use PaperlessService.get_token() which includes dynamically obtained tokens
        api_token = PaperlessService.get_token() or settings.paperless.api_token
        if not api_token:
            status_info["services"]["paperless"] = {
                "status": "error",
                "url": settings.paperless.url,
                "error": "No API token configured",
            }
        else:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Use /api/tags/ endpoint as /api/ redirects to schema docs
                response = await client.get(
                    f"{settings.paperless.url}/api/tags/",
                    headers={"Authorization": f"Token {api_token}"},
                )
                status_info["services"]["paperless"] = {
                    "status": "online" if response.status_code == 200 else "error",
                    "url": settings.paperless.url,
                }
    except Exception as e:
        status_info["services"]["paperless"] = {"status": "offline", "error": str(e)}
    
    # Ollama
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.llm.ollama_url}/api/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                status_info["services"]["ollama"] = {
                    "status": "online",
                    "url": settings.llm.ollama_url,
                    "models_available": model_names,
                    "configured_model": settings.llm.model,
                    "model_loaded": any(settings.llm.model in m for m in model_names),
                }
            else:
                status_info["services"]["ollama"] = {"status": "error"}
    except Exception as e:
        status_info["services"]["ollama"] = {"status": "offline", "error": str(e)}
    
    # Tesseract
    import shutil
    tesseract_path = shutil.which("tesseract") or settings.ocr.tesseract_path
    if tesseract_path:
        import subprocess
        try:
            result = subprocess.run(
                [tesseract_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version = result.stdout.split("\n")[0] if result.stdout else "unknown"
            status_info["services"]["tesseract"] = {
                "status": "available",
                "version": version,
                "path": tesseract_path,
            }
        except Exception as e:
            status_info["services"]["tesseract"] = {"status": "error", "error": str(e)}
    else:
        status_info["services"]["tesseract"] = {"status": "not_found"}
    
    return status_info


@router.post("/test-paperless")
async def test_paperless_connection(admin: AdminUser):
    """Test connection to Paperless-ngx."""
    settings = get_settings()

    import httpx
    from dedox.services.paperless_service import PaperlessService

    # Use PaperlessService.get_token() which includes dynamically obtained tokens
    api_token = PaperlessService.get_token() or settings.paperless.api_token

    if not api_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No Paperless API token configured",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Test API (use /api/tags/ as /api/ redirects to schema docs)
            response = await client.get(
                f"{settings.paperless.url}/api/tags/",
                headers={"Authorization": f"Token {api_token}"},
            )

            if response.status_code == 200:
                # Get some stats
                stats_response = await client.get(
                    f"{settings.paperless.url}/api/statistics/",
                    headers={"Authorization": f"Token {api_token}"},
                )

                stats = stats_response.json() if stats_response.status_code == 200 else {}

                return {
                    "status": "connected",
                    "url": settings.paperless.url,
                    "statistics": stats,
                }
            else:
                return {
                    "status": "error",
                    "code": response.status_code,
                    "message": response.text,
                }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to Paperless: {e}",
        )


@router.post("/test-ollama")
async def test_ollama_connection(admin: AdminUser):
    """Test connection to Ollama."""
    settings = get_settings()
    
    import httpx
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Check if model is available
            response = await client.get(f"{settings.llm.ollama_url}/api/tags")
            
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                
                return {
                    "status": "connected",
                    "url": settings.llm.ollama_url,
                    "available_models": model_names,
                    "configured_model": settings.llm.model,
                    "model_ready": any(settings.llm.model in m for m in model_names),
                }
            else:
                return {
                    "status": "error",
                    "code": response.status_code,
                }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to Ollama: {e}",
        )


@router.post("/extraction-fields/test")
async def test_extraction(
    request: TestExtractionRequest,
    current_user: CurrentUser,
):
    """Test an extraction prompt against sample text.

    This allows users to test their prompts before saving fields.
    Uses the same Chat API and system prompt as the main extraction pipeline.
    """
    import httpx
    import json
    import re

    settings = get_settings()

    # System prompt for extraction (same as main pipeline)
    system_prompt = """You are a document metadata extraction assistant. Your task is to extract structured information from OCR text of scanned documents.

Rules:
1. Extract ONLY information explicitly stated in the document
2. Do not infer or guess values that aren't clearly present
3. For dates, convert to YYYY-MM-DD format regardless of input format
4. For monetary amounts, extract only the numeric value without currency symbols
5. If a field's value cannot be determined with confidence, use null
6. For enum fields, choose the closest matching option or use null if none fit
7. Respond with valid JSON only - no explanations or commentary

Title/Subject format:
- Create searchable, descriptive titles combining: date (YYYY-MM), sender, document type, and amount if applicable
- Examples: "2024-01 Telekom Invoice 89.99€", "2024-03 Allianz Insurance Policy", "2024-02 Finanzamt Tax Notice"
- Keep titles concise (max 80 chars) but informative for searching
- Use the document language for the title

Common document patterns:
- Letterheads typically contain sender information at the top
- "Betreff:", "Subject:", "Re:" indicate the document subject
- "Fällig am:", "Due date:", "Zahlbar bis:" indicate payment deadlines
- Look for totals at the bottom of invoices ("Summe", "Total", "Gesamtbetrag")
"""

    # Build JSON schema for single field
    field_schema: dict = {"description": request.prompt}
    if request.field_type == "enum" and request.enum_values:
        field_schema["oneOf"] = [
            {"type": "string", "enum": request.enum_values},
            {"type": "null"}
        ]
    elif request.field_type == "boolean":
        field_schema["type"] = "boolean"
    elif request.field_type == "decimal":
        field_schema["type"] = ["number", "null"]
    elif request.field_type == "date":
        field_schema["type"] = ["string", "null"]
        field_schema["description"] += ". Format: YYYY-MM-DD"
    else:
        field_schema["type"] = ["string", "null"]

    json_schema = {
        "type": "object",
        "properties": {
            "value": field_schema
        },
        "required": ["value"] if request.field_type == "boolean" else [],
        "additionalProperties": False
    }

    user_prompt = f"""Extract the following field from this document:

Field: {request.prompt}

---
{request.sample_text[:4000]}
---"""

    try:
        async with httpx.AsyncClient(timeout=settings.llm.timeout_seconds) as client:
            response = await client.post(
                f"{settings.llm.base_url}/api/chat",
                json={
                    "model": settings.llm.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "stream": False,
                    "format": json_schema,
                    "options": {
                        "temperature": settings.llm.temperature,
                    }
                }
            )

            if response.status_code != 200:
                return TestExtractionResponse(
                    success=False,
                    error=f"LLM API error: {response.status_code}",
                )

            result = response.json()
            raw_response = result.get("message", {}).get("content", "").strip()

            # Parse JSON response
            try:
                parsed = json.loads(raw_response)
                extracted_value = parsed.get("value")
            except json.JSONDecodeError:
                # Fallback: try to use raw response
                extracted_value = raw_response

            # Clean and validate the response
            confidence = 0.0

            # Handle null and legacy strings
            if extracted_value is None:
                confidence = 0.0
            elif isinstance(extracted_value, str) and extracted_value.upper() in ["UNKNOWN", "NONE", "N/A", "NOT FOUND", ""]:
                extracted_value = None
                confidence = 0.0
            else:
                # Estimate confidence based on type
                if request.field_type == "enum" and request.enum_values:
                    if isinstance(extracted_value, str):
                        value_lower = extracted_value.lower()
                        matched = False
                        for allowed in request.enum_values:
                            if allowed.lower() == value_lower:
                                extracted_value = allowed
                                confidence = 0.9
                                matched = True
                                break
                        if not matched:
                            confidence = 0.3
                    else:
                        confidence = 0.3
                elif request.field_type == "date":
                    if isinstance(extracted_value, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', extracted_value):
                        confidence = 0.85
                    else:
                        confidence = 0.4
                elif request.field_type == "decimal":
                    if isinstance(extracted_value, (int, float)):
                        confidence = 0.8
                    elif isinstance(extracted_value, str):
                        try:
                            extracted_value = float(extracted_value.replace(",", "."))
                            confidence = 0.8
                        except ValueError:
                            confidence = 0.3
                    else:
                        confidence = 0.3
                elif request.field_type == "boolean":
                    if isinstance(extracted_value, bool):
                        confidence = 0.85
                    elif isinstance(extracted_value, str) and extracted_value.lower() in ["true", "false", "yes", "no", "1", "0"]:
                        extracted_value = extracted_value.lower() in ["true", "yes", "1"]
                        confidence = 0.85
                    else:
                        confidence = 0.4
                else:
                    # String/text
                    if isinstance(extracted_value, str) and len(extracted_value) > 3:
                        confidence = 0.75
                    else:
                        confidence = 0.5

            return TestExtractionResponse(
                extracted_value=str(extracted_value) if extracted_value is not None else None,
                confidence=confidence,
                raw_response=raw_response,
                success=True,
            )

    except httpx.TimeoutException:
        return TestExtractionResponse(
            success=False,
            error="LLM request timed out",
        )
    except httpx.ConnectError:
        return TestExtractionResponse(
            success=False,
            error=f"Cannot connect to LLM at {settings.llm.base_url}",
        )
    except Exception as e:
        logger.exception("Test extraction failed")
        return TestExtractionResponse(
            success=False,
            error=str(e),
        )
