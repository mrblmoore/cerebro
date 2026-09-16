#!/usr/bin/env python3
"""
Builds ``dist/Cerebro-Bridge.zip`` — a Power Automate "Package (.zip)" that
can be imported with **My flows → Import → Import Package (Legacy)** instead
of building flows by hand in the designer, field by field.

Run standalone:

    python packaging/build_power_automate_package.py

``build_windows.bat`` runs this before PyInstaller so the zip is always fresh
in the shipped installer; ``cerebro.spec`` bundles the output alongside the
other docs.

Four flows ship, at different levels of "done":

* **Outlook inbound/outbound** — fully wired. ``OnNewEmailV3``/``SendEmailV2``
  and OneDrive's ``CreateFile``/``OnNewFile``/``DeleteFile`` take no
  tenant-specific IDs, so nothing needs editing after import beyond picking
  a connection.
* **Dynamics 365 case updates** — also fully wired. The Case table's logical
  name (``incidents``) is identical in every Dynamics environment, unlike a
  Teams team/channel, so this is as safe to pre-fill as Outlook.
* **Teams inbound** — wired end-to-end *except* the trigger's ``teamId``/
  ``channelId``, which are unique per tenant and genuinely cannot be known
  ahead of time. Importing this still saves building the whole flow; the one
  remaining step is picking your team and channel from the trigger's own
  dropdowns in the designer (see docs/POWER_AUTOMATE_QUICKSTART.md).
"""

import json
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "packaging" / "power_automate" / "dist"
OUT_ZIP = OUT_DIR / "Cerebro-Bridge.zip"

INBOX_PATH = "/Cerebro/enterprise-inbox"
OUTBOX_PATH = "/Cerebro/enterprise-outbox"


def _connection_reference(api_name: str) -> dict:
    """
    An unresolved connection reference. Power Automate's import wizard shows
    each of these as "Select during import" and lets the user pick or create
    the real connection — nothing here needs to be a genuine identifier.
    """
    return {
        "connectionName": "",
        "source": "Invoker",
        "id": f"/providers/Microsoft.PowerApps/apis/{api_name}",
        "tier": "NotSpecified",
    }


def _wrapped_flow(flow_id: str, display_name: str, definition: dict, connection_refs: dict) -> dict:
    return {
        "name": flow_id,
        "id": f"/providers/Microsoft.Flow/flows/{flow_id}",
        "type": "Microsoft.Flow/flows",
        "properties": {
            "apiId": "/providers/Microsoft.PowerApps/apis/shared_logicflows",
            "displayName": display_name,
            "definition": definition,
            "connectionReferences": connection_refs,
            "flowFailureAlertSubscribed": False,
            "isManaged": False,
        },
    }


def _inbound_definition() -> dict:
    """Outlook → a JSON file in the Cerebro inbox folder. Field mapping mirrors
    docs/POWER_AUTOMATE.md's inbound-Outlook payload exactly."""
    return {
        "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {"$connections": {"defaultValue": {}, "type": "Object"}},
        "triggers": {
            "When_a_new_email_arrives": {
                "type": "OpenApiConnectionWebhook",
                "inputs": {
                    "host": {
                        "connectionName": "shared_office365",
                        "operationId": "OnNewEmailV3",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_office365",
                    },
                    "parameters": {
                        "folderPath": "Inbox",
                        "importance": "Any",
                        "fetchOnlyWithAttachment": False,
                        "includeAttachments": False,
                    },
                },
            }
        },
        "actions": {
            "Create_file": {
                "type": "OpenApiConnection",
                "inputs": {
                    "host": {
                        "connectionName": "shared_onedriveforbusiness",
                        "operationId": "CreateFile",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_onedriveforbusiness",
                    },
                    "parameters": {
                        "source": "me",
                        "folderPath": INBOX_PATH,
                        "name": "outlook-@{formatDateTime(utcNow(),'yyyyMMdd-HHmmssfff')}.json",
                        "body": {
                            "source": "outlook",
                            "type": "email",
                            "external_id": "@triggerOutputs()?['body/internetMessageId']",
                            "timestamp": "@triggerOutputs()?['body/receivedDateTime']",
                            "sender": "@triggerOutputs()?['body/from']",
                            "sender_name": "@triggerOutputs()?['body/sender']?['emailAddress']?['name']",
                            "recipients": "@triggerOutputs()?['body/toRecipients']",
                            "subject": "@triggerOutputs()?['body/subject']",
                            "body": "@triggerOutputs()?['body/bodyPreview']",
                            "thread_id": "@triggerOutputs()?['body/conversationId']",
                            "metadata": {
                                "importance": "@triggerOutputs()?['body/importance']",
                                "has_attachments": "@triggerOutputs()?['body/hasAttachments']",
                            },
                        },
                    },
                },
                "runAfter": {},
            }
        },
        "outputs": {},
    }


