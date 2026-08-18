# Configuration

Cerebro runs with no configuration at all. Every setting below has a working
default; you only need this page when you want to change one.

The easiest route is **Settings** in the dashboard (`http://localhost:8000/settings`)
— grouped into tabs, with a **Test connection** button for anything that talks to
another service. Saving writes `backend/.env` for you.

You can also edit `backend/.env` by hand. It is generated with every option
listed and commented out at its default value, so the file doubles as
documentation: uncomment a line to override it, delete the file to reset
everything.

---

## General

| Setting | Default | Notes |
|---|---|---|
| `APP_NAME` | `Cerebro` | Shown in the UI and API docs |
| `HOST` | `127.0.0.1` | Keep as-is for a local-only install. `0.0.0.0` exposes Cerebro on your network |
| `PORT` | `8000` | |
| `ENVIRONMENT` | `development` | |
| `DEBUG` | `false` | Verbose errors and auto-reload |
| `CORS_ORIGINS` | `*` | Comma-separated origins. `*` keeps the extension and widget working |

## Database

| Setting | Default | Notes |
|---|---|---|
| `DATABASE_URL` | SQLite in `data/cerebro.db` | Set to `postgresql+psycopg://user:pass@host:5432/cerebro` for PostgreSQL |
| `SQLALCHEMY_ECHO` | `false` | Log every SQL statement |

Changing the database needs a restart. Install the driver first:
`pip install -r backend/requirements-postgres.txt`.

## AI Provider

Optional. With `LLM_PROVIDER=none` Cerebro still tracks context, events and
knowledge — you only lose generated summaries and troubleshooting steps.

| Setting | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `none` | `none`, `openai`, `ollama` or `qwen` |
| `OPENAI_API_KEY` | — | Needs `pip install -r backend/requirements-ai.txt` |
| `OPENAI_MODEL` | `gpt-4o-mini` | |
| `OPENAI_BASE_URL` | — | For Azure OpenAI or any OpenAI-compatible gateway |
| `OLLAMA_URL` | `http://localhost:11434` | Local models — no key, nothing leaves the machine |
| `OLLAMA_MODEL` | `llama3.1` | |
| `QWEN_API_URL` / `QWEN_API_KEY` / `QWEN_MODEL` | — | Qwen-compatible chat completions endpoint |
| `LLM_TEMPERATURE` | `0.7` | |
| `LLM_MAX_TOKENS` | `500` | |
| `LLM_TIMEOUT` | `60` | Seconds |

## Knowledge Search

| Setting | Default | Notes |
|---|---|---|
| `VECTOR_BACKEND` | `auto` | `auto` uses Qdrant when reachable, otherwise the built-in store |
| `QDRANT_URL` | — | e.g. `http://localhost:6333` |
| `QDRANT_API_KEY` | — | |
| `EMBEDDING_PROVIDER` | `local` | `local` works offline; `openai` matches far more accurately |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | |

The built-in embedder is a hashed bag-of-words projection: no model download and
no network, and good enough to find “the article about this error code”. Switch
to OpenAI embeddings for genuine semantic matching — then run
**Settings → Knowledge Search → Reindex all documents**, since existing vectors
belong to the old embedding space.

## Desktop Capture

| Setting | Default | Notes |
|---|---|---|
| `SCREENPIPE_ENABLED` | `false` | Optional [Screenpipe](https://github.com/mediar-ai/screenpipe) integration |
| `SCREENPIPE_URL` | `http://localhost:3030` | |

## Logging

| Setting | Default | Notes |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARN` or `ERROR` |
| `CEREBRO_LOG_PATH` | `data/logs/cerebro.log` | Rotates at 5 MB, keeps 3 files |
| `LOG_TO_STDOUT` | `true` | |

---

## Widget preferences

The desktop widget keeps its own settings per user, separate from the server:
`%APPDATA%\Cerebro\widget.json` on Windows, `~/.config/cerebro/widget.json`
elsewhere. Change them from the widget's **☰ → Widget preferences**, or delete
the file to start fresh.

## Extension settings

The browser extension stores its API URL and per-CRM toggles in Chrome's synced
storage. Open them from the extension's options page.

---

## Environment variables

Anything in `.env` can also be set as an ordinary environment variable, which
takes precedence. Useful for containers and CI:

```bash
DATABASE_URL=postgresql+psycopg://... LOG_LEVEL=DEBUG python cerebro.py start
```
