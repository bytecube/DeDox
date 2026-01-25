"""
DeDox - FastAPI Application.

Main entry point for the document processing API.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from dedox.core.config import get_settings, reload_config
from dedox.ui import STATIC_DIR
from dedox.core.exceptions import (
    DedoxError,
    AuthenticationError,
    PaperlessError,
    LLMError,
    OCRError,
)
from dedox.db import init_database

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup
    logger.info("Starting DeDox...")
    
    # Load configuration
    reload_config()
    settings = get_settings()
    
    logger.info(f"Server: {settings.server.host}:{settings.server.port}")
    logger.info(f"Debug mode: {settings.server.debug}")
    
    # Initialize database
    await init_database()
    logger.info("Database initialized")

    # Initialize Paperless connection (auto-fetch token if needed)
    from dedox.services.paperless_service import init_paperless
    paperless_ok = await init_paperless()
    if paperless_ok:
        logger.info("Paperless-ngx integration initialized")

        # Ensure custom fields exist in Paperless if configured
        if settings.paperless.webhook.auto_create_custom_fields:
            try:
                from dedox.services.paperless_webhook_service import PaperlessWebhookService
                webhook_service = PaperlessWebhookService()
                field_ids = await webhook_service.ensure_custom_fields_exist()
                if field_ids:
                    logger.info(f"Ensured {len(field_ids)} custom fields exist in Paperless")
            except Exception as e:
                logger.warning(f"Could not ensure custom fields: {e}")

        # Auto-setup Paperless workflows if configured
        if settings.paperless.webhook.auto_setup_workflow:
            try:
                from dedox.services.paperless_setup_service import PaperlessSetupService
                setup_service = PaperlessSetupService()

                # Setup main document-added workflow
                result = await setup_service.setup_dedox_workflow()
                if result.get("success"):
                    if result.get("already_exists"):
                        logger.info("Paperless document-added workflow already configured")
                    else:
                        logger.info(f"Auto-created Paperless document-added workflow (ID: {result.get('workflow_id')})")
                else:
                    logger.warning(f"Could not auto-setup Paperless document-added workflow: {result.get('error')}")

                # Setup reprocess workflow (for tag-based reprocessing)
                reprocess_result = await setup_service.setup_reprocess_workflow()
                if reprocess_result.get("success"):
                    if reprocess_result.get("already_exists"):
                        logger.info("Paperless reprocess workflow already configured")
                    else:
                        logger.info(f"Auto-created Paperless reprocess workflow (ID: {reprocess_result.get('workflow_id')})")
                        logger.info(f"Use tag '{settings.paperless.reprocess_tag}' to trigger document reprocessing")
                else:
                    logger.warning(f"Could not auto-setup Paperless reprocess workflow: {reprocess_result.get('error')}")

                # Setup Open WebUI sync workflow (for document synchronization)
                if settings.openwebui.enabled:
                    sync_result = await setup_service.setup_openwebui_sync_workflow()
                    if sync_result.get("success"):
                        if sync_result.get("already_exists"):
                            logger.info("Paperless Open WebUI sync workflow already configured")
                        else:
                            logger.info(f"Auto-created Paperless Open WebUI sync workflow (ID: {sync_result.get('workflow_id')})")
                            logger.info("All document updates will be synced to Open WebUI")
                    else:
                        logger.warning(f"Could not auto-setup Open WebUI sync workflow: {sync_result.get('error')}")
                else:
                    logger.info("Open WebUI sync is disabled, skipping workflow setup")

            except Exception as e:
                logger.warning(f"Could not auto-setup Paperless workflows: {e}")

        # Log webhook status
        if settings.paperless.webhook.enabled:
            logger.info("Paperless webhook endpoints enabled:")
            logger.info("  - Document added: /api/webhooks/paperless/document-added")
            logger.info("  - Document updated (reprocess): /api/webhooks/paperless/document-updated")
            if settings.openwebui.enabled:
                logger.info("  - Document sync (Open WebUI): /api/webhooks/paperless/document-sync")
    else:
        logger.warning("Paperless-ngx integration not available")

    # Register pipeline processors
    from dedox.pipeline.processors import register_all_processors
    register_all_processors()
    logger.info("Pipeline processors registered")
    
    yield
    
    # Shutdown
    logger.info("Shutting down DeDox...")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    
    app = FastAPI(
        title="DeDox",
        description="Self-hosted document archival service with OCR, LLM metadata extraction, and Paperless-ngx integration",
        version="1.0.0",
        docs_url="/docs" if settings.server.debug else None,
        redoc_url="/redoc" if settings.server.debug else None,
        lifespan=lifespan,
    )
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Exception handlers
    @app.exception_handler(DedoxError)
    async def dedox_error_handler(request: Request, exc: DedoxError):
        return JSONResponse(
            status_code=500,
            content={"error": exc.__class__.__name__, "message": str(exc)},
        )
    
    @app.exception_handler(AuthenticationError)
    async def auth_error_handler(request: Request, exc: AuthenticationError):
        return JSONResponse(
            status_code=401,
            content={"error": "AuthenticationError", "message": str(exc)},
        )
    
    @app.exception_handler(PaperlessError)
    async def paperless_error_handler(request: Request, exc: PaperlessError):
        return JSONResponse(
            status_code=502,
            content={"error": "PaperlessError", "message": str(exc)},
        )
    
    @app.exception_handler(LLMError)
    async def llm_error_handler(request: Request, exc: LLMError):
        return JSONResponse(
            status_code=503,
            content={"error": "LLMError", "message": str(exc)},
        )
    
    @app.exception_handler(OCRError)
    async def ocr_error_handler(request: Request, exc: OCRError):
        return JSONResponse(
            status_code=500,
            content={"error": "OCRError", "message": str(exc)},
        )
    
    # Register routers
    from dedox.api.routes import (
        documents_router,
        jobs_router,
        search_router,
        auth_router,
        config_router,
        health_router,
        webhooks_router,
        admin_router,
    )
    from dedox.ui.routes import router as ui_router

    # API routes
    app.include_router(health_router, tags=["Health"])
    app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
    app.include_router(documents_router, prefix="/api/documents", tags=["Documents"])
    app.include_router(jobs_router, prefix="/api/jobs", tags=["Jobs"])
    app.include_router(search_router, prefix="/api/search", tags=["Search"])
    app.include_router(config_router, prefix="/api/config", tags=["Configuration"])
    app.include_router(webhooks_router, prefix="/api/webhooks", tags=["Webhooks"])
    app.include_router(admin_router, prefix="/api/admin", tags=["Admin"])
    
    # Static files for UI
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    
    # UI routes (must be last to catch all other paths)
    app.include_router(ui_router, tags=["UI"])
    
    return app


# Create the application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    
    settings = get_settings()
    uvicorn.run(
        "dedox.api.app:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.server.debug,
    )
