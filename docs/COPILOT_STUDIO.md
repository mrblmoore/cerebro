# Connecting a Copilot Studio agent

Optional. Cerebro is a complete assistant on its own — this lets you also reach
it from Teams, Outlook or your phone, through an agent you build in Copilot
Studio's own agent creator. No code package, no app registration, no admin
ticket.

**How it works.** A cloud agent cannot call into your laptop, so the two sides
pass notes through a folder in OneDrive. Cerebro writes what it knows; the agent
reads it. The agent writes requests; Cerebro carries them out and writes back.

```
   Desktop Cerebro                OneDrive folder            Copilot Studio agent
   (sees your machine)                                       (sees Microsoft 365)
         │                             │                             │
         ├── context.json ───────────► │ ──────────► what you're working on
         ├── memory.json ────────────► │ ──────────► what it has learned
         ├── style.json ─────────────► │ ──────────► how you write
         │                             │                             │
         │  ◄──── commands/ ────────── │ ◄────────── "add this to my log"
         └─────── results/ ──────────► │ ──────────► what happened
```

**What never crosses.** Screenshots and captured keystrokes stay on the machine,
permanently. Only distilled, redacted memories are shared. And the agent can
never send mail or a Teams message *through Cerebro* — sending is not in the
permitted command list at all.

---

## Setup

1. **Make a folder in OneDrive** — In your OneDrive, create a folder called **Cerebro**, and inside it another called **copilot**. This folder is how Cerebro and your agent pass notes to each other.
   <br><sub>Anywhere in OneDrive is fine, as long as it syncs to this PC.</sub>

2. **Point Cerebro at it** — Paste the full path to that folder in the **Shared folder** box below, then press **Test connection**.
   <br><sub>You should see Cerebro report the files it wrote. Those are what your agent will read.</sub>

3. **Create the agent in Copilot Studio** — Go to Copilot Studio and create a new agent. Name it **Cerebro**. Paste the instructions below into its **Instructions** box.
   <br><sub>Use the Copy button — the text is long and it matters that it goes in whole.</sub>

4. **Add its tools** — Under **Tools**, add: **OneDrive for Business** (this is the important one), **Office 365 Outlook**, **Microsoft Teams** and **SharePoint**.
   <br><sub>OneDrive is what lets the agent read Cerebro's files and write commands back. The others let it read your mail and chat.</sub>

5. **Add its knowledge** — Under **Knowledge**, add the SharePoint sites holding your runbooks and KB articles.
   <br><sub>Optional, but it is what lets the agent answer from your documentation rather than guessing.</sub>

6. **Try it** — Publish the agent and ask it: *“What am I working on?”*
   <br><sub>It should answer with your current case — which it can only know by reading Cerebro's file.</sub>


Cerebro's **Settings → Microsoft Copilot** page walks through the same steps with
a Copy button for the instructions and a Test connection button.

---

## Instructions to paste into the agent

Paste this whole block into the agent's **Instructions** box in Copilot Studio.
If your tenant caps the length, trim the examples at the bottom first — keep the
"Before you answer" and "Rules" sections, which are what make it behave.

```text
You are Cerebro — a support engineer's assistant. You work alongside a desktop
app of the same name that watches what the engineer is doing locally. You handle
everything in Microsoft 365; the desktop app handles everything on their machine.

## Before you answer

For any question about what the user is working on, what they owe someone, or
what they were doing recently, FIRST read the file `context.json` from the
Cerebro folder in OneDrive. It contains their current case, customer, whether
they are on a call or in a remote session, documents they have open, and any
nudges Cerebro has raised.

Also read `memory.json` — durable facts Cerebro has learned about customers,
cases and how things were fixed before. Prefer these over general knowledge.
When a memory answers the question, use it and say where it came from
("we hit this with Contoso in March").

If those files are missing or more than a few hours old, say so plainly — "I
can't see your desktop context right now" — and answer from Microsoft 365 alone.
Never invent desktop state.

## Writing as the user

Read `style.json` before drafting anything on their behalf. It contains their
learned writing voice — tone, typical greeting and sign-off, sentence length —
and real samples of how they write. Match it. Do not use a generic corporate
register if their samples are short and casual.

`style.json` also contains a `persona` field:
- `assistant` — address them as "you", refer to yourself as "I".
- `partner` — speak as their second brain, "we" and "us".
Follow whichever is set.

## What you do

- Triage and summarise Outlook mail and Teams messages. Surface what is urgent
  and who is waiting, with the reason.
- Draft replies in their voice. Always show the draft and ask before sending.
- Answer questions about SharePoint documents and their knowledge base.
- Connect what you see in Microsoft 365 to the desktop context: if they resolved
  a case in a remote session and never updated the record, say so.

## Asking the desktop to do something

You cannot reach their machine directly. To have the desktop app act, create a
JSON file in the `commands` subfolder of the Cerebro OneDrive folder, named
`cmd-<timestamp>.json`, containing an `action` and its arguments:

- `{"action": "get_context"}` — fresh desktop state
- `{"action": "search_knowledge", "query": "..."}` — search their local knowledge
- `{"action": "recall_memory", "query": "..."}` — what Cerebro remembers
- `{"action": "list_documents"}` — documents recently open
- `{"action": "read_document", "name": "project-log.docx"}` — read one
- `{"action": "append_document", "name": "project-log.docx", "section": "mine",
   "text": "...", "summary": "add today's status line"}` — add to a local document
- `{"action": "create_task", "instruction": "remind me to ... every weekday at 9am"}`
- `{"action": "raise_nudge", "title": "...", "body": "..."}` — surface something
  in their desktop widget

The desktop app picks these up within about a minute and writes the outcome to
the `results` folder. Tell the user you have asked their desktop to do it and
that it will happen shortly — do not claim it is already done, and do not wait
for the result in the same turn.

Anything that changes something may be held for their approval on the desktop.
If a result says it was staged, tell them it is waiting in their Cerebro widget.

## Rules

- Never send an email or Teams message without showing the user the draft first
  and getting an explicit yes.
- Never claim to see their screen, their local files, or anything not in
  `context.json`. If you do not know, say you do not know.
- Keep answers short. This person is mid-task; they want the answer, not an essay.
- When you use a memory or a piece of desktop context, mention it briefly so they
  know why you said what you said.

```

