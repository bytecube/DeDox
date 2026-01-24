"""
Repository for Document operations.
"""

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


def _utcnow() -> datetime:
    """Return current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)

from dedox.db.database import Database
from dedox.models.document import Document, DocumentCreate, DocumentStatus


class DocumentRepository:
    """Repository for Document CRUD operations."""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def create(self, doc_or_create: DocumentCreate | Document, original_path: str | None = None) -> Document:
        """Create a new document.

        Args:
            doc_or_create: Either a DocumentCreate schema or a full Document object
            original_path: Path to original file (required for DocumentCreate, optional for Document)

        Returns:
            The created Document
        """
        if isinstance(doc_or_create, Document):
            doc = doc_or_create
        else:
            if original_path is None:
                raise ValueError("original_path is required when using DocumentCreate")
            doc = Document(
                filename=doc_or_create.filename,
                original_filename=doc_or_create.filename,
                content_type=doc_or_create.content_type,
                file_size=doc_or_create.file_size,
                source=doc_or_create.source,
                original_path=original_path,
            )

        data = {
            "id": str(doc.id),
            "filename": doc.filename,
            "original_filename": doc.original_filename,
            "content_type": doc.content_type,
            "file_size": doc.file_size,
            "source": doc.source,
            "original_path": doc.original_path,
            "paperless_id": doc.paperless_id,
            "paperless_task_id": doc.paperless_task_id,
            "status": doc.status.value,
            "created_at": doc.created_at.isoformat(),
            "updated_at": doc.updated_at.isoformat(),
            "metadata": json.dumps(doc.metadata),
            "metadata_confidence": json.dumps(doc.metadata_confidence),
        }

        await self.db.insert("documents", data)
        return doc
    
    async def get_by_id(self, doc_id: UUID) -> Document | None:
        """Get a document by ID."""
        row = await self.db.fetch_one(
            "SELECT * FROM documents WHERE id = ?",
            (str(doc_id),)
        )
        
        if not row:
            return None
        
        return self._row_to_document(row)
    
    async def get_by_hash(self, file_hash: str) -> Document | None:
        """Get a document by file hash (for duplicate detection)."""
        row = await self.db.fetch_one(
            "SELECT * FROM documents WHERE file_hash = ?",
            (file_hash,)
        )
        
        if not row:
            return None
        
        return self._row_to_document(row)
    
    async def get_by_paperless_id(self, paperless_id: int) -> Document | None:
        """Get a document by Paperless-ngx ID."""
        row = await self.db.fetch_one(
            "SELECT * FROM documents WHERE paperless_id = ?",
            (paperless_id,)
        )
        
        if not row:
            return None
        
        return self._row_to_document(row)
    
    async def get_documents(
        self,
        status: DocumentStatus | None = None,
        limit: int = 100,
        offset: int = 0
    ) -> list[Document]:
        """Get documents with optional filtering."""
        conditions = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status.value)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        rows = await self.db.fetch_all(
            f"""
            SELECT * FROM documents 
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset)
        )
        
        return [self._row_to_document(row) for row in rows]
    
    async def update(self, doc: Document) -> Document:
        """Update a document."""
        doc.updated_at = _utcnow()
        
        data = {
            "filename": doc.filename,
            "original_path": doc.original_path,
            "processed_path": doc.processed_path,
            "ocr_text": doc.ocr_text,
            "ocr_confidence": doc.ocr_confidence,
            "ocr_language": doc.ocr_language,
            "file_hash": doc.file_hash,
            "content_hash": doc.content_hash,
            "paperless_id": doc.paperless_id,
            "paperless_task_id": doc.paperless_task_id,
            "status": doc.status.value,
            "updated_at": doc.updated_at.isoformat(),
            "processed_at": doc.processed_at.isoformat() if doc.processed_at else None,
            "metadata": json.dumps(doc.metadata),
            "metadata_confidence": json.dumps(doc.metadata_confidence),
        }

        await self.db.update("documents", data, "id = ?", (str(doc.id),))
        return doc
    
    async def update_by_id(self, doc_id: str, updates: dict) -> bool:
        """Update a document by ID with a dictionary of updates."""
        updates["updated_at"] = _utcnow().isoformat()
        await self.db.update("documents", updates, "id = ?", (doc_id,))
        return True
    
    async def delete(self, doc_id: UUID) -> bool:
        """Delete a document."""
        count = await self.db.delete("documents", "id = ?", (str(doc_id),))
        return count > 0
    
    async def count_by_status(self) -> dict[str, int]:
        """Count documents by status."""
        rows = await self.db.fetch_all(
            "SELECT status, COUNT(*) as count FROM documents GROUP BY status"
        )
        return {row["status"]: row["count"] for row in rows}
    
    async def search_by_content(
        self,
        query: str,
        limit: int = 20
    ) -> list[Document]:
        """Search documents by OCR content."""
        rows = await self.db.fetch_all(
            """
            SELECT * FROM documents 
            WHERE ocr_text LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (f"%{query}%", limit)
        )
        
        return [self._row_to_document(row) for row in rows]
    
    async def list_with_pagination(
        self,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        **kwargs,
    ) -> tuple[list[Document], int]:
        """List documents with pagination and filtering."""
        conditions = []
        params: list[Any] = []

        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Get total count
        count_row = await self.db.fetch_one(
            f"SELECT COUNT(*) as count FROM documents WHERE {where_clause}",
            tuple(params)
        )
        total = count_row["count"] if count_row else 0
        
        # Get paginated results
        offset = (page - 1) * page_size
        rows = await self.db.fetch_all(
            f"""
            SELECT * FROM documents 
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (page_size, offset)
        )
        
        documents = [self._row_to_document(row) for row in rows]
        return documents, total

    def _row_to_document(self, row: dict[str, Any]) -> Document:
        """Convert a database row to a Document model."""
        return Document(
            id=UUID(row["id"]),
            filename=row["filename"],
            original_filename=row["original_filename"],
            content_type=row["content_type"],
            file_size=row["file_size"],
            source=row["source"],
            original_path=row.get("original_path"),
            processed_path=row.get("processed_path"),
            ocr_text=row.get("ocr_text"),
            ocr_confidence=row.get("ocr_confidence"),
            ocr_language=row.get("ocr_language"),
            file_hash=row.get("file_hash"),
            content_hash=row.get("content_hash"),
            paperless_id=row.get("paperless_id"),
            paperless_task_id=row.get("paperless_task_id"),
            status=DocumentStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            processed_at=datetime.fromisoformat(row["processed_at"]) if row.get("processed_at") else None,
            metadata=json.loads(row.get("metadata", "{}")),
            metadata_confidence=json.loads(row.get("metadata_confidence", "{}")),
        )
