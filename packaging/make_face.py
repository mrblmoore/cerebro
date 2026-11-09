"""
Render the anime face (backend/app/web/static/ui/face.js) into sprite strips
for the desktop buddy and the tray, which can only show image frames.

    python packaging/make_face.py

For each palette and hairstyle it writes
``assets/faces/anime/<palette>/<hair>/<sprite>.png`` (one horizontal strip per
expression, quantized to keep the installer small) and ``face.json`` in the same format as
``assets/mascot/mascot.json``. ``talk`` holds mouth shapes from closed to wide
open, picked by speech level rather than by time. Run it after changing the
face; the output is committed, so building Cerebro doesn't need a browser.

    python packaging/make_face.py --vrm

renders the built-in 3D models (``assets/faces/vrm/<slug>/``) the same way,
into ``strips/`` and a ``preview.png`` for the agent editor's gallery.
"""

import http.server
import json
import threading
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "backend" / "app" / "web" / "static"
OUT = ROOT / "assets" / "faces" / "anime"
SIZE = 128

#: sprite -> (expression, frames, ms per frame, start time in ms)
SPRITES = {
    "calm": ("calm", 12, 150, 3600),
    "thinking": ("thinking", 8, 150, 0),
    "reading": ("reading", 8, 160, 3600),
    "focused": ("focused", 8, 140, 3600),
    "listening": ("listening", 8, 150, 3600),
    "surprised": ("surprised", 8, 95, 0),
    "dizzy": ("dizzy", 8, 90, 0),
    "asleep": ("asleep", 12, 220, 0),
    # Passing reactions the buddy shows for a few seconds.
    "happy": ("happy", 8, 120, 0),
    "sorry": ("sorry", 8, 140, 0),
}
TALK = (0.0, 0.25, 0.5, 0.75, 1.0)

