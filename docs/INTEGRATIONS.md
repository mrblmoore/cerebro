# RightAnswers, Dynamics 365 & SharePoint

Cerebro can work in three systems on its own:

- **RightAnswers**, your knowledge base;
- **Dynamics 365**, your cases;
- **SharePoint**, your documents and pages.

It searches them and reads articles, cases, documents and pages. With your
approval, it also posts notes, updates tickets, edits articles, changes
documents and updates pages.

## Your setup

| System | Address (pre-filled) |
|---|---|
| Dynamics 365 | `https://dental.crm.dynamics.com` |
| RightAnswers | `https://dexis.rightanswers.com` |
| SharePoint | `https://envistaconnect.sharepoint.com`, plus its OneDrive at `envistaconnect-my` |

1. Open Cerebro and go to the **Connect** tab.
2. Click **Connect** on each system. This switches the system on and opens a
   sign-in window; sign in as you normally do, and the window closes by itself.
   The card then shows **Signed in as …**.
3. For RightAnswers, click **Teach** and follow the three prompts:
   1. search for *password*;
   2. open any result;
   3. optionally, click **Edit** on it.

   Cerebro learns DEXIS's portal layout from what you did. Until it has seen
   the Edit step, it won't edit articles.
4. Try it in Ask:
   - "What's the latest on CAS-…?"
   - "Search RightAnswers for …"
   - paste any SharePoint link and ask about it.

If RightAnswers results look wrong after teaching, send
`%LOCALAPPDATA%\Cerebro\connectors\rightanswers-capture.zip`. It holds the
page addresses and page markup Cerebro saw, with hidden form values removed,
and no cookies or passwords.

