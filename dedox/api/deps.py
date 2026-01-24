"""Authentication dependencies and utilities."""

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader

from dedox.core.config import get_settings
from dedox.core.exceptions import AuthenticationError
from dedox.models.user import User, UserRole

logger = logging.getLogger(__name__)

# Security schemes
bearer_scheme = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# --- Rate Limiting ---

class RateLimiter:
    """Simple in-memory rate limiter.

    For production, consider using Redis-based rate limiting.
    """

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def _cleanup_old_requests(self, key: str, now: float) -> None:
        """Remove requests outside the current window."""
        cutoff = now - self.window_seconds
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]

    def is_allowed(self, key: str) -> bool:
        """Check if a request is allowed for the given key."""
        now = time.time()
        self._cleanup_old_requests(key, now)

        if len(self._requests[key]) >= self.max_requests:
            return False

        self._requests[key].append(now)
        return True

    def get_retry_after(self, key: str) -> int:
        """Get seconds until the rate limit resets."""
        if not self._requests[key]:
            return 0
        oldest = min(self._requests[key])
        return max(0, int(self.window_seconds - (time.time() - oldest)))


# Rate limiters for different endpoints
login_rate_limiter = RateLimiter(max_requests=5, window_seconds=60)  # 5 attempts per minute
register_rate_limiter = RateLimiter(max_requests=3, window_seconds=300)  # 3 per 5 minutes


def get_client_ip(request: Request) -> str:
    """Extract client IP from request, handling proxies."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def check_login_rate_limit(request: Request) -> None:
    """Dependency to check rate limit for login attempts."""
    client_ip = get_client_ip(request)
    if not login_rate_limiter.is_allowed(client_ip):
        retry_after = login_rate_limiter.get_retry_after(client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many login attempts. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )


async def check_register_rate_limit(request: Request) -> None:
    """Dependency to check rate limit for registration attempts."""
    client_ip = get_client_ip(request)
    if not register_rate_limiter.is_allowed(client_ip):
        retry_after = register_rate_limiter.get_retry_after(client_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many registration attempts. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )


def create_access_token(user_id: str, role: UserRole, expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    settings = get_settings()

    if expires_delta is None:
        expires_delta = timedelta(hours=settings.auth.token_expire_hours)

    now = datetime.now(timezone.utc)
    expire = now + expires_delta

    payload = {
        "sub": user_id,
        "role": role.value,
        "exp": expire,
        "iat": now,
    }

    token = jwt.encode(
        payload,
        settings.auth.jwt_secret,
        algorithm=settings.auth.jwt_algorithm,
    )

    return token


def verify_token(token: str) -> dict:
    """Verify and decode a JWT token."""
    settings = get_settings()
    
    try:
        payload = jwt.decode(
            token,
            settings.auth.jwt_secret,
            algorithms=[settings.auth.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Token has expired")
    except jwt.InvalidTokenError as e:
        raise AuthenticationError(f"Invalid token: {e}")


async def get_user_from_token(token: str) -> User:
    """Get user from JWT token."""
    from dedox.db.repositories.user_repository import UserRepository
    from dedox.db import get_database
    
    payload = verify_token(token)
    user_id = payload.get("sub")
    
    if not user_id:
        raise AuthenticationError("Invalid token payload")
    
    db = await get_database()
    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    
    if not user:
        raise AuthenticationError("User not found")
    
    if not user.is_active:
        raise AuthenticationError("User is disabled")
    
    return user


async def get_user_from_api_key(api_key: str) -> User:
    """Get user from API key."""
    from dedox.db.repositories.user_repository import UserRepository
    from dedox.db import get_database
    
    db = await get_database()
    repo = UserRepository(db)
    user = await repo.get_by_api_key(api_key)
    
    if not user:
        raise AuthenticationError("Invalid API key")
    
    if not user.is_active:
        raise AuthenticationError("User is disabled")
    
    return user


async def get_current_user(
    request: Request,
    bearer_token: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
    api_key: Annotated[str | None, Depends(api_key_header)] = None,
) -> User:
    """Get the current authenticated user.
    
    Supports both JWT bearer tokens and API keys.
    """
    # Try API key first
    if api_key:
        try:
            return await get_user_from_api_key(api_key)
        except AuthenticationError:
            pass
    
    # Try bearer token
    if bearer_token:
        try:
            return await get_user_from_token(bearer_token.credentials)
        except AuthenticationError:
            pass
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)]
) -> User:
    """Get the current active user."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is disabled",
        )
    return current_user


async def require_admin(
    current_user: Annotated[User, Depends(get_current_active_user)]
) -> User:
    """Require admin role."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


# Type aliases for cleaner dependency injection
CurrentUser = Annotated[User, Depends(get_current_active_user)]
AdminUser = Annotated[User, Depends(require_admin)]
