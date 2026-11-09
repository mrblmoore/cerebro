# Cerebro as your secretary

Cerebro keeps your day in order:

- your calendar and meetings;
- what you're waiting on and what you promised;
- a briefing each morning;
- notes from meetings;
- documents from your templates.

It all comes together on **Home → Today**, and you can ask for any of it in
chat.

> *What's on tomorrow? Find me 30 minutes with Priya on Thursday and send an invite.*

## Today

**Home** opens with four cards:

| Card | What's on it |
|---|---|
| **Agenda** | Today's meetings. The one on now is highlighted, and online meetings have **Join**. |
| **To-dos & follow-ups** | Everything to chase or deliver, with when it's due. Tick it off, snooze it a day, or **Chase**. |
| **Briefing** | This morning's briefing. **▶** reads it aloud and **Refresh** rebuilds it. |
| **Waiting on you** | Jobs with a question, and changes to approve. |

## Calendar and meetings

Cerebro uses your calendar in **Outlook on the web**, through the hidden
browser, signed in as you, the same way it reads your mail. To set it up,
turn on **Connect → Outlook**.

**Reading your calendar:**

| Ask | Tool |
|---|---|
| "What's on today / tomorrow / Friday?" | `calendar_today` |
| "When am I free this week for an hour?" | `find_free_time` |

`find_free_time` looks at your own calendar, during working hours (09:00 to
17:30) on weekdays. Other people's availability shows when you open the
invite in Outlook.

**Changing your calendar** always waits for you to press Approve:

| Ask | Tool |
|---|---|
| "Book 30 minutes with Priya tomorrow at 2" | `book_meeting` |
| "Move my 1:1 to Friday at 10" | `reschedule_meeting` |
| "Cancel the sync and tell them why" | `cancel_meeting` |

- **The approval card.** It shows the time, the subject and who's invited.
- **Names.** People's names are matched to the addresses Cerebro has seen in
  your mail. If it doesn't know someone, it asks.

**A brief before each meeting.** By default it arrives 10 minutes before the
meeting starts. It covers:

- who's coming;
- your recent mail and Teams messages with them;
- their open cases;
- the agenda from the invite;
- the join link.

It's posted in the **Briefings** chat, with a notification.

## Follow-ups and to-dos

Cerebro builds the list by itself, from messages it has seen:

| Kind | Found from | Example |
|---|---|---|
| **Waiting on** | A message you sent that asks for something, with no reply in that thread yet | "Could you send me the licence count?" |
| **You promised** | A message you sent that promises something; it's due when you said | "I'll update the quote by Friday." |
| **Asked of you** | A message to you that asks you for something | "Can you check the backup report?" |
| **To do** | Something you added, or a meeting's action item | "Order a spare sensor." |

- **Why it's on the list.** Each item keeps the words it came from. Hover
  over **why?** to see them.
- **When it leaves the list.** A reply in the thread closes a "waiting on"
  item by itself.

**Chasing.** If someone hasn't replied after 3 days, the item shows
**Chase**. Chase drafts a short, polite follow-up in the same thread, and
it's sent only when you approve it.

**Nudges.** You get a nudge the day before a promise is due, and when
something is ready to chase.

**In chat:**

| Ask | Tool |
|---|---|
| "What's on my list?" | `todo_list` |
| "Remind me to order a sensor tomorrow" | `add_todo` |
| "Tick off #4" | `done_todo` |
| "Chase Sam about the licence count" | `chase_followup` |

**Limits.** Cerebro finds requests and promises in the messages it handles:

- what's in your inbox and Teams;
- mail and messages sent through Cerebro.

Mail you send straight from Outlook isn't read yet. Add those items with
"remind me…".

## Briefings

| Briefing | When | What's in it |
|---|---|---|
| **Morning** | Weekdays at 08:30 | Today's meetings, urgent mail and mentions, follow-ups due, cases needing you, watches that fired, ideas, and what's waiting on you |
| **End of day** | Weekdays at 17:00 (off by default) | What got done (jobs, messages sent, changes made, cases closed) and what's still open |
| **Weekly** | Fridays at 16:00 | The week's work and what carries over |

**Where they appear:**

- on Home;
- in the **Briefings** chat, with a notification;
- the short spoken version plays when you press **▶**.

**Changing them:**

- They're ordinary jobs, so you can pause or edit them in **Work**.
- Turn them on or off, and set the time, in **Settings → Secretary**.
- To get one now, ask "brief me" or press **Refresh**.

## Meeting notes

> *Take notes from the last call.* · *Notes from this transcript* (with a
> `.vtt` or `.docx` attached)

`meeting_notes` works from:

| Source | Example |
|---|---|
| **A recorded call** | One Cerebro transcribed: "the last call" |
| **A transcript file** | `.vtt`, `.docx` or `.txt`, attached in chat or a path |
| **A Teams recap** | A link to the recap page |
| **Pasted text** | The transcript itself |

**What you get back:**

- a summary;
- the decisions;
- the action items.

**What happens to the action items:**

- Yours become promises on your list.
- Other people's become "waiting on".

To send a recap, ask. It's prepared as an email for you to approve.

**Without an AI provider,** it still picks out "Action:" lines and "I'll…" or
"Sam will…" sentences.

## Documents from templates

> *Make a site visit report for Smile Dental from the template.*

1. Put your Word or Excel templates in a folder, and set it under
   **Settings → Secretary → Templates folder**.
2. Mark the gaps with placeholders like `{{customer}}`, `{{date}}` or
   `{{engineer}}`. They work in paragraphs, tables, headers and footers, and
   cells.
3. Ask. `draft_document` finds the template by name, sees which placeholders
   it needs, and fills them in.

**Saving and approval:**

- The new document is saved next to the template in `Drafts`, or wherever
  you say.
- It's prepared as a change for approval, unless you turned on **Apply Word
  and Excel changes automatically**.
- An existing file it would replace is backed up first, and **Undo** puts it
  back.

## Settings

All of these are in **Settings → Secretary**:

| Setting | Default |
|---|---|
| Morning briefing / time | On / 08:30 |
| End-of-day summary | Off |
| Weekly summary | On (Fridays) |
| Meeting brief (minutes before) | 10 (0 turns it off) |
| Chase unanswered requests after (days) | 3 |
| Templates folder | none |

## For developers

| Module | What it does |
|---|---|
| `services/calendar.py` | Reads events in OWA and REST/Graph JSON shapes, finds free slots, books, moves and cancels |
| `browser/outlook.py` (`calendar`, `create_event`, `move_event`, `cancel_event`) | The Outlook calendar page; its selectors can be corrected in `connectors\outlook.json` |
| `services/followups.py` | The `FollowUp` model, the rule pass, chasing and nudges |
| `services/briefings.py` | Briefings, the `briefing` job kind, and meeting briefs |
| `services/secretary_docs.py` | Meeting notes and template filling |
| `agent/secretary_tools.py` | The tools, in the "calendar" and "secretary" scope groups |
| `api/secretary.py` | `/api/today`, `/api/followups`, `/api/briefings`, `/api/calendar` |
