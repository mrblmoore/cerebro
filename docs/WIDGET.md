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

| The brain is… | Cerebro is… |
|---|---|
| breathing, blinking | idle and ready |
| sparkling, eyes up | thinking (waiting on the AI model) |
| sweeping a magnifying glass | searching your sources |
| scanning, with a spinning globe | working in RightAnswers or Dynamics |
| wiggling a pencil | making an approved change |
| bouncing a "!" | waiting for you to approve something |
| circled by an orbit | syncing |
| showing sound waves | listening |
| red and dizzy | something failed. The tooltip says what. |
| grey and asleep | the server is not reachable |

- **Click** the icon to open Cerebro. Right-click for **Ask Cerebro…**, pending
  approvals, **RightAnswers & Dynamics…**, the dashboard, settings and
  **Start with Windows**.
- A notification appears when a change is waiting for your approval.
- **Quit Cerebro** in the tray menu is the only way to stop it, and it stops
  the local server too.
- Starting Cerebro again while it is in the tray simply brings the window back.
- With **Start with Windows** on, Cerebro starts quietly in the tray at sign-in.

Preview every animation with `python desktop/brain_frames.py --out preview/`.

---

## The tabs

**Connect** — sign in to RightAnswers and Dynamics 365, and check that the
saved sessions still work. See [INTEGRATIONS.md](INTEGRATIONS.md).

**Ask** — a real back-and-forth transcript. The composer stays at the bottom;
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
