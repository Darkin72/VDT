import os
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:  # noqa: BLE001 - dotenv is optional for script use.
    pass

def running_in_container() -> bool:
    return Path("/.dockerenv").exists() or os.getenv("RUNNING_IN_CONTAINER", "").strip().lower() in {"1", "true", "yes"}


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

def embedding_output_dimensions() -> int | None:
    raw_value = os.getenv("EMBEDDING_OUTPUT_DIMENSIONS", os.getenv("EMBEDDING_DIMENSIONS", "1024")).strip()
    if not raw_value:
        return None
    return max(1, int(raw_value))

def send_dimensions_parameter() -> bool:
    return os.getenv("EMBEDDING_SEND_DIMENSIONS", "false").strip().lower() in {"1", "true", "yes"}

def adjust_embedding_dimensions(embedding: list[float]) -> list[float]:
    dimensions = embedding_output_dimensions()
    if dimensions is None or len(embedding) == dimensions:
        return embedding
    if len(embedding) > dimensions:
        return embedding[:dimensions]
    raise ValueError(f"Embedding vector dimension mismatch: expected at least {dimensions}, got {len(embedding)}")


def embed_texts(texts: list[str], *, timeout_seconds: int = 120) -> list[list[float]]:
    if not texts:
        return []

    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = os.getenv("EMBEDDING_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict[str, Any] = {"model": embedding_model(), "input": texts}
    dimensions = embedding_output_dimensions()
    if send_dimensions_parameter() and dimensions is not None:
        payload["dimensions"] = dimensions

    response = requests.post(
        embedding_api_url(),
        json=payload,
        headers=headers,
        timeout=(10, timeout_seconds),
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:2000]
        raise requests.HTTPError(f"{exc}; response body: {detail}", response=response) from exc
    payload: dict[str, Any] = response.json()
    data = payload.get("data", [])
    if not isinstance(data, list) or len(data) != len(texts):
        raise ValueError("Embedding API returned an unexpected data length")

    embeddings: list[list[float]] = []
    for item in data:
        embedding = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(embedding, list) or not embedding:
            raise ValueError("Embedding API returned an invalid embedding")
        embeddings.append(adjust_embedding_dimensions([float(value) for value in embedding]))
    return embeddings


def embed_text(text: str, *, timeout_seconds: int = 120) -> list[float]:
    return embed_texts([text], timeout_seconds=timeout_seconds)[0]
