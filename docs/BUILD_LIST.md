# Build list — integrations, Ask, UI and tray

The working plan for the next Cerebro release. Each item is roughly one
commit; each phase can ship on its own. Tick items as they land.

Decisions already made:

- **UI:** a pywebview (Edge WebView2) desktop shell around a new HTML/CSS/JS
  app served by the backend — one design system for the widget and the
  dashboard. The Tkinter widget stays as a `--classic` fallback for a release.
- **Browser login:** Cerebro keeps its *own* persistent Microsoft Edge profile.
  "Sign in" opens it visibly once (SSO usually completes by itself); afterwards
  it runs hidden and reuses the saved session.
- **Write safety:** reads and searches run immediately. Every write to
  Dynamics 365 or RightAnswers is shown as an approval card first.

---

## Phase 0 — Shared foundation

- [x] **B0.1 Activity state.** `app/core/activity_state.py` records what is in
  progress (`thinking`, `searching`, `browsing`, `writing`,
  `awaiting_approval`, `syncing`, `listening`, `error`, `idle`).
  `GET /api/system/activity` and the SSE stream
  `/api/system/activity/stream` feed the tray brain and the app header.
- [x] **B0.2 Conversational LLM interface.** `LLMService.chat(messages, tools)`
  — real user/assistant turns and native tool calling for OpenAI, Ollama, Qwen
  and Bedrock (Converse `toolConfig`), with a JSON tool protocol for models
  that lack it. `_call_llm(prompt)` is unchanged for existing callers.

## Phase 1 — Ask answers the question (instead of repeating itself)

Why it repeated: retrieval had no relevance floor (any shared word scored
above zero), opened documents stayed "active" forever, the prompt put six
old-document excerpts *after* the question and flattened the previous answer
into the same message, and keyword shortcuts sent ordinary questions to fixed
template replies.

- [ ] **B1.1 Retrieval relevance.** Minimum score (`ASK_MIN_SOURCE_SCORE`) and
  a relative cutoff in `text_chunks.rank` / `RAGService` search; documents are
  "active" only for `ASK_ACTIVE_DOCUMENT_MINUTES` after last being seen.
- [ ] **B1.2 Prompt structure.** Ask-specific system prompt; history as real
  turns; sources in their own context block before the question, only when
  relevant.
- [ ] **B1.3 Agent tool loop.** `app/services/agent/` — a tool registry and a
  bounded loop (`ASK_MAX_STEPS`). The model decides when to search knowledge,
  sources, local files and the database, read a document, check the inbox,
  draft a reply or create a task.
- [ ] **B1.4 No keyword hijacks.** Only approve/discard and image questions are
  routed deterministically; everything else reaches the model. The no-AI
  fallback cites only genuinely relevant sources.
- [ ] **B1.5 Streaming.** `POST /api/chat/stream` (SSE) streams tool progress,
  approval cards and the answer.
- [ ] **B1.6 Tests.** Off-topic questions ignore stale documents; different
  questions get different prompts; the tool loop runs against a scripted
  provider; keyword false positives reach the model.

## Phase 2 — Hidden browser platform (Playwright for Python)

- [ ] **B2.1 Browser engine.** One worker thread owns Playwright's sync API and
  a persistent context in `DATA_DIR/browser_profile` (`channel="msedge"`).
  Modes: `headless` (default), `offscreen` (for SSO that refuses headless),
  `visible`. Idle shutdown, failure screenshots, `browsing` activity.
- [ ] **B2.2 Sign-in.** "Sign in" reopens the profile visibly, waits until the
  connector sees a signed-in page, then returns to hidden. Routes under
  `/api/integrations/{name}/auth`. Expired sessions surface as a "Sign in
  again" card.
- [ ] **B2.3 Connector base.** Selectors live in overridable JSON
  (`DATA_DIR/connectors/<name>.json`) so a tenant's layout can be tuned without
  a code change. Helpers for navigation, readable-text extraction and
  authenticated in-page `fetch`.
- [ ] **B2.4 Generic approvals.** `AgentAction` (tool, args, before/after
  preview, status) beside the existing email/Teams drafts.
- [ ] **B2.5 Settings & packaging.** Integrations settings group;
  `backend/requirements-browser.txt`; PyInstaller hooks. No bundled Chromium —
  the installed Edge is used.

## Phase 3 — Dynamics 365

- [ ] **B3.1 Connector.** Uses the Dataverse Web API from inside the signed-in
  page (the user's own session authenticates it — no app registration).
- [ ] **B3.2 Tools.** Search cases, read a case with its timeline, add a note,
  update fields, resolve — writes behind approval with a field diff.
- [ ] **B3.3 Context.** When the extension sees a Dynamics case open, Cerebro
  reads it in the background so Ask already knows it.

## Phase 4 — RightAnswers

- [ ] **B4.1 Connector.** Search, open and extract articles via configurable
  selectors (calibrated once against the real tenant).
- [ ] **B4.2 Tools.** Search, read, update and create articles (writes behind
  approval with a body diff); draft a KB article from a resolved case.
- [ ] **B4.3 Knowledge upsert.** Re-reading an article updates its indexed copy
  instead of duplicating it.

## Phase 5 — Modern UI

- [ ] **B5.1 Design system** (`static/ui/`): dark-first glass surfaces on the
  logo gradient, Inter, motion, light theme, reduced-motion support. No build
  step.
- [ ] **B5.2 App page** (`/app`): streaming Ask with tool timeline, source pills,
  approval diffs; Sources, Activity and Integrations tabs; animated brain in
  the header.
- [ ] **B5.3 Desktop shell** (`desktop/shell.py`, pywebview): frameless, always on
  top, snap/opacity/compact via a JS bridge.
- [ ] **B5.4 Restyle** dashboard, settings and setup on the same system.
- [ ] **B5.5 Packaging** for pywebview.

## Phase 6 — Runs in the background, with a tray brain

- [ ] **B6.1 Tray** (`desktop/tray.py`, pystray): closing the window hides it;
  menu with Open, Quick Ask, Integrations, Pause capture, Dashboard, Settings,
  Start with Windows, Quit. Quit stops the server too
  (`POST /api/system/shutdown`). Single instance. Start with Windows launches
  straight to the tray.
- [ ] **B6.2 Brain animation.** Generated frame sets per state — breathing
  (idle), sparkling neurons (thinking), scanning eyes (browsing), bouncing "!"
  (approval), sound waves (listening), orbit (syncing), dizzy (error),
  sleeping (offline). The same brain animates in the app header.

---

## Needed from the team

- RightAnswers base URL, and whether sign-in is SSO or a username/password form.
- Dynamics org URL (`https://<org>.crm.dynamics.com`).
- One session on a real machine to calibrate RightAnswers selectors.
