# Your first ten minutes with Cerebro

You have installed Cerebro (if not, start with the [README](README.md) — it is
one command). This walks you through actually using it.

---

## 1. Start it and finish the wizard — 2 minutes

```
Windows          double-click start.bat
macOS / Linux    ./cerebro.sh start
```

Your browser opens the setup wizard. If you are not sure about a step, take the
recommended option — every one of them can be changed later, and the defaults
need no external services.

You land on the dashboard at `http://localhost:8000`.

---

## 2. Give it something to know — 3 minutes

Cerebro's knowledge search is only as useful as what you put in it. On the
dashboard, click **+ Add document** and paste in a runbook, a KB article, or the
resolution notes from a case you solve often.

Add two or three. Then type a symptom into the search box — not the title, the
*symptom*, the way you would describe it to a colleague — and watch it come back.

Prefer the API?

```bash
curl -X POST http://localhost:8000/api/knowledge/documents \
  -H "Content-Type: application/json" \
  -d '{
    "title": "KB-1043 — Outlook 0x80040115",
    "source": "RightAnswers",
    "content": "Outlook cannot connect to Exchange. Error 0x80040115 means the server is unreachable. Check autodiscover, VPN, and MAPI over HTTP."
  }'
```

---

## 3. Put the widget where you can see it — 1 minute

```
Windows          double-click widget.bat
macOS / Linux    ./cerebro.sh widget
```

Drag it to a corner — release near an edge and it snaps flush. Try the three
tabs, then double-click the title bar to collapse it to a single strip. That is
how most people leave it: visible, out of the way, one double-click from the
full panel.

If you want it every day, open **☰ → Start with Windows**.

---

## 4. Connect your browser — 2 minutes

1. Open `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode**.
3. **Load unpacked** → select `browser-extension/src`.
4. On the options page that opens, click **Test connection**.

Now open a real Salesforce, ServiceNow or Zendesk case. Within a second the
widget shows the case number and customer, and the Assist tab suggests searching
your knowledge base for that customer.

That is the loop Cerebro is built around: you work, it follows, and what you need
is already on screen.

---

## 5. Optional — let it write your notes — 2 minutes

Cerebro can draft case summaries and troubleshooting steps. It needs a model:

* **Private and free** — install [Ollama](https://ollama.com), run
  `ollama pull llama3.1`, then pick **Ollama** in Settings → AI Provider.
  Nothing leaves your machine.
* **Best quality** — `pip install -r backend/requirements-ai.txt`, then paste an
  OpenAI key in the same place.

Press **Test connection** either way. Then create a case and watch the summary
appear:

```bash
curl -X POST http://localhost:8000/api/cases/ \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "500XY7", "system": "Salesforce", "customer": "Contoso",
    "title": "Outlook cannot connect after VPN change", "error_code": "0x80040115"
  }'
```

---

## What to explore next

| You want to… | Go to |
|---|---|
| Change any setting | `http://localhost:8000/settings` |
| See everything the API can do | `http://localhost:8000/docs` |
| Record and transcribe calls | [docs/INSTALL.md](docs/INSTALL.md#call-transcription) |
| Get the most out of the widget | [docs/WIDGET.md](docs/WIDGET.md) |
| Understand the settings | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) |
| Fix something | `python cerebro.py doctor` |

---

## The events Cerebro understands

Anything can post these — the extension, the desktop agent, or your own script.

| Event | Meaning |
|---|---|
| `CRM_CASE_OPENED` | A case was opened; sets the current case and customer |
| `CRM_CASE_CLOSED` | The case was closed; clears it |
| `CALL_STARTED` / `CALL_ENDED` | Call state changed |
| `REMOTE_SESSION_CONNECTED` / `_DISCONNECTED` | Remote support session state |
| `APPLICATION_CHANGED` | The active window or URL changed |
| `TRANSCRIPT` | A call transcript was captured |

```bash
curl -X POST http://localhost:8000/api/events/ \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "CRM_CASE_OPENED",
    "source": "my_script",
    "data": {"system": "Salesforce", "case_id": "500XY7", "customer": "Contoso"}
  }'
```

The response includes the updated context and any suggestions it produced.
