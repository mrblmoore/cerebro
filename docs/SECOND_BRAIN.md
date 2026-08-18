# The second brain and the secretary

Beyond tracking context, Cerebro can accumulate knowledge from what it watches,
write in your voice, carry out tasks on a schedule, and nudge you about things
you'd otherwise forget. These are the pieces that make it feel like an assistant
rather than a dashboard.

Everything here is optional and configured under **Settings → Second Brain**,
**→ Secretary**, and **→ Activity Capture**.

---

## Memory

Cerebro remembers durable facts — how a case was resolved, a customer's
preference, a procedure — and recalls the relevant ones when it drafts or
answers. That is what makes a reply reflect how you handled the same thing
before, instead of starting cold.

Memories come from three places:

- **Resolved cases.** Summarising a case files its resolution as a memory.
- **Captured activity**, distilled into a few durable facts (see below).
- **You**, directly: *"remember that Randy handles tier-3 escalations."*

```bash
curl -X POST http://localhost:8000/api/memory \
  -H "Content-Type: application/json" \
  -d '{"title":"Randy escalations","content":"Randy handles tier-3 escalations; give him a heads-up before contacting the vendor."}'

# See what Cerebro would recall for a task
curl "http://localhost:8000/api/memory/recall?query=who+do+I+tell+before+escalating"
```

Recall ranks by relevance, confidence and past usefulness, and boosts memories
tied to the same case or customer. A fact re-observed is reinforced rather than
duplicated. Recall is injected automatically into reply drafting and document
Q&A when memory is on.

## Writing voice and persona

**Voice** is how Cerebro writes *as you*. It learns from things you've actually
written — approved replies, your side of Teams threads, transcribed speech —
measuring tone, length, greeting and sign-off, and (with an AI provider) a prose
style card. Samples are redacted before they're stored.

```bash
curl -X POST http://localhost:8000/api/style/learn    # recompute from samples
curl http://localhost:8000/api/style/status
```

**Persona** is how Cerebro speaks *to you*: as an **assistant** ("you still owe
Randy a reply — want me to draft it?") or as a **partner/second brain** ("we
never replied to Randy — want me to draft it?"). Set it in
Settings → Second Brain.

## Tasks and the scheduler

Tell Cerebro what to do in plain language and it becomes a scheduled task:

```bash
curl -X POST http://localhost:8000/api/tasks/instruct \
  -H "Content-Type: application/json" \
  -d '{"instruction":"keep the project log updated daily at 9am with a line under my name"}'
```

It parses the schedule (`daily`, `weekdays`, `weekly`, `hourly`, one-off), the
time, whether it may act on its own, and whose name to write under. A scheduler
runs due tasks in the background — no second process to keep open.

Task kinds today: reminders, **document maintenance** (below), reply drafting,
and summaries. Anything that would leave your machine — an email, a Teams
message — is always staged for approval, never sent autonomously.

### Autonomous document maintenance

The "keep this document updated, under my name" case works end to end. Cerebro:

1. finds the section that is yours — by a heading like *"My daily log"* or one
   named for you — and never touches the rest of the document;
2. writes the new entry in your learned voice, attributed to you;
3. backs the file up, inserts the dated entry under your heading, and records
   the run.

With autonomy off it stages the entry for you to approve instead of writing it.

## Nudges

Cerebro raises things that need you, phrased in your chosen persona:

- **Unanswered important mail** — high-urgency, some hours old, no reply drafted.
- **Cases resolved but not written up** — a remote session happened and the case
  looks done, but its record was never updated. (*"We remoted into Contoso and
  it's resolved, but the case was never updated — want me to draft it?"*)
- **Due reminders** and task output waiting for review.

They appear in the widget's **Ask** tab and the dashboard, each with a one-click
action ("Yes, do it" drafts the reply; "Dismiss" clears it — and a dismissed
nudge doesn't come back). Cerebro checks roughly every two minutes.

```bash
curl http://localhost:8000/api/tasks/nudges
curl -X POST http://localhost:8000/api/tasks/nudges/scan   # check right now
```

---

## Activity capture — read this before enabling

To learn from what you *do* (not just what passes through the bridge), Cerebro
can take periodic downscaled screenshots of the active window and capture the
text you type. **This is off by default and should stay off until you've thought
it through — and, on a work machine, cleared it with IT.** It captures
colleagues' and customers' data alongside yours.

What's built in to limit the exposure:

- **Off by default**, behind a master switch. Nothing is captured until you turn
  it on.
- **Secrets are always redacted** before anything is stored — passwords, API
  keys, tokens, card numbers (Luhn-checked), and, optionally, names/emails/phone
  numbers. The redaction runs on the desktop *and* again in the backend.
- **Login, banking and password-manager windows are dropped whole**, not
  redacted — captured at all is the wrong outcome there.
- **An exclusion list** of apps/windows you name is never captured.
- **Screenshots are the active window only**, downscaled small — enough to
  recognise "the Q3 spreadsheet", not to read fine print — never the whole
  desktop, so other monitors are never swept in.
- **Retention** deletes capture after a set number of days, and
  `python cerebro.py capture` plus the API let you purge everything at once.
- **Windows only.** Screenshot/window capture relies on Win32 APIs; without a
  window title the safety filters are blind, so on macOS/Linux the recorder
  captures nothing rather than capture something it couldn't screen.

```bash
pip install -r desktop/requirements-capture.txt
python cerebro.py capture

# Forget everything captured
curl -X POST http://localhost:8000/api/activity/purge?everything=true
```

Captured activity is short-lived raw material: the memory engine distils the few
durable facts out of it, and retention clears the rest.

```bash
curl -X POST http://localhost:8000/api/memory/distil   # turn activity into memories now
```
