"""
Repository for User operations.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

from passlib.context import CryptContext

from dedox.db.database import Database
from dedox.models.user import User, UserCreate, UserInDB, UserRole, APIKey

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserRepository:
    """Repository for User CRUD operations."""
    
    def __init__(self, db: Database):
        self.db = db
    
    def _hash_password(self, password: str) -> str:
        """Hash a password using bcrypt."""
        return pwd_context.hash(password)
    
    def _verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash."""
        return pwd_context.verify(plain_password, hashed_password)
    
    async def create(self, user_create: UserCreate, hashed_password: str | None = None) -> UserInDB:
        """Create a new user.
        
        If hashed_password is not provided, the password from user_create will be hashed.
        """
        if hashed_password is None:
            hashed_password = self._hash_password(user_create.password)
        
        user = UserInDB(
            username=user_create.username,
            email=user_create.email,
            role=user_create.role,
            hashed_password=hashed_password,
        )
        
        data = {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "hashed_password": user.hashed_password,
            "role": user.role.value,
            "is_active": 1 if user.is_active else 0,
            "created_at": user.created_at.isoformat(),
            "updated_at": user.updated_at.isoformat(),
        }
        
        await self.db.insert("users", data)
        return user
    
    async def get_by_id(self, user_id: UUID) -> UserInDB | None:
        """Get a user by ID."""
        row = await self.db.fetch_one(
            "SELECT * FROM users WHERE id = ?",
            (str(user_id),)
        )
        
        if not row:
            return None
        
        return self._row_to_user(row)
    
    async def get_by_username(self, username: str) -> UserInDB | None:
        """Get a user by username."""
        row = await self.db.fetch_one(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        )
        
        if not row:
            return None
        
        return self._row_to_user(row)
    
    async def verify_password(self, username: str, password: str) -> UserInDB | None:
        """Verify username and password, return user if valid."""
        user = await self.get_by_username(username)
        if not user:
            return None
        
        if not self._verify_password(password, user.hashed_password):
            return None
        
        return user
    
    async def get_by_email(self, email: str) -> UserInDB | None:
        """Get a user by email."""
        row = await self.db.fetch_one(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        )
        
        if not row:
            return None
        
        return self._row_to_user(row)
    
    async def get_all(self, limit: int = 100, offset: int = 0) -> list[User]:
        """Get all users (without passwords)."""
        rows = await self.db.fetch_all(
            """
            SELECT id, username, email, role, is_active, created_at, updated_at, last_login
            FROM users
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset)
        )
        
        return [
            User(
                id=UUID(row["id"]),
                username=row["username"],
                email=row["email"],
                role=UserRole(row["role"]),
                is_active=bool(row["is_active"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                last_login=datetime.fromisoformat(row["last_login"]) if row.get("last_login") else None,
            )
            for row in rows
        ]
    
    async def update(self, user: UserInDB) -> UserInDB:
        """Update a user."""
        user.updated_at = _utcnow()
        
        data = {
            "email": user.email,
            "hashed_password": user.hashed_password,
            "role": user.role.value,
            "is_active": 1 if user.is_active else 0,
            "updated_at": user.updated_at.isoformat(),
            "last_login": user.last_login.isoformat() if user.last_login else None,
        }
        
        await self.db.update("users", data, "id = ?", (str(user.id),))
        return user
    
    async def update_last_login(self, user_id: UUID) -> None:
        """Update last login timestamp."""
        await self.db.update(
            "users",
            {"last_login": _utcnow().isoformat()},
            "id = ?",
            (str(user_id),)
        )
    
    async def delete(self, user_id: UUID) -> bool:
        """Delete a user."""
        count = await self.db.delete("users", "id = ?", (str(user_id),))
        return count > 0
    
    async def count(self) -> int:
        """Count total users."""
        row = await self.db.fetch_one("SELECT COUNT(*) as count FROM users")
        return row["count"] if row else 0
    
    async def has_admin(self) -> bool:
        """Check if any admin user exists."""
        row = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM users WHERE role = ?",
            (UserRole.ADMIN.value,)
        )
        return row["count"] > 0 if row else False
    
    # API Key methods
    
    async def create_api_key(
        self,
        user_id: UUID,
        name: str,
        key_hash: str,
        prefix: str,
        expires_at: datetime | None = None
    ) -> APIKey:
        """Create a new API key."""
        api_key = APIKey(
            user_id=user_id,
            name=name,
            key_hash=key_hash,
            prefix=prefix,
            expires_at=expires_at,
        )
        
        data = {
            "id": str(api_key.id),
            "user_id": str(api_key.user_id),
            "name": api_key.name,
            "key_hash": api_key.key_hash,
            "prefix": api_key.prefix,
            "created_at": api_key.created_at.isoformat(),
            "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
            "is_active": 1 if api_key.is_active else 0,
        }
        
        await self.db.insert("api_keys", data)
        return api_key
    
    async def get_api_key_by_prefix(self, prefix: str) -> APIKey | None:
        """Get an API key by its prefix."""
        row = await self.db.fetch_one(
            "SELECT * FROM api_keys WHERE prefix = ? AND is_active = 1",
            (prefix,)
        )
        
        if not row:
            return None
        
        return APIKey(
            id=UUID(row["id"]),
            user_id=UUID(row["user_id"]),
            name=row["name"],
            key_hash=row["key_hash"],
            prefix=row["prefix"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_used=datetime.fromisoformat(row["last_used"]) if row.get("last_used") else None,
            expires_at=datetime.fromisoformat(row["expires_at"]) if row.get("expires_at") else None,
            is_active=bool(row["is_active"]),
        )
    
    async def get_api_keys_by_user(self, user_id: UUID) -> list[APIKey]:
        """Get all API keys for a user."""
        rows = await self.db.fetch_all(
            "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
            (str(user_id),)
        )
        
        return [
            APIKey(
                id=UUID(row["id"]),
                user_id=UUID(row["user_id"]),
                name=row["name"],
                key_hash=row["key_hash"],
                prefix=row["prefix"],
                created_at=datetime.fromisoformat(row["created_at"]),
                last_used=datetime.fromisoformat(row["last_used"]) if row.get("last_used") else None,
                expires_at=datetime.fromisoformat(row["expires_at"]) if row.get("expires_at") else None,
                is_active=bool(row["is_active"]),
            )
            for row in rows
        ]
    
    async def update_api_key_last_used(self, key_id: UUID) -> None:
        """Update API key last used timestamp."""
        await self.db.update(
            "api_keys",
            {"last_used": _utcnow().isoformat()},
            "id = ?",
            (str(key_id),)
        )
    
    async def delete_api_key(self, key_id: UUID) -> bool:
        """Delete an API key."""
        count = await self.db.delete("api_keys", "id = ?", (str(key_id),))
        return count > 0
    
    def _row_to_user(self, row: dict[str, Any]) -> UserInDB:
        """Convert a database row to a UserInDB model."""
        return UserInDB(
            id=UUID(row["id"]),
            username=row["username"],
            email=row["email"],
            hashed_password=row["hashed_password"],
            role=UserRole(row["role"]),
            is_active=bool(row["is_active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_login=datetime.fromisoformat(row["last_login"]) if row.get("last_login") else None,
        )
