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

- [x] **B1.1 Retrieval relevance.** Minimum score (`ASK_MIN_SOURCE_SCORE`) and
  a relative cutoff in `text_chunks.rank` / `RAGService` search; documents are
  "active" only for `ASK_ACTIVE_DOCUMENT_MINUTES` after last being seen.
- [x] **B1.2 Prompt structure.** Ask-specific system prompt; history as real
  turns; sources in their own context block before the question, only when
  relevant.
- [x] **B1.3 Agent tool loop.** `app/services/agent/` — a tool registry and a
  bounded loop (`ASK_MAX_STEPS`). The model decides when to search knowledge,
  sources, local files and the database, read a document, check the inbox,
  draft a reply or create a task.
- [x] **B1.4 No keyword hijacks.** Only approve/discard and image questions are
  routed deterministically; everything else reaches the model. The no-AI
  fallback cites only genuinely relevant sources.
- [x] **B1.5 Streaming.** `POST /api/chat/stream` (SSE) streams tool progress,
  approval cards and the answer.
- [x] **B1.6 Tests.** Off-topic questions ignore stale documents; different
  questions get different prompts; the tool loop runs against a scripted
  provider; keyword false positives reach the model.

## Phase 2 — Hidden browser platform (Playwright for Python)

- [x] **B2.1 Browser engine.** One worker thread owns Playwright's sync API and
  a persistent context in `DATA_DIR/browser_profile` (`channel="msedge"`).
  Modes: `headless` (default), `offscreen` (for SSO that refuses headless),
  `visible`. Idle shutdown, failure screenshots, `browsing` activity.
- [x] **B2.2 Sign-in.** "Sign in" reopens the profile visibly, waits until the
  connector sees a signed-in page, then returns to hidden. Routes under
  `/api/integrations/{name}/auth`. Expired sessions surface as a "Sign in
  again" card.
- [x] **B2.3 Connector base.** Selectors live in overridable JSON
  (`DATA_DIR/connectors/<name>.json`) so a tenant's layout can be tuned without
  a code change. Helpers for navigation, readable-text extraction and
  authenticated in-page `fetch`.
- [x] **B2.4 Generic approvals.** `AgentAction` (tool, args, before/after
  preview, status) beside the existing email/Teams drafts.
- [x] **B2.5 Settings & packaging.** Integrations settings group;
  `backend/requirements-browser.txt`; PyInstaller hooks. No bundled Chromium —
  the installed Edge is used.

## Phase 3 — Dynamics 365

