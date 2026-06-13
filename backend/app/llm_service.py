import json
import os
from collections.abc import Iterator

import requests

ChatMessage = dict[str, str]


def system_message(content: str) -> ChatMessage:
    return {"role": "system", "content": content}


def user_message(content: str) -> ChatMessage:
    return {"role": "user", "content": content}


def build_api_url() -> str:
    base_url = os.getenv("CHAT_API_BASE_URL", "").rstrip("/")
    api_path = os.getenv("CHAT_API_PATH", "/v1/chat/completions")
    if not base_url:
        return ""
    return f"{base_url}{api_path if api_path.startswith('/') else '/' + api_path}"


def build_browser_headers() -> dict[str, str]:
    headers = {
        "Accept": "text/event-stream, application/json, text/plain, */*",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "DNT": "1",
        "Origin": os.getenv("CHAT_API_ORIGIN", "https://chat.openai.com"),
        "Pragma": "no-cache",
        "Referer": os.getenv("CHAT_API_REFERER", "https://chat.openai.com/"),
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": os.getenv(
            "CHAT_API_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36",
        ),
        "sec-ch-ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    api_key = os.getenv("CHAT_API_KEY", "")
    if api_key:
        auth_header = os.getenv("CHAT_API_AUTH_HEADER", "Authorization")
        auth_prefix = os.getenv("CHAT_API_AUTH_PREFIX", "Bearer")
        headers[auth_header] = f"{auth_prefix} {api_key}".strip()

    extra_headers = os.getenv("CHAT_API_EXTRA_HEADERS", "")
    if extra_headers:
        headers.update(json.loads(extra_headers))

    return headers


def build_payload(messages: list[ChatMessage], *, stream: bool = True) -> dict:
    return {
        "model": os.getenv("CHAT_MODEL", "default"),
        "messages": messages,
        "temperature": float(os.getenv("CHAT_TEMPERATURE", "0.2")),
        "stream": stream,
    }


def parse_stream_line(line: str) -> str:
    if not line:
        return ""
    if line.startswith(("event:", "id:", "retry:")):
        return ""
    if line.startswith("data:"):
        line = line.removeprefix("data:").strip()
    if line == "[DONE]":
        return ""

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return line

    choices = data.get("choices") or []
    if choices:
        delta = choices[0].get("delta") or {}
        message = choices[0].get("message") or {}
        return delta.get("content") or message.get("content") or choices[0].get("text") or ""

    return data.get("content") or data.get("text") or ""


def stream_messages(messages: list[ChatMessage]) -> Iterator[str]:
    with requests.post(
        build_api_url(),
        headers=build_browser_headers(),
        json=build_payload(messages, stream=True),
        stream=True,
        timeout=(10, 120),
    ) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=True):
            chunk = parse_stream_line(raw_line or "")
            if chunk:
                yield chunk


def complete_text(messages: list[ChatMessage]) -> str:
    return "".join(stream_messages(messages)).strip()