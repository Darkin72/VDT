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

    graphdb_url = os.getenv("GRAPHDB_URL", "http://graphdb:7200").rstrip("/")
    repository = os.getenv("GRAPHDB_REPOSITORY", "").strip()
    if not repository:
        repository = discover_repository(graphdb_url) or "vdt"
    return f"{graphdb_url}/repositories/{repository}"


def query(sparql: str) -> dict[str, Any]:
    repository_url = build_repository_url()
    timeout_seconds = int(os.getenv("GRAPHDB_QUERY_TIMEOUT_SECONDS", "150"))
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