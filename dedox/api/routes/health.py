"""Health check routes."""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

from dedox.core.config import get_settings

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Basic health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": _utcnow().isoformat(),
        "service": "dedox",
        "version": "1.0.0",
    }


@router.get("/health/detailed")
async def detailed_health_check() -> dict[str, Any]:
    """Detailed health check with service status."""
    settings = get_settings()
    status = {
        "status": "healthy",
        "timestamp": _utcnow().isoformat(),
        "services": {},
    }
    
    # Check database
    try:
        from dedox.db import get_database
        db = await get_database()
        await db.fetch_one("SELECT 1")
        status["services"]["database"] = {"status": "healthy"}
    except Exception as e:
        status["services"]["database"] = {"status": "unhealthy", "error": str(e)}
        status["status"] = "degraded"
    
    # Check Paperless-ngx
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{settings.paperless.url}/api/",
                headers={"Authorization": f"Token {settings.paperless.api_token}"},
            )
            if response.status_code == 200:
                status["services"]["paperless"] = {"status": "healthy"}
            else:
                status["services"]["paperless"] = {
                    "status": "unhealthy",
                    "error": f"HTTP {response.status_code}",
                }
                status["status"] = "degraded"
    except Exception as e:
        status["services"]["paperless"] = {"status": "unhealthy", "error": str(e)}
        status["status"] = "degraded"
    
    # Check Ollama
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.llm.ollama_url}/api/tags")
            if response.status_code == 200:
                status["services"]["ollama"] = {"status": "healthy"}
            else:
                status["services"]["ollama"] = {
                    "status": "unhealthy",
                    "error": f"HTTP {response.status_code}",
                }
                status["status"] = "degraded"
    except Exception as e:
        status["services"]["ollama"] = {"status": "unhealthy", "error": str(e)}
        status["status"] = "degraded"
    
    return status
