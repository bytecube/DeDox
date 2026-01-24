"""
Admin routes for system management and setup.

These endpoints handle administrative tasks like Paperless-ngx integration setup.
All endpoints require admin authentication.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dedox.api.deps import AdminUser
from dedox.core.config import get_settings
from dedox.services.paperless_setup_service import PaperlessSetupService

logger = logging.getLogger(__name__)

router = APIRouter()


class SetupPaperlessRequest(BaseModel):
    """Request body for setup-paperless endpoint."""
    force: bool = False
    webhook_url: str | None = None


class SetupPaperlessResponse(BaseModel):
    """Response for setup-paperless endpoint."""
    success: bool
    message: str
    workflow_id: int | None = None
    trigger_id: int | None = None
    action_id: int | None = None
    webhook_url: str | None = None
    already_exists: bool = False
    error: str | None = None


class PaperlessStatusResponse(BaseModel):
    """Response for Paperless integration status."""
    paperless_connected: bool
    paperless_url: str | None = None
    workflow_configured: bool
    workflow_id: int | None = None
    reprocess_workflow_configured: bool = False
    reprocess_workflow_id: int | None = None
    reprocess_tag: str | None = None
    dedox_webhook_url: str | None = None
    dedox_reprocess_webhook_url: str | None = None
    webhook_enabled: bool
    error: str | None = None


class SetupReprocessWorkflowRequest(BaseModel):
    """Request body for setup-reprocess-workflow endpoint."""
    force: bool = False


class SetupReprocessWorkflowResponse(BaseModel):
    """Response for setup-reprocess-workflow endpoint."""
    success: bool
    message: str
    workflow_id: int | None = None
    webhook_url: str | None = None
    reprocess_tag: str | None = None
    already_exists: bool = False
    error: str | None = None


@router.post(
    "/setup-paperless",
    response_model=SetupPaperlessResponse,
    summary="Setup Paperless-ngx workflow",
    description="Automatically create the webhook workflow in Paperless-ngx "
                "to send documents to DeDox for processing. Requires admin access."
)
async def setup_paperless(
    admin: AdminUser,
    request: SetupPaperlessRequest | None = None,
):
    """Setup the DeDox webhook workflow in Paperless-ngx.

    This creates:
    - A trigger for DOCUMENT_ADDED events (excluding dedox:* tagged docs)
    - A webhook action pointing to DeDox with document included
    - A workflow linking them together
    """
    settings = get_settings()

    if not settings.paperless.api_token:
        raise HTTPException(
            status_code=503,
            detail="Paperless-ngx is not configured (missing API token)"
        )

    request = request or SetupPaperlessRequest()
    service = PaperlessSetupService(dedox_webhook_url=request.webhook_url)

    result = await service.setup_dedox_workflow(force=request.force)

    return SetupPaperlessResponse(
        success=result.get("success", False),
        message=result.get("message", result.get("error", "Unknown error")),
        workflow_id=result.get("workflow_id"),
        trigger_id=result.get("trigger_id"),
        action_id=result.get("action_id"),
        webhook_url=result.get("webhook_url"),
        already_exists=result.get("already_exists", False),
        error=result.get("error"),
    )


@router.delete(
    "/setup-paperless",
    response_model=SetupPaperlessResponse,
    summary="Remove Paperless-ngx workflow",
    description="Remove the DeDox webhook workflow from Paperless-ngx. Requires admin access."
)
async def remove_paperless_workflow(admin: AdminUser):
    """Remove the DeDox webhook workflow from Paperless-ngx."""
    settings = get_settings()

    if not settings.paperless.api_token:
        raise HTTPException(
            status_code=503,
            detail="Paperless-ngx is not configured (missing API token)"
        )

    service = PaperlessSetupService()
    result = await service.remove_dedox_workflow()

    return SetupPaperlessResponse(
        success=result.get("success", False),
        message=result.get("message", result.get("error", "Unknown error")),
        error=result.get("error"),
    )


@router.get(
    "/paperless-status",
    response_model=PaperlessStatusResponse,
    summary="Get Paperless integration status",
    description="Check the current status of DeDox integration with Paperless-ngx. Requires admin access."
)
async def get_paperless_status(admin: AdminUser):
    """Get the current status of DeDox integration with Paperless-ngx."""
    settings = get_settings()

    if not settings.paperless.api_token:
        return PaperlessStatusResponse(
            paperless_connected=False,
            workflow_configured=False,
            reprocess_workflow_configured=False,
            webhook_enabled=settings.paperless.webhook.enabled,
            error="Paperless-ngx is not configured (missing API token)",
        )

    service = PaperlessSetupService()
    status = await service.get_status()

    return PaperlessStatusResponse(
        paperless_connected=status.get("paperless_connected", False),
        paperless_url=status.get("paperless_url"),
        workflow_configured=status.get("workflow_configured", False),
        workflow_id=status.get("workflow_id"),
        reprocess_workflow_configured=status.get("reprocess_workflow_configured", False),
        reprocess_workflow_id=status.get("reprocess_workflow_id"),
        reprocess_tag=status.get("reprocess_tag"),
        dedox_webhook_url=status.get("dedox_webhook_url"),
        dedox_reprocess_webhook_url=status.get("dedox_reprocess_webhook_url"),
        webhook_enabled=status.get("webhook_enabled", False),
        error=status.get("error"),
    )


@router.post(
    "/setup-reprocess-workflow",
    response_model=SetupReprocessWorkflowResponse,
    summary="Setup Paperless-ngx reprocess workflow",
    description="Automatically create the reprocess webhook workflow in Paperless-ngx. "
                "This allows users to add the reprocess tag to documents to trigger reprocessing. "
                "Requires admin access."
)
async def setup_reprocess_workflow(
    admin: AdminUser,
    request: SetupReprocessWorkflowRequest | None = None,
):
    """Setup the DeDox reprocess workflow in Paperless-ngx.

    This creates:
    - The reprocess tag (e.g., dedox:reprocess) if it doesn't exist
    - A trigger for DOCUMENT_UPDATED events with the reprocess tag
    - A webhook action pointing to DeDox reprocess endpoint
    - A workflow linking them together
    """
    settings = get_settings()

    if not settings.paperless.api_token:
        raise HTTPException(
            status_code=503,
            detail="Paperless-ngx is not configured (missing API token)"
        )

    request = request or SetupReprocessWorkflowRequest()
    service = PaperlessSetupService()

    result = await service.setup_reprocess_workflow(force=request.force)

    return SetupReprocessWorkflowResponse(
        success=result.get("success", False),
        message=result.get("message", result.get("error", "Unknown error")),
        workflow_id=result.get("workflow_id"),
        webhook_url=result.get("webhook_url"),
        reprocess_tag=result.get("reprocess_tag"),
        already_exists=result.get("already_exists", False),
        error=result.get("error"),
    )


@router.delete(
    "/setup-reprocess-workflow",
    response_model=SetupReprocessWorkflowResponse,
    summary="Remove Paperless-ngx reprocess workflow",
    description="Remove the DeDox reprocess workflow from Paperless-ngx. "
                "Note: The reprocess tag is NOT removed. Requires admin access."
)
async def remove_reprocess_workflow(admin: AdminUser):
    """Remove the DeDox reprocess workflow from Paperless-ngx."""
    settings = get_settings()

    if not settings.paperless.api_token:
        raise HTTPException(
            status_code=503,
            detail="Paperless-ngx is not configured (missing API token)"
        )

    service = PaperlessSetupService()
    result = await service.remove_reprocess_workflow()

    return SetupReprocessWorkflowResponse(
        success=result.get("success", False),
        message=result.get("message", result.get("error", "Unknown error")),
        error=result.get("error"),
    )
