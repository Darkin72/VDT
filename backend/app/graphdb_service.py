import json
import os
from typing import Any

import requests

from app import logging_service


def discover_repository(graphdb_url: str) -> str:
    try:
        response = requests.get(f"{graphdb_url}/rest/repositories", timeout=(3, 10))
        response.raise_for_status()
        repositories = response.json()
    except (requests.RequestException, json.JSONDecodeError):
        return ""

    if not isinstance(repositories, list):
        return ""

    for repository in repositories:
        repository_id = str(repository.get("id", "")).strip()
        if repository_id and repository_id.upper() != "SYSTEM":
            return repository_id
    return ""


def build_repository_url() -> str:
    explicit_url = os.getenv("GRAPHDB_REPOSITORY_URL", "").strip().rstrip("/")
    if explicit_url:
        return explicit_url

    graphdb_url = os.getenv("GRAPHDB_URL", "http://157.10.53.238:21150").rstrip("/")
    repository = os.getenv("GRAPHDB_REPOSITORY", "").strip()
    if not repository:
        repository = discover_repository(graphdb_url) or "DBPEDIA"
    return f"{graphdb_url}/repositories/{repository}"


def configured_query_timeout_seconds() -> int:
    return int(os.getenv("GRAPHDB_QUERY_TIMEOUT_SECONDS", "200"))


def effective_query_timeout_seconds(
    *,
    remaining_question_seconds: float | None = None,
    remaining_graphdb_queries: int = 1,
) -> int:
    configured_timeout = configured_query_timeout_seconds()
    if remaining_question_seconds is None:
        return configured_timeout

    finalization_reserve = int(os.getenv("QUESTION_FINALIZATION_RESERVE_SECONDS", "240"))
    usable_seconds = int(remaining_question_seconds) - finalization_reserve
    if usable_seconds <= 0:
        return 0

    query_count = max(1, remaining_graphdb_queries)
    return max(1, min(configured_timeout, usable_seconds // query_count))


def query(sparql: str, *, timeout_seconds: int | None = None) -> dict[str, Any]:
    repository_url = build_repository_url()
    timeout_seconds = timeout_seconds if timeout_seconds is not None else configured_query_timeout_seconds()
    logging_service.agent_step(
        "graphdb.query_start",
        {"repository_url": repository_url, "timeout_seconds": timeout_seconds, "sparql": sparql},
    )
    response = requests.post(
        repository_url,
        data={"query": sparql},
        headers={"Accept": "application/sparql-results+json"},
        timeout=(5, timeout_seconds),
    )
    try:
        response.raise_for_status()
    except requests.HTTPError:
        logging_service.agent_step(
            "graphdb.query_error",
            {
                "status_code": response.status_code,
                "reason": response.reason,
                "body": response.text,
            },
            limit=5000,
        )
        raise
    result = response.json()
    logging_service.agent_step("graphdb.query_result_summary", summarize_result(result))
    return result


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    if "boolean" in result:
        return {"type": "ask", "value": bool(result["boolean"])}

    bindings = result.get("results", {}).get("bindings", [])
    sample = bindings[:2] if isinstance(bindings, list) else []
    return {
        "type": "select",
        "vars": result.get("head", {}).get("vars", []),
        "row_count": len(bindings) if isinstance(bindings, list) else 0,
        "sample": sample,
    }


def has_result(result: dict[str, Any]) -> bool:
    if "boolean" in result:
        return True
    bindings = result.get("results", {}).get("bindings", [])
    return isinstance(bindings, list) and len(bindings) > 0


def format_result(result: dict[str, Any]) -> str:
    if "boolean" in result:
        return json.dumps({"ask": bool(result["boolean"])}, ensure_ascii=False)

    variables = result.get("head", {}).get("vars", [])
    bindings = result.get("results", {}).get("bindings", [])
    max_rows = int(os.getenv("GRAPHDB_MAX_ROWS", "20"))
    rows: list[dict[str, str]] = []
    for binding in bindings[:max_rows]:
        row: dict[str, str] = {}
        for variable in variables:
            value = binding.get(variable, {})
            row[variable] = str(value.get("value", ""))
        rows.append(row)
    return json.dumps({"rows": rows, "row_count": len(bindings)}, ensure_ascii=False, indent=2)
