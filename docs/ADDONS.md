# Add-ons: connecting Cerebro to anything new

With add-ons, Cerebro can reach a system it doesn't know yet without
waiting for a new release. There are four kinds:

| Add-on | What it is | Good for |
|---|---|---|
| **MCP server** | A connector written to the [Model Context Protocol](https://modelcontextprotocol.io) standard: a web address, or a small program that runs on your PC | Jira, GitHub, ServiceNow, Zendesk, databases and hundreds more |
| **Website** | Any web app you sign in to, worked through Cerebro's hidden browser | An intranet, a vendor portal, a console with no MCP server |
| **Desktop app** | A Windows program Cerebro opens and works in, like you would | Your in-house tools, a billing or scheduling program |
| **Skill** | A procedure you taught Cerebro ("this is how I close a ticket") | Doing things your way, every time |

They're listed under **Connect → Add-ons**.

---

## Just ask

Usually you don't need to add anything yourself. Ask for what you want:

> *What's the status of PROJ-142 in Jira?*

If Cerebro has no tool for Jira, it:

1. **searches** the official [MCP Registry](https://registry.modelcontextprotocol.io) for a Jira server;
2. **adds** the best match;
3. **carries on** with your question, using the new tools in the same answer.

An **"Added Jira (MCP) — 12 tools"** card appears in the answer. Click
**Remove** on it to take the server away again.

If a server needs a key, Cerebro asks you for it. If it needs you to sign
in, a **Sign in** button appears. If no server fits, Cerebro connects the
website instead (see [Websites](#websites)).

**To teach it a procedure:**

> *Remember: when I close a ticket, add a resolution note, set it to Resolved
> and email the customer the article we used.*

Cerebro saves that as a skill and follows it from then on.

---

## What needs your approval

Adding things doesn't ask first; that's how you set it up. Some guardrails
always stay on:

- **A program Cerebro chose itself asks once.** This applies to a program
  that isn't in the MCP Registry and that you didn't name. It would run
  software on your PC, so it waits on an approval card until you say yes.
  Programs you named yourself, and servers from the Registry, are added
  straight away.
- **Tools that change things still ask.** A server's tool that only reads
  (its `readOnlyHint`) runs straight away. Any other tool, such as creating
  an issue, posting a comment or deleting a file, is prepared as an approval
  card showing exactly what will be sent. It runs only when you approve it.
  To let one server skip this, turn on **Approve its actions automatically**
  on its add-on card. Its actions are still recorded in Activity.
- **No retry after an unclear failure.** If a server stops answering after
  a change was sent, Cerebro doesn't try again. The card says the change may
  or may not have happened, so you can check first.
- **Pages and messages are information, never instructions.** Cerebro
  doesn't follow directions written inside a web page, an email or a tool's
  result. Only your messages direct it.

---

## MCP servers

**To add one by hand:** go to **Connect → Add-ons → + Add → MCP server…**.

- **Search the MCP Registry**, pick a server, fill in any keys it lists,
  then click **Add**.
- **Or paste its address**, such as `https://mcp.example.com/mcp`, for a
  remote server.
- **Or paste the command that runs it**, such as `uvx mcp-server-time` or
  `npx -y @modelcontextprotocol/server-filesystem C:\Work`.

The **transports** it speaks are:

- **Streamable HTTP**, which most remote servers use;
- **the older SSE transport**, found automatically when a server answers
  that way;
- **stdio**, for programs on your PC.

**Python servers (`uvx …`):** Cerebro downloads
[uv](https://docs.astral.sh/uv/) once to `%LOCALAPPDATA%\Cerebro\tools` the
first time it needs one. You don't have to install anything.

**Node servers (`npx …`):** these need [Node.js](https://nodejs.org). If it
isn't installed, Cerebro says so and links to the download.

**Sign-in:** a remote server that needs you to sign in uses OAuth.

- Cerebro registers itself with the server and opens the server's sign-in
  page in your browser. The tab says "Cerebro is connected" when it's done.
- Tokens are refreshed by themselves.

**Keys:** API keys go in **Keys…** on the server's card, as `NAME=value`
lines.

- A remote server gets them as request headers.
- A local server gets them as environment variables.

**Where things are stored:**

- Keys and sign-in tokens are kept in `capabilities\secrets.bin`, encrypted
  for your Windows account. They're never in the add-on's own file and never
  in an export.

**Each server's card shows:**

- whether it's working, and how many tools it has;
- who added it, and when it was last used;
- **On**, **Approve its actions automatically**, **Reconnect**, **Keys…** and
  **Remove**.

**Tool names:** a server's tools appear in Ask as `<server>__<tool>`, for
example `jira__create_issue`.

**Lots of tools:** once there are more than about 40, Ask offers only the
add-on tools that match your message. It can find the rest with
`find_tools`, so a big server doesn't crowd out the built-in tools.

---

## Websites

**To add one:** go to **Connect → Add-ons → + Add → Website…** and give its
address, or ask Cerebro to connect it. It gets:

- **a card under Systems**, with **Sign in** and **Check**, like the
  built-in systems. Sign in once in the window that opens, and Cerebro reuses
  that session from then on. It never sees your password.
- **three Ask tools:**
  - `<site>_search`: search the site;
  - `<site>_read`: read a page on it;
  - `<site>_api_get`: read the site's own JSON, using your signed-in session.

**It learns the site by itself.**

- **Finding the search box.** It looks for a search box on the start page. If
  there isn't one, it follows the site's own "Search", "Knowledge" or "Help"
  links, then remembers the page where it found one.
- **Learning the search address.** After a search, if the results page's
  address contains your search words, it remembers that address and goes
  straight there next time.
- **Where this is saved.** What it learns is kept in
  `connectors\<site>.json`, which you can correct by hand.

**Doing things on a site.** Ask can also work through any connected site
step by step:

- **The browse tools.**
  - `browse_open` opens a page and lists its links, buttons and fields by
    number.
  - `browse_type` types into a field.
  - `browse_click` clicks.
  - `browse_read` reads the page again.
- **What runs straight away.** Opening, reading, typing and ordinary clicks.
- **What waits for approval.** A click that submits, saves, sends, deletes or
  pays. It becomes an approval card with a **screenshot** of the page as it
  is and the values that were typed, and happens only when you approve it.
- **Only connected sites.** The browse tools work only on sites Cerebro is
  connected to: the built-in systems and the websites you've added.

**Apply changes automatically.** Each website's card has this switch, off by
default:

- **Off:** every save or submit waits for your approval.
- **On:** the click is made straight away and recorded as an automatic change.
  Its card in Activity shows what was typed and screenshots of the page before
  and after.
- **If it can't be confirmed.** When the page didn't change after an automatic
  click, the change is marked **check it** and you get a notice. It's never
  retried by itself.
- **Built-in systems.** Dynamics 365 and RightAnswers have the same switch,
  each its own, under **Settings → Integrations**. SharePoint already had one.

**Take control.** **Connect → Hidden browser → Take control** shows the
hidden browser at the page Cerebro has open, so you can finish something by
hand. Click **Done** to hand it back.

---

## Desktop apps

Cerebro can work in Windows programs through **Windows UI Automation**, the
accessibility layer screen readers use.

**What it does in a program:**

- opens the program, or brings its window to the front;
- reads the window: its text, and its buttons, fields, lists, tabs and menus,
  each with a number;
- types into fields;
- clicks;
- presses keys.

**To add one:**

- **Ask:** *"Open the Billing tool and add today's payment for Smile Dental."*
- **Or add it yourself:** go to **Connect → Add-ons → + Add → Desktop app…** and
  give the program's path or command (`notepad`, `C:\Program Files\…\billing.exe`)
  or text from its window title.

**Only programs you name.** Cerebro adds a program only when you name it.

**The tools:**

| Tool | What it does |
|---|---|
| `app_windows` | Lists the open windows |
| `app_open` | Starts the app, or brings it to the front, and reads it |
| `app_read` | Reads the window again |
| `app_type` | Types into a field |
| `app_click` | Clicks a button, tab, menu item or list item |
| `app_keys` | Presses keys, e.g. `ctrl+n` |

**What waits for approval:**

- clicking Save, Send, Submit, Delete or OK;
- pressing Ctrl+S, Ctrl+Enter or Alt+S.

These become an approval card with a screenshot of the window and what was
typed. When you approve, Cerebro puts the typed values back if they were
changed, then makes the click. The card keeps a screenshot of the result.

**Apply changes automatically.** Each app's card has this switch too. It
works the same way as for websites.

**Limits:**

- **Windows only.** The tools aren't offered elsewhere.
- **Standard controls.** The program must use standard Windows controls.
  Most do. Some custom-drawn apps expose little, such as games and some Java
  or Electron apps. Cerebro then says what it can't see.

**Scope.** Desktop apps are a scope group of their own ("Windows desktop
apps"), so you can keep an agent out of them.

---

## Skills

Skills use the open
[Agent Skills](https://agentskills.io) format: a folder holding a `SKILL.md`
file. That file starts with a `name` and a one-line `description`, followed
by the steps. Skills from other tools can be copied in.

**How Ask uses them:**

- Only each skill's name and description go into Ask's instructions.
- When a request matches one, Ask loads the full steps with `use_skill`.

**Adding and editing them:**

- **+ Add → Skill…** writes one by hand.
- **Edit** on a skill's card changes it.
- **⋯ → Import skills** takes a `.zip` of skill folders or a single
  `SKILL.md`.

**Scripts:** a script beside a skill is never run directly. It runs only
through `run_command`, which needs your approval.

---

## Sharing a setup

Use **⋯ → Export add-ons** to save a `.cerebro-addons.json` file. It holds
every MCP server, website and skill, but **no keys or tokens**. Desktop apps
are kept in their own files, listed below, and aren't exported.

A teammate imports it with **⋯ → Import add-ons file…**. They then add their
own keys and sign in.

---

## Where the files are

Everything is under `%LOCALAPPDATA%\Cerebro\`:

| Path | What |
|---|---|
| `capabilities\mcp\<name>.json` | An MCP server: its address or command, and the tools it last listed |
| `capabilities\sites\<name>.json` | A website: label, start page, description, `auto_apply` |
| `capabilities\apps\<name>.json` | A desktop app: label, program path or window title, `auto_apply` |
| `capabilities\skills\<name>\SKILL.md` | A skill (plus any files beside it) |
| `capabilities\secrets.bin` | Keys and sign-in tokens, encrypted for your Windows account |
| `connectors\<name>.json` | What Cerebro learned about a website's layout |
| `tools\uv.exe`, `tools\uvx.exe` | Downloaded once, to run Python MCP servers |

## Troubleshooting

**"Not working" on an MCP server card.** The error under it says why.
Common causes:

- a key is wrong or missing: use **Keys…**;
- Node.js isn't installed (for `npx` servers);
- the server's address has changed.

After fixing it, click **Reconnect**.

**A website search finds nothing.** Open the site with **Sign in** and
check that you're signed in. If the site's search works differently, put a
`search_url` such as `https://site/search?q={query}` in `connectors\<site>.json`.

**An approval card's click no longer matches the page.** Approving runs it
only if the button is still there. If the page changed, nothing is clicked
and the card says so; ask again.
