"""
A small Model Context Protocol client: Cerebro talking to MCP servers.

MCP is JSON-RPC 2.0 over one of three transports, and this speaks all three:

* **Streamable HTTP** (``transport: "http"``) — each message is a POST; the
  reply is JSON or a short event stream. Most remote servers use this.
* **SSE** (``transport: "sse"``) — the older remote transport: a GET event
  stream that names a URL to POST messages to.
* **stdio** (``transport: "stdio"``) — a program on this PC (``uvx …``,
  ``npx …``) reading and writing one JSON message per line.

It is written here rather than taken from the ``mcp`` package because the
handshake is small and stable while that SDK's API changes between major
versions; Cerebro's tests check it against the official SDK's own server.

Sign-in: a remote server that answers 401 is signed in to with OAuth 2.1 as
the MCP authorization spec describes — discovery, dynamic client
registration and PKCE, with the consent page opened in the user's own
browser. Tokens are kept encrypted for the Windows user (DPAPI) by
:mod:`app.services.capabilities`, never in settings.
"""

import base64
import hashlib
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests

from app.core import logger
from app.core.paths import TOOLS_DIR

PROTOCOL_VERSION = "2025-11-25"
CLIENT_INFO = {"name": "Cerebro", "version": "0.7"}
LIST_TIMEOUT = 45
CALL_TIMEOUT = 120
START_TIMEOUT = 90          # first run of uvx/npx downloads the server
OAUTH_TIMEOUT = 300
UV_DOWNLOAD = ("https://github.com/astral-sh/uv/releases/latest/download/"
               "uv-x86_64-pc-windows-msvc.zip")
NODE_DOWNLOAD = "https://nodejs.org/en/download"


class MCPError(RuntimeError):
    pass


class OutcomeUnknown(MCPError):
    """The server went away after a call was sent: it may or may not have run.

    Never retried automatically — the user checks first.
    """


class NeedsSignIn(MCPError):
    def __init__(self, server: str, resource_metadata: str = ""):
        super().__init__(f"{server} needs you to sign in.")
        self.server = server
        self.resource_metadata = resource_metadata


# ------------------------------------------------------------- transports
class _Transport:
    on_notification: Optional[Callable[[dict], None]] = None

    def request(self, message: dict, timeout: float) -> dict:
        raise NotImplementedError

    def notify(self, message: dict) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    @property
    def alive(self) -> bool:
        return True


def _server_request_reply(message: dict) -> dict:
    """What Cerebro answers when a server asks *it* something."""
    method = message.get("method")
    if method == "ping":
        return {"jsonrpc": "2.0", "id": message["id"], "result": {}}
    if method == "roots/list":
        return {"jsonrpc": "2.0", "id": message["id"], "result": {"roots": []}}
    return {"jsonrpc": "2.0", "id": message["id"],
            "error": {"code": -32601, "message": f"Cerebro does not support {method}"}}


