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

And the built-in 3D characters, into ``assets/faces/vrm/<slug>/model.vrm`` next
to their committed ``meta.json``, ``LICENSE.txt``, strips and preview (see
``VRM_MODELS`` for where each comes from and the licence in the file itself),
each with pixiv's idle animation as ``gestures/idle_loop.vrma``.

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


RAW = "https://raw.githubusercontent.com/{repo}/{rev}/{path}"
VRM_DIR = ROOT / "assets" / "faces" / "vrm"
# Each model's own VRM licence allows redistribution, modification and use by
# companies (read from the file; LICENSE.txt in each folder quotes it).
VRM_MODELS = {
    "stella": {"repo": "pixiv/ChatVRM", "rev": "b542aa00e19dccf9fc48ba340cf7eee011d2329a",
               "path": "public/AvatarSample_B.vrm",
               "sha256": "ffbd8c92a9e67c0a948f69c7a2eec91e5c282c9ae70e9184309fc164d74cbc27"},
    "kaori": {"repo": "vrm-c/vrm-specification", "rev": "94e82dd346fa6cf0337c4421728640e5252dd38e",
              "path": "samples/VRM1_Constraint_Twist_Sample/vrm/VRM1_Constraint_Twist_Sample.vrm",
              "sha256": "12c2b97e95e700783a6a550dc0eee2d7880aeedccef9ae67bc4c5a2f0f2631a2"},
    "seed-san": {"repo": "vrm-c/vrm-specification", "rev": "94e82dd346fa6cf0337c4421728640e5252dd38e",
                 "path": "samples/Seed-san/vrm/Seed-san.vrm",
                 "sha256": "624d0d554bc205bbdc33e22a68a2c3c20edebb3e573011ead8878a65e5329b23"},
    "kuro": {"repo": "pixiv/three-vrm", "rev": "48019ebcbbf021ef011ea26bc87d195834e02e03",
             "path": "packages/three-vrm/examples/models/three-vrm-girl.vrm",
             "sha256": "e6e179dea8bafc712900b5012628166a64e982d45566fb705d2ec52f579279b9"},
}
# pixiv's idle loop from ChatVRM (MIT), every built-in model's resting motion.
IDLE_GESTURE = {"repo": "pixiv/ChatVRM", "rev": "b542aa00e19dccf9fc48ba340cf7eee011d2329a",
                "path": "public/idle_loop.vrma",
                "sha256": "ace95ba6dcc0bdf2ed1081c002332b4184441117c8d543b6f642b3d2c5cf99be"}


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


def _raw(spec: dict, local: Path, check: bool, manifest: dict, key: str) -> None:
    if not _ok(local, spec["sha256"]):
        if check:
            raise SystemExit(f"Missing or wrong: {local}")
        print(f"  downloading {spec['repo']}/{spec['path']}")
        data = _get(RAW.format(**spec))
        if hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise SystemExit(f"{spec['path']} from {spec['repo']} doesn't match its pinned SHA-256")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
    manifest[key] = {"sha256": spec["sha256"], "bytes": local.stat().st_size}


def _vrm(check: bool, manifest: dict) -> None:
    for slug, spec in VRM_MODELS.items():
        folder = VRM_DIR / slug
        if not (folder / "meta.json").is_file():
            raise SystemExit(f"{folder / 'meta.json'} is missing (it's committed with the model's credit)")
        _raw(spec, folder / "model.vrm", check, manifest, f"vrm/{slug}/model.vrm")
        _raw(IDLE_GESTURE, folder / "gestures" / "idle_loop.vrma", check, manifest, f"vrm/{slug}/idle_loop.vrma")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "models"))
    parser.add_argument("--check", action="store_true", help="verify only; download nothing")
    parser.add_argument("--no-uv", action="store_true", help="skip uv.exe (non-Windows builds)")
    parser.add_argument("--no-vrm", action="store_true", help="skip the built-in 3D characters")
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
    if not args.no_vrm:
        print("Built-in 3D characters")
        _vrm(args.check, manifest)
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    total = sum(item["bytes"] for item in manifest.values())
    print(f"Models ready in {out} ({total / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
