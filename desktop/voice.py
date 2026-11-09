"""
Push-to-talk: hold the hotkey, speak, let go — Cerebro acts and answers aloud.

Started by the desktop shell. A global key listener (pynput) watches for the
push-to-talk keys (``VOICE_HOTKEY``, Ctrl+Space by default) from any app.
While they're held, the microphone records (sounddevice); on release the
recording goes to ``/api/voice/command``, which turns it into text on this PC,
acts on it, and returns a short spoken summary, played here. The window
doesn't have to be open: the tray brain and the desktop buddy show that
Cerebro is listening, and the answer also arrives as a notification.

Pressing the keys while Cerebro is speaking stops it. The state machine
(:class:`PushToTalk`) has no hardware in it, so it is tested with a fake
recorder and fake key events.
"""

import base64
import io
import sys
import threading
import time
import wave
from typing import Callable, Optional, Set

SAMPLE_RATE = 16000
MIN_SECONDS = 0.35
MAX_SECONDS = 60

_ALIASES = {"control": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl", "alt_l": "alt", "alt_r": "alt",
            "alt_gr": "alt", "shift_l": "shift", "shift_r": "shift", "shift_r ": "shift",
            "cmd": "win", "cmd_l": "win", "cmd_r": "win", "super": "win", "windows": "win",
            "spacebar": "space", " ": "space"}


def parse_hotkey(text: str) -> Set[str]:
    keys = {_ALIASES.get(part.strip().lower(), part.strip().lower())
            for part in (text or "ctrl+space").replace("-", "+").split("+") if part.strip()}
    return keys or {"ctrl", "space"}


def key_name(key) -> str:
    """A pynput key as the names used in VOICE_HOTKEY."""
    name = getattr(key, "name", None)
    if name:
        return _ALIASES.get(name, name)
    char = getattr(key, "char", None)
    if char:
        if len(char) == 1 and ord(char) < 32:          # Ctrl+letter arrives as a control code
            return chr(ord(char) + 96)
        return _ALIASES.get(char.lower(), char.lower())
    return ""


def wav_bytes(samples: bytes, rate: int = SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples)
    return out.getvalue()


class Recorder:
    """The microphone, 16 kHz mono 16-bit, while :meth:`start` … :meth:`stop`."""

    def __init__(self):
        self._chunks = []
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        self._chunks = []

        def callback(data, frames, when, status):  # noqa: ARG001 - sounddevice's signature
            self._chunks.append(bytes(data))

        self._stream = sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                         callback=callback)
        self._stream.start()

    def stop(self) -> bytes:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return b"".join(self._chunks)


