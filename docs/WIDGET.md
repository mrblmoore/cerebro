# The Cerebro desktop app

A modern always-on-top window for grounded conversation, readable sources and
the work Cerebro is carrying out while you use Dynamics, Teams and everything
else. Closing it **does not quit Cerebro**. Cerebro keeps running in the system
tray, and its brain icon animates to show what it is doing.

```
Windows          double-click widget.bat (installed: Cerebro Widget)
macOS / Linux    ./cerebro.sh widget
```

The window is the same interface as `http://localhost:8000/app`. It is drawn
with Microsoft Edge WebView2 (via pywebview), which is already on Windows 10
and 11. If WebView2 or pywebview is missing, the previous Tkinter widget opens
instead. It is also available on purpose with `python desktop/shell.py --classic`.

## The tray brain

Cerebro's mascot is a pixel-art brain. The same animations play in the tray,
in the app and on the desktop buddy:

| The brain is… | Cerebro is… |
|---|---|
| dozing at a dimmed laptop, a few z's | idle and ready |
| typing on its laptop | thinking, working in RightAnswers, Dynamics or SharePoint, making an approved change, or syncing |
| studying a book, in a graduation cap | searching your sources and researching |
| bouncing a "!" | waiting for you to approve something |
| flushed red, stars circling | something failed. The tooltip says what. |
| grey, laptop closed | the server is not reachable |

- **Click** the icon to open Cerebro. Right-click for **Ask Cerebro…**, pending
  approvals, **RightAnswers & Dynamics…**, the dashboard, settings and
  **Start with Windows**.
- A notification appears when a change is waiting for your approval.
- **Quit Cerebro** in the tray menu is the only way to stop it, and it stops
  the local server too.
- Starting Cerebro again while it is in the tray simply brings the window back.
- With **Start with Windows** on, Cerebro starts quietly in the tray at sign-in.

Preview every animation with `python desktop/brain_frames.py --out preview/`.
The animations are built from the two drawings in `assets/mascot/source` by
`python packaging/make_mascot.py`.

## The desktop buddy

While Cerebro is working, a small animated brain slides up in the
bottom-right corner of the screen, above the taskbar, with a line saying what
it is doing, e.g. "Reading CAS-01234". It types on its laptop while working
and studies while it researches. A moment after the work is done it slides
away again.

- **Click** it to open Cerebro.
- **×** (hover to see it) hides it until the next piece of work.
- Turn it off entirely with **Show working buddy** in the tray menu.

It moves aside if the Cerebro window is in that corner, never takes focus and
stays out of Alt+Tab and the taskbar. Waiting for your approval does not count
as work, so it doesn't sit on screen while a card waits for you.

---

## The tabs

**Connect** — sign in to RightAnswers and Dynamics 365, and check that the
saved sessions still work. See [INTEGRATIONS.md](INTEGRATIONS.md).

