"""
Open WebUI synchronization service.

Handles uploading documents and metadata to Open WebUI knowledge bases.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from dedox.core.config import get_settings
from dedox.models.document import Document

logger = logging.getLogger(__name__)

# Lock to prevent concurrent knowledge base creation
_kb_creation_lock = asyncio.Lock()

# File to persist knowledge base ID across restarts
KB_ID_CACHE_FILE = "openwebui_kb_id.txt"


class OpenWebUISyncService:
    """Service for syncing documents to Open WebUI."""

    _cached_api_key: str | None = None  # Class-level cache for generated API key
    _cached_knowledge_base_id: str | None = None  # Class-level cache for knowledge base ID

    def __init__(self):
        self.settings = get_settings()
        self._kb_cache_path = Path(self.settings.storage.base_path) / KB_ID_CACHE_FILE

    def _load_cached_kb_id(self) -> str | None:
        """Load knowledge base ID from persistent file cache.

        Returns:
            Cached KB ID or None if not found
        """
        try:
            if self._kb_cache_path.exists():
                kb_id = self._kb_cache_path.read_text().strip()
                if kb_id:
                    logger.debug(f"Loaded KB ID from cache file: {kb_id}")
                    return kb_id
        except Exception as e:
            logger.warning(f"Failed to load KB ID from cache file: {e}")
        return None

    def _save_cached_kb_id(self, kb_id: str) -> None:
        """Save knowledge base ID to persistent file cache.

        Args:
            kb_id: Knowledge base ID to cache
        """
        try:
            # Ensure parent directory exists
            self._kb_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._kb_cache_path.write_text(kb_id)
            logger.debug(f"Saved KB ID to cache file: {kb_id}")
        except Exception as e:
            logger.warning(f"Failed to save KB ID to cache file: {e}")

    @classmethod
    def get_api_key(cls) -> str | None:
        """Get the API key from cache or settings."""
        if cls._cached_api_key:
            return cls._cached_api_key

        settings = get_settings()
        if settings.openwebui.api_key:
            cls._cached_api_key = settings.openwebui.api_key
            return cls._cached_api_key

        return None

    @classmethod
    async def generate_api_key(cls) -> str | None:
        """Generate authentication token automatically using admin credentials.

        Uses JWT token from login instead of API key since Open WebUI
        may have API key creation disabled.

        Returns:
            JWT token or None if generation fails.
        """
        settings = get_settings()

        # Check if auto-generation is enabled
        if not settings.openwebui.auto_generate_api_key:
            logger.warning("API key auto-generation is disabled")
            return None

        # Check if admin credentials are provided
        if not settings.openwebui.admin_email or not settings.openwebui.admin_password:
            logger.warning(
                "Cannot auto-generate API key: admin_email and admin_password must be configured. "
                "Set OPENWEBUI_ADMIN_EMAIL and OPENWEBUI_ADMIN_PASSWORD in .env"
            )
            return None

        try:
            async with httpx.AsyncClient(timeout=settings.openwebui.timeout_seconds) as client:
                # Login with admin credentials to get JWT token
                login_response = await client.post(
                    f"{settings.openwebui.base_url}/api/v1/auths/signin",
                    json={
                        "email": settings.openwebui.admin_email,
                        "password": settings.openwebui.admin_password,
                    },
                )

                if login_response.status_code != 200:
                    logger.error(
                        f"Failed to login to Open WebUI: {login_response.status_code} - {login_response.text}"
                    )
                    return None

                login_data = login_response.json()
                access_token = login_data.get("token")

                if not access_token:
                    logger.error("No access token returned from Open WebUI login")
                    return None

                logger.info("Successfully logged in to Open WebUI and obtained JWT token")

                # Cache and return the JWT token
                cls._cached_api_key = access_token
                return access_token

        except httpx.RequestError as e:
            logger.error(f"Connection error during authentication: {e}")
            return None
        except Exception as e:
            logger.exception(f"Unexpected error during authentication: {e}")
            return None

    async def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers with authentication.

        Automatically generates API key if not configured.
        """
        headers = {"Content-Type": "application/json"}

        # Try to get API key from cache or settings
        api_key = self.get_api_key()

        # If no API key, try to generate one
        if not api_key:
            logger.info("No API key configured, attempting automatic generation...")
            api_key = await self.generate_api_key()

        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            logger.warning("No API key available for Open WebUI authentication")

        return headers

    async def _get_knowledge_base_details(
        self, client: httpx.AsyncClient, headers: dict[str, str], kb_id: str
    ) -> dict | None:
        """Fetch details for a specific knowledge base by ID.

        Args:
            client: HTTP client
            headers: Request headers with auth
            kb_id: Knowledge base ID to fetch

        Returns:
            Knowledge base details dict or None if not found
        """
        try:
            response = await client.get(
                f"{self.settings.openwebui.base_url}/api/v1/knowledge/{kb_id}",
                headers=headers,
            )
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.debug(f"Failed to fetch KB details for {kb_id}: {e}")
        return None

    async def _find_existing_knowledge_base(
        self, client: httpx.AsyncClient, headers: dict[str, str], name: str
    ) -> str | None:
        """Search for an existing knowledge base by name.

        Args:
            client: HTTP client
            headers: Request headers with auth
            name: Knowledge base name to search for

        Returns:
            Knowledge base ID if found, None otherwise
        """
        try:
            response = await client.get(
                f"{self.settings.openwebui.base_url}/api/v1/knowledge/",
                headers=headers,
            )

            if response.status_code == 200:
                data = response.json()
                logger.debug(f"Knowledge base list response: {type(data).__name__}")

                # Handle different response formats from Open WebUI API
                # Could be: list directly, or {"data": list}, or {"items": list}
                if isinstance(data, dict):
                    knowledge_bases = data.get("data") or data.get("items") or []
                elif isinstance(data, list):
                    knowledge_bases = data
                else:
                    logger.warning(f"Unexpected knowledge base response format: {type(data)}")
                    return None

                for kb in knowledge_bases:
                    # Handle dict entries (full KB objects)
                    if isinstance(kb, dict):
                        if kb.get("name") == name:
                            kb_id = kb.get("id")
                            logger.info(f"Found existing knowledge base '{name}' with ID '{kb_id}'")
                            return kb_id
                    elif isinstance(kb, str):
                        # If kb is just an ID string, fetch its details to check the name
                        logger.debug(f"Fetching details for KB ID: {kb}")
                        kb_details = await self._get_knowledge_base_details(client, headers, kb)
                        if kb_details and kb_details.get("name") == name:
                            logger.info(f"Found existing knowledge base '{name}' with ID '{kb}'")
                            return kb

                logger.debug(f"No existing knowledge base found with name '{name}'")

            return None
        except Exception as e:
            logger.warning(f"Error searching for existing knowledge base: {e}")
            return None

    async def ensure_knowledge_base(self) -> str:
        """Ensure knowledge base exists, create if needed.

        If knowledge_base_id is not configured and auto_create_knowledge_base is enabled,
        a new knowledge base will be created automatically. On restart, it will find
        the existing "DeDox Documents" knowledge base by name to avoid creating duplicates.

        Uses a file-based cache to persist the KB ID across service restarts.
        Uses a lock to prevent concurrent creation of multiple knowledge bases.

        Returns:
            Knowledge base ID
        """
        # Check class-level cache first (fastest) - no lock needed for read
        if OpenWebUISyncService._cached_knowledge_base_id:
            return OpenWebUISyncService._cached_knowledge_base_id

        # Use lock to prevent concurrent KB creation
        async with _kb_creation_lock:
            # Double-check after acquiring lock (another coroutine may have set it)
            if OpenWebUISyncService._cached_knowledge_base_id:
                return OpenWebUISyncService._cached_knowledge_base_id

            # Check file-based cache (persists across restarts)
            cached_kb_id = self._load_cached_kb_id()

            kb_id = self.settings.openwebui.knowledge_base_id.strip() if self.settings.openwebui.knowledge_base_id else ""
            kb_name = "DeDox Documents"

            async with httpx.AsyncClient(timeout=self.settings.openwebui.timeout_seconds) as client:
                try:
                    headers = await self._get_headers()

                    # If we have a cached KB ID (from file), verify it still exists
                    if cached_kb_id:
                        response = await client.get(
                            f"{self.settings.openwebui.base_url}/api/v1/knowledge/{cached_kb_id}",
                            headers=headers,
                        )
                        if response.status_code == 200:
                            OpenWebUISyncService._cached_knowledge_base_id = cached_kb_id
                            logger.info(f"Using cached knowledge base ID: {cached_kb_id}")
                            return cached_kb_id
                        else:
                            logger.warning(f"Cached KB ID {cached_kb_id} no longer exists, will search for existing KB")

                    # If kb_id is configured in settings, check if it exists
                    if kb_id:
                        response = await client.get(
                            f"{self.settings.openwebui.base_url}/api/v1/knowledge/{kb_id}",
                            headers=headers,
                        )

                        if response.status_code == 200:
                            OpenWebUISyncService._cached_knowledge_base_id = kb_id
                            self._save_cached_kb_id(kb_id)
                            logger.info(f"Knowledge base '{kb_id}' exists")
                            return kb_id

                        if response.status_code != 404:
                            logger.error(f"Knowledge base check failed: {response.status_code} - {response.text}")
                            raise Exception(f"Failed to check knowledge base: {response.text}")

                    # kb_id is empty or doesn't exist - search for existing KB by name first
                    # This handles restarts where we previously auto-created a KB
                    existing_kb_id = await self._find_existing_knowledge_base(client, headers, kb_name)
                    if existing_kb_id:
                        OpenWebUISyncService._cached_knowledge_base_id = existing_kb_id
                        self._save_cached_kb_id(existing_kb_id)
                        return existing_kb_id

                    # No existing KB found - check if we can create one
                    if not self.settings.openwebui.auto_create_knowledge_base:
                        if kb_id:
                            raise Exception(f"Knowledge base '{kb_id}' not found and auto-create is disabled")
                        else:
                            raise Exception("No knowledge_base_id configured and auto-create is disabled")

                    # Create knowledge base - let Open WebUI generate the ID if not specified
                    create_payload = {
                        "name": kb_name,
                        "description": "Documents synced from DeDox / Paperless-ngx",
                        "data": {},
                        "access_control": {},  # Empty access_control for unrestricted access
                    }

                    # Only include ID if one was configured (allows Open WebUI to generate if empty)
                    if kb_id:
                        create_payload["id"] = kb_id

                    create_response = await client.post(
                        f"{self.settings.openwebui.base_url}/api/v1/knowledge/create",
                        headers=headers,
                        json=create_payload,
                    )

                    if create_response.status_code in (200, 201):
                        # Extract the actual ID from the response (Open WebUI generates its own UUID)
                        created_kb = create_response.json()
                        actual_kb_id = created_kb.get('id', kb_id)
                        OpenWebUISyncService._cached_knowledge_base_id = actual_kb_id
                        self._save_cached_kb_id(actual_kb_id)
                        logger.info(f"Created knowledge base '{kb_name}' with ID '{actual_kb_id}'")
                        return actual_kb_id
                    else:
                        logger.error(
                            f"Failed to create knowledge base: {create_response.status_code} - {create_response.text}"
                        )
                        raise Exception(f"Failed to create knowledge base: {create_response.text}")

                except httpx.RequestError as e:
                    logger.error(f"Failed to connect to Open WebUI: {e}")
                    raise Exception(f"Failed to connect to Open WebUI: {e}")

    async def format_document_markdown(self, doc: Document, paperless_metadata: dict[str, Any]) -> str:
        """Format document with metadata as markdown with frontmatter.

        Args:
            doc: Document model
            paperless_metadata: Metadata from Paperless API

        Returns:
            Formatted markdown string
        """
        # Parse DeDox extracted metadata
        metadata = json.loads(doc.metadata) if doc.metadata else {}

        # Build frontmatter
        frontmatter_data = {
            "title": paperless_metadata.get("title", doc.original_filename),
            "paperless_id": doc.paperless_id,
            "source": "paperless-ngx",
            "created_at": doc.created_at,
        }

        # Add DeDox extracted metadata
        if metadata:
            for key, value in metadata.items():
                # Truncate long values
                if isinstance(value, str) and len(value) > 200:
                    value = value[:200] + "..."
                frontmatter_data[key] = value

        # Add Paperless metadata
        if paperless_metadata.get("correspondent"):
            frontmatter_data["correspondent"] = paperless_metadata["correspondent"]
        if paperless_metadata.get("document_type"):
            frontmatter_data["document_type"] = paperless_metadata["document_type"]
        if paperless_metadata.get("tags"):
            frontmatter_data["tags"] = paperless_metadata["tags"]

        # Build frontmatter YAML
        frontmatter_lines = ["---"]
        for key, value in frontmatter_data.items():
            if value is not None:
                # Simple YAML formatting
                if isinstance(value, (list, tuple)):
                    frontmatter_lines.append(f"{key}:")
                    for item in value:
                        frontmatter_lines.append(f"  - {item}")
                elif isinstance(value, str):
                    # Escape quotes
                    escaped = value.replace('"', '\\"')
                    frontmatter_lines.append(f'{key}: "{escaped}"')
                else:
                    frontmatter_lines.append(f"{key}: {value}")
        frontmatter_lines.append("---")
        frontmatter_lines.append("")

        # Get OCR text
        ocr_text = doc.ocr_text or paperless_metadata.get("content", "")

        # Combine
        markdown = "\n".join(frontmatter_lines)
        markdown += f"# {frontmatter_data['title']}\n\n"
        markdown += ocr_text

        return markdown

    async def upload_document(
        self, file_path: Path, metadata: dict[str, Any], filename: str | None = None
    ) -> str | None:
        """Upload document content to Open WebUI as text.

        Args:
            file_path: Path to document file (not used - we send text content instead)
            metadata: Document metadata including 'content' with OCR text
            filename: Optional override filename

        Returns:
            File ID or None on failure
        """
        # Extract text content from metadata
        content = metadata.get("content", "")

        if not content:
            logger.error("No content available in document metadata")
            return None

        try:
            async with httpx.AsyncClient(timeout=self.settings.openwebui.timeout_seconds) as client:
                # Get headers with automatic API key generation
                headers = await self._get_headers()
                # Remove Content-Type for multipart form data upload
                headers.pop("Content-Type", None)

                # Create text file with content
                text_filename = (filename or file_path.name).replace(".pdf", ".txt")
                files = {"file": (text_filename, content.encode('utf-8'), "text/plain")}
                response = await client.post(
                    f"{self.settings.openwebui.base_url}/api/v1/files/",
                    headers=headers,
                    files=files,
                    params={"process": "true"},
                )

                if response.status_code in (200, 201):
                    result = response.json()
                    file_id = result.get("id")
                    logger.info(f"Uploaded document to Open WebUI: {file_id}")
                    return file_id
                else:
                    logger.error(
                        f"Failed to upload document: {response.status_code} - {response.text}"
                    )
                    return None

        except Exception as e:
            logger.exception(f"Error uploading document to Open WebUI: {e}")
            return None

    async def wait_for_processing(self, file_id: str) -> bool:
        """Wait for file processing to complete.

        Args:
            file_id: File ID to wait for

        Returns:
            True if processing succeeded, False otherwise
        """
        if not self.settings.openwebui.wait_for_processing:
            return True

        max_wait = self.settings.openwebui.max_processing_wait
        poll_interval = self.settings.openwebui.poll_interval
        elapsed = 0

        async with httpx.AsyncClient(timeout=self.settings.openwebui.timeout_seconds) as client:
            headers = await self._get_headers()

            while elapsed < max_wait:
                try:
                    response = await client.get(
                        f"{self.settings.openwebui.base_url}/api/v1/files/{file_id}/process/status",
                        headers=headers,
                    )

                    if response.status_code == 200:
                        status_data = response.json()
                        status = status_data.get("status", "pending")

                        if status == "completed":
                            logger.info(f"File {file_id} processing completed")
                            return True
                        elif status == "failed":
                            logger.error(f"File {file_id} processing failed")
                            return False
                        else:
                            logger.debug(f"File {file_id} status: {status}, waiting...")

                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                except Exception as e:
                    logger.warning(f"Error checking processing status: {e}")
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

        logger.warning(f"File {file_id} processing timeout after {max_wait}s")
        return False

    async def _find_file_by_content_hash(
        self, client: httpx.AsyncClient, headers: dict[str, str], kb_id: str, content_hash: str
    ) -> str | None:
        """Find a file in the knowledge base by its content hash.

        Args:
            client: HTTP client
            headers: Request headers
            kb_id: Knowledge base ID
            content_hash: Content hash to search for

        Returns:
            File ID if found, None otherwise
        """
        try:
            # Get knowledge base details including file list
            response = await client.get(
                f"{self.settings.openwebui.base_url}/api/v1/knowledge/{kb_id}",
                headers=headers,
            )

            if response.status_code != 200:
                return None

            kb_data = response.json()
            file_ids = kb_data.get("data", {}).get("file_ids", []) or kb_data.get("files", [])

            # Check each file for matching content hash
            for file_id in file_ids:
                # Handle both string IDs and dict objects
                fid = file_id if isinstance(file_id, str) else file_id.get("id")
                if not fid:
                    continue

                file_response = await client.get(
                    f"{self.settings.openwebui.base_url}/api/v1/files/{fid}",
                    headers=headers,
                )

                if file_response.status_code == 200:
                    file_data = file_response.json()
                    file_hash = file_data.get("hash") or file_data.get("data", {}).get("hash")
                    if file_hash == content_hash:
                        logger.debug(f"Found file {fid} with matching content hash")
                        return fid

            return None

        except Exception as e:
            logger.warning(f"Error searching for file by content hash: {e}")
            return None

    async def add_to_knowledge_base(self, file_id: str, kb_id: str | None = None) -> bool:
        """Add processed file to knowledge base.

        If duplicate content is detected, removes the existing file and retries
        to ensure the latest metadata is stored.

        Args:
            file_id: File ID to add
            kb_id: Knowledge base ID (uses default if None)

        Returns:
            True if successful, False otherwise
        """
        if kb_id is None:
            kb_id = await self.ensure_knowledge_base()

        try:
            async with httpx.AsyncClient(timeout=self.settings.openwebui.timeout_seconds) as client:
                headers = await self._get_headers()

                response = await client.post(
                    f"{self.settings.openwebui.base_url}/api/v1/knowledge/{kb_id}/file/add",
                    headers=headers,
                    json={"file_id": file_id},
                )

                if response.status_code in (200, 201):
                    logger.info(f"Added file {file_id} to knowledge base {kb_id}")
                    return True
                elif response.status_code == 400:
                    # Check if this is a duplicate content error
                    response_text = response.text.lower()
                    if "duplicate" in response_text or "already exists" in response_text:
                        logger.info(
                            f"Duplicate content detected for file {file_id}, "
                            "removing existing file to update with new metadata"
                        )

                        # Extract content hash from error message if available
                        # Format: "Document with hash XXXXX already exists"
                        import re
                        hash_match = re.search(r'hash\s+([a-f0-9]+)', response.text)
                        content_hash = hash_match.group(1) if hash_match else None

                        if content_hash:
                            # Find and remove the existing file with this hash
                            existing_file_id = await self._find_file_by_content_hash(
                                client, headers, kb_id, content_hash
                            )
                            if existing_file_id:
                                logger.info(f"Found existing file {existing_file_id} with same content hash")
                                # Remove from KB
                                await self.remove_file_from_knowledge_base(kb_id, existing_file_id)
                                # Delete the old file
                                await self.remove_document(existing_file_id)

                                # Retry adding the new file
                                retry_response = await client.post(
                                    f"{self.settings.openwebui.base_url}/api/v1/knowledge/{kb_id}/file/add",
                                    headers=headers,
                                    json={"file_id": file_id},
                                )

                                if retry_response.status_code in (200, 201):
                                    logger.info(
                                        f"Successfully updated file {file_id} in knowledge base {kb_id}"
                                    )
                                    return True
                                else:
                                    logger.error(
                                        f"Failed to add file after removing duplicate: "
                                        f"{retry_response.status_code} - {retry_response.text}"
                                    )
                                    return False
                            else:
                                logger.warning(
                                    f"Could not find existing file with hash {content_hash} to remove"
                                )
                                return False
                        else:
                            logger.warning("Duplicate content detected but could not extract hash")
                            return False
                    else:
                        logger.error(
                            f"Failed to add file to knowledge base: {response.status_code} - {response.text}"
                        )
                        return False
                else:
                    logger.error(
                        f"Failed to add file to knowledge base: {response.status_code} - {response.text}"
                    )
                    return False

        except Exception as e:
            logger.exception(f"Error adding file to knowledge base: {e}")
            return False

    async def sync_document(
        self, doc: Document, file_path: Path, paperless_metadata: dict[str, Any]
    ) -> bool:
        """Sync a document to Open WebUI.

        This is the main entry point that orchestrates the full sync process:
        1. Upload document file
        2. Wait for processing
        3. Add to knowledge base

        Args:
            doc: Document model
            file_path: Path to document file
            paperless_metadata: Metadata from Paperless API

        Returns:
            True if successful, False otherwise
        """
        if not self.settings.openwebui.enabled:
            logger.debug("Open WebUI sync is disabled")
            return False

        try:
            # Upload document
            file_id = await self.upload_document(
                file_path, paperless_metadata, filename=doc.original_filename
            )

            if not file_id:
                return False

            # Give Open WebUI time to process the file (even with process=true, it may return early)
            wait_time = self.settings.openwebui.file_processing_wait
            logger.info(f"Waiting {wait_time} seconds for file {file_id} to be processed...")
            await asyncio.sleep(wait_time)

            # Verify file has content before adding to knowledge base
            async with httpx.AsyncClient(timeout=self.settings.openwebui.timeout_seconds) as client:
                headers = await self._get_headers()
                file_response = await client.get(
                    f"{self.settings.openwebui.base_url}/api/v1/files/{file_id}",
                    headers=headers,
                )

                if file_response.status_code == 200:
                    file_data = file_response.json()
                    content = file_data.get("data", {}).get("content", "")
                    if not content:
                        logger.error(f"File {file_id} has no content after processing, skipping")
                        return False
                    logger.info(f"File {file_id} has {len(content)} characters of content")

            # Add to knowledge base
            added = await self.add_to_knowledge_base(file_id)

            if added:
                logger.info(f"Successfully synced document {doc.id} to Open WebUI")
                return True
            else:
                logger.error(f"Failed to add document {doc.id} to knowledge base")
                return False

        except Exception as e:
            logger.exception(f"Error syncing document {doc.id} to Open WebUI: {e}")
            return False

    async def find_files_by_filename(self, filename: str) -> list[dict[str, Any]]:
        """Find existing files in Open WebUI by filename.

        Args:
            filename: Filename to search for (exact or partial match)

        Returns:
            List of matching file objects with id, filename, etc.
        """
        try:
            async with httpx.AsyncClient(timeout=self.settings.openwebui.timeout_seconds) as client:
                headers = await self._get_headers()

                response = await client.get(
                    f"{self.settings.openwebui.base_url}/api/v1/files/",
                    headers=headers,
                )

                if response.status_code == 200:
                    files = response.json()
                    # Filter files matching the filename
                    matching = [
                        f for f in files
                        if f.get("filename") == filename or f.get("meta", {}).get("name") == filename
                    ]
                    if matching:
                        logger.debug(f"Found {len(matching)} existing files matching '{filename}'")
                    return matching
                else:
                    logger.warning(f"Failed to list files: {response.status_code}")
                    return []

        except Exception as e:
            logger.warning(f"Error searching for existing files: {e}")
            return []

    async def remove_file_from_knowledge_base(self, kb_id: str, file_id: str) -> bool:
        """Remove a file from a knowledge base (without deleting the file itself).

        Args:
            kb_id: Knowledge base ID
            file_id: File ID to remove from KB

        Returns:
            True if successful, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=self.settings.openwebui.timeout_seconds) as client:
                headers = await self._get_headers()

                response = await client.post(
                    f"{self.settings.openwebui.base_url}/api/v1/knowledge/{kb_id}/file/remove",
                    headers=headers,
                    json={"file_id": file_id},
                )

                if response.status_code in (200, 204):
                    logger.info(f"Removed file {file_id} from knowledge base {kb_id}")
                    return True
                else:
                    logger.warning(
                        f"Failed to remove file from KB: {response.status_code} - {response.text}"
                    )
                    return False

        except Exception as e:
            logger.warning(f"Error removing file from KB: {e}")
            return False

    async def remove_existing_document(self, filename: str) -> bool:
        """Find and remove existing document with the same filename.

        This removes the file from the knowledge base and deletes it entirely,
        allowing a new version with updated metadata to be uploaded.

        Args:
            filename: Filename to search for and remove

        Returns:
            True if file was found and removed, False otherwise
        """
        existing_files = await self.find_files_by_filename(filename)

        if not existing_files:
            return False

        kb_id = await self.ensure_knowledge_base()

        for file_info in existing_files:
            file_id = file_info.get("id")
            if file_id:
                logger.info(f"Removing existing file '{filename}' (ID: {file_id}) to update with new metadata")
                # Remove from KB first
                await self.remove_file_from_knowledge_base(kb_id, file_id)
                # Then delete the file
                await self.remove_document(file_id)

        return True

    async def remove_document(self, file_id: str) -> bool:
        """Remove a document from Open WebUI.

        Args:
            file_id: File ID to remove

        Returns:
            True if successful, False otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=self.settings.openwebui.timeout_seconds) as client:
                headers = await self._get_headers()

                response = await client.delete(
                    f"{self.settings.openwebui.base_url}/api/v1/files/{file_id}",
                    headers=headers,
                )

                if response.status_code in (200, 204):
                    logger.info(f"Removed file {file_id} from Open WebUI")
                    return True
                else:
                    logger.warning(
                        f"Failed to remove file from Open WebUI: {response.status_code} - {response.text}"
                    )
                    return False

        except Exception as e:
            logger.exception(f"Error removing file from Open WebUI: {e}")
            return False
