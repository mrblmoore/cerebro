# Agents: several Cerebros, one brain

Cerebro can run several named **agents** at once. Each works on its own
things, but they all share one brain: the same memory, add-ons, skills and
settings. For example, you might have:

- **Inbox assistant:** keeps on top of Outlook and Teams;
- **Case researcher:** digs into cases in Dynamics and RightAnswers;
- **KB writer:** turns fixes into articles.

They're listed down the left side of the window, like Grok's side panel.

Each agent has its own:

| | |
|---|---|
| **Role and instructions** | What it's for, and how it should work |
| **Chats** | Its own conversations (the chat list shows the selected agent's) |
| **Jobs** | Work it does in the background, tracked step by step |
| **Face and voice** | Brain, anime face, or your own Live2D/VRM model, plus a voice (see [FACES.md](FACES.md)) |
| **Tool scope** | Optionally, the only kinds of tools it may use (e.g. just Outlook and Teams) |

**What every agent has, whatever its scope:**
- **Memory.** If one agent learns something (`remember`), every agent can
  recall it.
- **Teamwork.** An agent can hand work to another one that fits it better
  (`delegate_to_agent`). It can also plan, ask you questions and use skills.

**The default agent.** **Cerebro** always exists. It has no scope limits,
and everything from before agents (your chats and tasks) belongs to it.

## The window

- **Sidebar.** The agents, each with a status ring: working, needs your
  answer, or needs approval. Below them are Home, Chat, Work, Activity,
  Sources and Connect.
  - **Narrow window:** the sidebar becomes a rail of faces.
  - **Very narrow:** it becomes a drawer behind the ☰ button.
- **Home.** A card per agent, showing its current jobs and plans, anything
  waiting for you, its ideas, and watches that fired.
- **Chat.** The selected agent's face sits above the chat. It reacts to what
  that agent is doing, and its mouth moves when it speaks. ⚙ edits the agent;
  ⌃ makes the face smaller.
- **Work.** The selected agent's jobs, projects, watches and ideas.

**Adding an agent.** Use **+** at the top of the sidebar. Start from a
template (Case researcher, Inbox assistant, KB writer, Watcher) or a blank
agent, then pick its name, role, instructions, face, voice and scope.

## Jobs

A job is work an agent does in the background. Ways to start one:
- **+ Job** on Home or Work;
- an idea you accept;
- a watch that fires;
- one agent handing work to another.

Jobs can run once, or hourly, daily, on weekdays, weekly, or only when you
run them.

**While a job runs:**
- **It keeps a plan.** You see its steps tick off (done, running, waiting).
- **It keeps a run log.** Each lookup, each change it prepared, and each
  thing it waited for is recorded with its time. Click a job's title to see
  them.
- **It can ask you something.** The job waits ("Needs your answer"). Answer
  on its card, or simply reply in the agent's chat, and it carries on.
- **It can prepare a change.** Anything that would send or change something
  becomes an approval card, as always, and the job waits ("Needs approval").
  When you approve or discard, it picks up from there.
- **You can control it:** Pause, Resume, Cancel or Retry. Pause and Cancel
  take effect between steps.

**What makes jobs dependable** (borrowed from OpenMuse):
- **Retries.** A recurring job that fails retries later (5, 10, 20 minutes…,
  at most 6 hours). After three failures in a row it pauses, with a
  notification saying why.
- **No running twice.** Each job is claimed before it runs, so it can't run
  twice at once. Two jobs can run at the same time by default (**Settings →
  Secretary → Jobs at the same time**).
- **Restarts.** A job cut off when Cerebro closed is picked up again on the
  next start. The one exception: if it had already made a change
  automatically, it isn't re-run, because it may or may not have finished.
  It shows as failed with "check, then Retry".

## Keep it updated

Give Cerebro a link or a file and say how to keep it up to date. It sets
everything up in one step.

> *Keep https://contoso.sharepoint.com/sites/IT/SitePages/Status.aspx updated
> from C:\Exports\cases.xlsx — add the number of open cases. Whenever the
> export changes.*

**What it can keep updated:**

- a SharePoint page or document;
- a page on an internal site or wiki;
- a Word or Excel file, local or synced from OneDrive or SharePoint;
- a Windows program's screen;
- anything reached through an MCP add-on.

**What `keep_updated` does:**

1. **Connects it.** It adds the website, asks you to sign in once, turns on
   SharePoint, or registers the program, whatever's needed. If it can't
   reach it at all, it looks for a connector first.
2. **Makes the job.** It runs hourly, daily, on weekdays or weekly, or **when
   the source changes**. For the last, it watches the source page or file
   and runs the job when it changes.
3. **Starts the first update now.** Changes wait for your approval as
   always.
4. **Remembers how it did it.** Once a run works, the agent saves the steps
   as a skill: where to go, what to read, what to change and how to save.
   Later runs follow those steps first, and correct them if the page has
   changed.
5. **Offers to run by itself.** After it has worked, the card offers **Let
   it update by itself from now on**. That turns on the target's own **Apply
   changes automatically** switch.

The switch belongs to the target:

- the website's;
- the desktop app's;
- SharePoint's;
- **Apply Word and Excel changes automatically**, for documents.

Automatic changes are still logged with before and after screenshots, and
one that can't be confirmed is never retried.

**Where to see them:**

- the **Keeping X updated** card in the chat;
- **Work → Updates**;
- **Home → Kept updated**.

Each shows how often it runs, the last and next run, and **Run now**,
**Pause** and **Stop**.

## Attaching documents

Click **📎** in the composer, drop files on the window, or paste them. You
can attach:

- Word, Excel, PowerPoint or PDF;
- text, CSV, Markdown, logs or zip;
- `.vtt` and `.srt` transcripts.

The limit is 50 MB a file.

**How the agent sees them:**

- It's told where each file is, so it can read, review or edit it, or keep
  it updated.
- The files show as chips on your message.
- They're added to the documents Cerebro tracks, so they're searchable later.

## Projects

A project is a goal with milestones, for example "Migrate the imaging PCs":
inventory, test the driver, roll out.
- Give jobs to a project from its card (**+ Job**).
- Tick milestones off yourself, or let the agent do it (`tick_milestone`).
- A project is done when every milestone is.

## Watches

A watch re-checks a page, or a file, and tells you when:
- it **changes**;
- some text **appears** ("Resolved");
- some text **is gone**;
- a number goes **below** or **above** a value (queue length, a price).

**Setting one up.** Ask an agent, for example:

> *Let me know when CAS-01234 changes in Dynamics.*

Or use **Work → Watches → + Watch**.

**Files.** A watch can follow a local file, or one synced from OneDrive or
SharePoint, given as `C:\…`, `\\server\share\…` or `file://…`.

- **Cheap checks.** Its modified time and size are checked first, so an
  untouched file costs nothing.
- **When it changes,** its text is read (Word, Excel, PowerPoint, PDF or
  text).
- **How often.** Files can be checked as often as every minute.

**How pages are read:**
- A page on a system Cerebro is connected to (Dynamics, an added website…)
  is read through the hidden browser, signed in as you.
- Any other page is fetched directly.
- Times and dates on a page don't count as changes.

**When a watch fires:**
- It alerts once per change.
- The alert arrives as a notification and in the agent's **Watches** chat.
- It can start a follow-up job, e.g. "draft an update to the customer".

**When a page can't be read:** the watch backs off, and pauses after five
failures in a row.

## Ideas

Agents suggest jobs from what they see, each with its evidence:
- an important message nobody has answered;
- a case worked on remotely but never written up;
- a case quiet for several days (**Settings → Secretary**);
- the same question arriving from three or more people, which suggests a
  knowledge article;
- a watch that fired.

**Optional AI suggestions:** turn on **Let the AI suggest jobs too** and, at
most hourly, the AI reads recent mail for anything else worth doing.

**Who gets each idea:** the agent whose scope fits the work. Replies go to
an agent with Outlook and Teams; articles go to one with RightAnswers.

**What you can do with an idea:**
- **Do it** — it becomes a job for that agent;
- **Edit first…** — reword the job;
- **Dismiss** — it never comes back.

## For developers

**Models:**
- `models/agent.py`: `Agent`.
- `models/work.py`: `TaskEvent`, `Project`, `Watch`, `Idea`.
- Jobs are the existing `Task`, with new columns: `agent_id`, `plan`,
  `question`, `answer`, `attempts`, `lease_until`, `state`.

**Services:**
- `services/agents.py`: templates, scope groups, the identity block.
- `services/jobs.py`: leases, worker threads, controls, recovery.
- `services/watches.py`, `services/ideas.py`.
- Tools: `services/agent/agent_tools.py`.

**API:** `/api/agents`, `/api/jobs`, `/api/projects`, `/api/watches`,
`/api/ideas`, `/api/home`.
