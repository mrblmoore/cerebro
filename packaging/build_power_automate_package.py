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
  and OneDrive's ``CreateFile``/``OnNewFile``/``DeleteFile`` need no
  tenant-specific IDs.
* **Dynamics 365 case updates** — also fully wired. The Case table's logical
  name is ``incident`` (singular) in Dataverse.
* **Teams inbound/outbound** — wired end-to-end except for the tenant-specific
  Team and Channel selections. The packaged definitions contain non-empty
  setup markers so the legacy importer can save them; after import the user
  replaces those markers from the trigger/action dropdowns.

The Teams outbound branch intentionally uses the connector's fixed-schema V3
channel action. The modern ``PostMessageToConversation`` action requests a
tenant-specific dynamic schema during legacy-package import; Power Automate can
make that metadata request before the selected connection is authenticated and
reject the whole flow with ``GetUnifiedActionSchema``/``Unauthorized``.
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

# Power Automate validates required connector parameters while it imports the
# package. Empty strings make the legacy importer reject the definition before
# the user gets a chance to pick their tenant-specific Team and Channel.
SELECT_TEAM = "SELECT_TEAM_AFTER_IMPORT"
SELECT_CHANNEL = "SELECT_CHANNEL_AFTER_IMPORT"

CONNECTOR_NAMES = {
    "shared_office365": "Office 365 Outlook",
    "shared_onedriveforbusiness": "OneDrive for Business",
    "shared_teams": "Microsoft Teams",
    "shared_commondataserviceforapps": "Microsoft Dataverse",
}


def _connection_reference(api_name: str, connection_name: str) -> dict:
    """
    An unresolved connection reference. Power Automate's import wizard shows
    each of these as "Select during import" and lets the user pick or create
    the real connection — nothing here needs to be a genuine identifier.
    """
    return {
        "connectionName": connection_name,
        "source": "Embedded",
        "id": f"/providers/Microsoft.PowerApps/apis/{api_name}",
        "tier": "NotSpecified",
    }


