"""
Amazon Nova Sonic — Bedrock's speech-to-speech model, for Cerebro's most
expressive voice and for **Talk live**.

Two ways it's used:

* **Reading a reply** (:func:`speak`): Cerebro's spoken summary — with its
  sound cues turned into stage directions ("(giggles)", "(whispering:) …") —
  is sent as a text turn with an instruction to read it aloud, in the agent's
  persona, and Nova Sonic's audio comes back as 24 kHz PCM, returned as WAV.
* **Talk live** (:class:`LiveSession`, driven by ``WS /api/voice/live``): the
  user just talks; Nova Sonic listens, answers at once and can be interrupted.
  Anything that needs real work — a case, mail, a file, a change — goes to
  Cerebro's own agent through the one tool it's given, ``ask_cerebro``, so the
  same tools, scope and approvals apply. Nova Sonic then says the result.

Protocol: one bidirectional stream per session (``invoke_model_with_bidirectional_stream``,
up to 8 minutes). Events are JSON, sent in order: ``sessionStart`` →
``promptStart`` (audio output config, voice, tools) → content blocks
(``contentStart`` / ``textInput`` or ``audioInput`` / ``contentEnd``) →
``promptEnd`` → ``sessionEnd``. Output events: ``contentStart``,
``textOutput``, ``audioOutput`` (base64 LPCM), ``toolUse``, ``contentEnd``,
``completionEnd``. Field names follow the official Nova 2 Sonic sample
(aws-samples/amazon-nova-samples). The client is the async Smithy SDK
``aws-sdk-bedrock-runtime`` (Python 3.12+); tests swap in a fake with
:func:`set_client_factory`.
"""

import asyncio
import base64
import json
import sys
import threading
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.core import logger
from app.core.config import settings

INPUT_RATE = 16000
OUTPUT_RATE = 24000
SPEAK_TIMEOUT = 45

#: Suggested voices (any voice id Nova Sonic accepts can be typed in Settings).
VOICES = {
    "tiffany": "Tiffany — US English, feminine", "matthew": "Matthew — US English, masculine",
    "amy": "Amy — British English, feminine", "olivia": "Olivia — Australian English, feminine",
    "lupe": "Lupe — Spanish, feminine", "carlos": "Carlos — Spanish, masculine",
    "ambre": "Ambre — French, feminine", "florian": "Florian — French, masculine",
    "beatrice": "Beatrice — Italian, feminine", "lorenzo": "Lorenzo — Italian, masculine",
    "greta": "Greta — German, feminine", "lennart": "Lennart — German, masculine",
}

READ_ALOUD = ("You are the voice of {name}. Read the user's text aloud exactly as written — same "
              "words, same meaning, nothing added or left out — but perform it: warm, natural and "
              "expressive. Words in brackets like (giggles), (gasps) or (whispering:) are stage "
              "directions: act them out, don't read them. {persona}")
LIVE = ("You are {name}, the user's Cerebro assistant, talking with them out loud. {persona} Keep "
        "spoken answers short and natural — one to three sentences. For anything that needs their "
        "work systems or data — cases, email, Teams, files, calendar, knowledge, websites, desktop "
        "apps, or making any change — call ask_cerebro with the request in the user's words and say "
        "what it returns; never make up results. Changes are only prepared for the user's approval; "
        "say so. You may react naturally (a giggle, a gasp) where a person would.")
ASK_TOOL = {"toolSpec": {
    "name": "ask_cerebro",
    "description": ("Ask Cerebro's agent to do or look up something in the user's systems (cases, "
                    "mail, Teams, files, calendar, knowledge base, websites, desktop apps) or to "
                    "prepare a change. Returns what to say back."),
    "inputSchema": {"json": json.dumps({"type": "object", "properties": {
        "request": {"type": "string", "description": "What the user wants, in their words."}},
        "required": ["request"]})},
}}


class SonicUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------- availability
def available() -> Tuple[bool, str]:
    """(usable, why not)."""
    if _factory is not None:
        return True, ""
    if sys.version_info < (3, 12):
        return False, "Nova Sonic needs Cerebro's Python 3.12 build."
    from importlib import util

    if util.find_spec("aws_sdk_bedrock_runtime") is None or util.find_spec("awscrt") is None:
        return False, "Nova Sonic support isn't installed (pip install -r desktop/requirements-voice.txt)."
    mode = (settings.BEDROCK_AUTH_MODE or "default").lower()
    if mode == "api_key":
        return False, "Nova Sonic needs AWS access keys, a profile or the default AWS identity — a Bedrock API key can't sign it."
    if mode == "keys" and not (settings.BEDROCK_AWS_ACCESS_KEY_ID and settings.BEDROCK_AWS_SECRET_ACCESS_KEY):
        return False, "Add the AWS access keys under Settings → AI Provider."
    if mode == "profile" and not settings.BEDROCK_AWS_PROFILE:
        return False, "Choose the AWS profile under Settings → AI Provider."
    if mode == "default" and (settings.LLM_PROVIDER or "").lower() != "bedrock":
        return False, "Nova Sonic uses your AWS sign-in — set up Amazon Bedrock under Settings → AI Provider."
    return True, ""


