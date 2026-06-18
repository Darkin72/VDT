import argparse
import json
import os
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except Exception:  # noqa: BLE001 - dotenv is optional in containers.
    pass

from app.rag import embedding_service, qdrant_store


DEFAULT_DOCUMENTS = REPO_DIR / "Ontology" / "benchmark_perfect" / "perfect_uri_documents.jsonl"
DEFAULT_COLLECTION = "ontology_benchmark_perfect"


def running_in_container() -> bool:
    return Path("/.dockerenv").exists() or os.getenv("RUNNING_IN_CONTAINER", "").strip().lower() in {"1", "true", "yes"}


def default_qdrant_url() -> str:
    configured = os.getenv("QDRANT_URL", "").strip()
    if configured:
        if not running_in_container():
            return configured.replace("http://qdrant:6333", "http://localhost:6363")
        return configured
    return "http://qdrant:6333" if running_in_container() else "http://localhost:6363"


def iter_jsonl(path: Path, *, limit: int = 0) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if limit and line_number > limit:
                break
            line = line.strip()
            if not line:
                continue
            document = json.loads(line)
            if not isinstance(document, dict):
                raise ValueError(f"Line {line_number} is not a JSON object")
            if not str(document.get("id") or "").strip():
                raise ValueError(f"Line {line_number} is missing id")
            if not str(document.get("text") or "").strip():
                raise ValueError(f"Line {line_number} is missing text")
            yield document


def batched(items: Iterable[dict[str, Any]], batch_size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def make_payload(document: dict[str, Any]) -> dict[str, Any]:
    payload = dict(document.get("payload") or {})
    for key in ("id", "parent_id", "chunk_index", "chunk_count", "kind", "uri", "curie", "label"):
        if key in document:
            payload[key] = document[key]
    payload["text"] = str(document["text"])
    return payload


def recreate_collection(vector_size: int) -> None:
    qdrant_store.recreate_collection(vector_size=vector_size)
    print(f"Recreated collection {qdrant_store.qdrant_collection()} with vector size {vector_size}")


def embed_documents(documents: Path, *, batch_size: int, limit: int, recreate: bool) -> int:
    indexed = 0
    collection_ready = not recreate
    for batch in batched(iter_jsonl(documents, limit=limit), max(1, batch_size)):
        vectors = embedding_service.embed_texts([str(document["text"]) for document in batch])
        if not collection_ready:
            recreate_collection(len(vectors[0]))
            collection_ready = True
        qdrant_store.upsert_points(
            (str(document["id"]), vector, make_payload(document))
            for document, vector in zip(batch, vectors, strict=True)
        )
        indexed += len(batch)
        print(f"Indexed {indexed} documents into {qdrant_store.qdrant_collection()}")
    return indexed


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed perfect benchmark JSONL documents into Qdrant.")
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS, help="JSONL file. Each line is one document.")
    parser.add_argument("--collection", default=os.getenv("QDRANT_COLLECTION", DEFAULT_COLLECTION), help="Qdrant collection name")
    parser.add_argument("--qdrant-url", default=default_qdrant_url(), help="Qdrant URL")
    parser.add_argument("--batch-size", type=int, default=8, help="Documents per embedding request")
    parser.add_argument("--limit", type=int, default=0, help="Only embed the first N documents")
    parser.add_argument("--recreate", action="store_true", help="Recreate the collection before upserting")
    args = parser.parse_args()

    os.environ["QDRANT_URL"] = args.qdrant_url
    os.environ["QDRANT_COLLECTION"] = args.collection

    print(f"Documents: {args.documents}")
    print(f"Qdrant URL: {args.qdrant_url}")
    print(f"Collection: {args.collection}")
    print(f"Embedding API: {embedding_service.embedding_api_url()}")

    indexed = embed_documents(
        args.documents,
        batch_size=args.batch_size,
        limit=max(0, args.limit),
        recreate=args.recreate,
    )
    print(f"Done. Indexed {indexed} documents.")


if __name__ == "__main__":
    main()
