# Faces: who you're talking to

Each agent has a face. It sits above its chat, reacts to what the agent is
doing and to how things turned out, and moves its mouth in time with its
voice. Choose it with **⚙** above the chat, or in the editor when making a
new agent.

| Face | What it is | Where it shows |
|---|---|---|
| **Brain** | Cerebro's pixel-art brain | Everywhere |
| **Anime** | An original idol-style support girl with a heart headset, drawn for Cerebro, in six colours and four hairstyles | Everywhere |
| **Live2D model** | A Live2D Cubism 3/4 model you load | The app window |
| **VRM 3D model** | A VRM model you load (make one for free in [VRoid Studio](https://vroid.com/en/studio)) | The app window |

The desktop buddy and the tray show the **default agent's** face. They can
only draw image frames, so Live2D and VRM faces appear there as the anime
face, in the colour and hairstyle you picked.

## The anime face

The face is an original character, not a copy of any existing one. She's
drawn in an idol / VTuber style: big glossy eyes with sparkles, layered
shiny hair with a bouncing ahoge, a sailor-collar jacket and a small
heart-shaped headset mic. She's cute and expressive, and kept wholesome.

**Look:**
- **Colours:** pink, black, silver, blue, brown and green.
- **Hairstyles:** long and straight, twin-tails with ribbons, bob, side
  ponytail.

Pick both in the agent editor; the preview updates as you click.

**Expressions** follow what the agent is doing:

| State | Expression |
|---|---|
| Ready | a soft smile, slow blinks |
| Thinking | eyes up, finger to chin, "…" |
| Searching | round reading glasses |
| Working | a determined squint |
| Listening | head tilt, headset light on |
| Waiting for approval | wide eyes, "!" |
| Error | swirly eyes, a sweat drop |
| Offline | asleep, "zz" |

**Reactions** play for a couple of seconds when something happens, then she
goes back to her state:

| What happened | Reaction |
|---|---|
| A reply finished something | `^ ^` happy, or a wink now and then |
| Something failed | teary and sorry |
| A change needs your approval | surprised |
| You approve a change | smug `:3`, "told you so" |
| You discard one | a pout |
| You say thanks or "great job" | heart eyes |

The desktop buddy shows the same reactions.

**Talking:** her mouth follows the loudness of the agent's voice, her blush
rises a little, and the ahoge bobs.

**Where it comes from:** she's drawn as SVG in
`backend/app/web/static/ui/face.js`. `packaging/make_face.py` renders her
into the image strips the buddy and tray use, in
`assets/faces/anime/<colour>/<hairstyle>/`.

## Loaded models: what you can tune

Both VRM and Live2D faces have a **Model** panel in the agent editor.

**Expressions.** Each of Cerebro's states and reactions (calm, happy, wink,
love, thinking, focused, listening, surprised, sorry, pout, smug, dizzy,
asleep) is mapped to one of the model's own expressions:
- Cerebro guesses by name first, for example `happy` → happy, `sorry` → sad,
  `wink` → blinkLeft, `smug` → relaxed.
- Change any of them from the list of what the model has, set a weight
  (how strongly it shows), and press **▶** to try it on the preview.
- **Auto-match** puts the guesses back.

**Framing.** **Zoom** and **Height** sliders, plus **Mirror** for Live2D.
The same framing is used in the chat and on the stage.

**Life.** Models blink at natural intervals (sometimes twice), glance about,
follow your cursor with their eyes and head, breathe, nod when something
works and tilt their head while thinking.

**Lip sync.** The voice is split into five frequency bands, which shape the
mouth into vowels: VRM's `aa` `ih` `ou` `ee` `oh`, and Live2D's
`ParamMouthOpenY` and `ParamMouthForm`.

## Loading a VRM model

1. Make or download a `.vrm` model. Check its licence allows your use.
2. Go to **⚙ → Face → VRM 3D model → Load a model…** and pick the file
   (100 MB at most).
3. Save.

The model is drawn with [three.js](https://threejs.org) and
[three-vrm](https://github.com/pixiv/three-vrm), both MIT-licensed and
included with Cerebro.

**Gestures.** VRM models can play **VRM Animation** (`.vrma`) files, a wave,
a nod or a cheer, for example. In the Model panel:
1. **Add gesture…** and pick a `.vrma` (20 MB at most). It's saved in the
   model's own folder.
2. Choose when it plays: greeting, done, approved, thinking, needs you, or
   now and then while idle.

Gestures use [three-vrm-animation](https://github.com/pixiv/three-vrm)
(MIT, included). They cross-fade back to the idle pose.

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
- The expressions (`.exp3.json`) and motion groups the model declares are
  listed in the Model panel and mapped as above.
- Where the model has them, Cerebro drives `ParamEyeLOpen`/`ParamEyeROpen`
  (blinks), `ParamEyeBallX/Y` and `ParamAngleX/Y/Z` (following your cursor),
  `ParamBodyAngleX` and `ParamBreath` (breathing), the mouth parameters (lip
  sync) and `ParamCheek` (blushing when happy).
- Tapping the model plays its `Tap…` motions; its own Idle motions play
  otherwise.

## Where models are kept

Loaded models are stored in `%LOCALAPPDATA%\Cerebro\faces\<id>\` and served
only to Cerebro's own window. Zips that try to write outside their folder are
refused, and gestures can only be saved inside the model's `gestures` folder.

## Not yet tried

The expression mapping, framing and gestures are tested against model-shaped
fixtures (a VRM's expression names, a `model3.json`'s expression and motion
lists, a minimal `.vrma`), not against a real downloaded model. Try your own
model in the editor's preview before relying on it.
