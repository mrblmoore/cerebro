# Outlook and Teams, via Power Automate

Cerebro never talks to Microsoft 365 directly. Power Automate owns the
connectors and the authentication, and hands work over as JSON files in a
folder. Cerebro reads that folder.

```
Outlook / Teams
      ↓  (Power Automate flow)
  inbound folder  ──▶  Cerebro ingests, triages, drafts
      ▲                            ↓
      └────  Power Automate  ◀── outbound folder
```

**Why a folder.** No app registration, no client secret, no Graph permissions to
get approved, and nothing outbound from your machine. Power Automate already has
sanctioned access to your mailbox; this borrows it. It also means you can see
exactly what crossed the boundary — the files are right there, readable.

---

## 1. Choose the folders

Anywhere both Power Automate and Cerebro can reach. A OneDrive-synced folder is
the normal choice, because Power Automate writes to OneDrive and the sync client
brings the file down to your machine.

```
C:\Users\<you>\OneDrive\Cerebro\enterprise-inbox    Power Automate writes here
C:\Users\<you>\OneDrive\Cerebro\enterprise-outbox   Cerebro writes here
```

Create both. Cerebro adds a `processed` folder inside the inbox itself.

## 2. Point Cerebro at them

**Settings → Outlook & Teams**, turn on the bridge, paste both paths, then press
**Test connection**. Cerebro sweeps the inbox every few seconds from then on —
no separate importer to keep running.

## 3. Build the inbound flow — Outlook

In Power Automate, **Create → Automated cloud flow**.

1. **Trigger:** *When a new email arrives (V3)* (Office 365 Outlook).
   Set `Include Attachments` to No. Add a folder or subject filter if you only
   want some mail to reach Cerebro.

2. **Action:** *Create file* (OneDrive for Business).
   - **Folder path:** `/Cerebro/enterprise-inbox`
   - **File name:**
     ```
     outlook-@{utcNow('yyyyMMdd-HHmmssfff')}.json
     ```
     The timestamp keeps names unique. Two messages in the same millisecond
     would collide, which is why Cerebro also de-duplicates on message id.
   - **File content:** switch to expression view and paste:

     ```json
     {
       "source": "outlook",
       "type": "email",
       "external_id": @{json(concat('"', triggerOutputs()?['body/internetMessageId'], '"'))},
       "timestamp": "@{triggerOutputs()?['body/receivedDateTime']}",
       "sender": "@{triggerOutputs()?['body/from']}",
       "sender_name": @{json(concat('"', replace(coalesce(triggerOutputs()?['body/sender/emailAddress/name'], ''), '"', ''), '"'))},
       "recipients": "@{triggerOutputs()?['body/toRecipients']}",
       "subject": @{json(concat('"', replace(coalesce(triggerOutputs()?['body/subject'], ''), '"', '\\"'), '"'))},
       "body": @{json(concat('"', replace(replace(coalesce(triggerOutputs()?['body/bodyPreview'], ''), '\\', '\\\\'), '"', '\\"'), '"'))},
       "thread_id": "@{triggerOutputs()?['body/conversationId']}",
       "metadata": {
         "importance": "@{triggerOutputs()?['body/importance']}",
         "has_attachments": "@{triggerOutputs()?['body/hasAttachments']}"
       }
     }
     ```

     Escaping quotes matters: a subject line containing `"` produces invalid
     JSON otherwise, and Cerebro will reject the file rather than guess.
     Using `bodyPreview` keeps files small; use `body` instead if you want the
     full message — Cerebro strips the HTML either way.

3. Save and send yourself a test email.

## 4. Build the inbound flow — Teams

Same shape, different trigger.

1. **Trigger:** *When a new channel message is added* or
   *When I am mentioned in a channel message* (Microsoft Teams). The mention
   trigger is usually the better one — it is the message that actually needs you.

2. **Action:** *Create file* (OneDrive for Business), folder
   `/Cerebro/enterprise-inbox`, file name
   `teams-@{utcNow('yyyyMMdd-HHmmssfff')}.json`, content:

   ```json
   {
     "source": "teams",
     "type": "message",
     "external_id": "@{triggerOutputs()?['body/id']}",
     "timestamp": "@{triggerOutputs()?['body/createdDateTime']}",
     "sender": "@{triggerOutputs()?['body/from/user/displayName']}",
     "chat_or_channel": "@{triggerOutputs()?['body/channelName']}",
     "body": @{json(concat('"', replace(replace(coalesce(triggerOutputs()?['body/body/content'], ''), '\\', '\\\\'), '"', '\\"'), '"'))},
     "thread_id": "@{triggerOutputs()?['body/replyToId']}"
   }
   ```

## 5. Build the outbound flow

Cerebro writes a reply as JSON; this flow sends it.

1. **Trigger:** *When a file is created* (OneDrive for Business), folder
   `/Cerebro/enterprise-outbox`.