def _outbound_definition() -> dict:
    """A file appearing in the Cerebro outbox → sent as email (or, best-effort,
    a Teams message) then deleted so it is never sent twice."""
    return {
        "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {"$connections": {"defaultValue": {}, "type": "Object"}},
        "triggers": {
            "When_a_file_is_created": {
                "type": "OpenApiConnectionWebhook",
                "inputs": {
                    "host": {
                        "connectionName": "shared_onedriveforbusiness",
                        "operationId": "OnNewFile",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_onedriveforbusiness",
                    },
                    "parameters": {"folderId": OUTBOX_PATH},
                },
            }
        },
        "actions": {
            "Get_file_content": {
                "type": "OpenApiConnection",
                "inputs": {
                    "host": {
                        "connectionName": "shared_onedriveforbusiness",
                        "operationId": "GetFileContent",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_onedriveforbusiness",
                    },
                    "parameters": {"id": "@triggerOutputs()?['body/Id']"},
                },
                "runAfter": {},
            },
            "Parse_JSON": {
                "type": "ParseJson",
                "inputs": {
                    "content": "@body('Get_file_content')",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "source": {"type": "string"},
                            "to": {"type": "array", "items": {"type": "string"}},
                            "chat_or_channel": {},
                            "thread_id": {},
                            "subject": {},
                            "body": {"type": "string"},
                            "cerebro_action_id": {"type": "integer"},
                            "created_at": {"type": "string"},
                        },
                        "required": ["action", "body"],
                    },
                },
                "runAfter": {"Get_file_content": ["Succeeded"]},
            },
            "Is_it_email_": {
                "type": "If",
                "expression": {
                    "or": [
                        {"equals": ["@body('Parse_JSON')?['action']", "send_email"]},
                        {"equals": ["@body('Parse_JSON')?['action']", "reply_email"]},
                    ]
                },
                "actions": {
                    "Send_an_email": {
                        "type": "OpenApiConnection",
                        "inputs": {
                            "host": {
                                "connectionName": "shared_office365",
                                "operationId": "SendEmailV2",
                                "apiId": "/providers/Microsoft.PowerApps/apis/shared_office365",
                            },
                            "parameters": {
                                "emailMessage/To": "@join(body('Parse_JSON')?['to'], ';')",
                                "emailMessage/Subject": "@body('Parse_JSON')?['subject']",
                                "emailMessage/Body": "@body('Parse_JSON')?['body']",
                            },
                        },
                        "runAfter": {},
                    }
                },
                "else": {
                    "actions": {
                        "Post_message_in_a_chat_or_channel": {
                            "type": "OpenApiConnection",
                            "inputs": {
                                "host": {
                                    "connectionName": "shared_teams",
                                    "operationId": "PostMessageToConversation",
                                    "apiId": "/providers/Microsoft.PowerApps/apis/shared_teams",
                                },
                                "parameters": {
                                    "poster": "Flow bot",
                                    "location": "Channel",
                                    "body/messageBody": "@body('Parse_JSON')?['body']",
                                },
                            },
                            "runAfter": {},
                        }
                    }
                },
                "runAfter": {"Parse_JSON": ["Succeeded"]},
            },
            "Delete_file": {
                "type": "OpenApiConnection",
                "inputs": {
                    "host": {
                        "connectionName": "shared_onedriveforbusiness",
                        "operationId": "DeleteFile",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_onedriveforbusiness",
                    },
                    "parameters": {"id": "@triggerOutputs()?['body/Id']"},
                },
                "runAfter": {"Is_it_email_": ["Succeeded"]},
            },
        },
        "outputs": {},
    }


