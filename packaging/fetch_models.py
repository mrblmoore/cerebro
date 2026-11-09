#!/usr/bin/env python3
"""
Fetch the models Cerebro ships, so nothing has to be downloaded on the user's PC.

Run before building (CI caches the result):

    python packaging/fetch_models.py            # into ./models
    python packaging/fetch_models.py --check    # only verify what's there

What goes into ``models/``:

* ``whisper/base.en/`` — the speech-to-text model (faster-whisper's CTranslate2
  conversion of OpenAI Whisper base.en; MIT).
* ``kokoro/model_quantized.onnx`` and ``kokoro/voices/*.bin`` — the offline
  voice (Kokoro-82M by hexgrad, ONNX export by onnx-community; Apache-2.0).
* ``tools/uv.exe`` — Astral's uv, for add-ons that run Python tools (Windows
  only; MIT / Apache-2.0).

Every file is checked: a pinned SHA-256 where one is known, otherwise a minimum
size, and all hashes are written to ``models/MANIFEST.json``.
"""

import argparse
import hashlib
import io
import json
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HF = "https://huggingface.co/{repo}/resolve/{rev}/{path}"

WHISPER = {
    "repo": "Systran/faster-whisper-base.en", "rev": "main", "dest": "whisper/base.en",
    "files": {"config.json": 1_000, "model.bin": 100_000_000, "tokenizer.json": 1_000_000,
              "vocabulary.txt": 300_000},
    "license": ("faster-whisper-base.en: OpenAI Whisper base.en (MIT, Copyright (c) 2022 OpenAI),\n"
                "converted to CTranslate2 by SYSTRAN (https://huggingface.co/Systran/faster-whisper-base.en, MIT).\n"),
}
KOKORO_VOICES = ("af_heart", "af_bella", "af_nicole", "af_sky", "af_nova", "bf_emma", "am_michael")
KOKORO = {
    "repo": "onnx-community/Kokoro-82M-v1.0-ONNX", "rev": "main", "dest": "kokoro",
    "files": {"onnx/model_quantized.onnx": "fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478",
              **{f"voices/{v}.bin": 500_000 for v in KOKORO_VOICES}},
    "license": ("Kokoro-82M by hexgrad (https://huggingface.co/hexgrad/Kokoro-82M), Apache License 2.0.\n"
                "ONNX export by onnx-community (https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX).\n"),
}
UV = {"url": "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",
      "dest": "tools", "members": ("uv.exe", "uvx.exe"), "min": {"uv.exe": 10_000_000, "uvx.exe": 100_000},
      "license": "uv by Astral (https://github.com/astral-sh/uv), MIT OR Apache-2.0.\n"}


def _get(url: str, tries: int = 4) -> bytes:
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "cerebro-build"})
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 - retried, then raised
            if attempt == tries - 1:
                raise RuntimeError(f"Couldn't download {url}: {exc}") from exc
            time.sleep(2 ** (attempt + 1))
    raise AssertionError("unreachable")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _ok(path: Path, expect) -> bool:
    if not path.is_file():
        return False
    if isinstance(expect, str):
        return _sha(path) == expect
    return path.stat().st_size >= expect


def _hf(spec: dict, out: Path, check: bool, manifest: dict) -> None:
    dest = out / spec["dest"]
    for remote, expect in spec["files"].items():
        local = dest / remote.split("/", 1)[1] if remote.startswith("onnx/") else dest / remote
        if not _ok(local, expect):
            if check:
                raise SystemExit(f"Missing or wrong: {local}")
            print(f"  downloading {spec['repo']}/{remote}")
            data = _get(HF.format(repo=spec["repo"], rev=spec["rev"], path=remote))
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            if not _ok(local, expect):
                raise SystemExit(f"Downloaded {remote} but it failed its check ({local.stat().st_size} bytes)")
        manifest[str(local.relative_to(out)).replace("\\", "/")] = {"sha256": _sha(local),
                                                                     "bytes": local.stat().st_size}
    (dest / "LICENSE.txt").write_text(spec["license"], encoding="utf-8")


def _uv(out: Path, check: bool, manifest: dict) -> None:
    folder = out / UV["dest"]
    missing = [m for m in UV["members"] if not _ok(folder / m, UV["min"][m])]
    if missing:
        if check:
            raise SystemExit(f"Missing or wrong: {', '.join(missing)} in {folder}")
        print("  downloading uv")
        with zipfile.ZipFile(io.BytesIO(_get(UV["url"]))) as archive:
            folder.mkdir(parents=True, exist_ok=True)
            for member in UV["members"]:
                name = next(n for n in archive.namelist() if n.endswith("/" + member) or n == member)
                (folder / member).write_bytes(archive.read(name))
    for member in UV["members"]:
        manifest[f"{UV['dest']}/{member}"] = {"sha256": _sha(folder / member), "bytes": (folder / member).stat().st_size}
    (folder / "LICENSE.txt").write_text(UV["license"], encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "models"))
    parser.add_argument("--check", action="store_true", help="verify only; download nothing")
    parser.add_argument("--no-uv", action="store_true", help="skip uv.exe (non-Windows builds)")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    print("Speech-to-text model")
    _hf(WHISPER, out, args.check, manifest)
    print("Offline voice (Kokoro)")
    _hf(KOKORO, out, args.check, manifest)
    if not args.no_uv:
        print("uv")
        _uv(out, args.check, manifest)
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    total = sum(item["bytes"] for item in manifest.values())
    print(f"Models ready in {out} ({total / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
