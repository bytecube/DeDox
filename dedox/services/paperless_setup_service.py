"""
Paperless-ngx automated setup service.

Handles automatic creation of workflows, triggers, and webhook actions
to integrate DeDox with Paperless-ngx via API.

Architecture:
    This service manages the Paperless-ngx integration by creating three
    independent workflows that communicate via webhooks.

    Workflow Types::

        +---------------------------+--------------------+---------------------------+
        | Workflow                  | Trigger            | Purpose                   |
        +---------------------------+--------------------+---------------------------+
        | DeDox Document Processing | DOCUMENT_ADDED     | New doc → OCR + LLM       |
        | DeDox Reprocess Trigger   | DOCUMENT_UPDATED   | Tag trigger → reprocess   |
        | DeDox Open WebUI Sync     | DOCUMENT_UPDATED   | Any update → RAG sync     |
        +---------------------------+--------------------+---------------------------+

    API Constants:
        The Paperless-ngx workflow API uses integer constants for trigger types,
        source types, and action types. These are defined at module level for
        maintainability.

    Workflow Setup Flow:
        1. Check Paperless connectivity (API reachable, token valid)
        2. Check if workflow already exists (skip or force-recreate)
        3. Create/get required tags (e.g., dedox:reprocess)
        4. Build trigger data with appropriate filters
        5. Build webhook action data with DeDox endpoint URL
        6. Create workflow via Paperless API (inline trigger + action)

    Error Handling:
        Each setup method returns a dict with 'success' boolean and either
        'workflow_id'/'message' on success or 'error' on failure.
"""

import logging
from typing import Any

import httpx

from dedox.core.config import get_settings
from dedox.core.exceptions import PaperlessError
from dedox.services.paperless_service import PaperlessService

logger = logging.getLogger(__name__)

# Constants for Paperless-ngx workflow API
# Trigger types from WorkflowTriggerTypeEnum
TRIGGER_TYPE_CONSUMPTION_STARTED = 1
TRIGGER_TYPE_DOCUMENT_ADDED = 2
TRIGGER_TYPE_DOCUMENT_UPDATED = 3
TRIGGER_TYPE_SCHEDULED = 4

# Trigger sources (values from Paperless-ngx DocumentSource IntEnum)
SOURCE_CONSUME_FOLDER = 1
SOURCE_API_UPLOAD = 2
SOURCE_MAIL_FETCH = 3
SOURCE_WEB_UI = 4
# API expects a list of source values
SOURCE_ALL_LIST = [SOURCE_CONSUME_FOLDER, SOURCE_API_UPLOAD, SOURCE_MAIL_FETCH, SOURCE_WEB_UI]

# Action types
ACTION_TYPE_ASSIGNMENT = 1
ACTION_TYPE_REMOVAL = 2
ACTION_TYPE_EMAIL = 3
ACTION_TYPE_WEBHOOK = 4

# Workflow names used by DeDox
DEDOX_WORKFLOW_NAME = "DeDox Document Processing"
DEDOX_REPROCESS_WORKFLOW_NAME = "DeDox Reprocess Trigger"
DEDOX_OPENWEBUI_SYNC_WORKFLOW_NAME = "DeDox Open WebUI Sync"


