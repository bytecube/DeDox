"""Sender matching logic for correspondent deduplication.

This module handles the two-phase sender extraction process:
1. Extract raw sender name from document
2. Match against existing Paperless-ngx correspondents to avoid duplicates
"""

import logging
from typing import Callable, Awaitable

import httpx

from dedox.core.config import get_settings

logger = logging.getLogger(__name__)

SENDER_MATCH_PROMPT = """Given the extracted sender name and a list of existing correspondents, determine if any existing correspondent matches the extracted sender.

Extracted sender: {extracted_sender}

Existing correspondents:
{existing_correspondents}

Matching rules:
- Match if names refer to the same entity despite:
  - Different spacing ("Deutsche Telekom" vs "DeutscheTelekom")
  - OCR errors (0/O, l/1/I confusion, common typos)
  - Abbreviations ("Dt. Telekom" vs "Deutsche Telekom")
  - Missing/extra legal forms ("Telekom" vs "Telekom AG" vs "Deutsche Telekom AG")
  - Minor punctuation differences
  - Case differences
- Do NOT match if names are clearly different entities
- When in doubt, prefer matching over creating duplicates

Respond with ONLY ONE of:
- The EXACT name from the existing correspondents list if a match is found
- "NEW" if no match exists (a new correspondent will be created with the extracted name)"""


class SenderMatcher:
    """Handles sender extraction and matching against existing correspondents.

    This class implements a two-phase approach to sender extraction:
    1. The LLM extracts the raw sender name from the document
    2. This class matches it against existing Paperless correspondents

    This prevents duplicate correspondents from being created due to:
    - OCR errors
    - Spacing differences
    - Abbreviations
    - Missing/extra legal suffixes
    """

    def __init__(self, llm_caller: Callable[[str, any], Awaitable[str]]):
        """Initialize with an LLM caller function.

        Args:
            llm_caller: Async function that takes a prompt and settings,
                       returns the LLM response as a string.
        """
        self.llm_caller = llm_caller
        self._correspondents_cache: list[str] | None = None
        self._cache_timestamp: float = 0
        self._cache_ttl = get_settings().processing.correspondent_cache_ttl

    async def match_sender(
        self,
        extracted_sender: str,
        settings
    ) -> str:
        """Match extracted sender against existing correspondents.

        Args:
            extracted_sender: The raw sender name extracted from the document.
            settings: Application settings containing Paperless config.

        Returns:
            The matched correspondent name if a match is found,
            or the original extracted name if no match exists.
        """
        if not extracted_sender:
            return extracted_sender

        extracted_sender = extracted_sender.strip()
        if not extracted_sender:
            return extracted_sender

        # Fetch existing correspondents
        existing = await self._fetch_correspondents(settings)

        if not existing:
            # No existing correspondents, use extracted name
            logger.debug(f"No existing correspondents, using extracted: {extracted_sender}")
            return extracted_sender

        # Quick exact match check (case-insensitive)
        extracted_lower = extracted_sender.lower()
        for correspondent in existing:
            if correspondent.lower() == extracted_lower:
                logger.info(f"Exact match found for '{extracted_sender}': '{correspondent}'")
                return correspondent

        # Ask LLM to match
        match_prompt = SENDER_MATCH_PROMPT.format(
            extracted_sender=extracted_sender,
            existing_correspondents="\n".join(f"- {c}" for c in existing)
        )

        try:
            result = await self.llm_caller(match_prompt, settings)
            result = result.strip()

            if result == "NEW":
                logger.info(f"No match found for '{extracted_sender}', will create new correspondent")
                return extracted_sender
            elif result in existing:
                logger.info(f"LLM matched '{extracted_sender}' to existing correspondent '{result}'")
                return result
            else:
                # LLM returned something unexpected
                # Check if it's a close match to any existing correspondent
                result_lower = result.lower()
                for correspondent in existing:
                    if correspondent.lower() == result_lower:
                        logger.info(f"LLM matched '{extracted_sender}' to '{correspondent}' (case-normalized)")
                        return correspondent

                # No match found, use original
                logger.warning(f"Unexpected LLM match result: '{result}', using original: '{extracted_sender}'")
                return extracted_sender

        except Exception as e:
            logger.warning(f"Sender matching failed: {e}, using original: '{extracted_sender}'")
            return extracted_sender

    async def _fetch_correspondents(
        self,
        settings,
        max_correspondents: int | None = None
    ) -> list[str]:
        """Fetch existing correspondent names from Paperless-ngx with pagination.

        Args:
            settings: Application settings containing Paperless config.
            max_correspondents: Maximum number of correspondents to return.
                               Limits context size for LLM matching.

        Returns:
            List of existing correspondent names, or empty list if
            Paperless is not enabled or fetch fails.
        """
        if not settings.paperless.api_token:
            return []

        # Use config values if not explicitly provided
        if max_correspondents is None:
            max_correspondents = settings.processing.max_correspondents

        correspondents: list[str] = []
        page = 1
        page_size = settings.processing.pagination_limit

        try:
            async with httpx.AsyncClient(
                timeout=settings.paperless.timeout_seconds,
                headers={"Authorization": f"Token {settings.paperless.api_token}"}
            ) as client:
                while len(correspondents) < max_correspondents:
                    response = await client.get(
                        f"{settings.paperless.base_url}/api/correspondents/",
                        params={
                            "page": page,
                            "page_size": page_size,
                            "ordering": "-document_count"  # Most used first
                        }
                    )
                    if response.status_code != 200:
                        logger.warning(f"Failed to fetch correspondents: HTTP {response.status_code}")
                        break

                    data = response.json()
                    results = data.get("results", [])

                    if not results:
                        break

                    for c in results:
                        if len(correspondents) >= max_correspondents:
                            break
                        correspondents.append(c["name"])

                    # Check if there are more pages
                    if not data.get("next"):
                        break

                    page += 1

                logger.debug(f"Fetched {len(correspondents)} correspondents from Paperless (limited to {max_correspondents})")
                return correspondents

        except httpx.ConnectError:
            logger.warning(f"Cannot connect to Paperless at {settings.paperless.base_url}")
        except Exception as e:
            logger.warning(f"Failed to fetch correspondents: {e}")

        return correspondents if correspondents else []
