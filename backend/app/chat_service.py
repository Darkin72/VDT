import json
import os
import time
from collections.abc import Iterator
from typing import Any

import requests

from app import graphdb_service, llm_service, logging_service
from app.agents import answer_formatter, central, sparql as sparql_agent

MAX_SPARQL_ATTEMPTS = 5


def question_timeout_seconds() -> int:
    return int(os.getenv("QUESTION_TIMEOUT_SECONDS", "1200"))


def remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def stream_with_optional_verbose_logging(messages: list[llm_service.ChatMessage], step: str) -> Iterator[str]:
    chunks: list[str] = []
    for chunk in llm_service.stream_messages(messages):
        chunks.append(chunk)
        yield chunk
    logging_service.verbose_text(step, "".join(chunks).strip())


def normalized_final_stream(
    message: str,
    raw_text: str,
    history: dict[str, Any],
) -> Iterator[str]:
    graphdb_result, graphdb_error = answer_formatter.latest_graphdb_evidence(history)
    yield central.normalize_answer_evidence_response(message, raw_text, graphdb_result, graphdb_error)


def execute_sparql_step(
    message: str,
    history: dict[str, Any],
    query_description: str,
    attempt: int,
    graphdb_timeout_seconds: int,
) -> None:
    sparql = ""
    graphdb_result = None
    graphdb_error = None
    try:
        sparql = sparql_agent.generate_sparql(message, query_description, history=history)
        if sparql:
            graphdb_result = graphdb_service.query(sparql, timeout_seconds=graphdb_timeout_seconds)
        else:
            graphdb_error = "SPARQL_GENERATION_EMPTY_OR_REJECTED"
    except requests.Timeout as exc:
        graphdb_error = "GRAPHDB_TIMEOUT: GraphDB query exceeded the configured timeout."
        logging_service.agent_step(
            "agent_pipeline.graphdb_timeout",
            {"type": type(exc).__name__, "message": str(exc), "sparql": sparql},
            limit=5000,
        )
    except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
        graphdb_error = f"GRAPHDB_ERROR: {type(exc).__name__}: {exc}"
        logging_service.agent_step("agent_pipeline.error", {"type": type(exc).__name__, "message": str(exc)})

    central.add_history_step(
        history,
        "sparql_execution",
        {
            "attempt": attempt,
            "query_description": query_description,
            "sparql": sparql,
            "graphdb_timeout_seconds": graphdb_timeout_seconds,
            "result": graphdb_result,
            "result_summary": graphdb_service.summarize_result(graphdb_result) if graphdb_result else None,
            "error": graphdb_error,
        },
    )


def agent_stream(message: str) -> Iterator[str]:
    try:
        deadline = time.monotonic() + question_timeout_seconds()
        logging_service.agent_text("user.prompt", message)
        history = central.build_history(message)
        routing_decision = central.plan_graphdb_usage(message)
        central.add_history_step(history, "central_routing", routing_decision)

        sparql_attempts = 0
        if routing_decision["use_graphdb"]:
            query_description = routing_decision["query_description"]
            while sparql_attempts < MAX_SPARQL_ATTEMPTS:
                sparql_attempts += 1
                graphdb_timeout_seconds = graphdb_service.effective_query_timeout_seconds(
                    remaining_question_seconds=remaining_seconds(deadline),
                    remaining_graphdb_queries=MAX_SPARQL_ATTEMPTS - sparql_attempts + 1,
                )
                if graphdb_timeout_seconds <= 0:
                    central.add_history_step(
                        history,
                        "sparql_execution",
                        {
                            "attempt": sparql_attempts,
                            "query_description": query_description,
                            "sparql": "",
                            "graphdb_timeout_seconds": 0,
                            "result": None,
                            "result_summary": None,
                            "error": "QUESTION_TIMEOUT: skipped GraphDB to reserve time for final answer.",
                        },
                    )
                    break

                execute_sparql_step(
                    message,
                    history,
                    query_description,
                    sparql_attempts,
                    graphdb_timeout_seconds,
                )

                next_action = central.decide_next_action(
                    message,
                    history,
                    sparql_attempts,
                    MAX_SPARQL_ATTEMPTS,
                )
                central.add_history_step(history, "central_next_action", next_action)

                if next_action["action"] != "sparql":
                    break
                query_description = next_action["query_description"]

        raw_answer = answer_formatter.format_final_answer(message, history)
        yield from normalized_final_stream(message, raw_answer, history)
    except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
        logging_service.logger.exception("agent_pipeline.llm_error")
        yield f"Xin loi, hien tai backend khong goi duoc model API ({type(exc).__name__})."