class StdioTransport(_Transport):
    def __init__(self, command: List[str], env: Dict[str, str] = None, cwd: str = None):
        full_env = {**os.environ, **(env or {})}
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=full_env, cwd=cwd or None, bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            raise MCPError(f"Couldn't start {command[0]}: {exc}") from exc
        self._pending: Dict[Any, "queue.Queue"] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.stderr_tail: List[str] = []
        threading.Thread(target=self._read, daemon=True, name="cerebro-mcp-stdio").start()
        threading.Thread(target=self._read_errors, daemon=True, name="cerebro-mcp-stderr").start()

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def _read(self) -> None:
        stream = self.process.stdout
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("{"):
                continue                      # a server printing to stdout by mistake
            try:
                message = json.loads(line)
            except ValueError:
                continue
            self._dispatch(message)
        with self._lock:
            waiting = list(self._pending.values())
        for box in waiting:
            box.put(None)                     # the process ended

    def _read_errors(self) -> None:
        for raw in iter(self.process.stderr.readline, b""):
            self.stderr_tail.append(raw.decode("utf-8", "replace").rstrip())
            del self.stderr_tail[:-20]

    def _dispatch(self, message: dict) -> None:
        if "method" in message and "id" in message:
            self._write(_server_request_reply(message))
        elif "method" in message:
            if self.on_notification:
                self.on_notification(message)
        else:
            with self._lock:
                box = self._pending.get(message.get("id"))
            if box is not None:
                box.put(message)

    def _write(self, message: dict) -> None:
        data = (json.dumps(message) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise MCPError(f"The server stopped: {self._last_error() or exc}") from exc

    def _last_error(self) -> str:
        return " / ".join(line for line in self.stderr_tail[-3:] if line)[:400]

    def request(self, message: dict, timeout: float) -> dict:
        box: "queue.Queue" = queue.Queue()
        with self._lock:
            self._pending[message["id"]] = box
        try:
            self._write(message)
            try:
                reply = box.get(timeout=timeout)
            except queue.Empty:
                raise OutcomeUnknown(f"No answer within {int(timeout)} s") from None
            if reply is None:
                raise OutcomeUnknown("The server stopped before answering"
                                     + (f": {self._last_error()}" if self._last_error() else ""))
            return reply
        finally:
            with self._lock:
                self._pending.pop(message["id"], None)

    def notify(self, message: dict) -> None:
        self._write(message)

    def close(self) -> None:
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                self.process.kill()
            except Exception:  # noqa: BLE001
                pass


def sse_events(lines: Iterable[str]) -> Iterable[Dict[str, str]]:
    """Server-sent events from a stream of text lines."""
    event, data = "message", []
    for line in lines:
        if line is None:
            continue
        if line == "":
            if data:
                yield {"event": event, "data": "\n".join(data)}
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield {"event": event, "data": "\n".join(data)}


class HttpTransport(_Transport):
    """Streamable HTTP."""

    def __init__(self, url: str, headers: Callable[[], Dict[str, str]], name: str):
        self.url, self._headers, self.name = url, headers, name
        self.session_id: Optional[str] = None
        self.protocol: Optional[str] = None
        self.http = requests.Session()

    def _post(self, message: dict, timeout: float) -> requests.Response:
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream", **self._headers()}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        try:
            response = self.http.post(self.url, json=message, headers=headers, timeout=timeout,
                                      stream=True)
        except requests.Timeout as exc:
            raise OutcomeUnknown(f"No answer within {int(timeout)} s") from exc
        except requests.RequestException as exc:
            raise MCPError(f"Couldn't reach {self.url}: {exc}") from exc
        if response.status_code == 401:
            response.close()
            raise NeedsSignIn(self.name, _resource_metadata(response))
        session = response.headers.get("Mcp-Session-Id")
        if session:
            self.session_id = session
        return response

    def request(self, message: dict, timeout: float) -> dict:
        response = self._post(message, timeout)
        if response.status_code == 404 and self.session_id and message.get("method") != "initialize":
            response.close()
            raise SessionExpired()
        if response.status_code >= 400:
            text = response.text[:300]
            response.close()
            raise MCPError(f"{self.name} answered HTTP {response.status_code}: {text}")
        kind = (response.headers.get("Content-Type") or "").lower()
        try:
            if "text/event-stream" in kind:
                for event in sse_events(response.iter_lines(decode_unicode=True)):
                    try:
                        reply = json.loads(event["data"])
                    except ValueError:
                        continue
                    for item in reply if isinstance(reply, list) else [reply]:
                        if item.get("id") == message["id"] and "method" not in item:
                            return item
                        if "method" in item and "id" in item:
                            self.notify(_server_request_reply(item))
                        elif "method" in item and self.on_notification:
                            self.on_notification(item)
                raise OutcomeUnknown("The server closed the stream before answering")
            reply = response.json()
        except requests.RequestException as exc:
            raise OutcomeUnknown(f"The connection dropped: {exc}") from exc
        except ValueError as exc:
            raise MCPError(f"{self.name} sent something that isn't JSON") from exc
        finally:
            response.close()
        if isinstance(reply, list):
            reply = next((r for r in reply if r.get("id") == message["id"]), {})
        return reply

    def notify(self, message: dict) -> None:
        try:
            self._post(message, 30).close()
        except NeedsSignIn:
            raise
        except MCPError:
            pass

    def close(self) -> None:
        if self.session_id:
            try:
                self.http.delete(self.url, headers={"Mcp-Session-Id": self.session_id,
                                                    **self._headers()}, timeout=5)
            except requests.RequestException:
                pass
        self.http.close()


class SessionExpired(MCPError):
    pass


class SseTransport(_Transport):
    """The 2024 HTTP+SSE transport."""

    def __init__(self, url: str, headers: Callable[[], Dict[str, str]], name: str):
        self.url, self._headers, self.name = url, headers, name
        self.http = requests.Session()
        self._pending: Dict[Any, "queue.Queue"] = {}
        self._lock = threading.Lock()
        self._endpoint: "queue.Queue" = queue.Queue()
        self.post_url: Optional[str] = None
        self._closed = False
        try:
            self._stream = self.http.get(url, headers={"Accept": "text/event-stream",
                                                       **headers()}, stream=True, timeout=(15, None))
        except requests.RequestException as exc:
            raise MCPError(f"Couldn't reach {url}: {exc}") from exc
        if self._stream.status_code == 401:
            raise NeedsSignIn(name, _resource_metadata(self._stream))
        if self._stream.status_code >= 400:
            raise MCPError(f"{name} answered HTTP {self._stream.status_code}")
        threading.Thread(target=self._read, daemon=True, name="cerebro-mcp-sse").start()
        try:
            self.post_url = urljoin(url, self._endpoint.get(timeout=20))
        except queue.Empty:
            raise MCPError(f"{name} didn't say where to send messages") from None

    @property
    def alive(self) -> bool:
        return not self._closed

    def _read(self) -> None:
        try:
            for event in sse_events(self._stream.iter_lines(decode_unicode=True)):
                if event["event"] == "endpoint":
                    self._endpoint.put(event["data"].strip())
                    continue
                try:
                    message = json.loads(event["data"])
                except ValueError:
                    continue
                if "method" in message and "id" in message:
                    self.notify(_server_request_reply(message))
                elif "method" in message:
                    if self.on_notification:
                        self.on_notification(message)
                else:
                    with self._lock:
                        box = self._pending.get(message.get("id"))
                    if box is not None:
                        box.put(message)
        except Exception:  # noqa: BLE001 - the stream ended
            pass
        self._closed = True
        with self._lock:
            for box in self._pending.values():
                box.put(None)

    def _post(self, message: dict) -> None:
        try:
            response = self.http.post(self.post_url, json=message,
                                      headers={"Content-Type": "application/json", **self._headers()},
                                      timeout=30)
        except requests.RequestException as exc:
            raise OutcomeUnknown(f"The connection dropped: {exc}") from exc
        if response.status_code == 401:
            raise NeedsSignIn(self.name, _resource_metadata(response))
        if response.status_code >= 400:
            raise MCPError(f"{self.name} answered HTTP {response.status_code}: {response.text[:200]}")

    def request(self, message: dict, timeout: float) -> dict:
        box: "queue.Queue" = queue.Queue()
        with self._lock:
            self._pending[message["id"]] = box
        try:
            self._post(message)
            try:
                reply = box.get(timeout=timeout)
            except queue.Empty:
                raise OutcomeUnknown(f"No answer within {int(timeout)} s") from None
            if reply is None:
                raise OutcomeUnknown("The server's stream ended before it answered")
            return reply
        finally:
            with self._lock:
                self._pending.pop(message["id"], None)

    def notify(self, message: dict) -> None:
        self._post(message)

    def close(self) -> None:
        self._closed = True
        try:
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        self.http.close()


def _resource_metadata(response) -> str:
    header = response.headers.get("WWW-Authenticate") or ""
    for part in header.replace("Bearer", "", 1).split(","):
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "resource_metadata":
            return value.strip().strip('"')
    return ""


# ------------------------------------------------------------ programs
def _which(program: str) -> Optional[str]:
    found = shutil.which(program)
    if found:
        return found
    local = TOOLS_DIR / (program + (".exe" if sys.platform == "win32" else ""))
    return str(local) if local.is_file() else None


def ensure_uv() -> str:
    """Path to ``uvx``, downloading uv into Cerebro's tools folder once."""
    found = _which("uvx")
    if found:
        return found
    if sys.platform != "win32":
        raise MCPError("uvx isn't installed. Install uv (https://docs.astral.sh/uv/) and try again.")
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    archive = TOOLS_DIR / "uv.zip"
    logger.info("mcp", "Downloading uv to run Python MCP servers", {"to": str(TOOLS_DIR)})
    try:
        with requests.get(UV_DOWNLOAD, stream=True, timeout=60) as response:
            response.raise_for_status()
            with open(archive, "wb") as handle:
                for chunk in response.iter_content(1 << 16):
                    handle.write(chunk)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                if member.rsplit("/", 1)[-1] in ("uv.exe", "uvx.exe"):
                    (TOOLS_DIR / member.rsplit("/", 1)[-1]).write_bytes(bundle.read(member))
    except Exception as exc:  # noqa: BLE001
        raise MCPError(f"Couldn't download uv, which runs Python MCP servers: {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)
    found = _which("uvx")
    if not found:
        raise MCPError("uv was downloaded but uvx.exe wasn't in it")
    return found


def resolve_command(command: str, args: List[str]) -> List[str]:
    """The command line to start a local server, finding (or fetching) the program."""
    program = (command or "").strip()
    if not program:
        raise MCPError("No command given for this server")
    base = os.path.basename(program).lower().removesuffix(".exe").removesuffix(".cmd")
    if base == "uvx":
        return [ensure_uv(), *args]
    if base == "uv":
        uvx = ensure_uv()
        return [str(os.path.join(os.path.dirname(uvx), os.path.basename(uvx).replace("uvx", "uv"))), *args]
    found = _which(program)
    if found is None:
        if base in ("npx", "node", "npm"):
            raise MCPError(f"This server needs Node.js, which isn't installed. Install it from "
                           f"{NODE_DOWNLOAD}, then try again.")
        raise MCPError(f"Couldn't find the program “{program}” on this PC")
    return [found, *args]


# ------------------------------------------------------------- the client
class MCPConnection:
    """One live connection to one MCP server."""

    def __init__(self, name: str, config: Dict[str, Any],
                 headers: Callable[[], Dict[str, str]] = None,
                 env: Dict[str, str] = None,
                 on_tools_changed: Callable[[], None] = None):
        self.name = name
        self.config = config
        self._headers = headers or (lambda: {})
        self._env = env or {}
        self._on_tools_changed = on_tools_changed
        self._ids = iter(range(1, 1 << 62))
        self._id_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self.transport: Optional[_Transport] = None
        self.server_info: Dict[str, Any] = {}
        self.instructions = ""

    def _next_id(self) -> int:
        with self._id_lock:
            return next(self._ids)

    @property
    def connected(self) -> bool:
        return self.transport is not None and self.transport.alive

    def _open_transport(self) -> _Transport:
        kind = (self.config.get("transport") or ("stdio" if self.config.get("command") else "http")).lower()
        if kind == "stdio":
            command = resolve_command(self.config.get("command"), list(self.config.get("args") or []))
            return StdioTransport(command, env={**(self.config.get("env") or {}), **self._env},
                                  cwd=self.config.get("cwd"))
        url = self.config.get("url") or ""
        if not url.startswith(("http://", "https://")):
            raise MCPError("This server has no web address")
        if kind == "sse":
            return SseTransport(url, self._headers, self.name)
        return HttpTransport(url, self._headers, self.name)

    def connect(self) -> Dict[str, Any]:
        with self._start_lock:
            if self.connected:
                return self.server_info
            transport = self._open_transport()
            try:
                result = self._handshake(transport)
            except (NeedsSignIn, OutcomeUnknown):
                transport.close()
                raise
            except MCPError:
                transport.close()
                if isinstance(transport, HttpTransport) and not self.config.get("transport"):
                    # An older server: try the SSE transport at the same address.
                    transport = SseTransport(self.config["url"], self._headers, self.name)
                    result = self._handshake(transport)
                    self.config["transport"] = "sse"
                else:
                    raise
            self.transport = transport
            self.server_info = result.get("serverInfo") or {}
            self.instructions = (result.get("instructions") or "")[:2000]
            return self.server_info

    def _handshake(self, transport: _Transport) -> dict:
        transport.on_notification = self._notification
        timeout = START_TIMEOUT if isinstance(transport, StdioTransport) else LIST_TIMEOUT
        reply = transport.request({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": CLIENT_INFO}}, timeout)
        result = self._result(reply, "initialize")
        if isinstance(transport, HttpTransport):
            transport.protocol = result.get("protocolVersion") or PROTOCOL_VERSION
        transport.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def _notification(self, message: dict) -> None:
        if message.get("method") == "notifications/tools/list_changed" and self._on_tools_changed:
            threading.Thread(target=self._on_tools_changed, daemon=True).start()

    def _result(self, reply: dict, what: str) -> dict:
        if reply.get("error"):
            error = reply["error"]
            raise MCPError(f"{self.name}: {error.get('message') or error}")
        result = reply.get("result")
        if result is None:
            raise MCPError(f"{self.name} gave no answer to {what}")
        return result

    def request(self, method: str, params: dict = None, timeout: float = LIST_TIMEOUT) -> dict:
        if not self.connected:
            self.connect()
        message = {"jsonrpc": "2.0", "id": self._next_id(), "method": method,
                   "params": params or {}}
        try:
            reply = self.transport.request(message, timeout)
        except SessionExpired:
            self.transport = None
            self.connect()
            reply = self.transport.request({**message, "id": self._next_id()}, timeout)
        return self._result(reply, method)

    def list_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor = None
        for _ in range(20):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, tool: str, arguments: Dict[str, Any], timeout: float = CALL_TIMEOUT) -> dict:
        return self.request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout)

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None


def result_text(result: Dict[str, Any], limit: int = 20_000) -> str:
    """A tool result as text the model can read."""
    parts: List[str] = []
    for block in result.get("content") or []:
        kind = block.get("type")
        if kind == "text":
            parts.append(block.get("text") or "")
        elif kind == "resource":
            resource = block.get("resource") or {}
            parts.append(resource.get("text") or f"[file {resource.get('uri', '')}]")
        elif kind == "resource_link":
            parts.append(f"[{block.get('name') or 'link'}: {block.get('uri', '')}]")
        elif kind in ("image", "audio"):
            parts.append(f"[{kind} returned: {block.get('mimeType', '')}]")
    if not any(p.strip() for p in parts) and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], indent=1, default=str))
    text = "\n".join(p for p in parts if p).strip() or "(no output)"
    return text[:limit]


