# Cerebro

A local-first operational copilot for technical support. Cerebro follows the case
you are working on, keeps the relevant knowledge one search away, and — if you
connect an AI model — drafts your case notes for you.

Everything runs on your machine: your database, your documents, your audio.

---

## Install

**Windows** — download or clone the repository, then double-click **`setup.bat`**.

**macOS / Linux**

```bash
git clone https://github.com/mrblmoore/cerebro.git
cd cerebro
./setup.sh
```

That is the whole install. It checks your Python, builds a virtual environment,
installs dependencies and prepares the database — nothing else to configure,
no database server to stand up, no API key required.

## Run

| | Windows | macOS / Linux |
|---|---|---|
| Start Cerebro | double-click `start.bat` | `./cerebro.sh start` |
| Desktop widget | double-click `widget.bat` | `./cerebro.sh widget` |
| Check the install | `cerebro.bat doctor` | `./cerebro.sh doctor` |

The first start opens a **setup wizard** in your browser. It takes about a
minute, and every answer can be changed later under **Settings**.

Prefer a menu? Run `cerebro.bat` (or `./cerebro.sh`) with no arguments.

---

## What you get

**Dashboard** — `http://localhost:8000`
Live context, recent events, knowledge search, system status and the activity log.

**Desktop widget**
A small always-on-top panel with your current case, the suggested next action and
instant knowledge search. Drag it anywhere, snap it to an edge, collapse it to a
single strip while you work, or set it to start with Windows.

**Browser extension**
Detects Salesforce, ServiceNow and Zendesk cases and keeps Cerebro in sync.
Load it from `browser-extension/src` — see [docs/INSTALL.md](docs/INSTALL.md).

**API** — `http://localhost:8000/docs`
Everything the UI does is a documented REST endpoint.

---

## Optional extras

Cerebro works fully without any of these. Add them when you want them:

| Extra | What it adds | How |
|---|---|---|
| AI provider | Case summaries, troubleshooting steps | Settings → AI Provider. A local [Ollama](https://ollama.com) model needs no API key. |
| OpenAI SDK | Required for the OpenAI provider | `pip install -r backend/requirements-ai.txt` |
| PostgreSQL | Shared database for a team | `docker compose up -d postgres`, then Settings → Database |
| Qdrant | Vector search at scale | `docker compose up -d qdrant`, then Settings → Knowledge Search |
| Call transcription | Records and transcribes calls locally | `pip install -r desktop/requirements-audio.txt` |
| Screenpipe | Continuous desktop capture | Settings → Desktop Capture |

---

## How it works

```
Browser extension ─┐
Desktop agent    ──┼──▶  Events  ──▶  Context Engine  ──▶  Context state
Audio recorder   ─┘                        │
                                           ├──▶  Knowledge search (RAG)
                                           ├──▶  AI summaries (optional)
                                           └──▶  Suggestions
                                                    │
                                     Dashboard  ◀───┴───▶  Desktop widget
```

Events describe what happened (`CRM_CASE_OPENED`, `CALL_STARTED`,
`REMOTE_SESSION_CONNECTED`, …). The Context Engine folds them into a single
state — which case, which customer, on a call or not — and every surface reads
from that one state.

---

## Documentation

| Document | Contents |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Full install guide, per platform, plus troubleshooting |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting and what it does |
| [docs/WIDGET.md](docs/WIDGET.md) | The desktop widget in detail |
| [docs/SCHEMA.md](docs/SCHEMA.md) | Database schema |
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | Integration patterns |
| [docs/LOGGING.md](docs/LOGGING.md) | Log format and locations |
| [MVP_SUMMARY.md](MVP_SUMMARY.md) | Architecture and roadmap |

---

## Something not working?

```bash
./cerebro.sh doctor        # or: cerebro.bat doctor
```

`doctor` checks your Python, the virtual environment, every dependency and each
running service, then tells you exactly what to do about anything it finds.

---

## Privacy

Cerebro is local-first by design. The database, indexed documents, logs and any
recordings stay in `data/` inside the project folder. Nothing is sent anywhere
unless you configure a cloud AI provider — and choosing Ollama keeps even that
on your machine.
