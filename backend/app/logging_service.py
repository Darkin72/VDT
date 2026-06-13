import json
import logging
import os
from typing import Any

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
AGENT_LOG_VERBOSE = os.getenv("AGENT_LOG_VERBOSE", "false").strip().lower() in {"1", "true", "yes"}

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger("vdt.agents")


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


def agent_step(step: str, payload: Any, *, limit: int = 3000) -> None:
    logger.info("%s\n%s", step, _safe_json(payload, limit=limit))


def agent_text(step: str, text: str, *, limit: int = 3000) -> None:
    logger.info("%s\n%s", step, _shorten_text(text, limit=limit))


def verbose_step(step: str, payload: Any, *, limit: int = 3000) -> None:
    if AGENT_LOG_VERBOSE:
        agent_step(step, payload, limit=limit)


def verbose_text(step: str, text: str, *, limit: int = 3000) -> None:
    if AGENT_LOG_VERBOSE:
        agent_text(step, text, limit=limit)