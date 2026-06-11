import json
import os
from typing import Iterator

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

load_dotenv()

app = FastAPI(title="VDT Chatbot API", version="0.1.0")

frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin, "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "graphdb_url": os.getenv("GRAPHDB_URL", "http://graphdb:7200"),
        "chat_api_base_url": os.getenv("CHAT_API_BASE_URL", "not-configured"),
    }


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


def build_payload(message: str) -> dict:
    messages = [
        SystemMessage(content="Bạn là một chatbot tiếng Việt hữu ích, trả lời ngắn gọn và rõ ràng."),
        HumanMessage(content=message),
    ]
    return {
        "model": os.getenv("CHAT_MODEL", "default"),
        "messages": [{"role": msg.type.replace("human", "user"), "content": msg.content} for msg in messages],
        "temperature": float(os.getenv("CHAT_TEMPERATURE", "0.2")),
        "stream": True,
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


def llm_stream(message: str) -> Iterator[str]:
    api_url = build_api_url()
    with requests.post(
        api_url,
        headers=build_browser_headers(),
        json=build_payload(message),
        stream=True,
        timeout=(10, 120),
    ) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=True):
            chunk = parse_stream_line(raw_line or "")
            if chunk:
                yield chunk

@app.post("/api/chat")
def chat(payload: ChatRequest) -> StreamingResponse:
    if not build_api_url():
        raise HTTPException(status_code=500, detail="Chưa cấu hình CHAT_API_BASE_URL")

    return StreamingResponse(
        llm_stream(payload.message),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


