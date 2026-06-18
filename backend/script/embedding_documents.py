import argparse
import json
import os
import sys
import time
import uuid
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback for minimal environments.
    tqdm = None

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except Exception:  # noqa: BLE001 - dotenv is optional in containers.
    pass

from qdrant_client import QdrantClient, models

DEFAULT_DOCUMENTS = REPO_DIR / "Ontology" / "normalized" / "embedding_documents.jsonl"
DEFAULT_COLLECTION = "ontology_normalized"
DEFAULT_SOURCE_NAME = "dbpedia-ontology-normalized"

EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
EMBEDDING_MAX_TOKENS = int(os.getenv("EMBEDDING_MAX_TOKENS", "32768"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
EMBEDDING_CONCURRENCY = int(os.getenv("EMBEDDING_CONCURRENCY", "64"))
# EMBEDDING_CONCURRENCY = 1
EMBEDDING_REQUEST_TIMEOUT_SECONDS = int(os.getenv("EMBEDDING_REQUEST_TIMEOUT_SECONDS", "60"))
EMBEDDING_MAX_RETRIES = int(os.getenv("EMBEDDING_MAX_RETRIES", "3"))
EMBEDDING_RETRY_DELAY_SECONDS = int(os.getenv("EMBEDDING_RETRY_DELAY_SECONDS", "1"))


def running_in_container() -> bool:
    return Path("/.dockerenv").exists() or os.getenv("RUNNING_IN_CONTAINER", "").strip().lower() in {"1", "true", "yes"}


def default_qdrant_url() -> str:
    configured = os.getenv("QDRANT_URL", "").strip()
    if configured:
        if not running_in_container():
            return configured.replace("http://qdrant:6333", "http://localhost:6363")
        return configured
    return "http://qdrant:6333" if running_in_container() else "http://localhost:6363"


def embedding_api_url() -> str:
    base_url = os.getenv("EMBEDDING_API_BASE_URL", "http://host.docker.internal:8001").strip().rstrip("/")
    if not running_in_container():
        base_url = base_url.replace("host.docker.internal", "localhost")
    path = os.getenv("EMBEDDING_API_PATH", "/v1/embeddings").strip() or "/v1/embeddings"
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base_url}{path}"


def embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-4B").strip() or "Qwen/Qwen3-Embedding-4B"


def send_dimensions_parameter() -> bool:
    return os.getenv("EMBEDDING_SEND_DIMENSIONS", "false").strip().lower() in {"1", "true", "yes"}


def iter_jsonl(path: Path, *, limit: int = 0) -> Iterator[dict[str, Any]]:
    yielded = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
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
            yielded += 1
            if limit and yielded >= limit:
                return


def batched(items: Iterator[dict[str, Any]], batch_size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def batch_windows(items: Iterator[dict[str, Any]], *, batch_size: int, concurrency: int) -> Iterator[list[list[dict[str, Any]]]]:
    window: list[list[dict[str, Any]]] = []
    for batch in batched(items, batch_size):
        window.append(batch)
        if len(window) >= concurrency:
            yield window
            window = []
    if window:
        yield window

def count_jsonl_records(path: Path, *, limit: int = 0) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
                if limit and count >= limit:
                    return count
    return count

def estimate_jsonl_records(path: Path, *, sample_lines: int) -> int | None:
    if sample_lines <= 0:
        return None
    sampled_records = 0
    sampled_bytes = 0
    with path.open("rb") as handle:
        for raw_line in handle:
            if raw_line.strip():
                sampled_records += 1
                sampled_bytes += len(raw_line)
                if sampled_records >= sample_lines:
                    break
    if sampled_records == 0 or sampled_bytes == 0:
        return None
    average_bytes = sampled_bytes / sampled_records
    return max(1, round(path.stat().st_size / average_bytes))

def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def embed_batch(texts: Sequence[str]) -> list[list[float]]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = os.getenv("EMBEDDING_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict[str, Any] = {"model": embedding_model(), "input": list(texts)}
    if send_dimensions_parameter():
        payload["dimensions"] = EMBEDDING_DIMENSIONS

    last_error: Exception | None = None
    for attempt in range(1, EMBEDDING_MAX_RETRIES + 1):
        try:
            response = requests.post(
                embedding_api_url(),
                json=payload,
                headers=headers,
                timeout=(10, EMBEDDING_REQUEST_TIMEOUT_SECONDS),
            )
            response.raise_for_status()
            return parse_embeddings(response.json(), expected_count=len(texts))
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt >= EMBEDDING_MAX_RETRIES:
                break
            time.sleep(EMBEDDING_RETRY_DELAY_SECONDS)
    raise RuntimeError(f"Embedding batch failed after {EMBEDDING_MAX_RETRIES} attempts: {last_error}")


def iter_embedded_batches(window: list[list[dict[str, Any]]]) -> Iterator[list[tuple[dict[str, Any], list[float]]]]:
    with ThreadPoolExecutor(max_workers=EMBEDDING_CONCURRENCY) as executor:
        future_to_batch = {
            executor.submit(embed_batch, [str(document["text"]) for document in batch]): batch
            for batch in window
        }
        for future in as_completed(future_to_batch):
            batch = future_to_batch[future]
            vectors = future.result()
            yield [
                (document, vector)
                for document, vector in zip(batch, vectors, strict=True)
            ]


def parse_embeddings(payload: dict[str, Any], *, expected_count: int) -> list[list[float]]:
    data = payload.get("data", [])
    if not isinstance(data, list) or len(data) != expected_count:
        raise ValueError("Embedding API returned an unexpected data length")

    vectors: list[list[float]] = []
    for item in sorted(data, key=lambda value: int(value.get("index", 0)) if isinstance(value, dict) else 0):
        embedding = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(embedding, list) or not embedding:
            raise ValueError("Embedding API returned an invalid embedding")
        vector = [float(value) for value in embedding]
        if len(vector) > EMBEDDING_DIMENSIONS:
            vector = vector[:EMBEDDING_DIMENSIONS]
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise ValueError(f"Embedding vector dimension mismatch: expected {EMBEDDING_DIMENSIONS}, got {len(vector)}")
        vectors.append(vector)
    return vectors


def make_payload(document: dict[str, Any], *, source_name: str) -> dict[str, Any]:
    payload = dict(document.get("payload") or {})
    for key in ("id", "parent_id", "chunk_index", "chunk_count", "kind", "uri", "curie", "label"):
        if key in document:
            payload[key] = document[key]
    payload["text"] = str(document["text"])
    payload["source_name"] = source_name
    return payload


def point_id(document_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, document_id))


def ensure_collection(client: QdrantClient, collection: str, *, recreate: bool) -> None:
    if recreate:
        client.recreate_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(size=EMBEDDING_DIMENSIONS, distance=models.Distance.COSINE),
        )
        print(f"Recreated collection {collection} with vector size {EMBEDDING_DIMENSIONS}")
        return
    if not collection_exists(client, collection):
        client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(size=EMBEDDING_DIMENSIONS, distance=models.Distance.COSINE),
        )
        print(f"Created collection {collection} with vector size {EMBEDDING_DIMENSIONS}")


