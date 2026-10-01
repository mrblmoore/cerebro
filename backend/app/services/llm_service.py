"""
LLM Service — optional AI generation across several providers.

Two rules shape this module:

1. **AI is optional.** With ``LLM_PROVIDER=none`` (the default) Cerebro is fully
   usable; generation calls return a short, honest placeholder instead of an
   exception, and nothing else in the app has to special-case it.
2. **Nothing is imported until it is used.** The OpenAI SDK is an optional
   dependency, so it is imported inside the call path and a missing package
   surfaces as actionable guidance rather than an ImportError at boot.
"""

import os
from typing import Any, Dict, List

import requests

from app.core import logger
from app.core.config import settings

SYSTEM_PROMPT = (
    "You are a technical support copilot. Be concise, specific and actionable. "
    "Prefer short numbered steps over prose."
)

NOT_CONFIGURED = (
    "AI generation is turned off. Open Settings → AI Provider to connect "
    "OpenAI, Amazon Bedrock, a local Ollama model, or Qwen."
)


class LLMNotConfigured(RuntimeError):
    """Raised internally when a provider is selected but missing credentials."""


def _bedrock_error(exc: Exception) -> Exception:
    """
    Translate a Bedrock failure into something a non-engineer can act on.

    Returned rather than raised so callers keep their ``raise ... from exc``
    chain and the original traceback survives.
    """
    text = str(exc)
    lowered = text.lower()

    # botocore raises MissingDependencyException asking for "pip install
    # botocore[crt]" when it needs SigV4a, which cross-Region inference
    # profiles use. Cerebro ships awscrt, so hitting this means the running
    # interpreter is not Cerebro's own — the usual cause is a hand-rolled
    # install into system Python. Say so, because the stock message sends
    # people to a pip that fixes the wrong environment.
    if "botocore[crt]" in lowered or "crt_auth" in lowered or "awscrt" in lowered:
        return LLMNotConfigured(
            "This Bedrock model needs AWS's CRT signing library, which Cerebro "
            "normally installs for you. Re-run Cerebro's setup ('cerebro.bat setup' "
            "on Windows, './cerebro.sh setup' otherwise) so it lands in Cerebro's "
            "own environment — installing it with a plain 'pip install' usually "
            "goes to a different Python and will not take effect."
        )

    if "accessdeniedexception" in lowered or "not authorized" in lowered:
        return LLMNotConfigured(
            "Those AWS credentials cannot invoke this model. The identity needs "
            "bedrock:InvokeModel, and the model must be enabled for your account "
            "under Model access in the Amazon Bedrock console."
        )
    if "validationexception" in lowered and "model" in lowered:
        return LLMNotConfigured(
            f"Amazon Bedrock rejected the model ID '{settings.BEDROCK_MODEL_ID}'. "
            "Pick one from the list in Settings — that list comes from your own "
            "account, so every entry in it is valid for this Region."
        )
    if "resourcenotfound" in lowered:
        return LLMNotConfigured(
            f"'{settings.BEDROCK_MODEL_ID}' does not exist in {settings.BEDROCK_REGION}. "
            "Choose a different model or Region in Settings."
        )
    if "unrecognizedclient" in lowered or ("invalid" in lowered and "token" in lowered):
        return LLMNotConfigured(
            "AWS rejected those credentials. Check them in Settings → AI Provider."
        )
    if "throttl" in lowered or "toomanyrequests" in lowered:
        return RuntimeError("Amazon Bedrock is rate-limiting this account. Try again shortly.")
    if "expiredtoken" in lowered:
        return LLMNotConfigured(
            "Those temporary AWS credentials have expired. Refresh them and save again."
        )
    return exc


