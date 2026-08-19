"""
Ask a provider which models this account can actually use.

A hard-coded list can only ever be a guess about someone else's account. Bedrock
will happily reject a perfectly well-spelled model ID because it is not enabled
in that Region; Ollama only has what has been pulled; an OpenAI-compatible
gateway may expose a handful of self-hosted names and nothing else. So the
dropdown is populated from the provider wherever credentials allow it, and falls
back to :mod:`app.core.model_catalog` when they do not.

Every function here degrades to ``(models, error_message)`` rather than raising:
a failure to *list* models must never block the settings page from rendering.
"""

from typing import Any, Dict, List, Tuple

import requests

from app.core import logger
from app.core.config import settings
from app.core.model_catalog import fallback_models

#: Listing is a convenience, not a critical path — keep it snappy.
LIST_TIMEOUT = 15


def _entry(identifier: str, label: str = "", note: str = "") -> Dict[str, str]:
    return {"id": identifier, "label": label or identifier, "note": note}


def discover(provider: str) -> Tuple[List[Dict[str, str]], str]:
    """
    Return ``(models, error)``. ``error`` is empty when the list came from the
    provider; otherwise it explains why the curated fallback is being shown.
    """
    provider = (provider or "").lower()
    try:
        if provider == "openai":
            return _openai(), ""
        if provider == "bedrock":
            return _bedrock(), ""
        if provider == "ollama":
            return _ollama(), ""
        if provider == "qwen":
            return _qwen(), ""
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, never raised
        logger.warn("model_discovery", f"Could not list {provider} models", {"error": str(exc)})
        return fallback_models(provider), _friendly(exc)

    return fallback_models(provider), f"Unknown provider: {provider}"


def _friendly(exc: Exception) -> str:
    """Turn a provider exception into something a non-engineer can act on."""
    text = str(exc)
    lowered = text.lower()
    if "credential" in lowered or "token" in lowered or "unrecognizedclient" in lowered:
        return "Could not authenticate. Check the credentials for this provider, then try again."
    if "accessdenied" in lowered or "not authorized" in lowered or "403" in lowered:
        return "Those credentials work but lack permission to list models. Showing the built-in list."
    if "could not connect" in lowered or "connection" in lowered or "timed out" in lowered:
        return "Could not reach the provider. Showing the built-in list."
    return f"Could not list models ({text[:160]}). Showing the built-in list."


def _openai() -> List[Dict[str, str]]:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("No OpenAI API key set.")

    base = (settings.OPENAI_BASE_URL or "https://api.openai.com/v1").rstrip("/")
    response = requests.get(
        f"{base}/models",
        headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
        timeout=LIST_TIMEOUT,
    )
    response.raise_for_status()

    identifiers = sorted(
        item.get("id", "") for item in response.json().get("data", []) if item.get("id")
    )
    # Chat-capable names only: the same endpoint also lists embedding, audio and
    # moderation models, and offering those as a chat model guarantees a failure.
    chat = [name for name in identifiers
            if name.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-"))]
    return [_entry(name) for name in (chat or identifiers)]


def _ollama() -> List[Dict[str, str]]:
    base = settings.OLLAMA_URL.rstrip("/")
    response = requests.get(f"{base}/api/tags", timeout=LIST_TIMEOUT)
    response.raise_for_status()

    models = []
    for item in response.json().get("models", []):
        name = item.get("name") or item.get("model")
        if not name:
            continue
        size = item.get("size")
        note = f"{round(size / 1_000_000_000, 1)} GB on disk" if isinstance(size, int) else ""
        models.append(_entry(name, note=note))
    if not models:
        raise RuntimeError("Ollama is running but has no models pulled yet. Try: ollama pull llama3.1")
    return sorted(models, key=lambda model: model["id"])


def _qwen() -> List[Dict[str, str]]:
    if not settings.QWEN_API_URL or not settings.QWEN_API_KEY:
        raise RuntimeError("Qwen URL and API key are both required.")

    # QWEN_API_URL points at the chat completions endpoint; the model list is
    # its sibling on an OpenAI-compatible gateway.
    base = settings.QWEN_API_URL.rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break

    response = requests.get(
        f"{base}/models",
        headers={"Authorization": f"Bearer {settings.QWEN_API_KEY}"},
        timeout=LIST_TIMEOUT,
    )
    response.raise_for_status()
    identifiers = sorted(
        item.get("id", "") for item in response.json().get("data", []) if item.get("id")
    )
    if not identifiers:
        raise RuntimeError("The gateway returned no models.")
    return [_entry(name) for name in identifiers]


def _bedrock() -> List[Dict[str, str]]:
    """
    List text models this AWS account can invoke in the configured Region.

    Both halves matter: ``list_inference_profiles`` returns the cross-Region
    ``us.*`` / ``eu.*`` IDs that are usually the right choice, while
    ``list_foundation_models`` returns the single-Region IDs. Listing lives on
    the ``bedrock`` control-plane client, not ``bedrock-runtime``.
    """
    from app.services.llm_service import bedrock_session  # local: optional dependency

    session = bedrock_session()
    client = session.client("bedrock", endpoint_url=None)

    models: List[Dict[str, str]] = []
    seen = set()

    try:
        paginator = client.get_paginator("list_inference_profiles")
        for page in paginator.paginate():
            for profile in page.get("inferenceProfileSummaries", []):
                identifier = profile.get("inferenceProfileId")
                if not identifier or identifier in seen:
                    continue
                seen.add(identifier)
                models.append(_entry(
                    identifier,
                    profile.get("inferenceProfileName") or identifier,
                    "Cross-Region inference profile",
                ))
    except Exception as exc:  # noqa: BLE001 - profiles are a bonus, not required
        logger.debug("model_discovery", "No inference profiles listed", {"error": str(exc)})

    response = client.list_foundation_models(byOutputModality="TEXT")
    for summary in response.get("modelSummaries", []):
        identifier = summary.get("modelId")
        if not identifier or identifier in seen:
            continue
        # ON_DEMAND is what Converse can call without a provisioned throughput ARN.
        supported = summary.get("inferenceTypesSupported") or []
        if "ON_DEMAND" not in supported and "INFERENCE_PROFILE" not in supported:
            continue
        seen.add(identifier)
        provider_name = summary.get("providerName") or ""
        model_name = summary.get("modelName") or identifier
        models.append(_entry(identifier, f"{provider_name} {model_name}".strip()))

    if not models:
        raise RuntimeError(
            "No text models are enabled in this Region. Enable model access in the "
            "Amazon Bedrock console under Model access."
        )
    return models