class PaperlessSetupService:
    """Service for automated Paperless-ngx workflow setup.

    Manages three independent workflows in Paperless-ngx:

    1. **DeDox Document Processing** (document-added trigger)
       - Fires when new documents are added to Paperless
       - Sends document to DeDox for OCR + LLM metadata extraction
       - Excludes documents already tagged with dedox:* tags

    2. **DeDox Reprocess Trigger** (document-updated trigger)
       - Fires when dedox:reprocess tag is added to a document
       - Re-runs DeDox processing on existing documents
       - Useful for correcting extraction errors

    3. **DeDox Open WebUI Sync** (document-updated trigger)
       - Fires on any document update (content/metadata changes)
       - Syncs document to Open WebUI knowledge base for RAG search
       - Independent of DeDox processing workflow

    Note: These workflows operate independently. A document can go through
    the processing workflow without syncing to Open WebUI, and vice versa.
    """

    def __init__(self, dedox_webhook_url: str | None = None):
        """Initialize the setup service.

        Args:
            dedox_webhook_url: Optional override for the DeDox webhook URL.
                             If not provided, will be constructed from settings.
        """
        self.settings = get_settings()
        self._dedox_webhook_url = dedox_webhook_url

    @property
    def dedox_webhook_url(self) -> str:
        """Get the DeDox webhook URL for document-added events."""
        if self._dedox_webhook_url:
            return self._dedox_webhook_url

        # Construct from settings - assume DeDox is reachable from Paperless
        # at the same host as configured in Paperless settings
        server = self.settings.server
        # Use configurable service hostname (default: 'dedox' for Docker environments)
        return f"http://{server.service_hostname}:{server.port}/api/webhooks/paperless/document-added"

    @property
    def dedox_reprocess_webhook_url(self) -> str:
        """Get the DeDox webhook URL for document-updated (reprocess) events."""
        server = self.settings.server
        return f"http://{server.service_hostname}:{server.port}/api/webhooks/paperless/document-updated"

    @property
    def dedox_openwebui_sync_webhook_url(self) -> str:
        """Get the DeDox webhook URL for Open WebUI sync events."""
        server = self.settings.server
        return f"http://{server.service_hostname}:{server.port}/api/webhooks/paperless/document-sync"

    def _get_token(self) -> str | None:
        """Get the API token from PaperlessService or settings."""
        return PaperlessService.get_token() or self.settings.paperless.api_token

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for Paperless API."""
        token = self._get_token()
        return {
            "Authorization": f"Token {token}" if token else "",
            "Accept": f"application/json; version={self.settings.paperless.api_version}",
            "Content-Type": "application/json",
        }

    def _get_base_url(self) -> str:
        """Get the base URL for Paperless API, ensuring no trailing slash."""
        return self.settings.paperless.base_url.rstrip("/")

    def _make_url(self, path: str) -> str:
        """Construct full URL for an API endpoint."""
        return f"{self._get_base_url()}{path}"

    async def _get_client(self) -> httpx.AsyncClient:
        """Create an HTTP client for Paperless API.

        Note: Headers are set per-request to ensure fresh token.
        """
        return httpx.AsyncClient(
            verify=self.settings.paperless.verify_ssl,
            timeout=self.settings.paperless.timeout_seconds,
        )

    async def check_connectivity(self) -> dict[str, Any]:
        """Check connectivity to Paperless-ngx.

        Returns:
            Dict with status info including version and API access.
        """
        token = PaperlessService.get_token() or self.settings.paperless.api_token
        if not token:
            logger.warning("No API token available for Paperless connectivity check")
            return {
                "connected": False,
                "error": "No API token available",
            }

        base_url = self.settings.paperless.base_url.rstrip("/")
        # Use /api/tags/ as a lightweight endpoint to verify connectivity
        # /api/ redirects to schema viewer, so we need an actual endpoint
        api_url = f"{base_url}/api/tags/"

        logger.info(f"Checking Paperless connectivity at {api_url}")

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.paperless.timeout_seconds,
                verify=self.settings.paperless.verify_ssl,
            ) as client:
                response = await client.get(
                    api_url,
                    headers={"Authorization": f"Token {token}"}
                )

                logger.info(f"Paperless connectivity check response: {response.status_code}")

                if response.status_code == 200:
                    return {
                        "connected": True,
                        "status_code": response.status_code,
                        "api_url": base_url,
                    }
                elif response.status_code == 401:
                    return {
                        "connected": False,
                        "error": "Authentication failed - check API token",
                        "status_code": response.status_code,
                    }
                elif response.status_code == 302:
                    # Redirect usually means auth issue
                    location = response.headers.get("location", "unknown")
                    logger.warning(f"Paperless returned redirect to: {location}")
                    return {
                        "connected": False,
                        "error": f"Redirect to {location} - authentication issue",
                        "status_code": response.status_code,
                    }
                else:
                    return {
                        "connected": False,
                        "error": f"Unexpected response: {response.status_code}",
                        "status_code": response.status_code,
                    }
        except httpx.ConnectError as e:
            logger.error(f"Connection failed to Paperless: {e}")
            return {
                "connected": False,
                "error": f"Connection failed: {e}",
            }
        except Exception as e:
            logger.exception(f"Unexpected error checking Paperless connectivity: {e}")
            return {
                "connected": False,
                "error": str(e),
            }

    async def check_workflow_exists(self, workflow_name: str = DEDOX_WORKFLOW_NAME) -> dict[str, Any]:
        """Check if a DeDox workflow already exists in Paperless.

        Args:
            workflow_name: The name of the workflow to check for.

        Returns:
            Dict with 'exists' bool and 'workflow_id' if exists.
        """
        async with await self._get_client() as client:
            try:
                response = await client.get(
                    self._make_url("/api/workflows/"),
                    headers=self._get_headers()
                )

                if response.status_code != 200:
                    raise PaperlessError(
                        f"Failed to list workflows: {response.text}",
                        status_code=response.status_code
                    )

                data = response.json()
                workflows = data.get("results", data) if isinstance(data, dict) else data

                for workflow in workflows:
                    if workflow.get("name") == workflow_name:
                        return {
                            "exists": True,
                            "workflow_id": workflow.get("id"),
                            "workflow": workflow,
                        }

                return {"exists": False}

            except PaperlessError:
                raise
            except Exception as e:
                logger.exception(f"Error checking workflow existence: {e}")
                raise PaperlessError(f"Error checking workflow: {e}")

    async def check_reprocess_workflow_exists(self) -> dict[str, Any]:
        """Check if the DeDox reprocess workflow already exists in Paperless.

        Returns:
            Dict with 'exists' bool and 'workflow_id' if exists.
        """
        return await self.check_workflow_exists(DEDOX_REPROCESS_WORKFLOW_NAME)

    async def check_openwebui_sync_workflow_exists(self) -> dict[str, Any]:
        """Check if the DeDox Open WebUI sync workflow already exists in Paperless.

        Returns:
            Dict with 'exists' bool and 'workflow_id' if exists.
        """
        return await self.check_workflow_exists(DEDOX_OPENWEBUI_SYNC_WORKFLOW_NAME)

    async def _get_dedox_tag_ids(self) -> list[int]:
        """Get IDs of all dedox:* tags for exclusion filter.

        Returns:
            List of tag IDs that match dedox:* pattern.
        """
        async with await self._get_client() as client:
            response = await client.get(
                self._make_url("/api/tags/"),
                headers=self._get_headers()
            )

            if response.status_code != 200:
                return []

            data = response.json()
            tags = data.get("results", data) if isinstance(data, dict) else data

            dedox_tag_ids = []
            for tag in tags:
                tag_name = tag.get("name", "")
                if tag_name.startswith("dedox:"):
                    dedox_tag_ids.append(tag["id"])

            return dedox_tag_ids

    async def _get_or_create_reprocess_tag(self) -> int:
        """Get or create the dedox:reprocess tag for workflow use.

        Returns:
            The tag ID.
        """
        tag_name = self.settings.paperless.reprocess_tag
        async with await self._get_client() as client:
            # Check if tag exists
            response = await client.get(
                self._make_url(f"/api/tags/?name__iexact={tag_name}"),
                headers=self._get_headers()
            )

            if response.status_code == 200:
                data = response.json()
                results = data.get("results", data) if isinstance(data, dict) else data
                for tag in results:
                    if tag.get("name", "").lower() == tag_name.lower():
                        return tag["id"]

            # Create the tag
            create_response = await client.post(
                self._make_url("/api/tags/"),
                headers=self._get_headers(),
                json={
                    "name": tag_name,
                    "color": self.settings.paperless.tag_colors.reprocess,
                    "is_inbox_tag": False,
                }
            )

            if create_response.status_code in [200, 201]:
                logger.info(f"Created reprocess tag '{tag_name}'")
                return create_response.json()["id"]

            raise PaperlessError(f"Failed to create reprocess tag: {create_response.text}")

    def _build_reprocess_trigger_data(self, reprocess_tag_id: int) -> dict[str, Any]:
        """Build trigger data for document updated (reprocess) events.

        This trigger fires when a document is updated and has the reprocess tag.

        Args:
            reprocess_tag_id: The ID of the reprocess tag to filter on.

        Returns:
            Trigger configuration dict.
        """
        return {
            "type": TRIGGER_TYPE_DOCUMENT_UPDATED,
            "sources": SOURCE_ALL_LIST,
            "filter_filename": "*",  # Match all files
            "filter_has_tags": [reprocess_tag_id],  # Must have the reprocess tag
            "filter_has_not_tags": [],
            "filter_has_correspondent": None,
            "filter_has_document_type": None,
        }

    def _build_trigger_data(self, exclude_tag_ids: list[int] | None = None) -> dict[str, Any]:
        """Build trigger data for document added events.

        Args:
            exclude_tag_ids: Optional list of tag IDs to exclude from triggering.

        Returns:
            Trigger configuration dict.
        """
        trigger_data = {
            "type": TRIGGER_TYPE_DOCUMENT_ADDED,
            "sources": SOURCE_ALL_LIST,
            "filter_filename": "*",  # Match all files
            "filter_has_tags": [],
            "filter_has_not_tags": [],  # Tags to exclude
            "filter_has_correspondent": None,
            "filter_has_document_type": None,
        }

        # Add tag exclusion filter if provided
        # Use filter_has_not_tags to exclude documents with these tags
        if exclude_tag_ids:
            trigger_data["filter_has_not_tags"] = exclude_tag_ids

        return trigger_data

    async def _get_or_create_pending_tag(self) -> int:
        """Get or create the dedox:pending tag for workflow use.

        Returns:
            The tag ID.
        """
        tag_name = "dedox:pending"
        async with await self._get_client() as client:
            # Check if tag exists
            response = await client.get(
                self._make_url(f"/api/tags/?name__iexact={tag_name}"),
                headers=self._get_headers()
            )

            if response.status_code == 200:
                data = response.json()
                results = data.get("results", data) if isinstance(data, dict) else data
                for tag in results:
                    if tag.get("name", "").lower() == tag_name.lower():
                        return tag["id"]

            # Create the tag
            create_response = await client.post(
                self._make_url("/api/tags/"),
                headers=self._get_headers(),
                json={
                    "name": tag_name,
                    "color": self.settings.paperless.tag_colors.pending,
                    "is_inbox_tag": False,
                }
            )

            if create_response.status_code in [200, 201]:
                return create_response.json()["id"]

            raise PaperlessError(f"Failed to create tag: {create_response.text}")

    def _build_webhook_action_data(self, webhook_url: str | None = None, include_document: bool = True) -> dict[str, Any]:
        """Build webhook action data for DeDox workflow.

        Note: Webhook actions can only be created when embedded in a workflow
        creation request, not as standalone actions (Paperless API limitation).

        Args:
            webhook_url: Optional custom webhook URL. Defaults to dedox_webhook_url.
            include_document: Whether to include the document file in the webhook.

        Returns:
            Action configuration dict with webhook settings.

        Available Paperless template variables for "Document Added" trigger:
        - doc_url: URL to the document in web UI (contains document ID)
        - doc_title: Current document title
        - original_filename: Original file name without extension
        - filename: Current file name without extension
        - correspondent: Assigned correspondent name
        - document_type: Assigned document type name
        - owner_username: Assigned owner username
        - created: Created datetime
        - added: Added datetime
        """
        url = webhook_url or self.dedox_webhook_url

        # Build the webhook parameters using correct Paperless template variables
        # Note: doc_pk is NOT directly available - we extract it from doc_url
        webhook_params = {
            "doc_url": "{{ doc_url }}",
            "doc_title": "{{ doc_title }}",
            "correspondent": "{{ correspondent }}",
            "document_type": "{{ document_type }}",
            "original_filename": "{{ original_filename }}",
            "filename": "{{ filename }}",
            "created": "{{ created }}",
            "added": "{{ added }}",
            "owner_username": "{{ owner_username }}",
        }

        # NOTE: When include_document=True, we MUST set as_json=False
        # because httpx cannot send both json and files in the same request.
        # Setting as_json=False sends params as form fields alongside the file.
        return {
            "type": ACTION_TYPE_WEBHOOK,
            "webhook": {
                "url": url,
                "use_params": True,
                "as_json": not include_document,  # Must be False when include_document is True
                "params": webhook_params,
                "body": None,
                "headers": None,
                "include_document": include_document,
            }
        }

    async def _create_workflow(
        self,
        trigger_data: dict[str, Any],
        action_data: dict[str, Any],
        workflow_name: str = DEDOX_WORKFLOW_NAME
    ) -> int:
        """Create the workflow with inline trigger and action data.

        Paperless-ngx API requires full trigger/action objects inline,
        not just IDs.

        Args:
            trigger_data: The trigger configuration dict.
            action_data: The action configuration dict.
            workflow_name: The name for the workflow.

        Returns:
            The created workflow ID.
        """
        async with await self._get_client() as client:
            workflow_data = {
                "name": workflow_name,
                "order": 0,
                "enabled": True,
                "triggers": [trigger_data],
                "actions": [action_data],
            }

            response = await client.post(
                self._make_url("/api/workflows/"),
                headers=self._get_headers(),
                json=workflow_data
            )

            if response.status_code not in [200, 201]:
                raise PaperlessError(
                    f"Failed to create workflow: {response.text}",
                    status_code=response.status_code
                )

            workflow_id = response.json()["id"]
            logger.info(f"Created workflow '{workflow_name}' with ID {workflow_id}")
            return workflow_id

    async def setup_dedox_workflow(self, force: bool = False) -> dict[str, Any]:
        """Set up the DeDox webhook workflow in Paperless.

        This creates:
        1. A trigger for DOCUMENT_ADDED events (excluding dedox:* tagged docs)
        2. A webhook action pointing to DeDox with document included
        3. A workflow linking them together

        Args:
            force: If True, remove existing workflow and recreate.

        Returns:
            Dict with setup results including workflow_id.
        """
        # Check connectivity first
        connectivity = await self.check_connectivity()
        if not connectivity.get("connected"):
            return {
                "success": False,
                "error": f"Cannot connect to Paperless: {connectivity.get('error')}",
            }

        # Check if workflow already exists
        existing = await self.check_workflow_exists()
        if existing.get("exists"):
            if force:
                logger.info("Force flag set - removing existing workflow")
                await self.remove_dedox_workflow()
            else:
                return {
                    "success": True,
                    "already_exists": True,
                    "workflow_id": existing.get("workflow_id"),
                    "message": f"Workflow '{DEDOX_WORKFLOW_NAME}' already exists",
                }

        try:
            # Get dedox:* tag IDs for exclusion filter
            exclude_tag_ids = await self._get_dedox_tag_ids()
            logger.info(f"Found {len(exclude_tag_ids)} dedox:* tags to exclude from trigger")

            # Build trigger data (DOCUMENT_ADDED from all sources)
            trigger_data = self._build_trigger_data(exclude_tag_ids)

            # Build webhook action data
            action_data = self._build_webhook_action_data()
            logger.info(f"Configured webhook action pointing to: {self.dedox_webhook_url}")

            # Create workflow with inline trigger and action data
            # Note: Webhook actions must be created inline with workflow,
            # not as separate API calls (Paperless API limitation)
            workflow_id = await self._create_workflow(trigger_data, action_data)

            return {
                "success": True,
                "workflow_id": workflow_id,
                "webhook_url": self.dedox_webhook_url,
                "message": f"Successfully created workflow '{DEDOX_WORKFLOW_NAME}' with webhook action.",
            }

        except PaperlessError as e:
            logger.error(f"Failed to setup workflow: {e}")
            return {
                "success": False,
                "error": str(e),
            }
        except Exception as e:
            logger.exception(f"Unexpected error during workflow setup: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def setup_reprocess_workflow(self, force: bool = False) -> dict[str, Any]:
        """Set up the DeDox reprocess workflow in Paperless.

        This creates:
        1. The reprocess tag (e.g., dedox:reprocess) if it doesn't exist
        2. A trigger for DOCUMENT_UPDATED events with the reprocess tag
        3. A webhook action pointing to DeDox reprocess endpoint
        4. A workflow linking them together

        When a user adds the reprocess tag to a document in Paperless,
        this workflow will trigger DeDox to reprocess it.

        Args:
            force: If True, remove existing workflow and recreate.

        Returns:
            Dict with setup results including workflow_id.
        """
        # Check connectivity first
        connectivity = await self.check_connectivity()
        if not connectivity.get("connected"):
            return {
                "success": False,
                "error": f"Cannot connect to Paperless: {connectivity.get('error')}",
            }

        # Check if workflow already exists
        existing = await self.check_reprocess_workflow_exists()
        if existing.get("exists"):
            if force:
                logger.info("Force flag set - removing existing reprocess workflow")
                await self.remove_reprocess_workflow()
            else:
                return {
                    "success": True,
                    "already_exists": True,
                    "workflow_id": existing.get("workflow_id"),
                    "message": f"Workflow '{DEDOX_REPROCESS_WORKFLOW_NAME}' already exists",
                }

        try:
            # Get or create the reprocess tag
            reprocess_tag_id = await self._get_or_create_reprocess_tag()
            logger.info(f"Reprocess tag ID: {reprocess_tag_id}")

            # Build trigger data (DOCUMENT_UPDATED with reprocess tag)
            trigger_data = self._build_reprocess_trigger_data(reprocess_tag_id)

            # Build webhook action data (pointing to document-updated endpoint)
            # For reprocess, we don't need to include the document file since DeDox
            # will download it from Paperless using the document ID
            action_data = self._build_webhook_action_data(
                webhook_url=self.dedox_reprocess_webhook_url,
                include_document=False  # DeDox will fetch the document itself
            )
            logger.info(f"Configured reprocess webhook pointing to: {self.dedox_reprocess_webhook_url}")

            # Create workflow with inline trigger and action data
            workflow_id = await self._create_workflow(
                trigger_data,
                action_data,
                workflow_name=DEDOX_REPROCESS_WORKFLOW_NAME
            )

            return {
                "success": True,
                "workflow_id": workflow_id,
                "webhook_url": self.dedox_reprocess_webhook_url,
                "reprocess_tag": self.settings.paperless.reprocess_tag,
                "message": f"Successfully created workflow '{DEDOX_REPROCESS_WORKFLOW_NAME}'. "
                          f"Add the '{self.settings.paperless.reprocess_tag}' tag to any document "
                          f"in Paperless to trigger reprocessing.",
            }

        except PaperlessError as e:
            logger.error(f"Failed to setup reprocess workflow: {e}")
            return {
                "success": False,
                "error": str(e),
            }
        except Exception as e:
            logger.exception(f"Unexpected error during reprocess workflow setup: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def setup_openwebui_sync_workflow(self, force: bool = False) -> dict[str, Any]:
        """Set up the Open WebUI sync workflow in Paperless.

        This creates:
        1. A trigger for DOCUMENT_UPDATED events (ALL documents, no filters)
        2. A webhook action pointing to DeDox Open WebUI sync endpoint
        3. A workflow linking them together

        This workflow fires on ANY document update to keep Open WebUI synchronized.

        Args:
            force: If True, remove existing workflow and recreate.

        Returns:
            Dict with setup results including workflow_id.
        """
        # Check if Open WebUI sync is enabled
        if not self.settings.openwebui.enabled:
            return {
                "success": False,
                "error": "Open WebUI sync is disabled in settings",
            }

        # Check connectivity first
        connectivity = await self.check_connectivity()
        if not connectivity.get("connected"):
            return {
                "success": False,
                "error": f"Cannot connect to Paperless: {connectivity.get('error')}",
            }

        # Check if workflow already exists
        existing = await self.check_openwebui_sync_workflow_exists()
        if existing.get("exists"):
            if force:
                logger.info("Force flag set - removing existing Open WebUI sync workflow")
                await self.remove_openwebui_sync_workflow()
            else:
                return {
                    "success": True,
                    "already_exists": True,
                    "workflow_id": existing.get("workflow_id"),
                    "message": f"Workflow '{DEDOX_OPENWEBUI_SYNC_WORKFLOW_NAME}' already exists",
                }

        try:
            # Build trigger data (DOCUMENT_UPDATED from all sources, no filters)
            trigger_data = {
                "type": TRIGGER_TYPE_DOCUMENT_UPDATED,
                "sources": SOURCE_ALL_LIST,
                "filter_filename": "*",  # Match all files
                "filter_has_tags": [],  # No tag requirements
                "filter_has_not_tags": [],  # No tag exclusions
                "filter_has_correspondent": None,
                "filter_has_document_type": None,
            }

            # Build webhook action data (pointing to document-sync endpoint)
            # For sync, we don't need to include the document file since the sync service
            # will download it from Paperless using the document ID
            action_data = self._build_webhook_action_data(
                webhook_url=self.dedox_openwebui_sync_webhook_url,
                include_document=False  # DeDox will fetch the document itself
            )
            logger.info(f"Configured Open WebUI sync webhook pointing to: {self.dedox_openwebui_sync_webhook_url}")

            # Create workflow with inline trigger and action data
            workflow_id = await self._create_workflow(
                trigger_data,
                action_data,
                workflow_name=DEDOX_OPENWEBUI_SYNC_WORKFLOW_NAME
            )

            return {
                "success": True,
                "workflow_id": workflow_id,
                "webhook_url": self.dedox_openwebui_sync_webhook_url,
                "message": f"Successfully created workflow '{DEDOX_OPENWEBUI_SYNC_WORKFLOW_NAME}'. "
                          f"All document updates will be synced to Open WebUI.",
            }

        except PaperlessError as e:
            logger.error(f"Failed to setup Open WebUI sync workflow: {e}")
            return {
                "success": False,
                "error": str(e),
            }
        except Exception as e:
            logger.exception(f"Unexpected error during Open WebUI sync workflow setup: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def remove_dedox_workflow(self) -> dict[str, Any]:
        """Remove the DeDox workflow from Paperless.

        This also removes the associated trigger and action.

        Returns:
            Dict with removal results.
        """
        existing = await self.check_workflow_exists()
        if not existing.get("exists"):
            return {
                "success": True,
                "message": "Workflow does not exist, nothing to remove",
            }

        workflow = existing.get("workflow", {})
        workflow_id = existing.get("workflow_id")
        trigger_ids = workflow.get("triggers", [])
        action_ids = workflow.get("actions", [])

        async with await self._get_client() as client:
            try:
                # Delete workflow first
                response = await client.delete(
                    self._make_url(f"/api/workflows/{workflow_id}/"),
                    headers=self._get_headers()
                )
                if response.status_code not in [200, 204]:
                    logger.warning(f"Failed to delete workflow {workflow_id}: {response.status_code}")
                else:
                    logger.info(f"Deleted workflow {workflow_id}")

                # Delete triggers
                for trigger_id in trigger_ids:
                    response = await client.delete(
                        self._make_url(f"/api/workflow_triggers/{trigger_id}/"),
                        headers=self._get_headers()
                    )
                    if response.status_code in [200, 204]:
                        logger.info(f"Deleted trigger {trigger_id}")

                # Delete actions
                for action_id in action_ids:
                    response = await client.delete(
                        self._make_url(f"/api/workflow_actions/{action_id}/"),
                        headers=self._get_headers()
                    )
                    if response.status_code in [200, 204]:
                        logger.info(f"Deleted action {action_id}")

                return {
                    "success": True,
                    "message": f"Removed workflow '{DEDOX_WORKFLOW_NAME}' and associated triggers/actions",
                    "removed_workflow_id": workflow_id,
                    "removed_trigger_ids": trigger_ids,
                    "removed_action_ids": action_ids,
                }

            except Exception as e:
                logger.exception(f"Error removing workflow: {e}")
                return {
                    "success": False,
                    "error": str(e),
                }

    async def remove_reprocess_workflow(self) -> dict[str, Any]:
        """Remove the DeDox reprocess workflow from Paperless.

        This also removes the associated trigger and action.
        Note: The reprocess tag is NOT removed.

        Returns:
            Dict with removal results.
        """
        existing = await self.check_reprocess_workflow_exists()
        if not existing.get("exists"):
            return {
                "success": True,
                "message": "Reprocess workflow does not exist, nothing to remove",
            }

        workflow = existing.get("workflow", {})
        workflow_id = existing.get("workflow_id")
        trigger_ids = workflow.get("triggers", [])
        action_ids = workflow.get("actions", [])

        async with await self._get_client() as client:
            try:
                # Delete workflow first
                response = await client.delete(
                    self._make_url(f"/api/workflows/{workflow_id}/"),
                    headers=self._get_headers()
                )
                if response.status_code not in [200, 204]:
                    logger.warning(f"Failed to delete reprocess workflow {workflow_id}: {response.status_code}")
                else:
                    logger.info(f"Deleted reprocess workflow {workflow_id}")

                # Delete triggers
                for trigger_id in trigger_ids:
                    response = await client.delete(
                        self._make_url(f"/api/workflow_triggers/{trigger_id}/"),
                        headers=self._get_headers()
                    )
                    if response.status_code in [200, 204]:
                        logger.info(f"Deleted trigger {trigger_id}")

                # Delete actions
                for action_id in action_ids:
                    response = await client.delete(
                        self._make_url(f"/api/workflow_actions/{action_id}/"),
                        headers=self._get_headers()
                    )
                    if response.status_code in [200, 204]:
                        logger.info(f"Deleted action {action_id}")

                return {
                    "success": True,
                    "message": f"Removed workflow '{DEDOX_REPROCESS_WORKFLOW_NAME}' and associated triggers/actions",
                    "removed_workflow_id": workflow_id,
                    "removed_trigger_ids": trigger_ids,
                    "removed_action_ids": action_ids,
                }

            except Exception as e:
                logger.exception(f"Error removing reprocess workflow: {e}")
                return {
                    "success": False,
                    "error": str(e),
                }

    async def remove_openwebui_sync_workflow(self) -> dict[str, Any]:
        """Remove the DeDox Open WebUI sync workflow from Paperless.

        This also removes the associated trigger and action.

        Returns:
            Dict with removal results.
        """
        existing = await self.check_openwebui_sync_workflow_exists()
        if not existing.get("exists"):
            return {
                "success": True,
                "message": "Open WebUI sync workflow does not exist, nothing to remove",
            }

        workflow = existing.get("workflow", {})
        workflow_id = existing.get("workflow_id")
        trigger_ids = workflow.get("triggers", [])
        action_ids = workflow.get("actions", [])

        async with await self._get_client() as client:
            try:
                # Delete workflow first
                response = await client.delete(
                    self._make_url(f"/api/workflows/{workflow_id}/"),
                    headers=self._get_headers()
                )
                if response.status_code not in [200, 204]:
                    logger.warning(f"Failed to delete Open WebUI sync workflow {workflow_id}: {response.status_code}")
                else:
                    logger.info(f"Deleted Open WebUI sync workflow {workflow_id}")

                # Delete triggers
                for trigger_id in trigger_ids:
                    response = await client.delete(
                        self._make_url(f"/api/workflow_triggers/{trigger_id}/"),
                        headers=self._get_headers()
                    )
                    if response.status_code in [200, 204]:
                        logger.info(f"Deleted trigger {trigger_id}")

                # Delete actions
                for action_id in action_ids:
                    response = await client.delete(
                        self._make_url(f"/api/workflow_actions/{action_id}/"),
                        headers=self._get_headers()
                    )
                    if response.status_code in [200, 204]:
                        logger.info(f"Deleted action {action_id}")

                return {
                    "success": True,
                    "message": f"Removed workflow '{DEDOX_OPENWEBUI_SYNC_WORKFLOW_NAME}' and associated triggers/actions",
                    "removed_workflow_id": workflow_id,
                    "removed_trigger_ids": trigger_ids,
                    "removed_action_ids": action_ids,
                }

            except Exception as e:
                logger.exception(f"Error removing Open WebUI sync workflow: {e}")
                return {
                    "success": False,
                    "error": str(e),
                }

    async def get_status(self) -> dict[str, Any]:
        """Get the current status of DeDox integration with Paperless.

        Returns:
            Dict with connectivity status, workflow status, and configuration.
        """
        connectivity = await self.check_connectivity()

        if not connectivity.get("connected"):
            return {
                "paperless_connected": False,
                "error": connectivity.get("error"),
                "workflow_configured": False,
                "reprocess_workflow_configured": False,
            }

        workflow_status = await self.check_workflow_exists()
        reprocess_workflow_status = await self.check_reprocess_workflow_exists()
        openwebui_sync_workflow_status = await self.check_openwebui_sync_workflow_exists()

        return {
            "paperless_connected": True,
            "paperless_url": self.settings.paperless.base_url,
            "workflow_configured": workflow_status.get("exists", False),
            "workflow_id": workflow_status.get("workflow_id"),
            "reprocess_workflow_configured": reprocess_workflow_status.get("exists", False),
            "reprocess_workflow_id": reprocess_workflow_status.get("workflow_id"),
            "reprocess_tag": self.settings.paperless.reprocess_tag,
            "openwebui_sync_workflow_configured": openwebui_sync_workflow_status.get("exists", False),
            "openwebui_sync_workflow_id": openwebui_sync_workflow_status.get("workflow_id"),
            "openwebui_sync_enabled": self.settings.openwebui.enabled,
            "dedox_webhook_url": self.dedox_webhook_url,
            "dedox_reprocess_webhook_url": self.dedox_reprocess_webhook_url,
            "dedox_openwebui_sync_webhook_url": self.dedox_openwebui_sync_webhook_url,
            "webhook_enabled": self.settings.paperless.webhook.enabled,
        }