SHEET = """<!doctype html><html><body style="margin:0;background:transparent">
<div id="host" style="width:{size}px;height:{size}px"></div>
<script type="module">
  import {{ animeSVG, poseAnime, PALETTE_NAMES, HAIR_NAMES }} from '/ui/face.js';
  window.PALETTES = PALETTE_NAMES;
  window.HAIRS = HAIR_NAMES;
  window.draw = (palette, hair, expr, t, mouth) => {{
    const host = document.getElementById('host');
    host.innerHTML = animeSVG({{ palette, hair }}, expr);
    const svg = host.querySelector('svg');
    svg.setAttribute('width', {size}); svg.setAttribute('height', {size});
    poseAnime(svg, t, mouth);
    return true;
  }};
  window.ready = true;
</script></body></html>"""


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/sheet.html"):
            body = SHEET.format(size=SIZE).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def build() -> dict:
    from io import BytesIO

    from PIL import Image
    from playwright.sync_api import sync_playwright

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler, directory=str(STATIC)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    summary = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": SIZE, "height": SIZE})
            page.goto(f"http://127.0.0.1:{server.server_port}/sheet.html")
            page.wait_for_function("window.ready === true")
            palettes = page.evaluate("window.PALETTES")
            hairs = page.evaluate("window.HAIRS")

            def shot(palette, hair, expr, t, mouth):
                page.evaluate("([p, h, e, t, m]) => window.draw(p, h, e, t, m)", [palette, hair, expr, t, mouth])
                return Image.open(BytesIO(page.locator("#host").screenshot(omit_background=True))).convert("RGBA")

            for palette in palettes:
                for old in (OUT / palette).glob("*.png") if (OUT / palette).is_dir() else ():
                    old.unlink()                          # the pre-0.9 single-hairstyle strips
                (OUT / palette / "face.json").unlink(missing_ok=True)
                for hair in hairs:
                    target = OUT / palette / hair
                    target.mkdir(parents=True, exist_ok=True)
                    manifest = {"frame": {"width": SIZE, "height": SIZE}, "states": {}}
                    for sprite, (expr, count, ms, start) in SPRITES.items():
                        frames = [shot(palette, hair, expr, start + i * ms, 0.0) for i in range(count)]
                        _strip(frames, target / f"{sprite}.png")
                        manifest["states"][sprite] = {"frames": count, "ms": ms, "file": f"{sprite}.png"}
                    frames = [shot(palette, hair, "calm", 1200, level) for level in TALK]
                    _strip(frames, target / "talk.png")
                    manifest["states"]["talk"] = {"frames": len(TALK), "ms": 80, "file": "talk.png",
                                                  "levels": list(TALK)}
                    (target / "face.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                    summary[f"{palette}/{hair}"] = len(manifest["states"])
            browser.close()
    finally:
        server.shutdown()
    return summary


def _strip(frames, path: Path) -> None:
    from PIL import Image

    strip = Image.new("RGBA", (SIZE * len(frames), SIZE), (0, 0, 0, 0))
    for index, frame in enumerate(frames):
        strip.paste(frame.resize((SIZE, SIZE)), (index * SIZE, 0))
    # 128 colours with alpha: about a quarter of the size, and indistinguishable at buddy size.
    strip.quantize(colors=128, method=Image.Quantize.FASTOCTREE).save(path, optimize=True)


VRM_DIR = ROOT / "assets" / "faces" / "vrm"


def build_vrm() -> dict:
    """Render each built-in VRM model (``assets/faces/vrm/<slug>/model.vrm``) into
    the desktop buddy's strips and a gallery preview, with the app's own
    renderer (face.js ``capture()``), so they match what the app shows."""
    import base64
    import os
    import sys
    import tempfile
    import time

    import uvicorn
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mktemp(suffix=".db"))
    sys.path.insert(0, str(ROOT / "backend"))
    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    summary = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/app")
            page.wait_for_timeout(1500)
            for folder in sorted(f for f in VRM_DIR.iterdir() if (f / "model.vrm").is_file()) if VRM_DIR.is_dir() else []:
                meta = json.loads((folder / "meta.json").read_text(encoding="utf-8")) if (folder / "meta.json").is_file() else {}
                result = page.evaluate("""async ([id, framing, expressions]) => {
                  const f = await import('/static/ui/face.js');
                  const host = document.createElement('div'); host.style.width = host.style.height = '256px';
                  document.body.append(host);
                  const style = { model: id, entry: 'model.vrm', framing: framing || {}, expressions: expressions || {} };
                  const face = f.createFace(host, { face: 'vrm', face_style: style });
                  const toB64 = b => new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result.split(',')[1]); fr.readAsDataURL(b); });
                  const strips = await face.capture({ size: %d });
                  const preview = await face.capture({ size: 256, strips: { preview: { ...f.STRIP_PLAN.calm, frames: 1 } } });
                  const out = { manifest: strips.manifest, files: {}, preview: await toB64(preview.strips.preview) };
                  for (const [name, blob] of Object.entries(strips.strips)) out.files[name] = await toB64(blob);
                  face.destroy();
                  return out;
                }""" % SIZE, [f"builtin-{folder.name}", meta.get("framing"), meta.get("expressions_map")])
                target = folder / "strips"
                target.mkdir(exist_ok=True)
                for name, data in result["files"].items():
                    (target / f"{name}.png").write_bytes(base64.b64decode(data))
                    _quantize(target / f"{name}.png")
                (target / "face.json").write_text(json.dumps(result["manifest"], indent=2), encoding="utf-8")
                (folder / "preview.png").write_bytes(base64.b64decode(result["preview"]))
                summary[folder.name] = len(result["files"])
            browser.close()
    finally:
        server.should_exit = True
    return summary


def _quantize(path: Path) -> None:
    from PIL import Image

    image = Image.open(path).convert("RGBA")
    image.quantize(colors=128, method=Image.Quantize.FASTOCTREE).save(path, optimize=True)


if __name__ == "__main__":
    import sys as _sys

    if "--vrm" in _sys.argv:
        for name, count in build_vrm().items():
            print(f"{name}: {count} strips")
    else:
        for palette, count in build().items():
            print(f"{palette:7} {count} strips")