It does this through a **hidden browser** driven by
[Playwright](https://playwright.dev/python/). The browser uses *your* sign-in.
Cerebro never sees or stores your password.

## How the AI uses the browser

The AI model never moves a mouse, looks at screenshots or types into pages.
It works with **tools**, and Cerebro's own code does the browsing:

1. With each message, Cerebro sends the model your question plus a list of
   tools it may use. Each tool has a name, a one-line description and the
   arguments it takes, for example
   `dynamics_get_case(case)` — "Read a Dynamics case: status, owner, notes".
2. The model replies with a request instead of an answer, for example
   "call `dynamics_get_case` with `{"case": "CAS-01234"}`".
3. Cerebro runs that tool in the hidden, signed-in browser:

   | System | How the tool talks to it |
   |---|---|
   | Dynamics 365 | The Dataverse Web API (`/api/data/v9.2/…`), called from inside the signed-in page, so it uses your session and your permissions. Labels such as priority names come from your org's own metadata. |
   | SharePoint | SharePoint's REST API, called the same way. Edits check the file out, change it, and check it back in. |
   | RightAnswers | The portal's own pages: search box, result list, article and editor. Cerebro learns their layout with **Teach**. |

4. Cerebro gives the result back to the model as plain text. The model may
   call more tools (search, then read the best match, then check a case),
   up to **6** steps per message. Then it writes the answer and cites what it
   used, e.g. `[S1]`.
5. A tool that would change something does not change it. It prepares the
   change and returns "proposed — waiting for approval". You see the card,
   with before/after, and only **Approve** runs it. The model cannot approve
   on your behalf.

The progress lines under an answer ("Reading CAS-01234…") are these tool
calls happening. The tray brain and the desktop buddy show the laptop pose
while Cerebro works in a browser, and the studying pose while it searches.

### Which AI models work

Every provider gets the same tools. Only the format on the wire differs:

| Provider | How tools are sent |
|---|---|
| Amazon Bedrock | The Converse API's `toolConfig` |
| OpenAI, Qwen, Ollama | Their function-calling format |
| Models without tool calling | Cerebro's JSON protocol: the tools are described in the prompt, and the model answers with a small JSON request that Cerebro runs the same way |

On **Amazon Bedrock**:

| Model family | Works as |
|---|---|
| Amazon Nova (Micro, Lite, Pro, Premier) | Native tools. Cerebro uses temperature 0 when tools are offered, as Amazon recommends for reliable tool choice. |
| Meta Llama 3.1, 3.2, 3.3, 4 | Native tools. |
| Mistral Large, Mistral Small | Native tools. |
| Anthropic Claude, Cohere Command R/R+ | Native tools. |
| Llama 3 (3.0), Llama 2, Mistral 7B, Mixtral, Titan Text | JSON protocol, automatically. The first time Bedrock says a model doesn't support tool use, Cerebro switches that model to the JSON protocol and remembers it. A model that rejects system prompts has them folded into the conversation instead. |

Model support changes as AWS adds features. Whatever the table says, a model
that refuses tools gets the JSON protocol automatically, so no model is shut
out. Inference-profile IDs (`us.meta.llama3-3-70b-instruct-v1:0`, ARNs) work
the same as plain model IDs.

**Settings → AI Provider → Tool use in Ask** overrides the automatic choice:

| Setting | Behaviour |
|---|---|
| Auto (default) | Native tools, with the JSON protocol as fallback. |
| Native | Native tools only. |
| JSON | Always use the JSON protocol. Try this if a model calls tools badly. |
| Off | Answer in one step from what is already in view, with no lookups. |

Smaller models choose tools less reliably. For work in Dynamics and
SharePoint, Nova Pro, Llama 3.3 70B and Mistral Large are good choices;
Nova Micro and 8B models are fine for plain questions.

## How it works

- Cerebro keeps its **own browser profile** in
  `%LOCALAPPDATA%\Cerebro\browser_profile`, separate from your everyday browser.
  It uses the Microsoft Edge that is already on Windows, so nothing extra is
  downloaded.
- **Sign in once.** Settings → RightAnswers & Dynamics → **Sign in** opens that
  profile in a normal window at the system's address. You sign in as you
  normally would; single sign-on usually completes by itself. As soon as
  Cerebro sees a signed-in page, the window closes.
- From then on the browser runs **hidden** and reuses the session. It closes
  itself when idle and reopens when needed. If the site ends your session,
  Ask shows a **Sign in again** card.
- Session cookies are saved between runs in
  `%LOCALAPPDATA%\Cerebro\browser_session.bin`, encrypted for your Windows
  account (DPAPI). **Sign out** removes them.

### Reading vs. changing

| | What happens |
|---|---|
| Search and read | Runs immediately while Ask works on your question. |
| Note, field update, resolution, article edit, document or page change | Ask prepares the change and shows an **approval card** with a before/after preview. Nothing is sent until you click **Approve**. Discarded changes never run. |

Every proposed and completed change is listed at `GET /api/chat/changes`.

## Setup

1. Install the component (the Windows installer includes it). From source:

   ```bash
   pip install -r backend/requirements-browser.txt
   ```

2. Use **Connect** in the app, as described above. Alternatively, go to
   Settings → **RightAnswers, Dynamics & SharePoint**, turn on **Use the
   hidden browser** and each system, check its address, and click **Test**.

   Addresses can be typed with or without `https://`, and a pasted page link
   works too: only the site is used.

| Setting | Default | When to change it |
|---|---|---|
| Browser | Microsoft Edge | Use Chrome if Edge is blocked. Use bundled Chromium after running `python -m playwright install chromium`. |
| Window | Hidden | Use **Off-screen window** if your sign-in page refuses hidden browsers. Use **Visible** to watch it work while you check a new setup. |
| Close when idle | 300 s | How long the browser stays open between jobs. |
| Page timeout | 30 s | Raise it for slow tenants. |

## What Ask can do

| Tool | Mode | Example request |
|---|---|---|
| `dynamics_search_cases` | read | "Find open cases for Contoso about VPN." |
| `dynamics_get_case` | read | "What's the latest on CAS-01234-ABCDE?" |
| `dynamics_add_note` | approval | "Add a note to CAS-01234 that the profile repair worked." |
| `dynamics_update_case` | approval | "Set CAS-01234 to high priority." |
| `dynamics_resolve_case` | approval | "Resolve CAS-01234: rebuilt the OST file." |
| `rightanswers_search` | read | "Search RightAnswers for 0x80040115." |
| `rightanswers_get_article` | read | "Read KB100 and remember it." |
| `rightanswers_update_article` | approval | "Add the OST rebuild step to KB100." |
| `rightanswers_create_article` | approval | "Write up CAS-01234 as a new RightAnswers article." |
| `sharepoint_read` | read | "What does <SharePoint link> say about escalations?" |
| `sharepoint_search` | read | "Find the onboarding checklist in SharePoint." |
| `sharepoint_update_document` | approval | "In <link>, change the Tier 2 extension to 5520." |
| `sharepoint_update_page` | approval | "On <page link>, replace 'two calls' with 'three calls'." |

When the browser extension sees you open a Dynamics case, Cerebro reads it in
the background. By the time you ask about it, Ask already has it.

These tools also work in **tasks assigned to a chat** (see
[WIDGET.md](WIDGET.md#chats)), for example "every weekday at 8:45, list new
cases assigned to me". A task's changes wait in its chat as approval cards,
just like in Ask.

## Dynamics 365 details

Cerebro does not click through Dynamics forms. It calls Dataverse's own Web API
(`/api/data/v9.2`) **from inside the signed-in page**, exactly as the Dynamics
web app does. This means:

- no app registration, client secret or admin consent is needed;
- form customisations cannot break it;
- it can only do what your own Dynamics security role allows.

Fields Cerebro may change are `title`, `description`, priority, severity and
status reason. Option-set fields accept a number or a label, and labels are
read from **your organisation's own metadata**, so customised values work.
A label your org doesn't use is refused with the list it does use. Anything
else is refused before an approval card is created.

## SharePoint details

Like Dynamics, SharePoint is driven through its own REST API (`/_api/…`) from
inside the signed-in browser. No app registration is needed, and Cerebro can do
only what your SharePoint permissions allow. This is separate from the optional
Microsoft Graph connection under Settings → Documents. When both are on, Ask
uses this one.

**Links Ask understands:**
- direct links to a file in a library;
- short sharing links (`/:w:/s/…`);
- Office Online links (`Doc.aspx?sourcedoc=…`);
- site pages (`/SitePages/….aspx`);
- the same company's OneDrive (`envistaconnect-my`).

Links to other companies' SharePoint are refused. When a message contains a
SharePoint link, Ask opens it without being told to.

**What Cerebro can do:**
- **Read** Word, Excel, PowerPoint, PDF, text and CSV files, and site pages. Ask
  cites them, and "remember it" adds them to the knowledge base.
- **Change documents.** Word and Excel files are edited on a copy, and the
  approval card shows the document text before and after. On approval, Cerebro
  downloads the file again and checks that nobody changed it since the
  preview, which it refuses if they did. It then applies the edit and uploads
  it back. SharePoint keeps the previous version in its version history.
  Libraries that require check-out are checked out and back in automatically.
- **Change pages.** Text on a modern page is replaced in place, without
  touching the page's layout or web parts. The page is then checked out,
  saved and republished.

## RightAnswers details

RightAnswers portals differ between companies, so the connector is driven by
**selectors** rather than a fixed page structure. **Teach** (on the Connect
tab) learns them for your portal automatically, and is the recommended way.
When the result markup doesn't match, searches also fall back to any link that
looks like an article. To adjust by hand, create
`%LOCALAPPDATA%\Cerebro\connectors\rightanswers.json` with only the keys that
differ, for example:

```json
{
  "search_url": "{base}/portal/ss/?searchText={query}",
  "result_item": "div.searchResult",
  "result_title": "a.solutionTitle",
  "article_url": "{base}/portal/app/portlets/results/viewsolution.jsp?solutionid={id}",
  "article_body": "#solutionContent",
  "edit_button": "button:has-text('Edit Solution')",
  "editor_body": "iframe.cke_wysiwyg_frame",
  "save_button": "button:has-text('Save')",
  "create_url": "{base}/portal/app/authoring/new"
}
```

To find the right selectors, record a session with the Playwright recorder,
signed in, and copy what it captures:

```bash
python -m playwright codegen https://company.rightanswers.com/portal
```

Templates may use `{base}`, `{query}` and `{id}`. Every selector accepts
Playwright selector syntax (`css`, `text=`, `:has-text()`). No restart is
needed; the file is read on each use.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "No browser could be started" | Install Edge or Chrome, or choose Bundled Chromium and run `python -m playwright install chromium`. |
| Sign-in window opens but never closes | Finish signing in until the system's home page shows. Cerebro checks every two seconds for up to ten minutes. |
| Works visibly, fails hidden | Set **Window** to **Off-screen window**. Some single sign-on pages refuse headless browsers. |
| A RightAnswers search finds nothing | Run **Teach** on the Connect tab. If it still fails, send the capture bundle described above. |
| A SharePoint change says the file changed | Someone edited it after the preview. Ask again so the change is based on the current version. |
| Bedrock: "doesn't support tool use" | Nothing to do. Cerebro switches that model to the JSON protocol by itself. If answers still skip lookups, set **Tool use in Ask** to **JSON**, or pick a larger model. |
| Something failed mid-way | A screenshot of the page is saved in `%LOCALAPPDATA%\Cerebro\browser_screenshots`. |
