# VDT Chatbot Stack

The stack has 3 services:

- `backend`: FastAPI backend that calls a custom chat-completions-compatible model API with `requests`, queries GraphDB first, and streams responses at `POST /api/chat`.
- `frontend`: Svelte/Vite chat UI at `http://localhost:5173`.
- `graphdb`: Ontotext GraphDB at `http://localhost:7200`, with the `Ontology` folder mounted as the import folder.

## Run With Docker Compose

```bash
cd docker
docker compose up --build
```

Open:

- Frontend: http://localhost:5173
- Backend health: http://localhost:8000/health
- GraphDB: http://localhost:7200

## Configure The Model API

Copy the sample env file:

```bash
cp backend/.env-example backend/.env
```

Update `backend/.env` for your API:

```env
CHAT_API_BASE_URL=https://your-model-provider.example.com
CHAT_API_PATH=/v1/chat/completions
CHAT_API_KEY=your_api_key_here
CHAT_API_AUTH_HEADER=Authorization
CHAT_API_AUTH_PREFIX=Bearer
CHAT_MODEL=your-model-name
GRAPHDB_REPOSITORY=vdt
GRAPHDB_QUERY_TIMEOUT_SECONDS=150
```

The backend uses a two-agent flow. GraphDB query timeout defaults to 150 seconds. The central agent first decides whether GraphDB is needed and writes a precise query description. If lookup is needed, the SPARQL coder agent turns that description into a read-only SELECT or ASK query. The backend executes the query, sends the GraphDB result back to the central agent, and the central agent writes the final answer. If GraphDB is not needed, or if lookup fails/returns no rows, the central agent can answer without GraphDB.

The model API request uses an OpenAI-compatible chat-completions streaming format, but it does not use the OpenAI SDK. Browser-like headers are sent by default: `User-Agent`, `Accept`, `Accept-Language`, `Origin`, `Referer`, `Sec-Fetch-*`, and `sec-ch-ua*`. If the API requires custom headers, add JSON to:

```env
CHAT_API_EXTRA_HEADERS={"X-Custom-Header":"value"}
```

If `CHAT_API_BASE_URL` is missing, the backend returns HTTP 500 so configuration errors are visible immediately.