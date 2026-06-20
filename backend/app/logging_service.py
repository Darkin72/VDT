import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime
from typing import Any

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
AGENT_LOG_VERBOSE = os.getenv("AGENT_LOG_VERBOSE", "false").strip().lower() in {"1", "true", "yes"}

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger("vdt.agents")
_trace_events: ContextVar[list[dict[str, Any]] | None] = ContextVar("trace_events", default=None)


def _shorten_text(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"


def _safe_json(value: Any, limit: int = 3000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        text = str(value)
    return _shorten_text(text, limit=limit)


def start_trace() -> tuple[Any, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    token = _trace_events.set(events)
    return token, events


def stop_trace(token: Any) -> None:
    try:
        _trace_events.reset(token)
    except ValueError:
        _trace_events.set(None)


def append_trace_event(step: str, payload: Any, *, limit: int = 3000) -> None:
    events = _trace_events.get()
    if events is None:
        return
    events.append(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "step": step,
            "detail": _safe_json(payload, limit=limit),
        }
    )




def trace_step(step: str, payload: Any, *, limit: int = 3000) -> None:
    append_trace_event(step, payload, limit=limit)
    if AGENT_LOG_VERBOSE:
        logger.info("%s\n%s", step, _safe_json(payload, limit=limit))

def trace_text(step: str, text: str, *, limit: int = 3000) -> None:
    append_trace_event(step, text, limit=limit)
    if AGENT_LOG_VERBOSE:
        logger.info("%s\n%s", step, _shorten_text(text, limit=limit))

def agent_step(step: str, payload: Any, *, limit: int = 3000) -> None:
    append_trace_event(step, payload, limit=limit)
    logger.info("%s\n%s", step, _safe_json(payload, limit=limit))


def agent_text(step: str, text: str, *, limit: int = 3000) -> None:
    append_trace_event(step, text, limit=limit)
    logger.info("%s\n%s", step, _shorten_text(text, limit=limit))


def verbose_step(step: str, payload: Any, *, limit: int = 3000) -> None:
    if AGENT_LOG_VERBOSE:
        agent_step(step, payload, limit=limit)


def verbose_text(step: str, text: str, *, limit: int = 3000) -> None:
    if AGENT_LOG_VERBOSE:
        agent_text(step, text, limit=limit)