2. **Action:** *Parse JSON* on the file content, using the outbound schema
   [below](#outbound-payload).
3. **Action:** *Condition* on `action`:
   - `reply_email` or `send_email` → *Send an email (V2)*, with `to`, `subject`
     and `body` from the payload.
   - `reply_teams_message` or `send_teams_message` → *Post message in a chat or
     channel*, using `chat_or_channel` and `body`.
4. **Action:** *Delete file*, so the same reply is never sent twice.

Cerebro writes each file to a temporary name and renames it into place, so this
flow never picks up a half-written file.

---

## Payload reference

### Inbound

Only `source` and `body` are really needed. Everything else improves what
Cerebro can do with the message.

| Field | Notes |
|---|---|
| `source` | `outlook` or `teams`. Required. |
| `type` | `email`, `message`, `mention`… |
| `external_id` | Provider message id. Prevents duplicates when a flow re-runs. Strongly recommended. |
| `timestamp` | ISO 8601. |
| `sender` | Address or display name. |
| `sender_name` | Display name, when you have both. |
| `recipients` | List or comma-separated string. |
| `chat_or_channel` | Teams channel or chat name. |
| `subject` | |
| `body` | Plain text or HTML — HTML is stripped on the way in. |
| `thread_id` | Conversation id, used to group a thread. |
| `metadata.importance` | `high` marks the message urgent. |
| `case_id` | Set it if your flow knows it; otherwise Cerebro finds case references in the text. |

Cerebro also accepts the raw Graph field names (`from`, `toRecipients`,
`receivedDateTime`, `bodyPreview`, `conversationId`), so a flow built by dragging
dynamic content straight in usually works without reshaping.

A file may contain one object or an array of them, if you batch.

### Outbound payload

```json
{
  "action": "reply_email",
  "source": "outlook",
  "to": ["person@company.com"],
  "chat_or_channel": null,
  "thread_id": "abc123",
  "subject": "Re: Need update on customer case",
  "body": "Latest status attached — I'll follow up at 4pm.",
  "cerebro_action_id": 12,
  "created_at": "2026-08-17T15:41:02Z"
}
```

Actions: `send_email`, `reply_email`, `send_teams_message`,
`reply_teams_message`.

Schema for *Parse JSON*:

```json
{
  "type": "object",
  "properties": {
    "action": { "type": "string" },
    "source": { "type": "string" },
    "to": { "type": "array", "items": { "type": "string" } },
    "chat_or_channel": {},
    "thread_id": {},
    "subject": {},
    "body": { "type": "string" },
    "cerebro_action_id": { "type": "integer" },
    "created_at": { "type": "string" }
  },
  "required": ["action", "body"]
}
```

---

## What Cerebro does with it

Ingested messages are triaged, linked to a case where one is mentioned, and
surfaced in the dashboard, the widget's **Inbox** tab, and the API:

```bash
# What arrived and what needs attention
curl "http://localhost:8000/api/enterprise/briefing?hours=12"

# Draft a reply (needs an AI provider)
curl -X POST http://localhost:8000/api/enterprise/messages/3/draft \
  -H "Content-Type: application/json" \
  -d '{"instruction": "Say the fix is deployed and we are monitoring."}'

# Queue it for the outbound flow to send
curl -X POST http://localhost:8000/api/enterprise/actions \
  -H "Content-Type: application/json" \
  -d '{"action": "reply_email", "in_reply_to": 3, "body": "...", "send": true}'
```

**Replies wait for you.** A draft stays in Cerebro until approved — writing to
the outbox *is* sending, because your flow is watching that folder. Turn on
*Send replies without approval* in Settings only if you mean it.

---

## Running the importer by hand

The backend sweeps the inbox itself, so this is only for one-off imports,
replaying an archive, or feeding a Cerebro on another machine:

```bash
python backend/enterprise_ingest.py "C:\Users\you\OneDrive\Cerebro\enterprise-inbox" --watch
python cerebro.py inbox --watch                       # same thing, shorter
python cerebro.py inbox <folder> --api http://other-pc:8000
```

## Troubleshooting

**Files pile up and nothing is ingested.**
The bridge is off, or pointed elsewhere. Check **Settings → Outlook & Teams →
Test connection**, and confirm the path matches where OneDrive actually syncs.

**A file lands in `processed/failed/`.**
It was not valid JSON — almost always an unescaped quote in a subject or body.
Open the file; the offending character is usually obvious. The `replace(...)`
expressions above exist to prevent this.

**Messages arrive twice.**
The flow is not sending `external_id`. Cerebro falls back to hashing the content,
which catches exact repeats but not a re-send with a changed timestamp. Add
`external_id` to the flow.

**Nothing is marked urgent.**
Urgency comes from the words in the message and the sender's importance flag.
`GET /api/enterprise/messages` shows `urgency_reason` for each one, so you can
see what it keyed on.
