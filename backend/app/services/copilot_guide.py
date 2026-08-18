"""
The setup guide for the Copilot Studio agent.

Kept as data rather than prose in a template so the same steps drive the
settings page, the setup wizard and the docs — and so the instructions text the
user pastes into Copilot Studio has exactly one source of truth.
"""

from typing import Any, Dict

from app.core.config import settings

#: Pasted into the agent's Instructions box in Copilot Studio. Written for the
#: agent, not the user — second person, concrete, with the failure modes spelled
#: out, because a vague instruction here produces a vague agent.
AGENT_INSTRUCTIONS = """\
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
"""

STEPS = [
    {
        "title": "Make a folder in OneDrive",
        "body": "In your OneDrive, create a folder called **Cerebro**, and inside "
                "it another called **copilot**. This folder is how Cerebro and "
                "your agent pass notes to each other.",
        "detail": "Anywhere in OneDrive is fine, as long as it syncs to this PC.",
    },
    {
        "title": "Point Cerebro at it",
        "body": "Paste the full path to that folder in the **Shared folder** box "
                "below, then press **Test connection**.",
        "detail": "You should see Cerebro report the files it wrote. Those are "
                  "what your agent will read.",
    },
    {
        "title": "Create the agent in Copilot Studio",
        "body": "Go to Copilot Studio and create a new agent. Name it **Cerebro**. "
                "Paste the instructions below into its **Instructions** box.",
        "detail": "Use the Copy button — the text is long and it matters that it "
                  "goes in whole.",
    },
    {
        "title": "Add its tools",
        "body": "Under **Tools**, add: **OneDrive for Business** (this is the "
                "important one), **Office 365 Outlook**, **Microsoft Teams** and "
                "**SharePoint**.",
        "detail": "OneDrive is what lets the agent read Cerebro's files and write "
                  "commands back. The others let it read your mail and chat.",
    },
    {
        "title": "Add its knowledge",
        "body": "Under **Knowledge**, add the SharePoint sites holding your "
                "runbooks and KB articles.",
        "detail": "Optional, but it is what lets the agent answer from your "
                  "documentation rather than guessing.",
    },
    {
        "title": "Try it",
        "body": "Publish the agent and ask it: *“What am I working on?”*",
        "detail": "It should answer with your current case — which it can only "
                  "know by reading Cerebro's file.",
    },
]


def guide() -> Dict[str, Any]:
    folder = (settings.COPILOT_BRIDGE_DIR or "").strip()
    return {
        "steps": STEPS,
        "instructions": AGENT_INSTRUCTIONS,
        "folder": folder or None,
        "enabled": settings.COPILOT_BRIDGE_ENABLED,
        "tools": [
            {"name": "OneDrive for Business", "required": True,
             "why": "Reads Cerebro's context/memory/style files and writes commands back.",
             "actions": ["Get file content using path", "Create file",
                         "List files in folder", "Update file"]},
            {"name": "Office 365 Outlook", "required": False,
             "why": "Read, triage and reply to mail.",
             "actions": ["Get emails (V3)", "Send an email (V2)",
                         "Reply to email (V3)", "Get events (V4)"]},
            {"name": "Microsoft Teams", "required": False,
             "why": "Read and post chat messages.",
             "actions": ["Get messages", "Post message in a chat or channel"]},
            {"name": "SharePoint", "required": False,
             "why": "Open documents your knowledge lives in.",
             "actions": ["Get file content", "Get items"]},
            {"name": "Excel Online (Business)", "required": False,
             "why": "Only if you keep a case or task log in a spreadsheet.",
             "actions": ["List rows present in a table", "Add a row into a table"]},
            {"name": "Microsoft Dataverse", "required": False,
             "why": "Only if you'd rather use a Dataverse table than the OneDrive "
                    "folder as the shared store.",
             "actions": ["List rows", "Add a new row"]},
        ],
        "knowledge": [
            {"name": "Your SharePoint knowledge sites",
             "why": "Runbooks, KB articles, process docs — so answers come from "
                    "your documentation."},
            {"name": "The Cerebro file guide (download below)",
             "why": "Explains the context/memory/style files so the agent reads "
                    "them correctly."},
        ],
    }
