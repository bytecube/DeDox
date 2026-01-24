"""
Database connection and initialization for DeDox.

Uses aiosqlite for async SQLite operations with the following features:

Security:
    - SQL injection prevention via parameterized queries
    - Table name whitelist to prevent dynamic table injection
    - Column name validation with regex pattern matching
    - Identifier length limits to prevent DoS

Performance:
    - WAL (Write-Ahead Logging) mode for better concurrency
    - Foreign keys enabled for referential integrity
    - Indexes on frequently queried columns

Schema:
    - users: Authentication and authorization
    - api_keys: API key management for programmatic access
    - documents: Document metadata and processing state
    - jobs: Processing job tracking
    - settings: Key-value configuration persistence
    - processing_logs: Audit trail for debugging
"""

import json
import logging
import os
import re
import secrets
import string
from pathlib import Path
from typing import Any

import aiosqlite

from dedox.core.config import get_settings

logger = logging.getLogger(__name__)

# Valid SQL identifier pattern (alphanumeric and underscore only)
_VALID_IDENTIFIER_PATTERN = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

# Allowed table names (whitelist)
_ALLOWED_TABLES = frozenset({
    'users', 'api_keys', 'documents', 'jobs', 'settings',
    'processing_logs'
})


def _validate_identifier(name: str, identifier_type: str = "identifier") -> str:
    """Validate and sanitize a SQL identifier (table or column name).

    Args:
        name: The identifier to validate
        identifier_type: Type for error messages ("table" or "column")

    Returns:
        The validated identifier

    Raises:
        ValueError: If the identifier is invalid
    """
    if not name:
        raise ValueError(f"Empty {identifier_type} name")

    if not _VALID_IDENTIFIER_PATTERN.match(name):
        raise ValueError(
            f"Invalid {identifier_type} name: {name!r}. "
            f"Must contain only alphanumeric characters and underscores."
        )

    # Length limit to prevent DoS
    if len(name) > 64:
        raise ValueError(f"{identifier_type.title()} name too long: {len(name)} chars (max 64)")

    return name


def _validate_table_name(table: str) -> str:
    """Validate a table name against the whitelist.

    Args:
        table: The table name to validate

    Returns:
        The validated table name

    Raises:
        ValueError: If the table name is not in the whitelist
    """
    _validate_identifier(table, "table")

    if table not in _ALLOWED_TABLES:
        raise ValueError(
            f"Unknown table: {table!r}. Allowed tables: {sorted(_ALLOWED_TABLES)}"
        )

    return table

# SQL schema for tables
SCHEMA = """
-- Users table
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login TEXT
);

-- API Keys table
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    prefix TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used TEXT,
    expires_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Documents table
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'paperless_webhook',
    original_path TEXT,
    processed_path TEXT,
    ocr_text TEXT,
    ocr_confidence REAL,
    ocr_language TEXT,
    file_hash TEXT,
    content_hash TEXT,
    paperless_id INTEGER,
    paperless_task_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    processed_at TEXT,
    metadata TEXT DEFAULT '{}',
    metadata_confidence TEXT DEFAULT '{}'
);

-- Jobs table
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    current_stage TEXT NOT NULL DEFAULT 'pending',
    progress_percent INTEGER NOT NULL DEFAULT 0,
    stages TEXT DEFAULT '[]',
    skipped_stages TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    result TEXT DEFAULT '{}',
    errors TEXT DEFAULT '[]',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- Settings table (for persisted configuration)
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_paperless_id ON documents(paperless_id);
CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(file_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_document_id ON jobs(document_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(prefix);
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
"""