class Player:
    """Plays a WAV reply; :meth:`stop` cuts it short."""

    def __init__(self):
        self.playing = False

    def play(self, audio: bytes) -> None:
        self.playing = True
        try:
            if sys.platform == "win32":
                import winsound

                winsound.PlaySound(audio, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
            else:
                import numpy as np
                import sounddevice as sd

                with wave.open(io.BytesIO(audio)) as handle:
                    rate = handle.getframerate()
                    data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
                sd.play(data, rate)
                sd.wait()
        finally:
            self.playing = False

    def stop(self) -> None:
        try:
            if sys.platform == "win32":
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            else:
                import sounddevice as sd

                sd.stop()
        except Exception:  # noqa: BLE001
            pass
        self.playing = False


class PushToTalk:
    """Hold the keys to record; let go to send. No hardware in here."""

    def __init__(self, api_url: str, hotkey: str = "ctrl+space",
                 recorder: Recorder = None, player: Player = None,
                 post: Callable = None, on_reply: Callable[[dict], None] = None):
        self.api_url = api_url.rstrip("/")
        self.hotkey = parse_hotkey(hotkey)
        self.recorder = recorder or Recorder()
        self.player = player or Player()
        self._post = post or self._http_post
        self.on_reply = on_reply
        self.held: Set[str] = set()
        self.recording = False
        self.busy = False
        self.started_at = 0.0
        self.last_reply: Optional[dict] = None
        self.enabled = True

    # ------------------------------------------------------------- keys
    def key_down(self, name: str) -> None:
        if not name or not self.enabled:
            return
        self.held.add(name)
        if not self.recording and self.hotkey <= self.held:
            if self.player.playing:
                self.player.stop()                 # the keys interrupt Cerebro talking
            if self.busy:
                return
            self._begin()

    def key_up(self, name: str) -> None:
        self.held.discard(name)
        if self.recording and name in self.hotkey:
            self._finish()

    # -------------------------------------------------------- recording
    def _begin(self) -> None:
        try:
            self.recorder.start()
        except Exception as exc:  # noqa: BLE001 - no microphone: say so once
            self._state("idle")
            self._notify_problem(f"The microphone couldn't be opened: {exc}")
            return
        self.recording, self.started_at = True, time.time()
        self._state("listening", "Listening… let go when you're done")

    def _finish(self) -> None:
        self.recording = False
        samples = self.recorder.stop()
        seconds = len(samples) / 2 / SAMPLE_RATE
        if seconds < MIN_SECONDS or time.time() - self.started_at < MIN_SECONDS:
            self._state("idle")
            return
        samples = samples[: int(MAX_SECONDS * SAMPLE_RATE * 2)]
        self.busy = True
        self._state("idle")
        threading.Thread(target=self._send, args=(wav_bytes(samples),), daemon=True,
                         name="cerebro-voice-send").start()

    def _send(self, audio: bytes) -> None:
        try:
            reply = self._post("/api/voice/command", files={"audio": ("speech.wav", audio, "audio/wav")},
                               data={"fmt": "wav", "speak": "true", "notify": "true"})
            self.last_reply = reply
            if self.on_reply:
                self.on_reply(reply)
            speech = (reply or {}).get("speech")
            if speech and speech.get("audio"):
                self.player.play(base64.b64decode(speech["audio"]))
        except Exception as exc:  # noqa: BLE001 - shown as a notice
            self._notify_problem(f"Cerebro couldn't act on that: {exc}")
        finally:
            self.busy = False

    # ------------------------------------------------------------ backend
    def _http_post(self, path: str, files=None, data=None, json=None, timeout: float = 300):
        import requests

        response = requests.post(self.api_url + path, files=files, data=data, json=json,
                                 timeout=timeout)
        response.raise_for_status()
        return response.json() if response.content else {}

    def _state(self, state: str, caption: str = None) -> None:
        try:
            self._post("/api/voice/state", json={"state": state, "caption": caption}, timeout=3)
        except Exception:  # noqa: BLE001 - the backend may be restarting
            pass

    def _notify_problem(self, text: str) -> None:
        print(text)
        try:
            self._post("/api/voice/notice", json={"title": "Voice", "body": text}, timeout=3)
        except Exception:  # noqa: BLE001
            pass


class VoiceService:
    """Runs :class:`PushToTalk` with a real global key listener."""

    def __init__(self, api_url: str):
        self.api_url = api_url.rstrip("/")
        self.ptt: Optional[PushToTalk] = None
        self._listener = None

    def _settings(self) -> dict:
        import requests

        try:
            return requests.get(f"{self.api_url}/api/voice/status", timeout=5).json()
        except Exception:  # noqa: BLE001
            return {}

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except ImportError:
            print("Push-to-talk unavailable (pip install pynput)")
            return False
        status = self._settings()
        if status and not status.get("enabled", True):
            return False
        self.ptt = PushToTalk(self.api_url, status.get("hotkey") or "ctrl+space")
        self._listener = keyboard.Listener(on_press=lambda k: self.ptt.key_down(key_name(k)),
                                           on_release=lambda k: self.ptt.key_up(key_name(k)))
        self._listener.daemon = True
        self._listener.start()
        threading.Thread(target=self._follow_settings, daemon=True, name="cerebro-voice-settings").start()
        return True

    def _follow_settings(self) -> None:
        while self._listener is not None:
            time.sleep(60)
            status = self._settings()
            if status and self.ptt:
                self.ptt.enabled = bool(status.get("enabled", True))
                self.ptt.hotkey = parse_hotkey(status.get("hotkey") or "ctrl+space")

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