def bedrock_session():
    """
    Build an authenticated boto3 Session from the configured credential mode.

    Shared by generation and by model discovery so the two can never disagree
    about which identity is in play.

    Bedrock has no single "API key" the way OpenAI does — AWS authenticates by
    signing each request, so a request needs either an access key pair or an
    ambient identity (SSO, an IAM role, environment variables). The one
    exception is a Bedrock API key, a bearer token AWS added specifically to
    give Bedrock the simple key-in-a-box flow every other provider has; boto3
    picks it up from ``AWS_BEARER_TOKEN_BEDROCK``.
    """
    try:
        import boto3
    except ImportError as exc:
        raise LLMNotConfigured(
            "The AWS SDK is not installed. Reinstall Cerebro's dependencies with "
            "'cerebro.bat setup' (Windows) or './cerebro.sh setup', which installs "
            "them into Cerebro's own environment."
        ) from exc

    auth_mode = (settings.BEDROCK_AUTH_MODE or "default").lower()
    session_kwargs: Dict[str, Any] = {"region_name": settings.BEDROCK_REGION}

    if auth_mode == "profile":
        if not settings.BEDROCK_AWS_PROFILE:
            raise LLMNotConfigured("Select an AWS profile for Amazon Bedrock.")
        session_kwargs["profile_name"] = settings.BEDROCK_AWS_PROFILE

    elif auth_mode == "keys":
        if not (settings.BEDROCK_AWS_ACCESS_KEY_ID
                and settings.BEDROCK_AWS_SECRET_ACCESS_KEY):
            raise LLMNotConfigured(
                "AWS access key ID and secret access key are both required."
            )
        session_kwargs.update({
            "aws_access_key_id": settings.BEDROCK_AWS_ACCESS_KEY_ID,
            "aws_secret_access_key": settings.BEDROCK_AWS_SECRET_ACCESS_KEY,
            "aws_session_token": settings.BEDROCK_AWS_SESSION_TOKEN or None,
        })

    elif auth_mode == "api_key":
        if not settings.BEDROCK_API_KEY:
            raise LLMNotConfigured(
                "Paste a Bedrock API key, or switch to another credential mode."
            )
        # botocore reads this from the environment; setting it here keeps the
        # key in Cerebro's .env rather than requiring a machine-wide variable.
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = settings.BEDROCK_API_KEY

    elif auth_mode != "default":
        raise LLMNotConfigured(f"Unknown Amazon Bedrock credential mode: {auth_mode}")

    if auth_mode != "api_key":
        os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)

    return boto3.Session(**session_kwargs)