# -------------------------------------------------------------- events
def _event(name: str, body: Dict[str, Any]) -> str:
    return json.dumps({"event": {name: body}})


def session_start(max_tokens: int = 1024, temperature: float = 0.7, top_p: float = 0.9) -> str:
    return _event("sessionStart", {"inferenceConfiguration": {
        "maxTokens": max_tokens, "topP": top_p, "temperature": temperature}})


def prompt_start(prompt: str, voice: str, tools: List[Dict[str, Any]] = None) -> str:
    body: Dict[str, Any] = {
        "promptName": prompt,
        "textOutputConfiguration": {"mediaType": "text/plain"},
        "audioOutputConfiguration": {"mediaType": "audio/lpcm", "sampleRateHertz": OUTPUT_RATE,
                                     "sampleSizeBits": 16, "channelCount": 1, "voiceId": voice,
                                     "encoding": "base64", "audioType": "SPEECH"}}
    if tools:
        body["toolUseOutputConfiguration"] = {"mediaType": "application/json"}
        body["toolConfiguration"] = {"tools": tools}
    return _event("promptStart", body)


def text_block(prompt: str, text: str, role: str = "USER", interactive: bool = False) -> List[str]:
    content = str(uuid.uuid4())
    return [_event("contentStart", {"promptName": prompt, "contentName": content, "type": "TEXT",
                                    "interactive": interactive, "role": role,
                                    "textInputConfiguration": {"mediaType": "text/plain"}}),
            _event("textInput", {"promptName": prompt, "contentName": content, "content": text}),
            _event("contentEnd", {"promptName": prompt, "contentName": content})]


def audio_start(prompt: str, content: str) -> str:
    return _event("contentStart", {"promptName": prompt, "contentName": content, "type": "AUDIO",
                                   "interactive": True, "role": "USER",
                                   "audioInputConfiguration": {"mediaType": "audio/lpcm",
                                                               "sampleRateHertz": INPUT_RATE,
                                                               "sampleSizeBits": 16, "channelCount": 1,
                                                               "audioType": "SPEECH", "encoding": "base64"}})


def audio_chunk(prompt: str, content: str, pcm: bytes) -> str:
    return _event("audioInput", {"promptName": prompt, "contentName": content,
                                 "content": base64.b64encode(pcm).decode("ascii")})


def content_end(prompt: str, content: str) -> str:
    return _event("contentEnd", {"promptName": prompt, "contentName": content})


def tool_result(prompt: str, tool_use_id: str, result: Dict[str, Any]) -> List[str]:
    content = str(uuid.uuid4())
    return [_event("contentStart", {"promptName": prompt, "contentName": content, "interactive": False,
                                    "type": "TOOL", "role": "TOOL",
                                    "toolResultInputConfiguration": {
                                        "toolUseId": tool_use_id, "type": "TEXT",
                                        "textInputConfiguration": {"mediaType": "text/plain"}}}),
            _event("toolResult", {"promptName": prompt, "contentName": content,
                                  "content": json.dumps(result)}),
            content_end(prompt, content)]


def prompt_end(prompt: str) -> str:
    return _event("promptEnd", {"promptName": prompt})


def session_end() -> str:
    return _event("sessionEnd", {})


def persona_text(agent=None) -> str:
    from app.services.personality import persona_block

    return persona_block(agent) if agent is not None else ""


# -------------------------------------------------------------- client
class _SdkStream:
    """The real stream, via the async Smithy SDK (aws-sdk-bedrock-runtime)."""

    def __init__(self, stream):
        self._stream = stream
        self._receiver = None

    async def send(self, event_json: str) -> None:
        from aws_sdk_bedrock_runtime.models import (BidirectionalInputPayloadPart,
                                                    InvokeModelWithBidirectionalStreamInputChunk)

        await self._stream.input_stream.send(InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=event_json.encode("utf-8"))))

    async def receive(self) -> Optional[Dict[str, Any]]:
        if self._receiver is None:
            _, self._receiver = await self._stream.await_output()
        result = await self._receiver.receive()
        if result is None:
            return None
        value = getattr(result, "value", None)
        data = getattr(value, "bytes_", None)
        return json.loads(data.decode("utf-8")) if data else {}

    async def close(self) -> None:
        try:
            await self._stream.input_stream.close()
        except Exception:  # noqa: BLE001 - closing a stream that already ended
            pass


