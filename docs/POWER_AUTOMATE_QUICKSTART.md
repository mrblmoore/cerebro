# Outlook, Teams, and Dynamics 365 — the fast setup

Three connections people usually want, three very different amounts of work:

| | Setup needed |
|---|---|
| **Dynamics 365 CRM** | None for viewing cases (browser extension). One import, for case-update notifications even when the case isn't open. |
| **Outlook** | Import one file, pick your account, done. |
| **Teams** | Import the same file, then pick your team and channel in the inbound trigger and outbound action — those IDs are per-tenant, so they can't be pre-filled. |

This page is the short version. `docs/POWER_AUTOMATE.md` has the full field-by-field
reference if you ever want to change what a flow does.

## Dynamics 365 CRM — case viewing needs nothing

The browser extension recognizes a Dynamics case page from its URL and title,
the same way it recognizes Salesforce and Zendesk — no app registration, no
API key, no flow. If Cerebro isn't picking up your cases, it's almost always
that the extension isn't installed or enabled — check `chrome://extensions`,
not this doc.

*Case updates while you're not looking at them* (a customer reply, a
reassignment) come from the imported **Cerebro - Dynamics 365 case updates**
flow below — optional, and separate from viewing.

## 1. Create the folders (the wizard can do this for you)

The setup wizard's **Microsoft 365** step has a **"Create the OneDrive folders
for me"** button — it finds your synced OneDrive automatically and creates:

```
OneDrive\Cerebro\enterprise-inbox
OneDrive\Cerebro\enterprise-outbox
```

If that button can't find OneDrive on your machine (uncommon — usually means
OneDrive isn't signed in yet), create those two folders yourself anywhere
inside your synced OneDrive.

## 2. Import the package

1. Go to [make.powerautomate.com](https://make.powerautomate.com).
2. **My flows → Import → Import Package (Legacy)**.
3. Upload the zip. Cerebro ships it at:
   - Installed app: `power_automate\Cerebro-Bridge.zip`, next to `Cerebro.exe`
     — the wizard's **"Reveal the flow package"** button opens this folder directly.
   - Source checkout: `packaging\power_automate\dist\Cerebro-Bridge.zip`
     (rebuild it with `python packaging\build_power_automate_package.py` if it's missing).
4. It lists four flows, each with connections marked *Select during import*.
   For each, choose *"Create new"* and sign in with the account whose mailbox
   and OneDrive you created the folders in (the same account for all four —
   Dynamics and Teams reuse the OneDrive connection to write into the same
   inbox folder).
   - Only using some of these? Untick the ones you don't want before
     finishing the import, or import all four and just leave the unused
     ones **Off** (see step 4).
5. **Import**.
6. Open each flow you want and turn it **On** — imported flows start stopped.
   Before turning on either Teams path, replace the visible setup markers:
   - In **Cerebro - Teams inbound**, open the trigger and pick your **Team** and
     **Channel**.
   - In **Cerebro - Microsoft 365 outbound**, open the Teams action in the
     **If no** branch and pick its **Team** and **Channel**. This becomes the
     destination for approved Teams posts from Ask.

## 3. Point Cerebro at the inbox folder

**Settings → Outlook & Teams** in Cerebro (or the setup wizard's *Microsoft 365*
step) → paste the inbox folder's real filesystem path (wherever OneDrive syncs
it locally, e.g. `C:\Users\you\OneDrive\Cerebro\enterprise-inbox` — already
filled in if you used the "Create the OneDrive folders for me" button) →
**Test connection**.

## 4. Send yourself a test email (or message, or update a case)

It should appear in Cerebro's Inbox tab within a few seconds. If it doesn't,
see *Troubleshooting* in `docs/POWER_AUTOMATE.md`.

## What each imported flow does

| Flow | Trigger | Needs editing after import? |
|---|---|---|
| Cerebro - Outlook inbound | New email arrives | No |
| Cerebro - Microsoft 365 outbound | A file appears in the outbox | For Teams only — pick the destination |
| Cerebro - Teams inbound | New channel message | Yes — pick team/channel |
| Cerebro - Dynamics 365 case updates | A Case is created or changed | No |

**Why Teams needs a manual selection.** Outlook's triggers take no
tenant-specific identifiers, and the Dataverse Case table uses the logical
name `incident`. Teams requires the exact Team and Channel IDs for both
watching and posting; those are unique to your organization and cannot be
guessed safely.

**Why the outbound Teams step says V3.** The package uses the Teams
connector's fixed-schema channel action for import compatibility. The newer
"Post message in a chat or channel" action asks Microsoft for a dynamic
tenant-specific schema while the legacy package is still importing, which can
fail with `GetUnifiedActionSchema` / `Unauthorized` before the selected
connection is available. The packaged action avoids that import-time lookup;
after import, the only setup is selecting the destination Team and Channel.
