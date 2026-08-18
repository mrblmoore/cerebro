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
    "OpenAI, a local Ollama model, or Qwen."
)


class LLMNotConfigured(RuntimeError):
    """Raised internally when a provider is selected but missing credentials."""


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
        provider = self.provider
        if provider == "openai":
            return self._call_openai(prompt)
        if provider == "ollama":
            return self._call_ollama(prompt)
        if provider == "qwen":
            return self._call_qwen(prompt)
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
