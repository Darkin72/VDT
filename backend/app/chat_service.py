import json
from collections.abc import Iterator

import requests

from app import central_agent, graphdb_service, llm_service, logging_service, sparql_agent


def stream_with_optional_verbose_logging(messages: list[llm_service.ChatMessage], step: str) -> Iterator[str]:
    chunks: list[str] = []
    for chunk in llm_service.stream_messages(messages):
        chunks.append(chunk)
        yield chunk
    logging_service.verbose_text(step, "".join(chunks).strip())


def normalized_final_stream(
    message: str,
    messages: list[llm_service.ChatMessage],
    step: str,
    graphdb_result: dict | None = None,
    graphdb_error: str | None = None,
) -> Iterator[str]:
    raw_text = llm_service.complete_text(messages)
    logging_service.verbose_text(step, raw_text)
    yield central_agent.normalize_answer_evidence_response(message, raw_text, graphdb_result, graphdb_error)


def agent_stream(message: str) -> Iterator[str]:
    try:
        logging_service.agent_text("user.prompt", message)
        decision = central_agent.plan_graphdb_usage(message)
        if not decision["use_graphdb"]:
            yield from normalized_final_stream(
                message,
                central_agent.direct_answer_messages(message),
                "central_agent.final_direct_answer",
            )
            return

        sparql = ""
        graphdb_result = None
        graphdb_error = None
        try:
            sparql = sparql_agent.generate_sparql(message, decision["query_description"])
            graphdb_result = graphdb_service.query(sparql) if sparql else None
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

        if graphdb_result and graphdb_service.has_result(graphdb_result):
            yield from normalized_final_stream(
                message,
                central_agent.final_answer_messages(message, decision, sparql, graphdb_result),
                "central_agent.final_graphdb_answer",
                graphdb_result,
            )
            return

        yield from normalized_final_stream(
            message,
            central_agent.final_answer_messages(message, decision, sparql, None, graphdb_error),
            "central_agent.final_fallback_answer",
            None,
            graphdb_error,
        )
    except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
        logging_service.logger.exception("agent_pipeline.llm_error")
        yield f"Xin loi, hien tai backend khong goi duoc model API ({type(exc).__name__})."