# ---------------------------------------------------------------- OAuth
def _pkce() -> tuple:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _json(url: str) -> Optional[dict]:
    try:
        response = requests.get(url, headers={"Accept": "application/json",
                                              "MCP-Protocol-Version": PROTOCOL_VERSION}, timeout=15)
        if response.ok:
            return response.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def discover_oauth(server_url: str, resource_metadata: str = "") -> Dict[str, Any]:
    """Where to sign in to ``server_url``: its authorization server's endpoints."""
    parsed = urlparse(server_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [resource_metadata] if resource_metadata else []
    candidates += [f"{origin}/.well-known/oauth-protected-resource{parsed.path.rstrip('/')}",
                   f"{origin}/.well-known/oauth-protected-resource"]
    resource = next((doc for doc in (_json(u) for u in candidates if u) if doc), {}) or {}
    issuer = (resource.get("authorization_servers") or [origin])[0].rstrip("/")
    ip = urlparse(issuer)
    issuer_origin, issuer_path = f"{ip.scheme}://{ip.netloc}", ip.path.rstrip("/")
    metadata = None
    for url in (f"{issuer_origin}/.well-known/oauth-authorization-server{issuer_path}",
                f"{issuer_origin}/.well-known/openid-configuration{issuer_path}",
                f"{issuer}/.well-known/openid-configuration",
                f"{issuer_origin}/.well-known/oauth-authorization-server"):
        metadata = _json(url)
        if metadata and metadata.get("authorization_endpoint"):
            break
    if not metadata or not metadata.get("authorization_endpoint"):
        metadata = {"authorization_endpoint": f"{issuer_origin}/authorize",
                    "token_endpoint": f"{issuer_origin}/token",
                    "registration_endpoint": f"{issuer_origin}/register"}
    return {"resource": resource.get("resource") or server_url,
            "scopes": resource.get("scopes_supported") or [], **metadata}


class _Callback(BaseHTTPRequestHandler):
    result: "queue.Queue"

    def do_GET(self):  # noqa: N802 - http.server's name
        query = parse_qs(urlparse(self.path).query)
        self.server.result.put({k: v[0] for k, v in query.items()})
        body = ("<html><body style='font-family:sans-serif;padding:40px'><h2>Cerebro is "
                "connected.</h2><p>You can close this tab.</p></body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence http.server
        pass


def sign_in(server_url: str, resource_metadata: str = "", client: Dict[str, Any] = None,
            open_browser: Callable[[str], Any] = webbrowser.open,
            timeout: float = OAUTH_TIMEOUT) -> Dict[str, Any]:
    """Run the OAuth sign-in in the user's browser; returns what to keep.

    ``{"access_token", "refresh_token", "expires_at", "token_endpoint",
    "client_id", "client_secret", "resource"}``.
    """
    meta = discover_oauth(server_url, resource_metadata)
    callback = HTTPServer(("127.0.0.1", 0), _Callback)
    callback.result = queue.Queue()
    redirect = f"http://127.0.0.1:{callback.server_port}/callback"
    threading.Thread(target=callback.serve_forever, daemon=True, name="cerebro-oauth").start()
    try:
        client = dict(client or {})
        if not client.get("client_id") or client.get("redirect_uri") != redirect:
            client = _register(meta, redirect) if meta.get("registration_endpoint") else client
            if not client.get("client_id"):
                raise MCPError("This server doesn't let apps register themselves; it needs an "
                               "API key or a client ID instead.")
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(16)
        params = {"response_type": "code", "client_id": client["client_id"],
                  "redirect_uri": redirect, "code_challenge": challenge,
                  "code_challenge_method": "S256", "state": state, "resource": meta["resource"]}
        if meta.get("scopes"):
            params["scope"] = " ".join(meta["scopes"])
        separator = "&" if "?" in meta["authorization_endpoint"] else "?"
        open_browser(meta["authorization_endpoint"] + separator + urlencode(params))
        try:
            answer = callback.result.get(timeout=timeout)
        except queue.Empty:
            raise MCPError("Sign-in didn't finish in time") from None
        if answer.get("error"):
            raise MCPError(f"Sign-in was refused: {answer.get('error_description') or answer['error']}")
        if answer.get("state") != state or not answer.get("code"):
            raise MCPError("The sign-in answer didn't match; try again")
        tokens = _token(meta["token_endpoint"], {
            "grant_type": "authorization_code", "code": answer["code"], "redirect_uri": redirect,
            "client_id": client["client_id"], "code_verifier": verifier,
            "resource": meta["resource"]}, client)
    finally:
        callback.shutdown()
        callback.server_close()
    return {**tokens, "token_endpoint": meta["token_endpoint"], "resource": meta["resource"],
            "client_id": client["client_id"], "client_secret": client.get("client_secret"),
            "redirect_uri": redirect}


def _register(meta: Dict[str, Any], redirect: str) -> Dict[str, Any]:
    try:
        response = requests.post(meta["registration_endpoint"], json={
            "client_name": "Cerebro", "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none"}, timeout=20)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MCPError(f"Couldn't register Cerebro with the sign-in server: {exc}") from exc
    return {"client_id": data.get("client_id"), "client_secret": data.get("client_secret"),
            "redirect_uri": redirect}


def _token(endpoint: str, form: Dict[str, str], client: Dict[str, Any] = None) -> Dict[str, Any]:
    if client and client.get("client_secret"):
        form = {**form, "client_secret": client["client_secret"]}
    try:
        response = requests.post(endpoint, data=form, headers={"Accept": "application/json"},
                                 timeout=30)
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MCPError(f"Couldn't finish signing in: {exc}") from exc
    if not response.ok or not data.get("access_token"):
        raise MCPError(f"Sign-in failed: {data.get('error_description') or data.get('error') or response.status_code}")
    return {"access_token": data["access_token"], "refresh_token": data.get("refresh_token"),
            "expires_at": time.time() + float(data.get("expires_in") or 3600)}


def refresh(oauth: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """New tokens from a refresh token, or ``None`` when sign-in is needed again."""
    if not oauth.get("refresh_token") or not oauth.get("token_endpoint"):
        return None
    try:
        tokens = _token(oauth["token_endpoint"], {
            "grant_type": "refresh_token", "refresh_token": oauth["refresh_token"],
            "client_id": oauth.get("client_id") or "", "resource": oauth.get("resource") or ""},
            oauth)
    except MCPError:
        return None
    return {**oauth, **{k: v for k, v in tokens.items() if v}}
