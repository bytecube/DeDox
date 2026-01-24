"""API routes module."""

from dedox.api.routes.health import router as health_router
from dedox.api.routes.auth import router as auth_router
from dedox.api.routes.documents import router as documents_router
from dedox.api.routes.jobs import router as jobs_router
from dedox.api.routes.search import router as search_router
from dedox.api.routes.config import router as config_router
from dedox.api.routes.webhooks import router as webhooks_router
from dedox.api.routes.admin import router as admin_router

__all__ = [
    "health_router",
    "auth_router",
    "documents_router",
    "jobs_router",
    "search_router",
    "config_router",
    "webhooks_router",
    "admin_router",
]