class LLMService:
    """Stateless wrapper around the configured provider.

    Settings are read per call rather than cached in ``__init__`` so that
    changes saved from the Settings UI take effect immediately.
    """

    # ------------------------------------------------------------- status
    @property
    def provider(self) -> str:
        return (settings.LLM_PROVIDER or "none").lower()

    @property
    def model(self) -> str:
        return settings.llm_model

    @property
    def enabled(self) -> bool:
        return self.provider != "none" and settings.llm_configured

    def status(self) -> Dict[str, Any]:
        """Human-readable configuration state, surfaced in diagnostics."""
        if self.provider == "none":
            return {"ok": True, "enabled": False, "provider": "none",
                    "detail": "AI features disabled"}
        if not settings.llm_configured:
            return {"ok": False, "enabled": False, "provider": self.provider,
                    "detail": f"{self.provider} selected but not fully configured"}
        return {"ok": True, "enabled": True, "provider": self.provider,
                "model": self.model, "detail": f"{self.provider} · {self.model}"}

    def test_connection(self) -> Dict[str, Any]:
        """Round-trip a tiny prompt so the user can verify their credentials."""
        if self.provider == "none":
            return {"ok": True, "detail": "AI features are disabled — nothing to test."}
        try:
            reply = self._dispatch("Reply with the single word: ready")
            return {"ok": True, "detail": f"{self.provider} responded: {reply.strip()[:80]}"}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}

    # --------------------------------------------------------- generation
    def generate_case_summary(self, case_data: Dict[str, Any]) -> str:
        transcript = case_data.get("transcript")
        prompt = f"""Summarise this support case for a CRM note.

Customer: {case_data.get('customer') or 'Unknown'}
Issue: {case_data.get('title') or 'Not stated'}
Error code: {case_data.get('error_code') or 'None'}
Application: {case_data.get('application') or 'Unknown'}
{f'Call transcript:{chr(10)}{transcript[:4000]}' if transcript else ''}

Write 2-3 sentences. No preamble."""
        return self._call_llm(prompt)

    def generate_troubleshooting_steps(self, case_data: Dict[str, Any],
                                       context: Dict[str, Any]) -> str:
        prompt = f"""Suggest troubleshooting steps for this support case.

Issue: {case_data.get('title') or 'Not stated'}
Error code: {case_data.get('error_code') or 'None'}
Application: {case_data.get('application') or 'Unknown'}

Give 3-5 numbered, actionable steps. No preamble."""
        return self._call_llm(prompt)

    def generate_next_steps(self, context: Dict[str, Any],
                            relevant_docs: List[Dict[str, Any]]) -> str:
        doc_summary = "\n".join(
            f"- {doc.get('title')}: {doc.get('excerpt', '')[:200]}"
            for doc in (relevant_docs or [])[:3]
        ) or "- (none found)"

        prompt = f"""Given the live support context, what should the engineer do next?

Case: {context.get('crm_case') or 'none'}
Customer: {context.get('customer') or 'unknown'}
Call active: {context.get('call_active')}
Relevant documentation:
{doc_summary}

Answer in 1-2 sentences."""
        return self._call_llm(prompt)

    def with_memory(self, prompt: str, query: str, db=None, **recall_kwargs) -> str:
        """
        Prepend relevant memories to a prompt.

        This is how the second brain reaches generation: the caller passes the
        text that describes the task (``query``), and whatever Cerebro remembers
        that bears on it is folded in above the instruction. With memory off, or
        nothing relevant, the prompt is returned unchanged.
        """
        if db is None:
            return prompt
        try:
            from app.services.memory_service import MemoryService

            block = MemoryService(db).recall_text(query, **recall_kwargs)
        except Exception:
            block = ""
        return f"{block}\n\n{prompt}" if block else prompt

    # ------------------------------------------------------------- vision
    def describe_image(self, image_path, question: str = None) -> str:
        """
        Answer a question about an image, or describe it if none is given.

        Only OpenAI and Ollama are wired for vision today — those are the two
        providers with a simple, well-documented image-in-chat format. Bedrock
        and Qwen degrade to a clear, honest message rather than silently
        ignoring the image, matching how an unconfigured provider behaves
        everywhere else in this class.
        """
        if self.provider == "none" or not settings.llm_configured:
            return NOT_CONFIGURED
        if self.provider not in ("openai", "ollama"):
            return (f"{self.provider} isn't wired for images in Cerebro yet — "
                    "switch to OpenAI or Ollama with a vision-capable model "
                    "(e.g. gpt-4o, llava, qwen2-vl) to have Cerebro look at images.")

        from pathlib import Path

        try:
            image_bytes = Path(image_path).read_bytes()
        except OSError as exc:
            return f"(Couldn't read that image: {exc})"

        prompt = question or ("Describe what's in this image, and call out anything "
                              "a support engineer would care about.")

        try:
            if self.provider == "openai":
                return self._describe_image_openai(image_bytes, prompt, Path(image_path))
            return self._describe_image_ollama(image_bytes, prompt)
        except Exception as exc:
            logger.error("llm_service", "Vision request failed", {"error": str(exc)})
            return f"(AI unavailable: {exc})"

    def _describe_image_openai(self, image_bytes: bytes, prompt: str, path) -> str:
        if not settings.OPENAI_API_KEY:
            raise LLMNotConfigured("No OpenAI API key set (Settings → AI Provider).")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMNotConfigured(
                "The openai package is not installed. Run: pip install -r "
                "backend/requirements-ai.txt"
            ) from exc

        import base64
        import mimetypes

        media_type = mimetypes.guess_type(str(path))[0] or "image/png"
        encoded = base64.b64encode(image_bytes).decode("ascii")

        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            organization=settings.OPENAI_ORG_ID or None,
            base_url=settings.OPENAI_BASE_URL or None,
            timeout=settings.LLM_TIMEOUT,
        )
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{media_type};base64,{encoded}"}},
                ]},
            ],
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
        )
        return (response.choices[0].message.content or "").strip()

    def _describe_image_ollama(self, image_bytes: bytes, prompt: str) -> str:
        import base64

        encoded = base64.b64encode(image_bytes).decode("ascii")
        endpoint = f"{settings.OLLAMA_URL.rstrip('/')}/api/chat"
        response = requests.post(
            endpoint,
            json={
                "model": settings.OLLAMA_MODEL,
                "stream": False,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt, "images": [encoded]},
                ],
            },
            timeout=settings.LLM_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return (data.get("message", {}).get("content") or "").strip()

    # --------------------------------------------------------------- chat
    def chat(self, messages: list, tools: list = None, system: str = None,
             max_tokens: int = None, temperature: float = None) -> dict:
        """
        One model turn over a real conversation, optionally offering tools.

        Raises on failure (unlike ``_call_llm``) so the caller can decide how to
        explain it; ``LLMNotConfigured`` when no provider is usable.
        """
        from app.core.activity_state import activity

        if not self.enabled:
            raise LLMNotConfigured(NOT_CONFIGURED)
        backend = _CHAT_BACKENDS.get(self.provider)
        if backend is None:
            raise LLMNotConfigured(f"Unknown LLM provider: {self.provider}")

        system = system or SYSTEM_PROMPT
        max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        temperature = settings.LLM_TEMPERATURE if temperature is None else temperature
        tools = tools or []
        mode = (settings.LLM_TOOL_MODE or "auto").lower()
        key = (self.provider, self.model)
        if not tools or mode == "off":
            tools, mode = [], "none"
        elif mode == "auto" and key in _NO_NATIVE_TOOLS:
            mode = "json"

        with activity("thinking", f"Thinking with {self.model or self.provider}"):
            if mode in ("auto", "native"):
                try:
                    result = backend(messages, tools, system, max_tokens, temperature)
                    return {**result, "mode": "native"}
                except LLMNotConfigured:
                    raise
                except Exception as exc:  # noqa: BLE001 - maybe just no tool support
                    lowered = str(exc).lower()
                    if mode == "native" or not any(h in lowered for h in _TOOL_UNSUPPORTED_HINTS):
                        raise
                    logger.warn("llm_service", "Native tools unsupported; using JSON protocol",
                                {"provider": self.provider, "model": self.model, "error": str(exc)[:200]})
                    _NO_NATIVE_TOOLS.add(key)
                    mode = "json"

            if mode == "json":
                protocol_system = f"{system}\n\n{_json_protocol_instructions(tools)}"
                result = backend(_to_json_protocol(messages), [], protocol_system,
                                 max_tokens, temperature)
                call = _extract_json_tool_call(result["content"], {t["name"] for t in tools})
                if call:
                    return {"content": "", "tool_calls": [call], "mode": "json"}
                return {"content": result["content"], "tool_calls": [], "mode": "json"}

            result = backend(_merge_same_role(messages), [], system, max_tokens, temperature)
            return {"content": result["content"], "tool_calls": [], "mode": "none"}


    # -------------------------------------------------------------- core
    def _call_llm(self, prompt: str) -> str:
        """Generation entry point. Degrades to a message, never raises."""
        if self.provider == "none":
            return NOT_CONFIGURED
        if not settings.llm_configured:
            return NOT_CONFIGURED

        import time

        started = time.time()
        try:
            logger.info("llm_service", "Sending request", {
                "provider": self.provider, "model": self.model,
                "prompt_preview": prompt[:200],
            })
            reply = self._dispatch(prompt)
            logger.info("llm_service", "Received response", {
                "provider": self.provider, "duration_s": round(time.time() - started, 2),
            })
            return reply
        except Exception as exc:
            logger.error("llm_service", "LLM request failed", {
                "error": str(exc), "duration_s": round(time.time() - started, 2),
            })
            return f"(AI unavailable: {exc})"

    def _dispatch(self, prompt: str) -> str:
        from app.core.activity_state import activity

        with activity("thinking", f"Asking {self.provider}"):
            return self._dispatch_now(prompt)

    def _dispatch_now(self, prompt: str) -> str:
        provider = self.provider
        if provider == "openai":
            return self._call_openai(prompt)
        if provider == "ollama":
            return self._call_ollama(prompt)
        if provider == "qwen":
            return self._call_qwen(prompt)
        if provider == "bedrock":
            return self._call_bedrock(prompt)
        raise LLMNotConfigured(f"Unknown LLM provider: {provider}")

    def _call_openai(self, prompt: str) -> str:
        if not settings.OPENAI_API_KEY:
            raise LLMNotConfigured("No OpenAI API key set (Settings → AI Provider).")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMNotConfigured(
                "The openai package is not installed. Run: pip install -r "
                "backend/requirements-ai.txt"
            ) from exc

        client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            organization=settings.OPENAI_ORG_ID or None,
            base_url=settings.OPENAI_BASE_URL or None,
            timeout=settings.LLM_TIMEOUT,
        )
        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
        )
        return (response.choices[0].message.content or "").strip()

    def _call_ollama(self, prompt: str) -> str:
        endpoint = f"{settings.OLLAMA_URL.rstrip('/')}/api/chat"
        response = requests.post(
            endpoint,
            json={
                "model": settings.OLLAMA_MODEL,
                "stream": False,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "options": {
                    "temperature": settings.LLM_TEMPERATURE,
                    "num_predict": settings.LLM_MAX_TOKENS,
                },
            },
            timeout=settings.LLM_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return (data.get("message", {}).get("content") or data.get("response") or "").strip()

    def _call_qwen(self, prompt: str) -> str:
        if not settings.QWEN_API_URL or not settings.QWEN_API_KEY:
            raise LLMNotConfigured("Qwen URL and API key are both required.")

        response = requests.post(
            settings.QWEN_API_URL,
            headers={
                "Authorization": f"Bearer {settings.QWEN_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.QWEN_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": settings.LLM_TEMPERATURE,
                "max_tokens": settings.LLM_MAX_TOKENS,
            },
            timeout=settings.LLM_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices") or []
        if choices:
            choice = choices[0]
            message = choice.get("message") or {}
            return (message.get("content") or choice.get("text") or "").strip()
        return (data.get("answer") or response.text).strip()

    def _call_bedrock(self, prompt: str) -> str:
        """Generate through Bedrock's provider-neutral Converse API."""
        if not settings.BEDROCK_REGION or not settings.BEDROCK_MODEL_ID:
            raise LLMNotConfigured("Amazon Bedrock Region and model ID are required.")

        from botocore.config import Config

        session = bedrock_session()
        client = session.client(
            "bedrock-runtime",
            endpoint_url=settings.BEDROCK_ENDPOINT_URL or None,
            config=Config(
                connect_timeout=settings.LLM_TIMEOUT,
                read_timeout=settings.LLM_TIMEOUT,
                retries={"mode": "standard", "max_attempts": 3},
            ),
        )
        try:
            response = client.converse(
                modelId=settings.BEDROCK_MODEL_ID,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={
                    "temperature": settings.LLM_TEMPERATURE,
                    "maxTokens": settings.LLM_MAX_TOKENS,
                },
            )
        except Exception as exc:  # noqa: BLE001 - re-raised as guidance below
            raise _bedrock_error(exc) from exc

        blocks = (
            response.get("output", {})
            .get("message", {})
            .get("content", [])
        )
        text = "\n".join(
            block.get("text", "") for block in blocks
            if isinstance(block, dict) and block.get("text")
        ).strip()
        if not text:
            raise RuntimeError("Amazon Bedrock returned no text content.")
        return text


# =================================================================== chat API
#
# ``_call_llm`` above is one prompt in, one string out — fine for a case note,
# wrong for a conversation. Ask needs real turns (so the model can tell its own
# earlier answer from the new question) and tool calls (so it can go and look
# things up instead of being handed a fixed bundle of context).
#
# Messages use one neutral shape regardless of provider:
#
#   {"role": "user" | "assistant", "content": str,
#    "tool_calls": [{"id", "name", "arguments": dict}]}      (assistant only)
#   {"role": "tool", "tool_call_id": str, "name": str, "content": str}
#
# Tools are ``{"name", "description", "parameters": <JSON schema>}``.
# ``chat`` returns ``{"content": str, "tool_calls": [...], "mode": str}``.

import json as _json
import re as _re
import uuid as _uuid

#: Provider/model pairs that rejected native tools this session, so the JSON
#: protocol is used straight away instead of failing once per message.
_NO_NATIVE_TOOLS: set = set()

_TOOL_UNSUPPORTED_HINTS = (
    "does not support tools", "tool use is not supported", "tools is not supported",
    "unsupported parameter", "unknown field", "extra inputs are not permitted",
    "unrecognized request argument", "tool_choice", "function calling",
    "doesn't support tool", "not support tool",
)


def _new_call_id() -> str:
    return "call_" + _uuid.uuid4().hex[:12]


def _parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = _json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _json_protocol_instructions(tools: list) -> str:
    listing = "\n".join(
        f"- {tool['name']}: {tool.get('description', '')}\n"
        f"  arguments: {_json.dumps(tool.get('parameters', {}).get('properties', {}))}"
        for tool in tools)
    return (
        "You can use these tools to look things up or prepare actions:\n"
        f"{listing}\n\n"
        "To use a tool, reply with ONLY a JSON object on its own, for example:\n"
        '{"tool": "search_knowledge", "arguments": {"query": "error 0x80040115"}}\n'
        "You will then receive the result and can call another tool or answer. "
        "When you have enough to answer, reply normally in plain text "
        "(no JSON)."
    )


def _extract_json_tool_call(text: str, tool_names: set):
    """Recognise a JSON-protocol tool call in a plain-text reply."""
    candidate = (text or "").strip()
    fenced = _re.match(r"^```(?:json)?\s*(\{.*\})\s*```$", candidate, _re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    if not (candidate.startswith("{") and candidate.endswith("}")):
        return None
    try:
        data = _json.loads(candidate)
    except ValueError:
        return None
    name = data.get("tool") or data.get("name")
    if name not in tool_names:
        return None
    return {"id": _new_call_id(), "name": name,
            "arguments": _parse_arguments(data.get("arguments") or data.get("args"))}


def _to_json_protocol(messages: list) -> list:
    """Rewrite tool turns as plain text for models without tool support."""
    converted = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            converted.append({
                "role": "user",
                "content": f"[Result of {message.get('name') or 'tool'}]\n{message.get('content') or ''}",
            })
        elif role == "assistant" and message.get("tool_calls"):
            call = message["tool_calls"][0]
            converted.append({"role": "assistant", "content": _json.dumps(
                {"tool": call["name"], "arguments": call.get("arguments") or {}})})
        else:
            converted.append({"role": role, "content": message.get("content") or ""})
    return _merge_same_role(converted)


def _merge_same_role(messages: list) -> list:
    """Several providers require strictly alternating user/assistant turns."""
    merged = []
    for message in messages:
        if merged and merged[-1]["role"] == message["role"] \
                and message["role"] in ("user", "assistant") \
                and not merged[-1].get("tool_calls") and not message.get("tool_calls"):
            merged[-1] = {**merged[-1], "content":
                          f"{merged[-1].get('content') or ''}\n\n{message.get('content') or ''}".strip()}
        else:
            merged.append(dict(message))
    return merged


def _openai_messages(messages: list, system: str) -> list:
    out = [{"role": "system", "content": system}]
    for message in messages:
        role = message["role"]
        if role == "assistant" and message.get("tool_calls"):
            out.append({
                "role": "assistant", "content": message.get("content") or None,
                "tool_calls": [{
                    "id": call["id"], "type": "function",
                    "function": {"name": call["name"],
                                 "arguments": _json.dumps(call.get("arguments") or {})},
                } for call in message["tool_calls"]],
            })
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": message["tool_call_id"],
                        "content": message.get("content") or ""})
        else:
            out.append({"role": role, "content": message.get("content") or ""})
    return out


def _openai_tools(tools: list) -> list:
    return [{"type": "function", "function": {
        "name": tool["name"], "description": tool.get("description", ""),
        "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
    }} for tool in tools]


def _openai_style_result(message: dict) -> dict:
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        calls.append({"id": call.get("id") or _new_call_id(),
                      "name": function.get("name"),
                      "arguments": _parse_arguments(function.get("arguments"))})
    return {"content": (message.get("content") or "").strip(), "tool_calls": calls}


def _chat_openai(messages, tools, system, max_tokens, temperature) -> dict:
    if not settings.OPENAI_API_KEY:
        raise LLMNotConfigured("No OpenAI API key set (Settings → AI Provider).")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMNotConfigured(
            "The openai package is not installed. Run: pip install -r "
            "backend/requirements-ai.txt") from exc

    client = OpenAI(api_key=settings.OPENAI_API_KEY,
                    organization=settings.OPENAI_ORG_ID or None,
                    base_url=settings.OPENAI_BASE_URL or None,
                    timeout=settings.LLM_TIMEOUT)
    kwargs = {"model": settings.OPENAI_MODEL,
              "messages": _openai_messages(messages, system),
              "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        kwargs["tools"] = _openai_tools(tools)
    response = client.chat.completions.create(**kwargs)
    message = response.choices[0].message
    return _openai_style_result({
        "content": message.content,
        "tool_calls": [{"id": call.id, "function": {
            "name": call.function.name, "arguments": call.function.arguments}}
            for call in (message.tool_calls or [])],
    })


def _chat_qwen(messages, tools, system, max_tokens, temperature) -> dict:
    if not settings.QWEN_API_URL or not settings.QWEN_API_KEY:
        raise LLMNotConfigured("Qwen URL and API key are both required.")
    body = {"model": settings.QWEN_MODEL,
            "messages": _openai_messages(messages, system),
            "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = _openai_tools(tools)
    response = requests.post(
        settings.QWEN_API_URL,
        headers={"Authorization": f"Bearer {settings.QWEN_API_KEY}",
                 "Content-Type": "application/json"},
        json=body, timeout=settings.LLM_TIMEOUT)
    if response.status_code >= 400:
        raise RuntimeError(f"Qwen returned {response.status_code}: {response.text[:300]}")
    data = response.json()
    choices = data.get("choices") or []
    if choices:
        choice = choices[0]
        message = choice.get("message") or {"content": choice.get("text")}
        return _openai_style_result(message)
    return {"content": (data.get("answer") or response.text).strip(), "tool_calls": []}


def _chat_ollama(messages, tools, system, max_tokens, temperature) -> dict:
    converted = [{"role": "system", "content": system}]
    for message in messages:
        if message["role"] == "assistant" and message.get("tool_calls"):
            converted.append({"role": "assistant", "content": message.get("content") or "",
                              "tool_calls": [{"function": {
                                  "name": call["name"],
                                  "arguments": call.get("arguments") or {}}}
                                  for call in message["tool_calls"]]})
        elif message["role"] == "tool":
            converted.append({"role": "tool", "content": message.get("content") or "",
                              "tool_name": message.get("name")})
        else:
            converted.append({"role": message["role"], "content": message.get("content") or ""})
    body = {"model": settings.OLLAMA_MODEL, "stream": False, "messages": converted,
            "options": {"temperature": temperature, "num_predict": max_tokens}}
    if tools:
        body["tools"] = _openai_tools(tools)
    response = requests.post(f"{settings.OLLAMA_URL.rstrip('/')}/api/chat",
                             json=body, timeout=settings.LLM_TIMEOUT)
    if response.status_code >= 400:
        raise RuntimeError(f"Ollama returned {response.status_code}: {response.text[:300]}")
    message = (response.json() or {}).get("message") or {}
    calls = [{"id": _new_call_id(), "name": (call.get("function") or {}).get("name"),
              "arguments": _parse_arguments((call.get("function") or {}).get("arguments"))}
             for call in message.get("tool_calls") or []]
    return {"content": (message.get("content") or "").strip(), "tool_calls": calls}


def _chat_bedrock(messages, tools, system, max_tokens, temperature) -> dict:
    if not settings.BEDROCK_REGION or not settings.BEDROCK_MODEL_ID:
        raise LLMNotConfigured("Amazon Bedrock Region and model ID are required.")
    from botocore.config import Config

    converted = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            block = {"toolResult": {"toolUseId": message["tool_call_id"],
                                    "content": [{"text": message.get("content") or "(empty)"}]}}
            if converted and converted[-1]["role"] == "user" and \
                    all("toolResult" in item for item in converted[-1]["content"]):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue
        content = []
        if message.get("content"):
            content.append({"text": message["content"]})
        for call in message.get("tool_calls") or []:
            content.append({"toolUse": {"toolUseId": call["id"], "name": call["name"],
                                        "input": call.get("arguments") or {}}})
        if not content:
            content.append({"text": "(empty)"})
        if converted and converted[-1]["role"] == role and role == "user" and \
                not any("toolResult" in item for item in converted[-1]["content"]):
            converted[-1]["content"].extend(content)
        else:
            converted.append({"role": role, "content": content})

    kwargs = {"modelId": settings.BEDROCK_MODEL_ID, "system": [{"text": system}],
              "messages": converted,
              "inferenceConfig": {"temperature": temperature, "maxTokens": max_tokens}}
    if tools:
        kwargs["toolConfig"] = {"tools": [{"toolSpec": {
            "name": tool["name"], "description": tool.get("description", "") or tool["name"],
            "inputSchema": {"json": tool.get("parameters") or {"type": "object", "properties": {}}},
        }} for tool in tools]}

    client = bedrock_session().client(
        "bedrock-runtime", endpoint_url=settings.BEDROCK_ENDPOINT_URL or None,
        config=Config(connect_timeout=settings.LLM_TIMEOUT, read_timeout=settings.LLM_TIMEOUT,
                      retries={"mode": "standard", "max_attempts": 3}))
    try:
        response = client.converse(**kwargs)
    except Exception as exc:  # noqa: BLE001 - re-raised as guidance
        raise _bedrock_error(exc) from exc
    blocks = response.get("output", {}).get("message", {}).get("content", [])
    text = "\n".join(block["text"] for block in blocks if block.get("text")).strip()
    calls = [{"id": block["toolUse"].get("toolUseId") or _new_call_id(),
              "name": block["toolUse"].get("name"),
              "arguments": _parse_arguments(block["toolUse"].get("input"))}
             for block in blocks if block.get("toolUse")]
    return {"content": text, "tool_calls": calls}


_CHAT_BACKENDS = {
    "openai": _chat_openai, "qwen": _chat_qwen,
    "ollama": _chat_ollama, "bedrock": _chat_bedrock,
}