def _teams_inbound_definition() -> dict:
    """
    Teams → the Cerebro inbox, same shape as the Outlook flow.

    Unlike Outlook, this trigger needs a specific ``teamId``/``channelId`` —
    those are unique per tenant and can't be filled in ahead of time. The
    trigger and action are wired end-to-end so importing this only leaves one
    thing to do: open the trigger step in the designer and pick your team and
    channel from the dropdowns it already knows how to show.
    """
    return {
        "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {"$connections": {"defaultValue": {}, "type": "Object"}},
        "triggers": {
            "When_a_new_channel_message_is_added": {
                "type": "OpenApiConnectionWebhook",
                "inputs": {
                    "host": {
                        "connectionName": "shared_teams",
                        "operationId": "OnNewChannelMessage",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_teams",
                    },
                    # Left blank on purpose — pick your team/channel after import.
                    "parameters": {"teamId": "", "channelId": ""},
                },
            }
        },
        "actions": {
            "Create_file": {
                "type": "OpenApiConnection",
                "inputs": {
                    "host": {
                        "connectionName": "shared_onedriveforbusiness",
                        "operationId": "CreateFile",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_onedriveforbusiness",
                    },
                    "parameters": {
                        "source": "me",
                        "folderPath": INBOX_PATH,
                        "name": "teams-@{formatDateTime(utcNow(),'yyyyMMdd-HHmmssfff')}.json",
                        "body": {
                            "source": "teams",
                            "type": "message",
                            "external_id": "@triggerOutputs()?['body/id']",
                            "timestamp": "@triggerOutputs()?['body/createdDateTime']",
                            "sender": "@triggerOutputs()?['body/from']?['user']?['displayName']",
                            "chat_or_channel": "@triggerOutputs()?['body/channelIdentity']?['channelId']",
                            "body": "@triggerOutputs()?['body/body']?['content']",
                            "thread_id": "@triggerOutputs()?['body/replyToId']",
                        },
                    },
                },
                "runAfter": {},
            }
        },
        "outputs": {},
    }


def _dynamics_inbound_definition() -> dict:
    """
    Dynamics 365 (Dataverse) case created/updated → the Cerebro inbox.

    Unlike Teams, the Case table's logical name (``incidents``) is identical
    in every Dynamics 365 environment, so — unlike a team/channel — this
    trigger is safe to ship fully filled in. It complements the browser
    extension: the extension only notices a case while someone has it open;
    this notices a case the moment it changes, open or not (a customer
    reply, a reassignment, a status change from someone else's desk).
    """
    return {
        "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            "$connections": {"defaultValue": {}, "type": "Object"},
            "$authentication": {"defaultValue": {}, "type": "SecureObject"},
        },
        "triggers": {
            "When_a_case_is_created_or_modified": {
                "type": "OpenApiConnectionNotification",
                "inputs": {
                    "host": {
                        "connectionName": "shared_commondataserviceforapps",
                        "operationId": "SubscribeWebhookTrigger",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_commondataserviceforapps",
                    },
                    "parameters": {
                        "subscriptionRequest/message": 4,      # Added or Modified
                        "subscriptionRequest/entityname": "incidents",  # the Case table
                        "subscriptionRequest/scope": 4,         # Organization
                    },
                    "authentication": "@parameters('$authentication')",
                },
            }
        },
        "actions": {
            "Create_file": {
                "type": "OpenApiConnection",
                "inputs": {
                    "host": {
                        "connectionName": "shared_onedriveforbusiness",
                        "operationId": "CreateFile",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_onedriveforbusiness",
                    },
                    "parameters": {
                        "source": "me",
                        "folderPath": INBOX_PATH,
                        "name": "dynamics-@{formatDateTime(utcNow(),'yyyyMMdd-HHmmssfff')}.json",
                        "body": {
                            "source": "dynamics365",
                            "type": "case_update",
                            "external_id": "@triggerOutputs()?['body/incidentid']",
                            "timestamp": "@utcNow()",
                            "case_id": "@triggerOutputs()?['body/ticketnumber']",
                            "customer": "@triggerOutputs()?['body/customerid_value']",
                            "subject": "@triggerOutputs()?['body/title']",
                            "body": "@triggerOutputs()?['body/description']",
                        },
                    },
                },
                "runAfter": {},
            }
        },
        "outputs": {},
    }


