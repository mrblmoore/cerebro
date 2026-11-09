# Third-party components bundled with Cerebro

Components added in 0.10 for working offline (see the notices next to each
file in the installed app):

| Component | Licence | Where |
|---|---|---|
| faster-whisper `base.en` model (OpenAI Whisper, converted by SYSTRAN) | MIT | `models/whisper/base.en/` |
| Kokoro-82M (hexgrad), ONNX export (onnx-community) and voices | Apache-2.0 | `models/kokoro/` |
| misaki American English dictionaries (hexgrad) | Apache-2.0 | `app/services/g2p_data/` (with LICENSE and NOTICE) |
| uv / uvx (Astral) | MIT or Apache-2.0 | `models/tools/` |
| three-vrm-animation (pixiv) | MIT | `static/vendor/three-vrm-animation/` |
| Built-in VRM characters | CC0 (each model's `LICENSE.txt`) | `assets/faces/vrm/` |

Not bundled: Live2D Cubism Core (proprietary; downloaded or imported only with
your agreement) and Screenpipe (separately licensed).
