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

Setup finishes by opening a short **wizard** — the same one the Windows
installer runs as its last step. It checks what is installed, prepares the
database, connects a model and tests each one, so "installed" and "ready to
use" are the same moment. Reopen it any time with `cerebro.bat configure`, or
change any answer later under **Settings**.

Prefer a menu? Run `cerebro.bat` (or `./cerebro.sh`) with no arguments.

---

## What you get

**Dashboard** — `http://localhost:8000`
Live context, recent events, knowledge search, system status and the activity log.

**Desktop app & tray**
A modern always-on-top window with a streaming Ask transcript, live progress
while Cerebro searches and works, source citations, and before/after approval
cards. Closing it leaves Cerebro running in the system tray, where an animated
brain shows what it is doing. See [docs/WIDGET.md](docs/WIDGET.md).

**Add-ons — connects to anything new, by itself**
Ask for something Cerebro can't reach yet ("what's PROJ-142 in Jira?") and it
finds a connector in the MCP Registry, adds it, and carries on. It can also add
any website you sign in to, and learn procedures you teach it. Anything that
changes something still waits for your approval. See [docs/ADDONS.md](docs/ADDONS.md).

**Agents — several Cerebros, one brain**
A Grok-style side panel of agents, such as an Inbox assistant, a Case
researcher and a KB writer. Each has its own role, chats, jobs and face, and
they all share one memory. Jobs keep a plan and a run log, ask you when they
need to, and wait for your approval. Agents can watch pages for changes,
suggest ideas, and work on projects with milestones. See
[docs/AGENTS.md](docs/AGENTS.md).

**Keep it updated — in one step**
Give Cerebro a SharePoint page, an internal site, a Word or Excel file, or a
Windows program, and say how to keep it up to date ("from the export, whenever
it changes"). It connects it, makes the job, and runs the first update with
your approval. It remembers how it did it, and once it works you can let it
update by itself. Every automatic change is logged with screenshots. See
[docs/AGENTS.md](docs/AGENTS.md#keep-it-updated).

**Windows desktop apps**
Cerebro can open and work in your Windows programs: read the window, type,
click and press keys. Saving, sending and confirming wait for your approval.
See [docs/ADDONS.md](docs/ADDONS.md#desktop-apps).

**Secretary**
Home → Today shows your agenda, to-dos and follow-ups, and a morning
briefing. Cerebro books and moves meetings with your approval and briefs you
before each one. It tracks what you're waiting on and what you promised, and
drafts the chaser. It takes notes from calls and fills in your Word and Excel
templates. See [docs/SECRETARY.md](docs/SECRETARY.md).

**Faces**
Pick the pixel brain, an original anime-style face that reacts and lip-syncs
as it speaks, or load your own Live2D or VRM model. See
[docs/FACES.md](docs/FACES.md).

**Talk to it**
Hold **Ctrl+Space** anywhere (or click the mic), say what you want, let go.
Speech is turned into text on your PC; Cerebro acts and answers aloud.
"Yes, send it" approves. See [docs/VOICE.md](docs/VOICE.md).

**Browser extension**
Detects Salesforce, Dynamics 365, ServiceNow and Zendesk cases and the SharePoint
documents you open. **Read this page** explicitly captures the current tab and
confirms whether it became a readable source. The Windows installer
includes its complete folder and an **Install Browser Extension** Start-menu
shortcut; source users load `browser-extension/src`. See [docs/INSTALL.md](docs/INSTALL.md).

**Outlook & Teams**
Power Automate drops each message into a folder as JSON; Cerebro ingests it,
triages what is urgent, links it to the right case, and drafts replies that go
back out through a second flow. No Microsoft credentials live in Cerebro.
See [docs/POWER_AUTOMATE.md](docs/POWER_AUTOMATE.md).

**RightAnswers, Dynamics 365 & SharePoint**
A hidden browser that uses your own sign-in lets Ask search and read
RightAnswers articles, Dynamics cases, and any SharePoint document or page you
paste a link to. With your approval, it can also post case notes, update
tickets, edit articles, and change Word and Excel files and pages. Every change
is shown as a before/after card first. See
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

**Documents**
Reads Word, Excel, PowerPoint and PDF files you have open, with local OCR for
scanned PDF pages. SharePoint works through either a locally synced copy or an
optional delegated, read-only Graph connection. It can edit local Word and Excel
files too, with a dry run first and a backup every time.
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

Every Python package Cerebro can use is installed during setup, so none of
these needs a trip to `pip` — they are switches, not installs:

| Extra | What it adds | How |
|---|---|---|
| AI provider | Case summaries, troubleshooting steps, reply drafts, document Q&A | Settings → AI Provider, then pick a model from the list. A local [Ollama](https://ollama.com) model needs no API key. |
| Outlook & Teams | Mail and chat as live context | Two Power Automate flows and a folder — [guide](docs/POWER_AUTOMATE.md) |
| PostgreSQL | Shared database for a team | `docker compose up -d postgres`, then Settings → Database |
| Qdrant | Vector search at scale | `docker compose up -d qdrant`, then Settings → Knowledge Search |
| Call transcription | Records and transcribes calls locally | Settings → Audio |
| Activity capture | Screenshots and typed text for the memory | Settings → Activity capture |
| Screenpipe | Continuous desktop capture through a separately licensed official installation; Cerebro connects automatically | Settings → Desktop Capture |

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
| [docs/ADDONS.md](docs/ADDONS.md) | MCP servers, websites, desktop apps and skills added at runtime |
| [docs/VOICE.md](docs/VOICE.md) | Push-to-talk, the mic button and spoken replies |
| [docs/AGENTS.md](docs/AGENTS.md) | Agents, jobs, keeping things updated, attachments, projects, watches and ideas |
| [docs/SECRETARY.md](docs/SECRETARY.md) | Calendar, follow-ups and to-dos, briefings, meeting notes, templates |
| [docs/FACES.md](docs/FACES.md) | The anime face, and loading Live2D or VRM models |
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

- **Outlook and Teams** use a folder of JSON files written by Power Automate.
  Replies wait for your approval before they leave. Optional direct SharePoint
  reading is separate: it uses a delegated read-only token stored locally and
  never grants mail or Teams permissions.
- **The browser extension** reports recognised case and document pages. Tracking
  every tab is off by default, capturing page text is off by default, and any
  domain on your exclusion list is never read. Clicking **Read this page** grants
  access only to the selected tab and shows a success or failure result.
- **Voice** is transcribed on your PC; only the short spoken reply uses an online
  voice (Polly or Edge), and you can choose the offline Windows voice instead.
- **Add-ons** keep their keys and sign-in tokens encrypted for your Windows
  account, and an export never includes them.
- **Documents** are read from disk when you open them. Edits are explicit,
  backed up, and refused while the file is open in Office.
