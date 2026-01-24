"""Authentication routes."""

import logging
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status, Response
from pydantic import BaseModel, EmailStr

from dedox.api.deps import (
    CurrentUser,
    AdminUser,
    create_access_token,
    check_login_rate_limit,
    check_register_rate_limit,
)
from dedox.core.config import get_settings
from dedox.db import get_database
from dedox.db.repositories.user_repository import UserRepository
from dedox.models.user import User, UserCreate, UserRole, Token, APIKey

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    """Login request body."""
    username: str
    password: str


class LoginResponse(BaseModel):
    """Login response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class RegisterRequest(BaseModel):
    """User registration request."""
    username: str
    email: EmailStr
    password: str


class APIKeyCreate(BaseModel):
    """API key creation request."""
    name: str
    expires_days: int | None = None


class APIKeyResponse(BaseModel):
    """API key response (only shown once)."""
    key: str
    name: str
    created_at: datetime
    expires_at: datetime | None = None


@router.post("/login", response_model=LoginResponse, dependencies=[Depends(check_login_rate_limit)])
async def login(request: LoginRequest, response: Response):
    """Authenticate and get an access token."""
    db = await get_database()
    repo = UserRepository(db)
    
    # Verify credentials
    user = await repo.verify_password(request.username, request.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is disabled",
        )
    
    # Create token
    settings = get_settings()
    token = create_access_token(str(user.id), user.role)
    
    # Set cookie for web UI
    # Use secure=True in production (when not in debug mode)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.auth.token_expire_hours * 3600,
        samesite="lax",
        secure=not settings.server.debug,  # Secure in production
    )
    
    return LoginResponse(
        access_token=token,
        expires_in=settings.auth.token_expire_hours * 3600,
        user={
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "role": user.role.value,
        },
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, dependencies=[Depends(check_register_rate_limit)])
async def register(request: RegisterRequest):
    """Register a new user.
    
    Note: In production, this should require admin approval or be disabled.
    """
    settings = get_settings()
    
    # Check if registration is allowed
    if not settings.auth.allow_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User registration is disabled",
        )
    
    db = await get_database()
    repo = UserRepository(db)
    
    # Check if username exists
    existing = await repo.get_by_username(request.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )
    
    # Create user
    user_create = UserCreate(
        username=request.username,
        email=request.email,
        password=request.password,
        role=UserRole.USER,
    )
    
    user = await repo.create(user_create)
    
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "message": "User created successfully",
    }


@router.get("/me")
async def get_current_user_info(current_user: CurrentUser):
    """Get current user information."""
    return {
        "id": str(current_user.id),
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role.value,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
    }


@router.post("/api-keys", response_model=APIKeyResponse)
async def create_api_key(request: APIKeyCreate, current_user: CurrentUser):
    """Create a new API key for the current user."""
    db = await get_database()
    repo = UserRepository(db)
    
    api_key = await repo.create_api_key(
        user_id=current_user.id,
        name=request.name,
        expires_days=request.expires_days,
    )
    
    return APIKeyResponse(
        key=api_key.key,
        name=api_key.name,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    )


@router.get("/api-keys")
async def list_api_keys(current_user: CurrentUser):
    """List API keys for the current user (keys are masked)."""
    db = await get_database()
    repo = UserRepository(db)
    
    keys = await repo.list_api_keys(current_user.id)
    
    return [
        {
            "id": str(key.id),
            "name": key.name,
            "key_prefix": key.key[:8] + "..." if key.key else None,
            "created_at": key.created_at.isoformat() if key.created_at else None,
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
            "is_active": key.is_active,
        }
        for key in keys
    ]


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(key_id: str, current_user: CurrentUser):
    """Revoke an API key."""
    db = await get_database()
    repo = UserRepository(db)
    
    success = await repo.revoke_api_key(key_id, current_user.id)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )
    
    return {"message": "API key revoked"}


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(request: RegisterRequest, admin: AdminUser):
    """Create a new user (admin only)."""
    db = await get_database()
    repo = UserRepository(db)
    
    # Check if username exists
    existing = await repo.get_by_username(request.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )
    
    user_create = UserCreate(
        username=request.username,
        email=request.email,
        password=request.password,
        role=UserRole.USER,
    )
    
    user = await repo.create(user_create)
    
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "role": user.role.value,
    }


@router.get("/users")
async def list_users(admin: AdminUser):
    """List all users (admin only)."""
    db = await get_database()
    repo = UserRepository(db)
    
    users = await repo.list_all()
    
    return [
        {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "role": user.role.value,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }
        for user in users
    ]
