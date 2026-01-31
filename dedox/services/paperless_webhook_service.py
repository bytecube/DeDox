"""
Paperless-ngx webhook service.

Handles downloading documents from Paperless and updating metadata.
"""

import logging
import mimetypes
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from dedox.core.config import get_settings, get_metadata_fields
from dedox.core.exceptions import PaperlessError

logger = logging.getLogger(__name__)


class PaperlessWebhookService:
    """Service for handling Paperless-ngx webhook operations.

    This service handles:
    - Downloading documents from Paperless
    - Managing tags on documents
    - Creating/updating custom fields
    - Syncing metadata back to Paperless
    """

    def __init__(self):
        self.settings = get_settings()
        self._custom_field_cache: dict[str, int] = {}  # name -> id
        self._tag_cache: dict[str, int] = {}  # name -> id

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for Paperless API."""
        return {
            "Authorization": f"Token {self.settings.paperless.api_token}",
            "Accept": f"application/json; version={self.settings.paperless.api_version}",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Create an HTTP client for Paperless API."""
        return httpx.AsyncClient(
            base_url=self.settings.paperless.base_url,
            headers=self._get_headers(),
            verify=self.settings.paperless.verify_ssl,
            timeout=self.settings.paperless.timeout_seconds,
        )

    async def download_document(
        self,
        paperless_id: int,
        download_original: bool = True
    ) -> tuple[Path | None, dict[str, Any]]:
        """Download a document from Paperless-ngx.

        Args:
            paperless_id: Paperless document ID
            download_original: If True, download original file; else archived version

        Returns:
            Tuple of (file_path, file_info dict) or (None, {}) on failure
        """
        async with await self._get_client() as client:
            try:
                # Get document metadata first
                response = await client.get(f"/api/documents/{paperless_id}/")
                if response.status_code != 200:
                    logger.error(
                        f"Failed to get document {paperless_id}: {response.status_code}"
                    )
                    return None, {}

                doc_data = response.json()

                # Determine which file to download
                if download_original:
                    download_url = f"/api/documents/{paperless_id}/download/?original=true"
                else:
                    download_url = f"/api/documents/{paperless_id}/download/"

                # Download the file
                response = await client.get(download_url, timeout=self.settings.paperless.document_download_timeout)
                if response.status_code != 200:
                    logger.error(
                        f"Failed to download document {paperless_id}: {response.status_code}"
                    )
                    return None, {}

                # Determine filename and content type
                content_disposition = response.headers.get("content-disposition", "")
                content_type = response.headers.get("content-type", "application/octet-stream")

                # Extract filename from content-disposition or use document title
                original_filename = doc_data.get("original_file_name") or doc_data.get("title", f"document_{paperless_id}")

                # Ensure proper extension
                if "." not in original_filename:
                    ext = mimetypes.guess_extension(content_type) or ".pdf"
                    original_filename = f"{original_filename}{ext}"

                # Save to upload directory
                storage_settings = self.settings.storage
                upload_dir = Path(storage_settings.upload_path)
                upload_dir.mkdir(parents=True, exist_ok=True)

                # Generate unique filename
                unique_filename = f"{uuid4().hex}_{original_filename}"
                file_path = upload_dir / unique_filename

                # Write file
                with open(file_path, "wb") as f:
                    f.write(response.content)

                file_info = {
                    "filename": unique_filename,
                    "original_filename": original_filename,
                    "content_type": content_type,
                    "file_size": len(response.content),
                    "paperless_title": doc_data.get("title"),
                    "paperless_correspondent_id": doc_data.get("correspondent"),
                    "paperless_document_type_id": doc_data.get("document_type"),
                    "paperless_tags": doc_data.get("tags", []),
                    "paperless_created": doc_data.get("created"),
                    "paperless_added": doc_data.get("added"),
                }

                logger.info(
                    f"Downloaded document {paperless_id} to {file_path} "
                    f"({file_info['file_size']} bytes)"
                )

                return file_path, file_info

            except httpx.TimeoutException:
                logger.error(f"Timeout downloading document {paperless_id}")
                return None, {}
            except Exception as e:
                logger.exception(f"Error downloading document {paperless_id}: {e}")
                return None, {}

    async def get_or_create_tag(self, tag_name: str) -> int:
        """Get or create a tag in Paperless.

        Args:
            tag_name: Name of the tag

        Returns:
            Tag ID
        """
        # Check cache first
        if tag_name in self._tag_cache:
            return self._tag_cache[tag_name]

        async with await self._get_client() as client:
            # Search for existing tag
            response = await client.get(
                "/api/tags/",
                params={"name__iexact": tag_name}
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("results"):
                    tag_id = data["results"][0]["id"]
                    self._tag_cache[tag_name] = tag_id
                    return tag_id

            # Create tag
            # Choose color based on tag type
            tag_colors = self.settings.paperless.tag_colors
            color = tag_colors.default
            if "processing" in tag_name.lower():
                color = tag_colors.processing
            elif "enhanced" in tag_name.lower():
                color = tag_colors.enhanced
            elif "error" in tag_name.lower():
                color = tag_colors.error
            elif "review" in tag_name.lower():
                color = tag_colors.review

            response = await client.post(
                "/api/tags/",
                json={"name": tag_name, "color": color}
            )

            if response.status_code in [200, 201]:
                tag_id = response.json()["id"]
                self._tag_cache[tag_name] = tag_id
                logger.info(f"Created tag '{tag_name}' with ID {tag_id}")
                return tag_id

            raise PaperlessError(
                f"Failed to create tag '{tag_name}': {response.text}",
                status_code=response.status_code
            )

    async def add_tag_to_document(self, paperless_id: int, tag_name: str) -> bool:
        """Add a tag to a document in Paperless.

        Args:
            paperless_id: Paperless document ID
            tag_name: Tag name to add

        Returns:
            True if successful
        """
        try:
            tag_id = await self.get_or_create_tag(tag_name)

            async with await self._get_client() as client:
                # Get current tags
                response = await client.get(f"/api/documents/{paperless_id}/")
                if response.status_code != 200:
                    return False

                current_tags = response.json().get("tags", [])

                # Add new tag if not already present
                if tag_id not in current_tags:
                    current_tags.append(tag_id)

                    response = await client.patch(
                        f"/api/documents/{paperless_id}/",
                        json={"tags": current_tags}
                    )

                    if response.status_code != 200:
                        logger.error(
                            f"Failed to add tag to document {paperless_id}: {response.text}"
                        )
                        return False

            logger.info(f"Added tag '{tag_name}' to document {paperless_id}")
            return True

        except Exception as e:
            logger.error(f"Error adding tag to document {paperless_id}: {e}")
            return False

    async def remove_tag_from_document(self, paperless_id: int, tag_name: str) -> bool:
        """Remove a tag from a document in Paperless.

        Args:
            paperless_id: Paperless document ID
            tag_name: Tag name to remove

        Returns:
            True if successful
        """
        try:
            # Get tag ID (don't create if doesn't exist)
            if tag_name in self._tag_cache:
                tag_id = self._tag_cache[tag_name]
            else:
                async with await self._get_client() as client:
                    response = await client.get(
                        "/api/tags/",
                        params={"name__iexact": tag_name}
                    )
                    if response.status_code != 200:
                        return False

                    data = response.json()
                    if not data.get("results"):
                        return True  # Tag doesn't exist, nothing to remove

                    tag_id = data["results"][0]["id"]
                    self._tag_cache[tag_name] = tag_id

            async with await self._get_client() as client:
                # Get current tags
                response = await client.get(f"/api/documents/{paperless_id}/")
                if response.status_code != 200:
                    return False

                current_tags = response.json().get("tags", [])

                # Remove tag if present
                if tag_id in current_tags:
                    current_tags.remove(tag_id)

                    response = await client.patch(
                        f"/api/documents/{paperless_id}/",
                        json={"tags": current_tags}
                    )

                    if response.status_code != 200:
                        logger.error(
                            f"Failed to remove tag from document {paperless_id}: {response.text}"
                        )
                        return False

            logger.info(f"Removed tag '{tag_name}' from document {paperless_id}")
            return True

        except Exception as e:
            logger.error(f"Error removing tag from document {paperless_id}: {e}")
            return False

    async def get_or_create_custom_field(
        self,
        field_name: str,
        field_type: str = "string"
    ) -> int:
        """Get or create a custom field in Paperless.

        Args:
            field_name: Name of the custom field
            field_type: Type of field (string, date, boolean, integer, float, monetary, url, documentlink)

        Returns:
            Custom field ID
        """
        # Check cache first
        if field_name in self._custom_field_cache:
            return self._custom_field_cache[field_name]

        async with await self._get_client() as client:
            # Search for existing field
            response = await client.get(
                "/api/custom_fields/",
                params={"name__iexact": field_name}
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("results"):
                    field_id = data["results"][0]["id"]
                    self._custom_field_cache[field_name] = field_id
                    return field_id

            # Map our field types to Paperless types
            paperless_type_map = {
                "string": "string",
                "text": "string",
                "date": "date",
                "boolean": "boolean",
                "decimal": "float",
                "integer": "integer",
                "enum": "string",  # Enums stored as strings
                "array": "string",  # Arrays stored as JSON strings
            }
            paperless_type = paperless_type_map.get(field_type, "string")

            # Create custom field
            response = await client.post(
                "/api/custom_fields/",
                json={
                    "name": field_name,
                    "data_type": paperless_type,
                }
            )

            if response.status_code in [200, 201]:
                field_id = response.json()["id"]
                self._custom_field_cache[field_name] = field_id
                logger.info(f"Created custom field '{field_name}' with ID {field_id}")
                return field_id

            raise PaperlessError(
                f"Failed to create custom field '{field_name}': {response.text}",
                status_code=response.status_code
            )

    async def ensure_custom_fields_exist(self) -> dict[str, int]:
        """Ensure all configured metadata fields exist as Paperless custom fields.

        Returns:
            Dict mapping field names to Paperless custom field IDs
        """
        if not self.settings.paperless.webhook.auto_create_custom_fields:
            return {}

        metadata_fields = get_metadata_fields()
        field_ids = {}

        for field in metadata_fields.fields:
            # Only create fields that have paperless_mapping with custom_field type
            if field.paperless_mapping and field.paperless_mapping.type == "custom_field":
                paperless_field_name = field.paperless_mapping.field_name or field.name
                field_type = field.paperless_mapping.field_type or field.type

                try:
                    field_id = await self.get_or_create_custom_field(
                        paperless_field_name,
                        field_type
                    )
                    field_ids[field.name] = field_id
                except Exception as e:
                    logger.error(f"Failed to ensure custom field '{field.name}': {e}")

        return field_ids

    async def update_document_metadata(
        self,
        paperless_id: int,
        metadata: dict[str, Any],
        title: str | None = None,
        correspondent_id: int | None = None,
        document_type_id: int | None = None,
    ) -> bool:
        """Update document metadata in Paperless.

        Args:
            paperless_id: Paperless document ID
            metadata: Extracted metadata to sync as custom fields
            title: Optional new title
            correspondent_id: Optional correspondent ID
            document_type_id: Optional document type ID

        Returns:
            True if successful
        """
        try:
            metadata_fields = get_metadata_fields()
            custom_fields = []
            tags_to_add = []

            # Map metadata to custom fields and tags
            for field_name, value in metadata.items():
                field_config = metadata_fields.get_field(field_name)

                if not field_config or not field_config.paperless_mapping:
                    continue

                mapping = field_config.paperless_mapping

                if mapping.type == "custom_field":
                    paperless_field_name = mapping.field_name or field_name

                    try:
                        field_id = await self.get_or_create_custom_field(
                            paperless_field_name,
                            mapping.field_type or field_config.type
                        )

                        # Convert value to appropriate type
                        if isinstance(value, list):
                            value = ", ".join(str(v) for v in value)
                        elif isinstance(value, bool):
                            value = value
                        elif value is not None:
                            value = str(value)

                        # Truncate string values to Paperless custom field limit (128 chars)
                        if isinstance(value, str) and len(value) > 128:
                            logger.warning(
                                f"Truncating field '{field_name}' from {len(value)} to 128 chars"
                            )
                            value = value[:125] + "..."

                        if value is not None:
                            custom_fields.append({
                                "field": field_id,
                                "value": value
                            })
                    except Exception as e:
                        logger.error(f"Failed to map field '{field_name}': {e}")

                elif mapping.type == "tag":
                    # Handle boolean fields that map to tags
                    if mapping.apply_if_true and value is True:
                        tag_name = mapping.tag_name or field_name
                        tags_to_add.append(tag_name)

                elif mapping.type == "tags":
                    # Handle array fields that map to multiple tags (e.g., keywords)
                    if isinstance(value, list):
                        for keyword in value:
                            if keyword and isinstance(keyword, str):
                                tags_to_add.append(keyword.strip())

            # Build update payload
            update_data = {}

            if title:
                update_data["title"] = title

            if correspondent_id:
                update_data["correspondent"] = correspondent_id

            if document_type_id:
                update_data["document_type"] = document_type_id

            if custom_fields:
                update_data["custom_fields"] = custom_fields

            if not update_data:
                logger.info(f"No metadata to update for document {paperless_id}")
                return True

            async with await self._get_client() as client:
                response = await client.patch(
                    f"/api/documents/{paperless_id}/",
                    json=update_data
                )

                if response.status_code != 200:
                    logger.error(
                        f"Failed to update document {paperless_id}: {response.text}"
                    )
                    return False

            logger.info(f"Updated metadata for document {paperless_id}")

            # Apply tags for boolean fields with tag mappings
            for tag_name in tags_to_add:
                try:
                    await self.add_tag_to_document(paperless_id, tag_name)
                    logger.info(f"Applied tag '{tag_name}' to document {paperless_id}")
                except Exception as e:
                    logger.error(f"Failed to apply tag '{tag_name}': {e}")

            return True

        except Exception as e:
            logger.exception(f"Error updating document {paperless_id} metadata: {e}")
            return False

    async def update_document_content(
        self,
        paperless_id: int,
        content: str
    ) -> bool:
        """Update the document content (OCR text) in Paperless.

        This overwrites the built-in OCR result with externally extracted text,
        such as from a Vision-Language model.

        Args:
            paperless_id: Paperless document ID
            content: The text content to set

        Returns:
            True if successful
        """
        try:
            async with await self._get_client() as client:
                response = await client.patch(
                    f"/api/documents/{paperless_id}/",
                    json={"content": content}
                )

                if response.status_code != 200:
                    logger.error(
                        f"Failed to update content for document {paperless_id}: "
                        f"{response.status_code} - {response.text}"
                    )
                    return False

            logger.info(
                f"Updated content for document {paperless_id} "
                f"({len(content)} characters)"
            )
            return True

        except Exception as e:
            logger.error(f"Error updating content for document {paperless_id}: {e}")
            return False

    async def finalize_document_processing(
        self,
        paperless_id: int,
        metadata: dict[str, Any],
        success: bool = True,
        error_message: str | None = None,
        title: str | None = None,
    ) -> bool:
        """Finalize document processing - update metadata and swap tags.

        Args:
            paperless_id: Paperless document ID
            metadata: Extracted metadata
            success: Whether processing was successful
            error_message: Error message if not successful
            title: Optional title to set

        Returns:
            True if successful
        """
        try:
            settings = self.settings

            # Update metadata
            if success:
                await self.update_document_metadata(
                    paperless_id,
                    metadata,
                    title=title
                )

            # Remove processing tag
            await self.remove_tag_from_document(
                paperless_id,
                settings.paperless.processing_tag
            )

            # Add appropriate result tag
            if success:
                await self.add_tag_to_document(
                    paperless_id,
                    settings.paperless.enhanced_tag
                )
            else:
                await self.add_tag_to_document(
                    paperless_id,
                    settings.paperless.error_tag
                )

                # Add error as a note/comment if possible
                if error_message:
                    logger.warning(
                        f"Document {paperless_id} processing failed: {error_message}"
                    )

            return True

        except Exception as e:
            logger.exception(f"Error finalizing document {paperless_id}: {e}")
            return False
