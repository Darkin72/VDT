import argparse
import os
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
except Exception:  # noqa: BLE001 - dotenv is optional in containers.
    pass

from app.rag import qdrant_store


def collection_exists(client: Any, collection: str) -> bool:
    if hasattr(client, "collection_exists"):
        return bool(client.collection_exists(collection))
    collections = client.get_collections().collections
    return any(item.name == collection for item in collections)


def collection_info(client: Any, collection: str) -> tuple[int | None, int | None]:
    try:
        info = client.get_collection(collection)
    except Exception:  # noqa: BLE001 - older servers may not expose full collection info.
        return None, None

    points_count = getattr(info, "points_count", None)
    vectors_count = getattr(info, "vectors_count", None)
    return (
        int(points_count) if points_count is not None else None,
        int(vectors_count) if vectors_count is not None else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete a whole Qdrant collection.")
    parser.add_argument("--collection", default=qdrant_store.qdrant_collection(), help="Qdrant collection name")
    parser.add_argument("--url", default=qdrant_store.qdrant_url(), help="Qdrant URL")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete the collection. Without this, only prints what would be deleted.",
    )
    args = parser.parse_args()

    os.environ["QDRANT_URL"] = args.url
    os.environ["QDRANT_COLLECTION"] = args.collection

    client = qdrant_store.make_client()
    collection = args.collection

    print(f"Qdrant URL: {args.url}")
    print(f"Collection: {collection}")

    if not collection_exists(client, collection):
        print("Collection does not exist.")
        return

    points_count, vectors_count = collection_info(client, collection)
    if points_count is not None:
        print(f"Reported points: {points_count}")
    if vectors_count is not None:
        print(f"Reported vectors: {vectors_count}")

    if not args.yes:
        print("Dry run only. Re-run with --yes to delete this collection.")
        return

    client.delete_collection(collection_name=collection)
    print(f"Done. Deleted collection {collection}.")


if __name__ == "__main__":
    main()
