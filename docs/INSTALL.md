# Installing Cerebro

Cerebro installs with one command and runs with no configuration. This page
covers the details, the optional pieces and what to do when something fails.

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

To install an optional extra at the same time:

```bash
python cerebro.py setup --with ai --with search
```

Valid extras: `ai` (OpenAI SDK), `search` (Qdrant), `postgres` (PostgreSQL
driver), `audio` (call recording and transcription).

If an install goes wrong, `python cerebro.py setup --recreate` rebuilds the
virtual environment from scratch.

---

## 2. Start Cerebro

| | Windows | macOS / Linux |
|---|---|---|
| Start | double-click `start.bat` | `./cerebro.sh start` |
| Menu | double-click `cerebro.bat` | `./cerebro.sh` |

Your browser opens the **setup wizard** the first time. Six short steps:

1. **Welcome** — a system check confirming everything installed correctly.
2. **Storage** — keep the built-in SQLite database, or point at PostgreSQL.
3. **AI model** — optional; skip it and add one later if you want to.
4. **Knowledge search** — built-in vector store, or Qdrant.
5. **Desktop & browser** — how to start the widget and load the extension.
6. **Done** — a summary of what you chose.

After that, `http://localhost:8000` is your dashboard and `/settings` has
everything the wizard asked, plus the advanced options it did not.

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
3. Click **Load unpacked** and select the `browser-extension/src` folder.
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
pip install -r backend/requirements-postgres.txt
```

Then in **Settings → Database** set:

```
postgresql+psycopg://cerebro:cerebro@localhost:5432/cerebro
```

Restart Cerebro for the change to take effect.

### Qdrant

```bash
docker compose up -d qdrant
pip install -r backend/requirements-search.txt
```

Then in **Settings → Knowledge Search** set the Qdrant URL to
`http://localhost:6333`.

### AI generation

* **Ollama** (local, no key): install [Ollama](https://ollama.com), run
  `ollama pull llama3.1`, then choose Ollama in **Settings → AI Provider**.
* **OpenAI**: `pip install -r backend/requirements-ai.txt`, then paste your key
  in **Settings → AI Provider** and press **Test connection**.

### Call transcription

```bash
pip install -r desktop/requirements-audio.txt
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
