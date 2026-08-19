# Installing Cerebro

Cerebro's Windows installer includes Python, the cloud AI libraries, document
readers, the desktop widget, and the optional activity-capture dependencies.
Source installs use the commands below.

### Windows release installer

Download `CerebroSetup.exe` from the matching GitHub release and run it. No
separate Python installation is needed — the runtime and every dependency are
inside the installer.

**Setup happens inside the installer.** Its last step opens a short wizard that
checks what is installed, prepares the database, connects an AI model and, if
you want it, the Microsoft 365 folders. Each of those is *tested* before you
move on, so when the wizard closes Cerebro is configured and working — there is
no browser page to visit afterwards and nothing else to fill in.

Anything missing that Cerebro can install itself, it offers to install on the
spot, into its own environment. You can reopen the wizard any time from
**Start → Cerebro Setup**.

The installer also creates shortcuts for Cerebro, the widget, and the bundled
browser extension folder. The widget starts Cerebro and its desktop helpers, and
starts at sign-in by default.

Chrome and Edge intentionally require one user confirmation for unpacked
extensions: open the **Install Browser Extension** Start-menu shortcut, then use
that folder with **Load unpacked** on the browser's extensions page.

Screenpipe is separately licensed software and is not redistributed by Cerebro.
Its integration is enabled by default; if an official Screenpipe installation
is present, the Cerebro widget detects and launches it automatically.

---

## Requirements

