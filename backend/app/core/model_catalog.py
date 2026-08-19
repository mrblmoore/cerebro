"""
Which models a user can pick, per provider.

Two sources, in order of authority:

1. **The provider itself.** Every provider Cerebro supports can list the models
   the caller is actually entitled to, so :mod:`app.services.model_discovery`
   asks it. That is the only way to be certain an ID is spelled correctly *and*
   enabled for this account — Bedrock in particular refuses model IDs that are
   real but not enabled in the Region.

2. **The curated list below**, used when credentials are not set yet (so the
   setup wizard still shows something useful) or when the provider cannot be
   reached. Treat it as a starting point, not gospel: model line-ups change,
   and a stale entry here is exactly the "invalid model ID" error we are trying
   to spare people. Anything the live listing returns wins.

The free-text box is still there under a "Custom model ID" option, because no
list survives contact with a private deployment or a brand-new release.
"""

from typing import Dict, List

#: Marker value meaning "let me type an ID myself".
CUSTOM = "__custom__"


def _model(identifier: str, label: str, note: str = "") -> Dict[str, str]:
    return {"id": identifier, "label": label, "note": note}


#: Bedrock IDs are Region-scoped. A bare ``anthropic.*`` ID works only in
#: Regions carrying that model; the ``us.`` / ``eu.`` / ``apac.`` prefixes are
#: cross-Region inference profiles, which route automatically and are the
#: better default. Those profiles sign with SigV4a — hence botocore[crt].
BEDROCK_MODELS: List[Dict[str, str]] = [
    _model("us.anthropic.claude-sonnet-4-20250514-v1:0",
           "Claude Sonnet 4 (US cross-Region)", "Strong general default"),
    _model("us.anthropic.claude-opus-4-20250514-v1:0",
           "Claude Opus 4 (US cross-Region)", "Most capable, highest cost"),
    _model("us.anthropic.claude-3-5-sonnet-20241022-v2:0",
           "Claude 3.5 Sonnet v2 (US cross-Region)"),
    _model("us.anthropic.claude-3-5-haiku-20241022-v1:0",
           "Claude 3.5 Haiku (US cross-Region)", "Fastest and cheapest Claude"),
    _model("eu.anthropic.claude-sonnet-4-20250514-v1:0",
           "Claude Sonnet 4 (EU cross-Region)"),
    _model("anthropic.claude-3-5-sonnet-20241022-v2:0",
           "Claude 3.5 Sonnet v2 (single Region)"),
    _model("anthropic.claude-3-haiku-20240307-v1:0",
           "Claude 3 Haiku (single Region)"),
    _model("amazon.nova-pro-v1:0", "Amazon Nova Pro"),
    _model("amazon.nova-lite-v1:0", "Amazon Nova Lite"),
    _model("meta.llama3-1-70b-instruct-v1:0", "Llama 3.1 70B Instruct"),
    _model("mistral.mistral-large-2407-v1:0", "Mistral Large (24.07)"),
]

OPENAI_MODELS: List[Dict[str, str]] = [
    _model("gpt-4o-mini", "GPT-4o mini", "Fast and inexpensive — good default"),
    _model("gpt-4o", "GPT-4o"),
    _model("gpt-4.1", "GPT-4.1"),
    _model("gpt-4.1-mini", "GPT-4.1 mini"),
    _model("o4-mini", "o4-mini", "Reasoning model"),
]

OLLAMA_MODELS: List[Dict[str, str]] = [
    _model("llama3.1", "Llama 3.1 8B", "Runs on most laptops"),
    _model("llama3.2", "Llama 3.2"),
    _model("qwen2.5", "Qwen 2.5"),
    _model("mistral", "Mistral 7B"),
    _model("phi3", "Phi-3"),
]

QWEN_MODELS: List[Dict[str, str]] = [
    _model("qwen-plus", "Qwen Plus"),
    _model("qwen-turbo", "Qwen Turbo", "Fastest"),
    _model("qwen-max", "Qwen Max", "Most capable"),
]

#: Embedding models are a separate list — an embedding endpoint rejects chat
#: model IDs, and mixing the two is an easy mistake to make in a dropdown.
OPENAI_EMBEDDING_MODELS: List[Dict[str, str]] = [
    _model("text-embedding-3-small", "text-embedding-3-small", "Good default"),
    _model("text-embedding-3-large", "text-embedding-3-large", "Higher quality"),
]

FALLBACK: Dict[str, List[Dict[str, str]]] = {
    "openai": OPENAI_MODELS,
    "bedrock": BEDROCK_MODELS,
    "ollama": OLLAMA_MODELS,
    "qwen": QWEN_MODELS,
    "openai_embedding": OPENAI_EMBEDDING_MODELS,
}

#: Which settings key holds the model ID for each provider, so the UI and the
#: discovery endpoint agree without hard-coding the mapping in two places.
MODEL_KEY: Dict[str, str] = {
    "openai": "OPENAI_MODEL",
    "bedrock": "BEDROCK_MODEL_ID",
    "ollama": "OLLAMA_MODEL",
    "qwen": "QWEN_MODEL",
    "openai_embedding": "OPENAI_EMBEDDING_MODEL",
}


def fallback_models(provider: str) -> List[Dict[str, str]]:
    """The curated list for a provider, or an empty list if unknown."""
    return list(FALLBACK.get((provider or "").lower(), []))


def options(provider: str, current: str = "") -> List[Dict[str, str]]:
    """
    Dropdown options for a provider: the curated models, the value already
    configured if it is not among them (so an existing choice is never silently
    dropped), and the custom escape hatch last.
    """
    models = fallback_models(provider)
    if current and not any(model["id"] == current for model in models):
        models.insert(0, _model(current, f"{current} (current)"))
    models.append(_model(CUSTOM, "Custom model ID…", "Type any ID the provider accepts"))
    return models