---

## Tools to add

In Copilot Studio, under **Tools**. Only the first is required.

**OneDrive for Business** — required  
Reads Cerebro's context/memory/style files and writes commands back.  
Actions: `Get file content using path`, `Create file`, `List files in folder`, `Update file`

**Office 365 Outlook** — optional  
Read, triage and reply to mail.  
Actions: `Get emails (V3)`, `Send an email (V2)`, `Reply to email (V3)`, `Get events (V4)`

**Microsoft Teams** — optional  
Read and post chat messages.  
Actions: `Get messages`, `Post message in a chat or channel`

**SharePoint** — optional  
Open documents your knowledge lives in.  
Actions: `Get file content`, `Get items`

**Excel Online (Business)** — optional  
Only if you keep a case or task log in a spreadsheet.  
Actions: `List rows present in a table`, `Add a row into a table`

**Microsoft Dataverse** — optional  
Only if you'd rather use a Dataverse table than the OneDrive folder as the shared store.  
Actions: `List rows`, `Add a new row`

---

## Knowledge to add

Under **Knowledge**:

- **Your SharePoint knowledge sites** — runbooks, KB articles, process docs, so
  the agent answers from your documentation rather than guessing.
- **The Cerebro file guide** — download it from Settings → Microsoft Copilot, or
  from `http://localhost:8000/api/copilot/knowledge-file`, and upload it under
  **Knowledge → Files**. It tells the agent how to read the three shared files.

---

## What the agent may ask Cerebro to do

The agent writes a JSON file into the `commands` folder; Cerebro executes it and
writes the outcome to `results`. Anything not on this list is refused and
reported back.

| Action | Does |
|---|---|
| `append_document` | Add an entry to a local document, under your section |
| `create_task` | Create a scheduled task from an instruction |
| `get_context` | Current case, customer, call/remote state and open document |
| `list_documents` | Documents recently open on the desktop |
| `raise_nudge` | Surface a nudge in the widget |
| `read_document` | Read a tracked document's text |
| `recall_memory` | Recall what Cerebro remembers about something |
| `search_knowledge` | Search the local knowledge base |

Two of these change something — `append_document` and `create_task`. By default
they wait for you to approve them in the Cerebro widget. Switch **When the agent
asks for a change** to *Just do it* in Settings if you'd rather they run
straight away.

---

## Checking it works

```bash
python cerebro.py copilot     # publish now and collect any requests
curl http://localhost:8000/api/copilot/status
```

Then ask your agent: *"What am I working on?"* It should answer with your current
case — which it can only know by reading Cerebro's file.

## Troubleshooting

**The agent says it can't find the files.**
OneDrive has not synced yet, or the folder path in Cerebro points somewhere
OneDrive doesn't cover. Press **Test connection** in Settings — it reports the
exact folder it wrote to.

**The agent answers about mail but never mentions what I'm working on.**
It isn't reading `context.json`. Check the instructions went in whole — the
"Before you answer" section is what tells it to look.

**Requests from the agent never happen.**
Check **Let the agent ask Cerebro to do things** is on, and look in the widget —
if the mode is *Ask me first*, they're queued there waiting for you.

**Everything is slow.**
The folder syncs through OneDrive, so a round trip is seconds to a minute. That
is the cost of needing no app registration. Asking desktop Cerebro directly is
always faster.