- [x] **B3.1 Connector.** Uses the Dataverse Web API from inside the signed-in
  page (the user's own session authenticates it — no app registration).
- [x] **B3.2 Tools.** Search cases, read a case with its timeline, add a note,
  update fields, resolve — writes behind approval with a field diff.
- [x] **B3.3 Context.** When the extension sees a Dynamics case open, Cerebro
  reads it in the background so Ask already knows it.

## Phase 4 — RightAnswers

- [x] **B4.1 Connector.** Search, open and extract articles via configurable
  selectors. *Still to do: calibrate the selectors once against the real
  tenant* (see docs/INTEGRATIONS.md).
- [x] **B4.2 Tools.** Search, read, update and create articles (writes behind
  approval with a body diff); draft a KB article from a resolved case.
- [x] **B4.3 Knowledge upsert.** Re-reading an article updates its indexed copy
  instead of duplicating it.

## Phase 5 — Modern UI

- [x] **B5.1 Design system** (`static/ui/`): dark-first glass surfaces on the
  logo gradient, Inter, motion, light theme, reduced-motion support. No build
  step.
- [x] **B5.2 App page** (`/app`): streaming Ask with tool timeline, source pills,
  approval diffs; Sources, Activity and Integrations tabs; animated brain in
  the header.
- [x] **B5.3 Desktop shell** (`desktop/shell.py`, pywebview): frameless, always on
  top, snap/opacity/compact via a JS bridge.
- [x] **B5.4 Restyle** dashboard, settings and setup on the same system.
- [x] **B5.5 Packaging** for pywebview.

## Phase 6 — Runs in the background, with a tray brain

- [x] **B6.1 Tray** (`desktop/tray.py`, pystray): closing the window hides it;
  menu with Open, Quick Ask, Integrations, Pause capture, Dashboard, Settings,
  Start with Windows, Quit. Quit stops the server too
  (`POST /api/system/shutdown`). Single instance. Start with Windows launches
  straight to the tray.
- [x] **B6.2 Brain animation.** One loop per state, in the tray and the app
  header. Replaced by the pixel mascot in B8.1.

---

## Phase 7 — Company setup and SharePoint

- [x] **B7.1 Addresses.** `dental.crm.dynamics.com`, `dexis.rightanswers.com`
  and `envistaconnect.sharepoint.com` are pre-filled. Addresses are reduced to
  their site, and **Connect** switches a system on and signs in with one click.
- [x] **B7.2 Dynamics metadata.** Priority, severity and status labels come from
  the org's own option sets. The connection check shows who is signed in.
- [x] **B7.3 RightAnswers Teach.** Learns the portal's search, article, result
  and editor layout from one search by the user. It saves a diagnostic bundle,
  and searches fall back to article-looking links.
- [x] **B7.4 SharePoint connector.** Opens pasted links (files, sharing links,
  Office Online links, site pages, OneDrive), reads and cites them, and
  searches the site. It changes Word and Excel files and pages, with approval
  and before/after previews, refusing a change if the file moved on since the
  preview.
- [ ] **B7.5 Real-tenant check** on a work machine: Connect all three, run Teach
  for RightAnswers, and try one read and one approved change in each.

---

## Phase 8 — Pixel mascot, desktop buddy, chats and Bedrock

- [x] **B8.1 Pixel mascot.** The two pixel-art brains (laptop = working,
  graduation cap and book = studying) are animated by
  `packaging/make_mascot.py`. Dozing, alert, dizzy and asleep poses are
  derived from the same art. They are used in the tray, the app header, the
  hero, the answer-in-progress bubble and the Activity card, which picks
  studying for research steps and working for the rest.
- [x] **B8.2 Desktop buddy** (`desktop/buddy.py`). A small, closable animated
  brain on the desktop while Cerebro works, with a caption of what it is
  doing. × hides it until the next task; the tray toggles it.
- [x] **B8.3 Chats.** Separate conversations in Ask with their own history,
  titles, pin, search, end (archive) and delete. Older messages are moved
  to "Earlier chat".
- [x] **B8.4 Per-chat instructions** reach the agent with every message in
  that chat.
- [x] **B8.5 Tasks assigned to a chat.** The `agent` task kind runs Ask's loop
  on a schedule and posts the result into its chat; approvals wait there.
- [x] **B8.6 Bedrock.** Models that refuse tool use or system prompts fall back
  automatically instead of failing as a "bad model ID". Nova uses temperature
  0 when choosing tools.
- [x] **B8.7 Automatic SharePoint updates.** An opt-in setting (Connect tab
  toggle) applies SharePoint document and page changes without approval. Every
  SharePoint change, automatic or approved, can be undone for 30 days, and Undo
  refuses if someone edited the item since. Chats show each change's current
  status after a reload.
- [ ] **B8.8 Check on Windows** with the user's Bedrock models: the buddy's
  transparency, a scheduled chat task, and tool use with Nova and Llama.

---

## Phase 9 — Window size, RightAnswers workspace, Outlook and Teams (0.5.0)

- [x] **B9.1 Resizable window.** Resize handles on the frameless window, Expand
  and double-click to maximise, Window size presets, Text size, and the
  window kept inside the screen it is on. Narrow windows tighten the layout
  instead of cutting it off.
- [x] **B9.2 RightAnswers workspace.** Starts from SolutionManager's
  `/solutionmanger/controller/workspace/`, with a spelling fallback, frame-aware
  search and reading, and Teach that recognises in-page searches.
- [x] **B9.3 Outlook and Teams connectors.** Read from the apps' own data,
  search, read, reply and send through their compose screens; approval by
  default, "Send replies without asking" per app.
- [x] **B9.4 Inbox monitor.** Checks every minute, stores new messages,
  notifies about important ones, and researches them in a pinned Inbox chat
  with a draft reply (capped per hour).
- [ ] **B9.5 Real-tenant check** of Outlook and Teams on a work machine:
  connect both, receive a test email and @mention, approve a reply in each.

## Phase 10 — Add-ons and voice (0.7.0)

- [x] **B10.1 Add-ons at runtime.** A capabilities store under
  `capabilities\`, with tools that appear and disappear without a restart,
  and `find_tools` when there are many.
- [x] **B10.2 MCP client.** Streamable HTTP, SSE and stdio; OAuth sign-in
  with discovery, client registration and PKCE; keys and tokens encrypted
  with DPAPI; uv downloaded on demand. Read-only tools run; others need
  approval unless the server is set to auto-approve.
- [x] **B10.3 Any website.** A connector made from an address that learns its
  own search, plus `browse_*` tools. A click that submits becomes an approval
  card with a screenshot. **Take control** hands the browser to the user.
- [x] **B10.4 Skills.** Agent Skills `SKILL.md`: saved, imported and used by
  name.
- [x] **B10.5 Cerebro adds what it needs.** `find_connector` (the MCP
  Registry), `add_mcp_server`, `add_website` and `save_skill`, with an
  "Added" card that can be removed. A local program it chose itself asks
  once.
- [x] **B10.6 Add-ons UI.** A Connect-tab section with Add, enable,
  auto-approve, keys, sign-in, export and import.
- [x] **B10.7 Voice in.** Push-to-talk (Ctrl+Space) and a mic button, with
  faster-whisper on the PC and domain-word hints.
- [x] **B10.8 Voice mode.** Short spoken answers, a plan card for bigger
  jobs, 12 steps, and spoken approvals ("yes, send it").
- [x] **B10.9 Voice out.** Polly, then Edge voices, then the Windows voice.
- [ ] **B10.10 Real-world check** on a work PC: add a real MCP server from the
  Registry, connect an intranet site, and run a push-to-talk reply end to end.

## Phase 11 — Agents, faces and OpenMuse-style work (0.7.0)

- [x] **B11.1 Agents.** Named agents sharing one memory and the same tools,
  each with its own role, instructions, chats, jobs, face, voice and tool
  scope. They can hand work to each other, and a spoken name sends a request
  to that agent.
- [x] **B11.2 Durable jobs.** Plans, a run log, questions for you, approval
  waits that resume, pause/resume/cancel/retry, leases, backoff, and
  recovery after a restart (no retry after an automatic change).
- [x] **B11.3 Projects and milestones.**
- [x] **B11.4 Watches.** Change, contains, absent, below and above
  conditions; one alert per change; follow-up jobs; backoff and pause.
- [x] **B11.5 Ideas.** From nudges, quiet cases, repeated questions and
  watches (plus an optional AI pass), routed by scope; accept or dismiss.
- [x] **B11.6 Grok-style layout.** Agents sidebar (full, rail, or drawer),
  Home board, Work view, the face stage, and the agent editor.
- [x] **B11.7 Faces.** An original anime face (six colours, expressions,
  lip sync); Live2D and VRM models you load; desktop strips for the buddy
  and tray, with the buddy's mouth moving in time with speech.
- [ ] **B11.8 Real-world check** on a work PC: three agents running jobs
  side by side, a watch on a real case, and a VRM face speaking a reply.

## Phase 12 — "Do it all": keep it updated, desktop apps, secretary (0.8.0)

- [x] **B12.1 Keep it updated.** `keep_updated` connects the target
  (SharePoint, a website, a Word/Excel file, a desktop program, an add-on),
  makes the job, watches its source, and posts a "Keeping X updated" card
  with Run now / Pause / Stop.
- [x] **B12.2 Automatic changes per target.** Switches for each website,
  each desktop app, Dynamics, RightAnswers and Word/Excel. Automatic clicks
  keep after-screenshots; an unconfirmed result is flagged, never retried.
- [x] **B12.3 Windows desktop apps.** pywinauto (UI Automation): the
  `app_*` tools, a "Desktop app" add-on kind, approval for save/send/OK and
  Ctrl+S, and a scope group. Tested against a fake desktop everywhere and
  against the real Notepad on Windows CI.
- [x] **B12.4 Documents in chat.** 📎, drag-and-drop and paste for Word,
  Excel, PDF, logs and more (50 MB), with file chips. `edit_document` for
  Word and Excel, with backups and Undo.
- [x] **B12.5 File watches.** Local and synced files, checked first by
  modified time and size.
- [x] **B12.6 Remembering how.** A job saves its working steps as a skill
  and follows them on later runs.
- [x] **B12.7 Secretary.** Calendar (read, free time, book / move / cancel
  with approval), meeting briefs, follow-ups and to-dos with chasing,
  morning / end-of-day / weekly briefings, meeting notes, and documents
  from templates. Home → Today.
- [x] **B12.8 Fix.** `browse_open`, `browse_read` and website read tools
  declared an activity state the tracker rejected, so they failed when
  called from a chat. Tool states are now checked when tools register.
- [ ] **B12.9 Real-world check** on a work PC: keep a SharePoint page
  updated from a synced Excel export, drive an in-house desktop tool, and
  get a morning briefing from the real calendar.

## Phase 13 — A cuter face, expressive models and an expressive voice (0.9.0)

- [x] **B13.1 Idol-style anime face.** Redrawn: glossy sparkle eyes,
  layered shiny hair with an ahoge, a sailor collar and a heart headset.
  Four hairstyles (long, twin-tails, bob, side ponytail) and 14 expressions.
- [x] **B13.2 Reactions.** The face reacts to how things turned out (done,
  failed, needs approval, approved, discarded, thanked), on the stage and on
  the desktop buddy.
- [x] **B13.3 Model expressions.** VRM and Live2D expressions are matched to
  Cerebro's states by name, editable with weights and a preview; framing
  (zoom, height, mirror).
- [x] **B13.4 Model life and lip sync.** Natural blinks, gaze and head
  following the cursor, breathing, nods; vowel lip sync from five speech
  bands; Live2D parameters and tap motions.
- [x] **B13.5 VRMA gestures.** `.vrma` files per VRM model, played on
  events, via three-vrm-animation (MIT, vendored).
- [x] **B13.6 Desktop strips** for every colour × hairstyle, quantized
  (about 4 MB in all).
- [x] **B13.7 Personality.** Presets (sweetheart by default: cute,
  cheerful, teasing), sliders and custom text in each agent's system
  prompt, with guardrails; customer drafts keep the user's style.
- [x] **B13.8 Expressive speech.** A cue language (giggle, gasp, whisper,
  tease…), never shown in chat, rendered per voice: Polly SSML per engine
  (generative, neural, long-form, standard) with fallbacks, Edge and
  Windows prosody.
- [x] **B13.9 Amazon Nova Sonic.** Replies voiced in character, and **Talk
  live**: a WebSocket with a bidirectional Nova Sonic stream, barge-in, and
  `ask_cerebro` handing real work to the agent loop with approvals.
- [ ] **B13.10 Real-world check** with AWS credentials: Polly generative
  with cues, Nova Sonic reading a reply, and a Talk live session; a real
  VRM with a `.vrma` gesture and a real Live2D model.

## Phase 14 — Works on a locked-down network; nicer avatars; Settings in the app (0.10.0)

- [x] **B14.1 Offline speech-to-text.** faster-whisper `base.en` ships inside the installer and is loaded
  from its folder (never from the model site); other sizes import from a zip.
- [x] **B14.2 Kokoro offline voice.** Kokoro-82M (Apache-2.0) via onnxruntime, with Cerebro's own
  text-to-phoneme converter on misaki's dictionaries (no eSpeak/GPL); cues as speed/loudness;
  Best available = offline first; blocked hosts named in errors.
- [x] **B14.3 Built-in 3D characters.** VRM models (licences allowing redistribution) in `assets/faces/vrm/`, gallery, tuned defaults,
  pre-rendered buddy strips (`make_face.py --vrm`), read-only.
- [x] **B14.4 Desktop buddy.** Follows the agent you're talking to; shows VRM faces from strips the app
  renders (`face.js capture()`).
- [x] **B14.5 Settings in the app.** One module for the app view and `/settings`; tray, widget and links
  open it in the window.
- [x] **B14.6 Installer.** Everything bundled (models, voice, uv); setup pages applied and tested by the
  headless wizard; clean upgrades; clearing parts of the old memory after a backup; new artwork.
- [x] **B14.7 Fixes.** The hidden browser no longer hangs on an aborted response or runs two workers;
  Nova Sonic keeps botocore's awscrt.
- [x] **B14.8 Built-in models.**
  - Stella (AvatarSample_B), Kaori, Kuro and Seed-san, each with pixiv's idle loop as a resting motion.
  - Every model's own licence allows redistribution, changes and company use.
  - Fetched at pinned commits with SHA-256 checks.
- [x] **B14.10 Live2D in one click.**
  - Live2D's runtime and six free sample models are downloaded from Live2D after you agree.
  - Available in the editor and from an installer checkbox; never bundled, because Live2D's licence treats an
    app that loads any model as an "Expandable Application".
- [x] **B14.11 Fixes.** PyAV is pinned below 19, which broke faster-whisper's decoding.
- [ ] **B14.9 Real-world check** on the work network: install over 0.9, push-to-talk, Kokoro, Talk live,
  and the buddy following the active agent.

