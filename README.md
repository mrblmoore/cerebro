# Cerebro

A local-first operational copilot for technical support. Cerebro follows the case
you are working on, keeps the relevant knowledge one search away, and — if you
connect an AI model — drafts your case notes for you.

Everything runs on your machine: your database, your documents, your audio.

---

## Install

Clone and run the setup script — it needs Python 3.9+ and nothing else:

```bash
git clone https://github.com/mrblmoore/cerebro.git
cd cerebro
./setup.sh          # or double-click setup.bat on Windows
```

It checks your Python, builds a virtual environment, installs dependencies and
prepares the database. No database server to stand up, no API key required.

<details>
<summary>Prefer a one-click installer (CerebroSetup.exe)?</summary>

The repository builds a self-contained `CerebroSetup.exe` — no Python, no
prerequisites, per-user install — but a build has to be **published** before it
appears on the [releases page](https://github.com/mrblmoore/cerebro/releases),
which requires a Windows build run (PyInstaller can only build on the platform it
targets). Two ways to get one:

- **Publish a release.** Push a `v*` tag (e.g. `v0.3.0`); the
  `build-windows.yml` GitHub Action builds it on a Windows runner, smoke-tests
  it, and attaches `CerebroSetup.exe` to the release.
- **Build it locally on a Windows machine.** Run
  `packaging\build_windows.bat` — details in [docs/PACKAGING.md](docs/PACKAGING.md).

Until then, use the source install above — it has every feature.
</details>

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
Detects Salesforce, Dynamics 365, ServiceNow and Zendesk cases, and the SharePoint documents
you open, keeping Cerebro in sync with the tab you are on. Load it from
`browser-extension/src` — see [docs/INSTALL.md](docs/INSTALL.md).

**Outlook & Teams**
Power Automate drops each message into a folder as JSON; Cerebro ingests it,
triages what is urgent, links it to the right case, and drafts replies that go
back out through a second flow. No Microsoft credentials live in Cerebro.
See [docs/POWER_AUTOMATE.md](docs/POWER_AUTOMATE.md).

**Documents**
Reads the Word, Excel, PowerPoint and PDF files you have open — including
SharePoint files, via the locally synced copy — so you can ask about them. It
can edit Word and Excel too, with a dry run first and a backup every time.
See [docs/DOCUMENTS.md](docs/DOCUMENTS.md).

**Microsoft Copilot (optional)**
Connect a Copilot Studio agent and reach Cerebro from Teams or your phone. The
two sides share a OneDrive folder — no code package, no app registration. Cerebro
stays the primary assistant; this is an addition, not a replacement. See
[docs/COPILOT_STUDIO.md](docs/COPILOT_STUDIO.md).

**Second brain & secretary**
Remembers how you solved things and recalls it when drafting; learns your
writing voice; takes plain-language tasks ("keep this doc updated daily under my
name") and runs them on a schedule; and nudges you about unanswered mail and
cases resolved but never written up. See
[docs/SECOND_BRAIN.md](docs/SECOND_BRAIN.md).

**API** — `http://localhost:8000/docs`
Everything the UI does is a documented REST endpoint.

---

## Optional extras

Cerebro works fully without any of these. Add them when you want them:

| Extra | What it adds | How |
|---|---|---|
| AI provider | Case summaries, troubleshooting steps, reply drafts, document Q&A | Settings → AI Provider. A local [Ollama](https://ollama.com) model needs no API key. |
| Cloud AI SDKs | Required for OpenAI and Amazon Bedrock | `pip install -r backend/requirements-ai.txt` |
| Outlook & Teams | Mail and chat as live context | Two Power Automate flows and a folder — [guide](docs/POWER_AUTOMATE.md) |
| PostgreSQL | Shared database for a team | `docker compose up -d postgres`, then Settings → Database |
| Qdrant | Vector search at scale | `docker compose up -d qdrant`, then Settings → Knowledge Search |
| Call transcription | Records and transcribes calls locally | `pip install -r desktop/requirements-audio.txt` |
| Screenpipe | Continuous desktop capture | Settings → Desktop Capture |

---

## How it works

```
Browser extension ─┐                                    Outlook / Teams
Desktop agent    ──┤                                          │
Document watcher ──┼──▶  Events  ──▶  Context Engine    Power Automate
Audio recorder   ──┘                       │                  │
                                           │            inbound folder
                    Documents  ────────────┤                  │
                    (Word, Excel, PDF)     ├◀─────────────────┘
                                           │
                                           ├──▶  Knowledge search (RAG)
                                           ├──▶  AI summaries & drafts (optional)
                                           └──▶  Suggestions
                                                    │
                                     Dashboard  ◀───┴───▶  Desktop widget
                                                    │
                                            outbound folder ──▶ Power Automate
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
| [docs/POWER_AUTOMATE.md](docs/POWER_AUTOMATE.md) | Outlook and Teams integration, flow by flow |
| [docs/DOCUMENTS.md](docs/DOCUMENTS.md) | Reading and editing Word, Excel and PDF |
| [docs/SECOND_BRAIN.md](docs/SECOND_BRAIN.md) | Memory, writing voice, tasks, nudges, activity capture |
| [docs/COPILOT_STUDIO.md](docs/COPILOT_STUDIO.md) | Connecting a Microsoft Copilot Studio agent |
| [docs/PACKAGING.md](docs/PACKAGING.md) | Building the Windows installer |
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
recordings stay on your machine — in `data/` for a source install, or
`%LOCALAPPDATA%\Cerebro` for the packaged one. Nothing is sent anywhere unless
you configure a cloud AI provider, and choosing Ollama keeps even that local.

The pieces that touch other systems stay deliberately narrow:

- **Outlook and Teams** reach Cerebro only through a folder of JSON files that
  Power Automate writes. Cerebro holds no Microsoft credentials and makes no
  calls to Microsoft 365. Replies wait for your approval before they leave.
- **The browser extension** reports recognised case and document pages. Tracking
  every tab is off by default, capturing page text is off by default, and any
  domain on your exclusion list is never read at all.
- **Documents** are read from disk when you open them. Edits are explicit,
  backed up, and refused while the file is open in Office.
