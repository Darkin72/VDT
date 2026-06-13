import json
import re
from typing import Any

from app import graphdb_service, llm_service, logging_service

OPTIONS_BLOCK_PATTERN = re.compile(
    r"(?is)(?:c[aá]c\s+)?(?:(?:đ|d)[aá]p\s*[aá]n|answer\s+options?|options?)\s*:?\s*\n?.*$"
)
NUMBERED_OPTION_PATTERN = re.compile(r"(?m)^\s*(?:[1-5]|[A-Ea-e])[\).:-]\s+.+$")


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass

    fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    object_match = fenced_match or re.search(r"\{.*?\}", text, flags=re.DOTALL)
    if not object_match:
        return {}

    json_text = object_match.group(1) if fenced_match else object_match.group(0)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def strip_answer_options(text: str) -> str:
    stripped = OPTIONS_BLOCK_PATTERN.sub("", text).strip()
    stripped = NUMBERED_OPTION_PATTERN.sub("", stripped).strip()
    return stripped or text


def plan_graphdb_usage(message: str) -> dict[str, Any]:
    lookup_prompt = strip_answer_options(message)
    prompt = (
        "You are the central routing agent for a Vietnamese chatbot backed by GraphDB/DBpedia.\n"
        "Decide whether the user's core question needs GraphDB lookup before answering.\n\n"
        "Important boundary: if the original prompt is multiple-choice, do not pass answer options or option IDs to the SPARQL coder. "
        "The SPARQL coder should only retrieve neutral facts needed to answer the core question. The central agent will compare facts with choices later.\n\n"
        "Use GraphDB for factual questions about entities, relationships, attributes, dates, places, people, organizations, "
        "classes, or multiple-choice questions that likely require stored knowledge.\n"
        "Do not use GraphDB for greetings, chitchat, writing/transformation tasks, pure math, or requests that can be answered "
        "without factual lookup.\n\n"
        "Return only valid JSON with this schema:\n"
        "{\"use_graphdb\":true,\"query_description\":\"neutral fact lookup description without answer options\",\"reason\":\"short reason\"}\n"
        "If GraphDB is not needed, set use_graphdb=false and query_description=\"\".\n\n"
        f"Original prompt:\n{message}\n\n"
        f"Core question without answer options:\n{lookup_prompt}"
    )
    raw_text = llm_service.complete_text(
        [
            llm_service.system_message("You are a central agent. Return only routing JSON."),
            llm_service.user_message(prompt),
        ]
    )
    logging_service.verbose_text("central_agent.raw_routing_response", raw_text)
    data = extract_json_object(raw_text)
    if not data:
        decision = {"use_graphdb": True, "query_description": lookup_prompt, "reason": "routing_json_parse_failed"}
        logging_service.agent_step("central_agent.parsed_decision", decision)
        return decision

    use_graphdb = parse_bool(data.get("use_graphdb"))
    query_description = strip_answer_options(str(data.get("query_description", "")).strip())
    if use_graphdb and not query_description:
        query_description = lookup_prompt

    decision = {
        "use_graphdb": use_graphdb,
        "query_description": query_description,
        "reason": str(data.get("reason", "")).strip(),
    }
    logging_service.agent_step("central_agent.parsed_decision", decision)
    return decision


def direct_answer_messages(message: str) -> list[llm_service.ChatMessage]:
    return [
        llm_service.system_message(
            "You are a helpful Vietnamese chatbot. Answer clearly and concisely. "
            "For multiple-choice prompts, return only valid JSON with this schema: "
            "{\"answer\":\"1\",\"evidence\":[\"short evidence or fallback reason\"]}. "
            "The answer value must be the selected option ID as a string."
        ),
        llm_service.user_message(message),
    ]


def is_multiple_choice_prompt(message: str) -> bool:
    return bool(NUMBERED_OPTION_PATTERN.search(message))


def normalize_answer_evidence_response(
    message: str,
    raw_text: str,
    graphdb_result: dict[str, Any] | None = None,
    graphdb_error: str | None = None,
) -> str:
    if not is_multiple_choice_prompt(message):
        return raw_text

    data = extract_json_object(raw_text)
    answer = str(data.get("answer", "")).strip() if data else ""
    if not re.fullmatch(r"[1-5]", answer):
        return raw_text

    raw_evidence = data.get("evidence")
    evidence = [str(item).strip() for item in raw_evidence if str(item).strip()] if isinstance(raw_evidence, list) else []
    if not evidence:
        if graphdb_result and graphdb_service.has_result(graphdb_result):
            evidence = [f"SPARQL evidence: {graphdb_service.format_result(graphdb_result)}"]
        elif graphdb_error:
            evidence = [f"No usable SPARQL evidence ({graphdb_error}); selected as a best-effort guess."]
        else:
            evidence = ["No usable SPARQL evidence; selected as a best-effort guess."]

    return json.dumps({"answer": answer, "evidence": evidence}, ensure_ascii=False)


def final_answer_messages(
    message: str,
    decision: dict[str, Any],
    sparql: str,
    graphdb_result: dict[str, Any] | None,
    graphdb_error: str | None = None,
) -> list[llm_service.ChatMessage]:
    if graphdb_result:
        result_text = graphdb_service.format_result(graphdb_result)
    elif graphdb_error:
        result_text = graphdb_error
    else:
        result_text = "NO_GRAPHDB_RESULT"
    return [
        llm_service.system_message(
            "You are the central answering agent for a Vietnamese chatbot. "
            "For multiple-choice prompts, always return only valid JSON with this schema: "
            "{\"answer\":\"1\",\"evidence\":[\"short evidence text\"]}. "
            "The answer value must be the selected option ID as a string. "
            "Evidence must be a JSON array of short strings. "
            "If GraphDB rows are provided, compare those facts with the original choices and choose only the option supported by GraphDB evidence. "
            "In that case, every evidence item must cite a concrete value, entity, relationship, date, count, or literal from the GraphDB result. "
            "If GraphDB returned no result, no SPARQL was executed, or a timeout/error status is provided, then and only then choose the option that seems most likely from general knowledge. "
            "For fallback answers, set evidence to a single item explaining that there was no usable SPARQL evidence and that the choice is a best-effort guess. "
            "For non-multiple-choice prompts, answer clearly and include GraphDB evidence when available."
        ),
        llm_service.user_message(
            f"Original prompt including any answer choices:\n{message}\n\n"
            f"Central routing decision:\n{json.dumps(decision, ensure_ascii=False)}\n\n"
            f"Executed SPARQL:\n{sparql or 'NO_SPARQL_EXECUTED'}\n\n"
            f"GraphDB result:\n{result_text}\n\n"
            "Return the final answer to the user."
        ),
    ]