def collection_exists(client: QdrantClient, collection: str) -> bool:
    if hasattr(client, "collection_exists"):
        return bool(client.collection_exists(collection))
    collections = client.get_collections().collections
    return any(item.name == collection for item in collections)


def upsert_embedded(client: QdrantClient, collection: str, embedded: list[tuple[dict[str, Any], list[float]]], *, source_name: str) -> None:
    points = [
        models.PointStruct(
            id=point_id(str(document["id"])),
            vector=vector,
            payload=make_payload(document, source_name=source_name),
        )
        for document, vector in embedded
    ]
    if points:
        client.upsert(collection_name=collection, points=points, wait=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed normalized ontology JSONL documents into Qdrant concurrently.")
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS, help="JSONL file. Each line is one document.")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Qdrant collection name")
    parser.add_argument("--qdrant-url", default=default_qdrant_url(), help="Qdrant URL")
    parser.add_argument("--source-name", default=DEFAULT_SOURCE_NAME)
    parser.add_argument("--limit", type=int, default=0, help="Only embed the first N documents")
    parser.add_argument("--recreate", action="store_true", help="Recreate the collection before upserting")
    parser.add_argument(
        "--estimate-sample-lines",
        type=int,
        default=10000,
        help="Sample this many JSONL records to estimate total file size and full-index ETA. Use 0 to disable.",
    )
    args = parser.parse_args()

    if not args.documents.exists():
        raise FileNotFoundError(args.documents)
    if args.limit < 0:
        raise ValueError("--limit must be greater than or equal to 0")

    client = QdrantClient(url=args.qdrant_url)
    ensure_collection(client, args.collection, recreate=args.recreate)

    print(f"Documents: {args.documents}")
    print(f"Qdrant URL: {args.qdrant_url}")
    print(f"Collection: {args.collection}")
    print(f"Embedding API: {embedding_api_url()}")
    print(
        "Embedding config: "
        f"dimensions={EMBEDDING_DIMENSIONS} max_tokens={EMBEDDING_MAX_TOKENS} "
        f"batch_size={EMBEDDING_BATCH_SIZE} concurrency={EMBEDDING_CONCURRENCY} "
        f"timeout={EMBEDDING_REQUEST_TIMEOUT_SECONDS}s retries={EMBEDDING_MAX_RETRIES} "
        f"retry_delay={EMBEDDING_RETRY_DELAY_SECONDS}s"
    )

    indexed = 0
    started_at = time.monotonic()
    total = count_jsonl_records(args.documents, limit=max(0, args.limit))
    estimated_total = estimate_jsonl_records(args.documents, sample_lines=max(0, args.estimate_sample_lines))
    if estimated_total is not None:
        print(f"Estimated full file documents: {estimated_total:,}")
    progress = tqdm(
        total=total,
        unit="doc",
        desc="Embedding",
        dynamic_ncols=True,
        file=sys.stdout,
    ) if tqdm is not None else None
    documents = iter_jsonl(args.documents, limit=max(0, args.limit))
    try:
        for window in batch_windows(documents, batch_size=EMBEDDING_BATCH_SIZE, concurrency=EMBEDDING_CONCURRENCY):
            for embedded in iter_embedded_batches(window):
                upsert_embedded(client, args.collection, embedded, source_name=args.source_name)
                indexed += len(embedded)
                elapsed = max(time.monotonic() - started_at, 0.001)
                rate = indexed / elapsed
                full_eta = ""
                if estimated_total is not None and rate > 0:
                    full_eta = f" full_eta={format_duration(estimated_total / rate)}"
                if progress is not None:
                    progress.update(len(embedded))
                    progress.set_postfix_str(f"collection={args.collection} rate={rate:.1f}/s{full_eta}")
                else:
                    print(f"Indexed {indexed}/{total} documents into {args.collection} elapsed={elapsed:.1f}s rate={rate:.1f}/s{full_eta}", flush=True)
    finally:
        if progress is not None:
            progress.close()

    print(f"Done. Indexed {indexed} documents.")


if __name__ == "__main__":
    main()
