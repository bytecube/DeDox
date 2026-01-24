"""
Paperless-ngx integration service.

Handles API token management and connectivity.
"""

import logging
import os

import httpx

from dedox.core.config import get_settings

logger = logging.getLogger(__name__)


class PaperlessService:
    """Service for managing Paperless-ngx integration."""

    _token: str | None = None

    @classmethod
    async def ensure_token(cls) -> bool:
        """Ensure we have a valid Paperless API token.

        If no token is configured, attempts to obtain one using admin credentials.

        Returns:
            True if token is available, False otherwise
        """
        settings = get_settings()

        # Check if token is already configured
        configured_token = settings.paperless.api_token.strip() if settings.paperless.api_token else ""
        if configured_token:
            logger.info("Paperless API token already configured")
            cls._token = configured_token
            # Ensure stripped token is stored back
            settings.paperless.api_token = configured_token
            return True

        # No token configured, try to get one using admin credentials
        if not settings.paperless.auto_generate_token:
            logger.warning(
                "No Paperless API token configured and auto_generate_token is disabled. "
                "Paperless integration will be disabled."
            )
            return False

        logger.info("No Paperless API token configured, attempting to generate one...")

        # Get admin credentials from settings (with fallback to environment variables for backwards compatibility)
        admin_user = settings.paperless.admin_user or os.environ.get("PAPERLESS_ADMIN_USER", "admin")
        admin_password = settings.paperless.admin_password or os.environ.get("PAPERLESS_ADMIN_PASSWORD", "")

        if not admin_password:
            logger.warning(
                "No Paperless API token or admin_password configured. "
                "Set paperless.admin_password in settings.yaml or PAPERLESS_ADMIN_PASSWORD env var. "
                "Paperless integration will be disabled."
            )
            return False

        try:
            token = await cls._obtain_token(
                settings.paperless.base_url,
                admin_user,
                admin_password
            )

            if token:
                cls._token = token
                # Update settings in memory
                settings.paperless.api_token = token
                logger.info("Successfully obtained Paperless API token")
                return True

            logger.error("Failed to obtain Paperless API token")
            return False

        except Exception as e:
            logger.error(f"Error obtaining Paperless API token: {e}")
            return False

    @classmethod
    async def _obtain_token(
        cls,
        base_url: str,
        username: str,
        password: str
    ) -> str | None:
        """Obtain an API token from Paperless-ngx using credentials.

        Args:
            base_url: Paperless-ngx base URL
            username: Admin username
            password: Admin password

        Returns:
            API token string or None if failed
        """
        settings = get_settings()
        async with httpx.AsyncClient(timeout=settings.paperless.timeout_seconds) as client:
            # Paperless-ngx API token endpoint
            token_url = f"{base_url}/api/token/"

            try:
                response = await client.post(
                    token_url,
                    data={
                        "username": username,
                        "password": password,
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    token = data.get("token")
                    if token:
                        # Strip any whitespace/newlines from the token
                        token = token.strip()
                        if token:
                            return token
                    logger.error("Paperless returned empty token")
                    return None

                logger.error(
                    f"Failed to obtain token: {response.status_code} - {response.text}"
                )
                return None

            except httpx.ConnectError:
                logger.warning(
                    f"Cannot connect to Paperless at {base_url}. "
                    "It may not be running yet."
                )
                return None
            except Exception as e:
                logger.error(f"Error obtaining token: {e}")
                return None

    @classmethod
    async def test_connection(cls) -> dict:
        """Test the connection to Paperless-ngx.

        Returns:
            Dict with status and details
        """
        settings = get_settings()

        token = cls._token or settings.paperless.api_token
        if not token:
            return {
                "status": "unconfigured",
                "message": "No API token available"
            }

        try:
            async with httpx.AsyncClient(timeout=settings.paperless.connection_test_timeout) as client:
                response = await client.get(
                    f"{settings.paperless.base_url}/api/",
                    headers={"Authorization": f"Token {token}"}
                )

                if response.status_code == 200:
                    return {
                        "status": "connected",
                        "url": settings.paperless.base_url
                    }
                else:
                    return {
                        "status": "error",
                        "code": response.status_code,
                        "message": response.text
                    }
        except Exception as e:
            return {
                "status": "offline",
                "error": str(e)
            }

    @classmethod
    def get_token(cls) -> str | None:
        """Get the current API token.

        Returns:
            API token or None if not configured
        """
        if cls._token:
            return cls._token

        settings = get_settings()
        token = settings.paperless.api_token
        if token:
            token = token.strip()
            if token:
                return token
        return None


async def init_paperless() -> bool:
    """Initialize Paperless connection and ensure token is available.

    Call this during application startup.

    Returns:
        True if Paperless is properly configured, False otherwise
    """
    return await PaperlessService.ensure_token()
