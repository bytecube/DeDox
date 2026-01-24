"""
User model definitions for authentication.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, EmailStr, Field


class UserRole(str, Enum):
    """User role enumeration."""
    ADMIN = "admin"
    USER = "user"


class UserCreate(BaseModel):
    """Schema for creating a new user."""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.USER
    
    class Config:
        from_attributes = True


class UserUpdate(BaseModel):
    """Schema for updating a user."""
    email: EmailStr | None = None
    password: str | None = Field(None, min_length=8)
    role: UserRole | None = None
    is_active: bool | None = None
    
    class Config:
        from_attributes = True


class User(BaseModel):
    """Public user model (without password)."""
    id: UUID = Field(default_factory=uuid4)
    username: str
    email: EmailStr
    role: UserRole = UserRole.USER
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login: datetime | None = None
    
    class Config:
        from_attributes = True


class UserInDB(User):
    """User model with hashed password (for database storage)."""
    hashed_password: str
    
    class Config:
        from_attributes = True


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class TokenPayload(BaseModel):
    """JWT token payload."""
    sub: str  # user_id
    username: str
    role: UserRole
    exp: datetime
    iat: datetime


class APIKey(BaseModel):
    """API key model."""
    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    name: str
    key_hash: str
    prefix: str  # First 8 chars for identification
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used: datetime | None = None
    expires_at: datetime | None = None
    is_active: bool = True
    
    class Config:
        from_attributes = True


class APIKeyCreate(BaseModel):
    """Schema for creating an API key."""
    name: str = Field(..., min_length=1, max_length=100)
    expires_in_days: int | None = None  # None = never expires
    
    class Config:
        from_attributes = True


class APIKeyResponse(BaseModel):
    """API key response (shown only once on creation)."""
    id: UUID
    name: str
    key: str  # Full key, shown only on creation
    prefix: str
    created_at: datetime
    expires_at: datetime | None = None
    
    class Config:
        from_attributes = True