* **Python 3.9 or newer** — [python.org/downloads](https://python.org/downloads).
  On Windows, tick **“Add python.exe to PATH”** during installation.
* About 300 MB of disk space.

Nothing else. No database server, no Node, no API key.

---

## 1. Install

### Windows

1. Download or clone the repository.
2. Double-click **`setup.bat`**.
3. Wait for “Setup complete”.

### macOS / Linux

```bash
git clone https://github.com/mrblmoore/cerebro.git
cd cerebro
./setup.sh
```

Either route runs the same cross-platform installer. It will:

1. verify your Python version;
2. create a virtual environment in `.venv/`;
3. install the backend and desktop dependencies;
4. create the SQLite database under `data/`;
5. write `backend/.env` with every default documented inline.

Setup installs **everything** Cerebro can use — the cloud AI SDKs, document
readers, Qdrant and PostgreSQL drivers, activity capture and audio — so turning
a feature on later is a setting, never another install. Anything that needs a
compiler your machine lacks is reported and skipped; the rest still installs.

For a lean install of just the core:

```bash
python cerebro.py setup --minimal
```

If an install goes wrong, `python cerebro.py setup --recreate` rebuilds the
virtual environment from scratch.

---

## 2. Start Cerebro

| | Windows | macOS / Linux |
|---|---|---|
| Start | double-click `start.bat` | `./cerebro.sh start` |
| Menu | double-click `cerebro.bat` | `./cerebro.sh` |

On a source install, `setup` finishes by opening the same **setup wizard** the
Windows installer runs. Five short steps, each verified before you continue:

1. **Welcome** — what is installed, with a one-click fix for anything missing.
2. **Storage** — the built-in database, or PostgreSQL. Tested by opening it.
3. **AI model** — pick a provider and a model from a list. Tested by asking it
   a question.
4. **Microsoft 365** — optional Outlook/Teams and Copilot Studio folders.
   Tested by writing and reading them.
5. **Finish** — a summary of what passed.

Reopen it any time with `cerebro.bat configure` (or `./cerebro.sh configure`).
`http://localhost:8000` is your dashboard, and `/settings` has everything the
wizard asked plus the advanced options it did not.

---

## 3. Desktop widget

| Windows | macOS / Linux |
|---|---|
| double-click `widget.bat` | `./cerebro.sh widget` |

`widget.bat` starts the widget with no console window and pins cleanly to the
taskbar or Start menu. To have it launch at sign-in, open the widget's **☰ menu →
Start with Windows**.

See [WIDGET.md](WIDGET.md) for everything it can do.

---

## 4. Browser extension

1. Open `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode**.
3. Click **Load unpacked** and select `browser-extension/src` for a source
   checkout, or the folder opened by the installed **Install Browser Extension** shortcut.
4. The options page opens. Confirm the API URL matches your Cerebro
   (`http://127.0.0.1:8000` by default) and click **Test connection**.

The toolbar icon shows a badge when Cerebro is unreachable, and events recorded
while it was down are queued and sent when it comes back.

Supported out of the box: Salesforce (Classic and Lightning), ServiceNow and
Zendesk. Untick any you do not use on the options page.

---

## Optional services

Cerebro ships with a built-in database and vector store. Add these only if you
need them.

### PostgreSQL

```bash
docker compose up -d postgres
```

Then in **Settings → Database** set:

```
postgresql+psycopg://cerebro:cerebro@localhost:5432/cerebro
```

Restart Cerebro for the change to take effect.

### Qdrant

```bash
docker compose up -d qdrant
```

Then in **Settings → Knowledge Search** set the Qdrant URL to
`http://localhost:6333`.

### AI generation

Every AI SDK ships with the install, so switching provider is only ever a
setting. In each case, press **Refresh** beside Model to list what your account
can actually use, rather than typing a model ID from memory.

* **Ollama** (local, no key): install [Ollama](https://ollama.com), run
  `ollama pull llama3.1`, then choose Ollama in **Settings → AI Provider**.
  Refresh lists exactly the models you have pulled.
* **OpenAI**: paste your key in **Settings → AI Provider** and press
  **Test connection**.
* **Amazon Bedrock**: choose Amazon Bedrock in **Settings → AI Provider**, pick
  your AWS Region, then press **Refresh** next to Model to list the models your
  account has enabled — everything in that list is guaranteed to work, so there
  is no model ID to type.

  AWS has no single API key the way OpenAI does: it signs every request with an
  identity. Four ways to give it one, under **How to sign in to AWS**:

  | Option | Use it when |
  |---|---|
  | Use the AWS sign-in already on this computer | You already use the AWS CLI, SSO, or an IAM role — nothing to enter |
  | Bedrock API key | You want the simplest setup: create one key in the Bedrock console under **API keys** and paste it |
  | Named AWS profile | You keep several AWS accounts in `~/.aws/config` |
  | AWS access key ID and secret | You were handed a key pair, ideally a temporary one |

  Whichever you pick, the identity needs `bedrock:InvokeModel`, and the model
  must be switched on for your account under **Model access** in the Bedrock
  console.

### Call transcription

```bash
python desktop/audio_recorder.py
```

Recording starts when Cerebro sees a call begin and stops when it ends;
transcription runs locally and the text is attached to the open case.

---

## Troubleshooting

Run this first — it usually names the fix:

```bash
python cerebro.py doctor
```

**“Python was not found” on Windows**
Python is not on your PATH. Reinstall from python.org with
**“Add python.exe to PATH”** ticked, or install it from the Microsoft Store.

**“Could not create the virtual environment” on Debian/Ubuntu**
`sudo apt install python3-venv`

**The widget will not start / “no module named tkinter”**
Tkinter ships with Python on Windows and macOS. On Debian/Ubuntu:
`sudo apt install python3-tk`

**Port 8000 is already in use**
`python cerebro.py start --port 8010`, or change the port in
**Settings → General**.

**The extension shows “offline”**
Cerebro is not running, or its URL differs from the one on the extension's
options page. Start Cerebro, then press **Test connection** there.

**“Install botocore[crt]” keeps appearing, but it is already installed**
You almost certainly installed it into a different Python. Cerebro runs from its
own virtual environment in `.venv/`, so a plain `pip install` at a normal prompt
lands somewhere Cerebro never looks. Re-run setup, which installs into the right
place:

```bash
python cerebro.py setup        # or setup.bat on Windows
```

To check which interpreter has it:

```bash
.venv/bin/python -c "import awscrt; print(awscrt.__version__)"      # macOS / Linux
.venv\Scripts\python -c "import awscrt; print(awscrt.__version__)"  # Windows
```

**Everything is broken and I want to start over**
Delete `data/` and `backend/.env`, then run setup again. That resets Cerebro
completely — including your indexed documents and case history.

---

## Where files live

```
data/
├── cerebro.db      SQLite database (cases, events, context, documents)
├── logs/           Rotating log files
├── audio/          Call recordings, if you enabled transcription
└── vectors/        Reserved for local vector-store data
backend/.env        Your configuration
.venv/              The virtual environment
```

Widget preferences are stored per user, outside the project:
`%APPDATA%\Cerebro\widget.json` on Windows, `~/.config/cerebro/widget.json`
elsewhere.
