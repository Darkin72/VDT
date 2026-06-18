import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from app.rag.ontology_retriever import normalize_term


DOCUMENTS_PATH = Path(os.getenv("LOOKUP_DOCUMENTS_PATH", "/app/Ontology/normalized/embedding_documents.jsonl"))
DB_PATH = Path(os.getenv("LOOKUP_DB_PATH", "/app/Ontology/normalized/embedding_lookup.sqlite"))

app = FastAPI(title="VDT ontology lookup", version="1.0.0")


def database_exists() -> bool:
    return DB_PATH.exists() and DB_PATH.stat().st_size > 0


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": database_exists(),
        "documents_path": str(DOCUMENTS_PATH),
        "db_path": str(DB_PATH),
        "message": "Build locally with: python backend\\script\\build_embedding_lookup_db.py build --recreate",
    }


@app.get("/lookup")
def lookup(
    q: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=100),
    include_document: bool = True,
) -> dict[str, Any]:
    if not database_exists():
        raise HTTPException(status_code=503, detail=f"Lookup DB not found: {DB_PATH}")

    term = normalize_term(q)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT d.* FROM terms t
            JOIN documents d ON d.doc_id = t.doc_id
            WHERE t.term = ? ORDER BY d.line_number LIMIT ?
            """,
            (term, limit),
        ).fetchall()
    finally:
        connection.close()

    results: list[dict[str, Any]] = []
    handle = DOCUMENTS_PATH.open("rb") if include_document else None
    try:
        for row in rows:
            item = {
                "line_number": row["line_number"],
                "byte_offset": row["byte_offset"],
                "id": row["doc_id"],
                "kind": row["kind"],
                "label": row["label"],
                "curie": row["curie"],
                "uri": row["uri"],
            }
            if handle is not None:
                handle.seek(row["byte_offset"])
                item["document"] = json.loads(handle.readline())
            results.append(item)
    finally:
        if handle is not None:
            handle.close()

    return {"query": q, "normalized_query": term, "count": len(results), "results": results}