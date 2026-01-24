"""Search routes for metadata-based document search."""

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from dedox.api.deps import CurrentUser
from dedox.core.config import get_settings
from dedox.db import get_database

logger = logging.getLogger(__name__)

router = APIRouter()


# Semantic search removed - use Open WebUI for RAG and document search
# Keeping metadata search routes below


@router.get("/metadata")
async def search_by_metadata(
    current_user: CurrentUser,
    sender: str | None = None,
    document_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    urgency: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    """Search documents by metadata fields."""
    db = await get_database()

    # Build query
    # Note: Documents table doesn't have user_id column currently
    conditions = ["1 = 1"]
    params: list[Any] = []

    if sender:
        conditions.append("d.metadata LIKE ?")
        params.append(f'%"sender"%"{sender}"%')

    if document_type:
        conditions.append("d.metadata LIKE ?")
        params.append(f'%"document_type"%"{document_type}"%')

    if date_from:
        conditions.append("d.created_at >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("d.created_at <= ?")
        params.append(date_to)

    if urgency:
        conditions.append("d.metadata LIKE ?")
        params.append(f'%"urgency"%"{urgency}"%')

    query = f"""
        SELECT d.id, d.filename, d.original_filename, d.metadata, d.created_at
        FROM documents d
        WHERE {' AND '.join(conditions)}
        ORDER BY d.created_at DESC
        LIMIT ?
    """
    params.append(limit)

    rows = await db.fetch_all(query, tuple(params))
    
    results = []
    for row in rows:
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        
        # Filter by amount if specified
        if amount_min is not None or amount_max is not None:
            amount = metadata.get("total_amount")
            if amount is None:
                continue
            try:
                amount = float(amount)
                if amount_min and amount < amount_min:
                    continue
                if amount_max and amount > amount_max:
                    continue
            except (ValueError, TypeError):
                continue
        
        results.append({
            "document_id": row["id"],
            "filename": row["original_filename"],
            "metadata": metadata,
            "created_at": row["created_at"],
        })
    
    return {
        "results": results,
        "total": len(results),
    }


@router.get("/recent")
async def get_recent_documents(
    current_user: CurrentUser,
    limit: int = Query(10, ge=1, le=50),
):
    """Get recently processed documents."""
    db = await get_database()

    # Note: Documents table doesn't have user_id column currently
    # This returns all documents, ordered by most recent
    query = """
        SELECT id, filename, original_filename, status, metadata,
               created_at, processed_at
        FROM documents
        ORDER BY COALESCE(processed_at, created_at) DESC
        LIMIT ?
    """

    rows = await db.fetch_all(query, (limit,))

    return {
        "documents": [
            {
                "id": row["id"],
                "filename": row["original_filename"],
                "status": row["status"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "created_at": row["created_at"],
                "processed_at": row["processed_at"],
            }
            for row in rows
        ],
    }


# Similar document search removed - use Open WebUI for semantic search