def build() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    flows = [
        ("Cerebro - Outlook inbound", _inbound_definition(),
         {"shared_office365": _connection_reference("shared_office365"),
          "shared_onedriveforbusiness": _connection_reference("shared_onedriveforbusiness")}),
        ("Cerebro - Outlook outbound", _outbound_definition(),
         {"shared_office365": _connection_reference("shared_office365"),
          "shared_onedriveforbusiness": _connection_reference("shared_onedriveforbusiness"),
          "shared_teams": _connection_reference("shared_teams")}),
        ("Cerebro - Teams inbound (pick your team/channel after import)", _teams_inbound_definition(),
         {"shared_teams": _connection_reference("shared_teams"),
          "shared_onedriveforbusiness": _connection_reference("shared_onedriveforbusiness")}),
        ("Cerebro - Dynamics 365 case updates", _dynamics_inbound_definition(),
         {"shared_commondataserviceforapps": _connection_reference("shared_commondataserviceforapps"),
          "shared_onedriveforbusiness": _connection_reference("shared_onedriveforbusiness")}),
    ]

    manifest_resources = {}
    connections_all = {}
    entries = []  # (flow_id, wrapped_definition)

    for display_name, definition, conn_refs in flows:
        flow_id = str(uuid.uuid4())
        manifest_resources[flow_id] = {
            "type": "Microsoft.Flow/flows",
            "suggestedCreationType": "New",
            "creationType": "New",
            "configurableBy": "User",
            "hierarchy": "Root",
            "dependsOn": [],
            "details": {"displayName": display_name},
            "id": f"/providers/Microsoft.Flow/flows/{flow_id}",
            "name": flow_id,
        }
        entries.append((flow_id, _wrapped_flow(flow_id, display_name, definition, conn_refs)))
        connections_all.update(conn_refs)

    manifest = {
        "schema": "1.0",
        "details": {
            "displayName": "Cerebro Bridge",
            "description": "Feeds Outlook, Teams and Dynamics 365 into Cerebro, and sends its replies.",
            "createdTime": now,
            "packageTelemetryId": str(uuid.uuid4()),
            "creator": "N/A",
            "sourceEnvironment": "",
        },
        "resources": manifest_resources,
    }
    connections_json = {"connectionReferences": connections_all}

    if OUT_ZIP.exists():
        OUT_ZIP.unlink()
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("connections.json", json.dumps(connections_json, indent=2))
        for flow_id, wrapped in entries:
            base = f"Microsoft.Flow/flows/{flow_id}"
            zf.writestr(f"{base}/definition.json", json.dumps(wrapped, indent=2))
            zf.writestr(f"{base}/flow.json", json.dumps(
                {"properties": {"displayName": wrapped["properties"]["displayName"], "state": "Stopped"}},
                indent=2))

    return OUT_ZIP


if __name__ == "__main__":
    path = build()
    print(f"Wrote {path} ({path.stat().st_size:,} bytes)")
