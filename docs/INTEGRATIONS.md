# RightAnswers & Dynamics 365

Cerebro can work in **RightAnswers** (your knowledge base) and **Dynamics 365**
(your cases) on its own: search them, read articles and cases, and, with your
approval, post notes, update tickets and edit articles.

It does this through a **hidden browser** driven by
[Playwright](https://playwright.dev/python/). The browser uses *your* sign-in.
Cerebro never sees or stores your password.

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
| Note, field update, resolution, article edit | Ask prepares the change and shows an **approval card** with a before/after preview. Nothing is sent until you click **Approve**. Discarded changes never run. |

Every proposed and completed change is listed at `GET /api/chat/changes`.

## Setup

1. Install the component (the Windows installer includes it). From source:

   ```bash
   pip install -r backend/requirements-browser.txt
   ```

2. Settings → **RightAnswers & Dynamics**:
   - turn on **Use the hidden browser**;
   - turn on **Dynamics 365** and enter your org address, e.g.
     `https://contoso.crm.dynamics.com`;
   - turn on **RightAnswers** and enter the address you open it at, e.g.
     `https://company.rightanswers.com/portal`.
3. Click **Sign in** for each system, then **Test**.

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

When the browser extension sees you open a Dynamics case, Cerebro reads it in
the background. By the time you ask about it, Ask already has it.

## Dynamics 365 details

Cerebro does not click through Dynamics forms. It calls Dataverse's own Web API
(`/api/data/v9.2`) **from inside the signed-in page**, exactly as the Dynamics
web app does. This means:

- no app registration, client secret or admin consent is needed;
- form customisations cannot break it;
- it can only do what your own Dynamics security role allows.

Fields Cerebro may change are `title`, `description`, priority, severity and
status reason. Option-set fields accept a number or a label (High, Normal, Low).
Anything else is refused before an approval card is created.

## RightAnswers details

RightAnswers portals differ between companies, so the connector is driven by
**selectors** rather than a fixed page structure. The defaults match the common
portal layout. If your portal differs, create
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
| A RightAnswers search finds nothing | Your portal's layout differs; see the selectors section above. |
| Something failed mid-way | A screenshot of the page is saved in `%LOCALAPPDATA%\Cerebro\browser_screenshots`. |