class Database:
    """Async SQLite database wrapper."""
    
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._connection: aiosqlite.Connection | None = None
    
    async def connect(self) -> None:
        """Connect to the database."""
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._connection = await aiosqlite.connect(
            self.db_path,
            isolation_level=None  # Auto-commit mode
        )
        
        # Enable WAL mode for better concurrency
        settings = get_settings()
        if settings.database.wal_mode:
            await self._connection.execute("PRAGMA journal_mode=WAL")
        
        # Enable foreign keys
        await self._connection.execute("PRAGMA foreign_keys=ON")
        
        # Row factory for dict-like access
        self._connection.row_factory = aiosqlite.Row
        
        logger.info(f"Connected to database: {self.db_path}")
    
    async def disconnect(self) -> None:
        """Disconnect from the database."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Disconnected from database")
    
    async def init_schema(self) -> None:
        """Initialize database schema."""
        if not self._connection:
            raise RuntimeError("Database not connected")
        
        await self._connection.executescript(SCHEMA)
        logger.info("Database schema initialized")
    
    @property
    def connection(self) -> aiosqlite.Connection:
        """Get the database connection."""
        if not self._connection:
            raise RuntimeError("Database not connected")
        return self._connection
    
    async def execute(
        self,
        query: str,
        parameters: tuple | dict | None = None
    ) -> aiosqlite.Cursor:
        """Execute a query."""
        if parameters:
            return await self.connection.execute(query, parameters)
        return await self.connection.execute(query)
    
    async def execute_many(
        self,
        query: str,
        parameters: list[tuple | dict]
    ) -> aiosqlite.Cursor:
        """Execute a query with multiple parameter sets."""
        return await self.connection.executemany(query, parameters)
    
    async def fetch_one(
        self,
        query: str,
        parameters: tuple | dict | None = None
    ) -> dict[str, Any] | None:
        """Fetch a single row."""
        cursor = await self.execute(query, parameters)
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None
    
    async def fetch_all(
        self,
        query: str,
        parameters: tuple | dict | None = None
    ) -> list[dict[str, Any]]:
        """Fetch all rows."""
        cursor = await self.execute(query, parameters)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    
    async def insert(
        self,
        table: str,
        data: dict[str, Any]
    ) -> str:
        """Insert a row and return the id.

        Args:
            table: Table name (must be in allowed tables whitelist)
            data: Column name to value mapping

        Returns:
            The id of the inserted row

        Raises:
            ValueError: If table or column names are invalid
        """
        # Validate table name
        _validate_table_name(table)

        # Convert complex types to JSON and validate column names
        processed_data = {}
        for key, value in data.items():
            _validate_identifier(key, "column")
            if isinstance(value, (dict, list)):
                processed_data[key] = json.dumps(value)
            else:
                processed_data[key] = value

        columns = ", ".join(processed_data.keys())
        placeholders = ", ".join(["?" for _ in processed_data])
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

        await self.execute(query, tuple(processed_data.values()))
        return data.get("id", "")
    
    async def update(
        self,
        table: str,
        data: dict[str, Any],
        where: str,
        where_params: tuple
    ) -> int:
        """Update rows and return affected count.

        Args:
            table: Table name (must be in allowed tables whitelist)
            data: Column name to value mapping for SET clause
            where: WHERE clause (should use ? placeholders)
            where_params: Parameters for WHERE clause placeholders

        Returns:
            Number of affected rows

        Raises:
            ValueError: If table or column names are invalid
        """
        # Validate table name
        _validate_table_name(table)

        # Convert complex types to JSON and validate column names
        processed_data = {}
        for key, value in data.items():
            _validate_identifier(key, "column")
            if isinstance(value, (dict, list)):
                processed_data[key] = json.dumps(value)
            else:
                processed_data[key] = value

        set_clause = ", ".join([f"{k} = ?" for k in processed_data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {where}"

        cursor = await self.execute(
            query,
            tuple(processed_data.values()) + where_params
        )
        return cursor.rowcount
    
    async def delete(
        self,
        table: str,
        where: str,
        where_params: tuple
    ) -> int:
        """Delete rows and return affected count.

        Args:
            table: Table name (must be in allowed tables whitelist)
            where: WHERE clause (should use ? placeholders)
            where_params: Parameters for WHERE clause placeholders

        Returns:
            Number of affected rows

        Raises:
            ValueError: If table name is invalid
        """
        # Validate table name
        _validate_table_name(table)

        query = f"DELETE FROM {table} WHERE {where}"
        cursor = await self.execute(query, where_params)
        return cursor.rowcount


# Global database instance
_database: Database | None = None


async def get_database() -> Database:
    """Get the global database instance."""
    global _database
    if _database is None:
        settings = get_settings()
        _database = Database(settings.database.path)
        await _database.connect()
        await _database.init_schema()
    return _database


async def init_database() -> Database:
    """Initialize the database (for application startup)."""
    db = await get_database()

    # Create default admin user if no users exist
    await _create_default_admin(db)

    # Initialize additional tables
    await _init_additional_tables(db)

    return db


async def _init_additional_tables(db: Database) -> None:
    """Initialize additional tables that are not in the main schema."""
    from dedox.db.repositories.processing_log_repository import ProcessingLogRepository

    # Run schema migrations
    await _run_migrations(db)

    # Processing logs table
    log_repo = ProcessingLogRepository(db)
    await log_repo.ensure_table()


async def _run_migrations(db: Database) -> None:
    """Run database migrations for schema changes."""
    # Migration: Add skipped_stages column to jobs table
    try:
        await db.execute(
            "ALTER TABLE jobs ADD COLUMN skipped_stages TEXT DEFAULT '[]'"
        )
        logger.info("Migration: Added skipped_stages column to jobs table")
    except Exception:
        # Column already exists
        pass


def _generate_secure_password(length: int = 16) -> str:
    """Generate a cryptographically secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def _create_default_admin(db: Database) -> None:
    """Create default admin user if no users exist.

    Password is either taken from DEDOX_ADMIN_PASSWORD environment variable
    or generated randomly and printed to logs (only on first run).
    """
    from dedox.db.repositories.user_repository import UserRepository
    from dedox.models.user import UserCreate, UserRole

    repo = UserRepository(db)

    # Check if any users exist
    result = await db.fetch_one("SELECT COUNT(*) as count FROM users")
    if result and result["count"] > 0:
        return

    # Get password from environment or generate a secure one
    admin_password = os.environ.get("DEDOX_ADMIN_PASSWORD")
    password_was_generated = False

    if not admin_password:
        admin_password = _generate_secure_password()
        password_was_generated = True

    admin_email = os.environ.get("DEDOX_ADMIN_EMAIL", "admin@example.com")

    # Create default admin
    logger.info("Creating default admin user...")
    user_create = UserCreate(
        username="admin",
        email=admin_email,
        password=admin_password,
        role=UserRole.ADMIN,
    )

    await repo.create(user_create)

    if password_was_generated:
        logger.warning("=" * 60)
        logger.warning("DEFAULT ADMIN ACCOUNT CREATED")
        logger.warning(f"Username: admin")
        logger.warning(f"Password: {admin_password}")
        logger.warning("Please change this password immediately!")
        logger.warning("Set DEDOX_ADMIN_PASSWORD env var to specify password on startup.")
        logger.warning("=" * 60)
    else:
        logger.info("Default admin user created with password from DEDOX_ADMIN_PASSWORD")


async def close_database() -> None:
    """Close the database connection (for application shutdown)."""
    global _database
    if _database:
        await _database.disconnect()
        _database = None