async def _sdk_open(model_id: str, region: str):
    from aws_sdk_bedrock_runtime.client import (AsyncBedrockRuntimeClient,
                                                InvokeModelWithBidirectionalStreamOperationInput)
    from aws_sdk_bedrock_runtime.config import AsyncBedrockRuntimeConfig
    from smithy_aws_core.identity.components import AWSCredentialsIdentity
    from smithy_aws_core.identity.static import StaticCredentialsResolver
    from smithy_http.aio.crt import AWSCRTHTTPClient, AWSCRTHTTPClientConfig

    from app.services.llm_service import bedrock_session

    creds = bedrock_session().get_credentials()
    if creds is None:
        raise SonicUnavailable("No AWS credentials were found for Nova Sonic.")
    frozen = creds.get_frozen_credentials()
    identity = AWSCredentialsIdentity(access_key_id=frozen.access_key, secret_access_key=frozen.secret_key,
                                      session_token=frozen.token)
    # The bidirectional stream needs HTTP/2, which only the CRT transport speaks
    # (the SDK's default, aiohttp, can't interleave request and response).
    config = await AsyncBedrockRuntimeConfig.resolve(
        region=region, aws_credentials_identity_resolver=StaticCredentialsResolver(identity),
        transport=AWSCRTHTTPClient(client_config=AWSCRTHTTPClientConfig(force_http_2=True)))
    client = AsyncBedrockRuntimeClient(config=config)
    stream = await client.invoke_model_with_bidirectional_stream(
        InvokeModelWithBidirectionalStreamOperationInput(model_id=model_id))
    return _SdkStream(stream)


_factory: Optional[Callable[[str, str], Awaitable[Any]]] = None


def set_client_factory(factory: Optional[Callable[[str, str], Awaitable[Any]]]) -> None:
    """``async factory(model_id, region) -> stream`` (send/receive/close). Tests use a fake."""
    global _factory
    _factory = factory


async def open_stream():
    ok, why = available()
    if not ok:
        raise SonicUnavailable(why)
    opener = _factory or _sdk_open
    return await opener(settings.VOICE_SONIC_MODEL or "amazon.nova-2-sonic-v1:0",
                        settings.VOICE_SONIC_REGION or "us-east-1")