**Ask** — a real back-and-forth transcript, in [separate chats](#chats). The composer stays at the bottom;
active-source chips show what is in scope, and cited source links are attached
to answers. Questions and immediate requests are answered now. Only genuine
scheduled or action-oriented instructions become tasks.

Ask can also use Cerebro's services directly. It shows progress and completion
cards while it searches sources, reads the current document, builds an inbox
briefing, searches SharePoint, or prepares a Power Automate action. Email and
Teams actions appear as full draft previews with **Approve and send** and
**Discard** controls. They never enter the Power Automate outbox before an
explicit approval.

Examples:

* `Summarize my inbox today`
* `Draft a reply to the latest email from Alex`
* `Email alex@example.com: The deployment is complete.`
* `Post to Teams channel Support Escalations: The issue is resolved.`
* `Search SharePoint for the rollout plan`
* `Summarize this document`

<a id="chats"></a>
### Chats

Ask keeps separate conversations, like Copilot or Grok. Each chat has its own
history, so Cerebro remembers what was said in *that* chat and nothing else.

- **New chat**: the pencil button or `Ctrl+N`. A chat is named after its
  first message; click the name to rename it.
- **Chats**: the speech-bubble button or `Ctrl+K` opens the list, with search
  across titles and messages. On a wide window the list stays open on the
  left. Pinned chats come first, then the most recent.
- **⋯ menu** on a chat:

  | Item | What it does |
  |---|---|
  | Rename… | Give the chat your own name. |
  | Set instructions… | Standing instructions Cerebro follows for every message in this chat, e.g. "This chat is about CAS-01234 for Contoso", "Answer in Spanish", "Keep replies to three bullets". They show at the top of the chat. |
  | Assign a task… | Give the chat a job (see below). |
  | Pin to top | Keep it at the top of the list. |
  | End conversation | You're done with it: it moves to **Archived**, where it can still be read or restored, a fresh chat opens, and its scheduled tasks stop. |
  | Delete… | Removes the chat and its messages for good, after asking. |

- Switching chats while Cerebro is answering is fine. The answer is still
  worked out and saved; a pulsing dot marks chats that are busy.

**Tasks for a chat.** "Assign a task…" takes what to do and when:

| When | Runs |
|---|---|
| Now, once | Straight away, in the background. |
| Once, later today | At the time you pick. |
| Every day / Every weekday / Every week | At the time you pick. |
| Every hour | Hourly. |
| Only when I run it | When you press ▶ on it. |

A task runs exactly like a message in Ask, with the same tools (knowledge
base, Dynamics, RightAnswers, SharePoint, inbox) and the chat's instructions.
Its result is posted into the chat with a clock tag. Anything it would change
waits there as an approval card and is listed under **Activity**. The chat's tasks
are listed under its title with **▶ Run now** and **× Stop**.

You can also just ask in the chat — "every weekday at 8:45, list new cases
assigned to me and flag anything about data loss" — and the task is created
for that chat.

**Sources** — connection readiness, current browser/document context, and
knowledge search in one place. Include or exclude items from Ask and open a
source from its citation. This is also where browser, document, OCR,
Screenpipe, SharePoint and knowledge status is explained.

**Activity** — suggestions, pending approvals, tasks, mail/Teams items and
recent events. It replaces the separate context and inbox surfaces so work does
not disappear between tabs.

---

## Making it fit your desktop

**Move it** — drag the title bar. Release near a screen edge and it snaps flush.

**Collapse it** — double-click the title bar, press `Esc`, or use the `—` button.
The widget shrinks to a single strip that still shows your case, customer and
whether you are on a call. Do it again to expand.

**Resize it** — drag the `◢` grip in the bottom-right corner.

**Park it** — ☰ menu → **Move to** → any corner.

Position, size, tab and every preference are remembered between sessions.

---

## The ☰ menu

| Item | What it does |
|---|---|
| Refresh now | Force an immediate update (`Ctrl+R`) |
| Reset context | Clear the current case and session state |
| Open dashboard / settings | Open Cerebro in your browser |
| Appearance | Dark or light theme, text size, opacity |
| Move to | Snap to a screen corner |
| Always on top | Keep the widget above other windows |
| Snap to screen edges | Toggle edge snapping while dragging |
| **Start with Windows** | Launch the widget at sign-in |
| Widget preferences… | API URL, refresh rate, opacity, notifications |

## Keyboard shortcuts

| Key | Action |
|---|---|
| `Esc` | Collapse / expand |
| `Ctrl+R` | Refresh now |
| `Ctrl+F` | Jump to Sources / search |
| `Ctrl+N` | New chat |
| `Ctrl+K` | Chat list and search |
| `Ctrl+Q` | Quit |

---

## Windows touches

The widget applies these automatically where Windows supports them:

* **Per-monitor DPI awareness** — sharp on high-resolution and mixed-DPI setups.
* **Tool-window style** — stays out of Alt+Tab and off the taskbar, so it behaves
  like part of the desktop rather than another app to cycle through.
* **Rounded corners** on Windows 11.
* **Taskbar flash** when the context changes — a case opening or a call starting
  gets your attention without a popup stealing focus.
* **Start with Windows** writes a small launcher to your Startup folder. Turning
  it off removes the file; you can also delete
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Cerebro Widget.bat`
  by hand.

None of these are required — on macOS and Linux each one is simply skipped.

---

## Connection handling

The status strip under the title bar always tells you where you stand: green
when Cerebro is answering, red with the reason when it is not. The widget keeps
retrying on its own, so you can start it before Cerebro and it will connect as
soon as the API comes up.

Starting the widget also starts the desktop agent, activity recorder (when
enabled), and document watcher. The UI only rebuilds when data changes, so a
normal poll no longer makes the panel visibly refresh every few seconds.

Pointing the widget at a different Cerebro — a shared instance, or a different
port — is one field in **☰ → Widget preferences**, or:

```bash
python desktop/widget.py --api http://192.168.1.20:8000
```

To forget the saved position and preferences entirely:

```bash
python desktop/widget.py --reset
```
