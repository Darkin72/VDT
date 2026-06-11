# VDT Chatbot Stack

Stack gồm 3 service:

- `backend`: FastAPI + LangChain message objects, gọi model API riêng bằng `requests`, stream response tại `POST /api/chat`.
- `frontend`: Svelte/Vite chat UI tại `http://localhost:5173`.
- `graphdb`: Ontotext GraphDB tại `http://localhost:7200`, mount thư mục `Ontology` vào import folder.

## Chạy bằng Docker Compose

```bash
cd docker
docker compose up --build
```

Mở:

- Frontend: http://localhost:5173
- Backend health: http://localhost:8000/health
- GraphDB: http://localhost:7200

## Cấu hình model API riêng

Sao chép file env mẫu:

```bash
cp backend/.env-example backend/.env
```

Cập nhật `backend/.env` theo API của bạn:

```env
CHAT_API_BASE_URL=https://your-model-provider.example.com
CHAT_API_PATH=/v1/chat/completions
CHAT_API_KEY=your_api_key_here
CHAT_API_AUTH_HEADER=Authorization
CHAT_API_AUTH_PREFIX=Bearer
CHAT_MODEL=your-model-name
```

Backend gửi request theo format chat-completions streaming tương thích OpenAI, nhưng không dùng OpenAI SDK. Header mặc định được set giống browser: `User-Agent`, `Accept`, `Accept-Language`, `Origin`, `Referer`, `Sec-Fetch-*`, `sec-ch-ua*`. Nếu API cần header riêng, thêm JSON vào:

```env
CHAT_API_EXTRA_HEADERS={"X-Custom-Header":"value"}
```

Nếu chưa có `CHAT_API_BASE_URL`, backend trả lời HTTP 500 để lỗi cấu hình lộ ra ngay.