def _run(coroutine, timeout: float):
    """Run a coroutine to completion from sync code (on a thread if a loop is running)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(asyncio.wait_for(coroutine, timeout))
    box: Dict[str, Any] = {}

    def work():
        try:
            box["value"] = asyncio.run(asyncio.wait_for(coroutine, timeout))
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout + 5)
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ------------------------------------------------------------ read aloud
def speak(text: str, voice: str = None, style: Dict[str, Any] = None) -> bytes:
    """Nova Sonic reads ``text`` aloud, expressively, in the agent's persona
    (``style["persona"]`` / ``style["name"]``). Returns WAV bytes."""
    from app.services.tts import pcm_to_wav

    style = style or {}
    voice = (voice or settings.VOICE_SONIC_VOICE or "tiffany").lower()
    system = READ_ALOUD.format(name=style.get("name") or "Cerebro", persona=style.get("persona") or "")
    pcm = _run(_speak(text, voice, system), SPEAK_TIMEOUT)
    if not pcm:
        raise SonicUnavailable("Nova Sonic didn't return any speech.")
    return pcm_to_wav(pcm, OUTPUT_RATE)


async def _speak(text: str, voice: str, system: str) -> bytes:
    stream = await open_stream()
    prompt = str(uuid.uuid4())
    audio = bytearray()
    try:
        for event in [session_start(), prompt_start(prompt, voice)] + text_block(prompt, system, "SYSTEM") \
                + text_block(prompt, f"Read this aloud:\n{text}", "USER", interactive=True):
            await stream.send(event)
        while True:
            message = await stream.receive()
            if message is None:
                break
            event = (message or {}).get("event") or {}
            if "audioOutput" in event:
                audio.extend(base64.b64decode(event["audioOutput"].get("content") or ""))
            elif "contentEnd" in event and event["contentEnd"].get("type") == "AUDIO" \
                    and event["contentEnd"].get("stopReason") in ("END_TURN", "INTERRUPTED"):
                break
            elif "completionEnd" in event:
                break
        for event in (prompt_end(prompt), session_end()):
            try:
                await stream.send(event)
            except Exception:  # noqa: BLE001 - it already finished
                break
    finally:
        await stream.close()
    return bytes(audio)


# -------------------------------------------------------------- Talk live
def is_interruption(text: str) -> bool:
    """Nova Sonic marks barge-in with a text event of ``{ "interrupted" : true }``."""
    text = (text or "").strip()
    if not text.startswith("{"):
        return False
    try:
        return bool(json.loads(text).get("interrupted"))
    except (ValueError, AttributeError):
        return False


class LiveSession:
    """One live conversation. ``emit(dict)`` sends to the page:

    ``{"type": "audio", "data": <base64 PCM 24 kHz>}``, ``{"type": "transcript", "role", "text"}``,
    ``{"type": "interrupted"}``, ``{"type": "working", "request"}``, ``{"type": "result", "reply", "cards"}``,
    ``{"type": "ended", "reason"}``, ``{"type": "error", "detail"}``.

    ``ask(request) -> {"speech": str, "reply": str, "cards": list}`` runs Cerebro's agent.
    """

    def __init__(self, emit: Callable[[Dict[str, Any]], Awaitable[None]],
                 ask: Callable[[str], Awaitable[Dict[str, Any]]], voice: str = None,
                 name: str = "Cerebro", agent=None):
        self.emit, self.ask = emit, ask
        self.voice = (voice or settings.VOICE_SONIC_VOICE or "tiffany").lower()
        self.name, self.agent = name, agent
        self.prompt = str(uuid.uuid4())
        self.audio_content = str(uuid.uuid4())
        self.stream = None
        self.role = None
        self.speculative = False
        self.alive = False
        self._reader: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self.stream = await open_stream()
        self.alive = True
        system = LIVE.format(name=self.name, persona=persona_text(self.agent))
        for event in [session_start(), prompt_start(self.prompt, self.voice, [ASK_TOOL])] \
                + text_block(self.prompt, system, "SYSTEM") + [audio_start(self.prompt, self.audio_content)]:
            await self.stream.send(event)
        self._reader = asyncio.create_task(self._read())

    async def audio(self, pcm: bytes) -> None:
        if self.alive and pcm:
            await self.stream.send(audio_chunk(self.prompt, self.audio_content, pcm))

    async def say(self, text: str) -> None:
        """Typed text into the live conversation (cross-modal input)."""
        if self.alive and text.strip():
            for event in text_block(self.prompt, text, "USER", interactive=True):
                await self.stream.send(event)

    async def _read(self) -> None:
        try:
            while self.alive:
                message = await self.stream.receive()
                if message is None:
                    break
                await self._handle((message or {}).get("event") or {})
        except Exception as exc:  # noqa: BLE001 - told to the page
            logger.warn("voice", "Live session ended", {"error": str(exc)[:200]})
            await self.emit({"type": "error", "detail": str(exc)[:300]})
        finally:
            if self.alive:
                self.alive = False
                await self.emit({"type": "ended", "reason": "The live session ended."})

    async def _handle(self, event: Dict[str, Any]) -> None:
        if "contentStart" in event:
            start = event["contentStart"]
            self.role = start.get("role")
            fields = start.get("additionalModelFields")
            try:
                stage = json.loads(fields).get("generationStage") if fields else None
            except ValueError:
                stage = None
            self.speculative = stage == "SPECULATIVE"
        elif "textOutput" in event:
            text = event["textOutput"].get("content") or ""
            if is_interruption(text):
                await self.emit({"type": "interrupted"})
                return
            # The assistant's words arrive twice (a fast guess, then final): show the final.
            if self.role == "ASSISTANT" and self.speculative:
                return
            await self.emit({"type": "transcript", "role": (self.role or "").lower(), "text": text})
        elif "audioOutput" in event:
            await self.emit({"type": "audio", "data": event["audioOutput"].get("content") or ""})
        elif "toolUse" in event:
            use = event["toolUse"]
            try:
                args = json.loads(use.get("content") or "{}")
            except ValueError:
                args = {}
            request = str(args.get("request") or "").strip()
            await self.emit({"type": "working", "request": request})
            try:
                outcome = await self.ask(request) if request else {"speech": "What would you like me to do?"}
            except Exception as exc:  # noqa: BLE001 - said back rather than dropped
                outcome = {"speech": f"I couldn't do that: {exc}"}
            await self.emit({"type": "result", "reply": outcome.get("reply", ""),
                             "cards": outcome.get("cards") or []})
            for item in tool_result(self.prompt, use.get("toolUseId") or "",
                                    {"result": outcome.get("speech") or outcome.get("reply") or "Done."}):
                await self.stream.send(item)

    async def stop(self) -> None:
        if not self.stream:
            return
        was_alive, self.alive = self.alive, False
        if was_alive:
            for event in (content_end(self.prompt, self.audio_content), prompt_end(self.prompt), session_end()):
                try:
                    await self.stream.send(event)
                except Exception:  # noqa: BLE001
                    break
        if self._reader:
            self._reader.cancel()
        await self.stream.close()