def _add_connector_authentication(definition: dict) -> dict:
    """Make connector actions match Power Automate's exported-flow contract."""
    parameters = definition.setdefault("parameters", {})
    parameters.setdefault(
        "$authentication", {"defaultValue": {}, "type": "SecureObject"})

    def visit(node):
        if isinstance(node, dict):
            node_type = str(node.get("type") or "")
            inputs = node.get("inputs")
            if node_type.startswith("OpenApiConnection") and isinstance(inputs, dict):
                inputs.setdefault("authentication", "@parameters('$authentication')")
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(definition)
    return definition


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
                # OnNewEmailV3 is a polling connector trigger, not a webhook.
                "type": "OpenApiConnection",
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
    """A Cerebro outbox file → email or the configured Teams channel.

    The Teams action uses a fixed parameter schema so legacy package import
    does not need an authenticated ``GetUnifiedActionSchema`` metadata call.
    Its Team and Channel are deliberately setup markers that the user replaces
    from two dropdowns after import.
    """
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
                                    "operationId": "PostMessageToChannelV3",
                                    "apiId": "/providers/Microsoft.PowerApps/apis/shared_teams",
                                },
                                "parameters": {
                                    "groupId": SELECT_TEAM,
                                    "channelId": SELECT_CHANNEL,
                                    "subject": "@body('Parse_JSON')?['subject']",
                                    "content": "@body('Parse_JSON')?['body']",
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

    Unlike Outlook, this trigger needs a specific ``groupId``/``channelId`` —
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
                # OnNewChannelMessage is polling; Power Automate rejects it if
                # it is declared as OpenApiConnectionWebhook.
                "type": "OpenApiConnection",
                "inputs": {
                    "host": {
                        "connectionName": "shared_teams",
                        "operationId": "OnNewChannelMessage",
                        "apiId": "/providers/Microsoft.PowerApps/apis/shared_teams",
                    },
                    # Replace these two markers from the designer dropdowns.
                    "parameters": {
                        "groupId": SELECT_TEAM,
                        "channelId": SELECT_CHANNEL,
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

    Unlike Teams, the Case table's logical name (``incident``) is identical
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
                        "subscriptionRequest/entityname": "incident",  # the Case table
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
         ("shared_office365", "shared_onedriveforbusiness")),
        ("Cerebro - Microsoft 365 outbound (pick Teams destination after import)", _outbound_definition(),
         ("shared_office365", "shared_onedriveforbusiness", "shared_teams")),
        ("Cerebro - Teams inbound (pick your team/channel after import)", _teams_inbound_definition(),
         ("shared_teams", "shared_onedriveforbusiness")),
        ("Cerebro - Dynamics 365 case updates", _dynamics_inbound_definition(),
         ("shared_commondataserviceforapps", "shared_onedriveforbusiness")),
    ]

    manifest_resources = {}
    connector_assets = {}
    for api_name in sorted({api for _, _, apis in flows for api in apis}):
        api_asset_id = str(uuid.uuid4())
        connection_asset_id = str(uuid.uuid4())
        display_name = CONNECTOR_NAMES[api_name]
        connection_name = f"{api_name.replace('_', '-')}-{uuid.uuid4()}"

        manifest_resources[api_asset_id] = {
            "id": f"/providers/Microsoft.PowerApps/apis/{api_name}",
            "name": api_name,
            "type": "Microsoft.PowerApps/apis",
            "suggestedCreationType": "Existing",
            "details": {"displayName": display_name},
            "configurableBy": "System",
            "hierarchy": "Child",
            "dependsOn": [],
        }
        manifest_resources[connection_asset_id] = {
            "type": "Microsoft.PowerApps/apis/connections",
            "suggestedCreationType": "Existing",
            "creationType": "Existing",
            "details": {"displayName": f"Select {display_name} connection"},
            "configurableBy": "User",
            "hierarchy": "Child",
            "dependsOn": [api_asset_id],
        }
        connector_assets[api_name] = {
            "api": api_asset_id,
            "connection": connection_asset_id,
            "connection_name": connection_name,
        }

    entries = []  # (flow_id, wrapped_definition, apis_map, connections_map)

    for display_name, definition, api_names in flows:
        flow_id = str(uuid.uuid4())
        dependency_ids = [
            asset_id
            for api_name in api_names
            for asset_id in (connector_assets[api_name]["api"],
                             connector_assets[api_name]["connection"])
        ]
        manifest_resources[flow_id] = {
            "type": "Microsoft.Flow/flows",
            "suggestedCreationType": "New",
            "creationType": "Existing, New, Update",
            "configurableBy": "User",
            "hierarchy": "Root",
            "dependsOn": dependency_ids,
            "details": {"displayName": display_name},
            "id": f"/providers/Microsoft.Flow/flows/{flow_id}",
            "name": flow_id,
        }
        conn_refs = {
            api_name: _connection_reference(
                api_name, connector_assets[api_name]["connection_name"])
            for api_name in api_names
        }
        apis_map = {
            api_name: connector_assets[api_name]["api"]
            for api_name in api_names
        }
        connections_map = {
            api_name: connector_assets[api_name]["connection"]
            for api_name in api_names
        }
        definition = _add_connector_authentication(definition)
        entries.append((
            flow_id,
            _wrapped_flow(flow_id, display_name, definition, conn_refs),
            apis_map,
            connections_map,
        ))

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
    flow_manifest = {
        "packageSchemaVersion": "1.0",
        "flowAssets": {"assetPaths": [flow_id for flow_id, *_ in entries]},
    }

    if OUT_ZIP.exists():
        OUT_ZIP.unlink()
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr(
            "Microsoft.Flow/flows/manifest.json",
            json.dumps(flow_manifest, indent=2),
        )
        for flow_id, wrapped, apis_map, connections_map in entries:
            base = f"Microsoft.Flow/flows/{flow_id}"
            zf.writestr(f"{base}/definition.json", json.dumps(wrapped, indent=2))
            zf.writestr(f"{base}/apisMap.json", json.dumps(apis_map, indent=2))
            zf.writestr(
                f"{base}/connectionsMap.json",
                json.dumps(connections_map, indent=2),
            )

    return OUT_ZIP


if __name__ == "__main__":
    path = build()
    print(f"Wrote {path} ({path.stat().st_size:,} bytes)")
