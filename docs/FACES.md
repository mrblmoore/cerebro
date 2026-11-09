# Faces: who you're talking to

Each agent has a face. It sits above its chat, reacts to what the agent is
doing, and moves its mouth in time with its voice. Choose it with **⚙** above
the chat, or in the editor when making a new agent.

| Face | What it is | Where it shows |
|---|---|---|
| **Brain** | Cerebro's pixel-art brain | Everywhere |
| **Anime** | An original anime-style support girl with a headset, drawn for Cerebro, in six colours | Everywhere |
| **Live2D model** | A Live2D Cubism 3/4 model you load | The app window |
| **VRM 3D model** | A VRM model you load (make one for free in [VRoid Studio](https://vroid.com/en/studio)) | The app window |

The desktop buddy and the tray show the **default agent's** face. They can
only draw image frames, so Live2D and VRM faces appear there as the anime
face, in the colour you picked.

## The anime face

The face is an original character, not a copy of any existing one.

**What it does:**
- **Moves:** blinks, breathes, sways a little, and follows your cursor with
  its eyes.
- **Reacts:**

  | State | Expression |
  |---|---|
  | Ready | a soft smile |
  | Thinking | eyes up, "…" |
  | Searching | reading glasses on |
  | Working | focused |
  | Listening | headset light on |
  | Waiting for approval | "!" |
  | Error | swirly eyes |
  | Offline | asleep |

- **Speaks:** its mouth opens with the loudness of the agent's voice.
- **Colours:** pink, black, silver, blue, brown and green hair, so you can
  tell agents apart.

**Where it comes from:** it's drawn as SVG in
`backend/app/web/static/ui/face.js`. `packaging/make_face.py` renders it into
the image strips the buddy and tray use, in `assets/faces/anime/<colour>/`.

## Loading a VRM model

1. Make or download a `.vrm` model. Check its licence allows your use.
2. Go to **⚙ → Face → VRM 3D model → Load a model…** and pick the file
   (100 MB at most).
3. Save.

The model is drawn with [three.js](https://threejs.org) and
[three-vrm](https://github.com/pixiv/three-vrm), both MIT-licensed and
included with Cerebro.

**How it's animated:**
- the `aa` mouth expression follows the voice;
- it blinks;
- it looks a little happy when ready, and surprised when it needs you;
- it nods while thinking, and sways gently.

## Loading a Live2D model

1. Get a Cubism 3 or 4 model: a folder with a `*.model3.json`, the `.moc3`
   and its textures. Zip the folder.
2. Go to **⚙ → Face → Live2D model → Load a model…** and pick the zip.
3. **The first time only:** Cerebro explains Live2D's licence and asks
   before downloading **Live2D Cubism Core** from Live2D's own website, into
   `%LOCALAPPDATA%\Cerebro\vendor`. Live2D's runtime is proprietary, so
   Cerebro is not allowed to include it.

**Licence warning.** Live2D's terms require a paid publication licence for
commercial use by organisations above Live2D's revenue threshold. Check with
Live2D, or your company's legal team, before using a Live2D face at work.
The model you load must allow your use as well.

**How it's animated:**
- The model is drawn with
  [pixi-live2d-display](https://github.com/guansss/pixi-live2d-display) and
  PixiJS 6, both MIT-licensed and included.
- The mouth (`ParamMouthOpenY`) follows the voice.
- The model's own Idle motions play.

## Where models are kept

Loaded models are stored in `%LOCALAPPDATA%\Cerebro\faces\<id>\` and served
only to Cerebro's own window. Zips that try to write outside their folder are
refused.
