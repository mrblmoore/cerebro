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
| Stella (AvatarSample_B, VRoid Project / pixiv Inc.) | VRM Public License 1.0, redistribution and company use allowed | `assets/faces/vrm/stella/` |
| Kaori (VRM1_Constraint_Twist_Sample, pixiv Inc.) | VRM Public License 1.0, redistribution and company use allowed | `assets/faces/vrm/kaori/` |
| Kuro (three-vrm-girl, pixiv Inc.) | VRoid Hub licence, redistribution and company use allowed | `assets/faces/vrm/kuro/` |
| Seed-san (VirtualCast, Inc.) | VRM Public License 1.0, credit required: "Seed-san model by VirtualCast, Inc." | `assets/faces/vrm/seed-san/` |
| idle_loop.vrma (pixiv ChatVRM) | MIT | `assets/faces/vrm/*/gestures/` |

Each character's folder has its `LICENSE.txt`, quoting the licence written in
the model file itself.

Not bundled:
- **Live2D Cubism Core and Live2D's sample models.** Live2D's licence doesn't
  allow shipping them in an app that loads any model. They're downloaded from
  Live2D, or imported, only with your agreement.
- **Screenpipe**, which is separately licensed.
