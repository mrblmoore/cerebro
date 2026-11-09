# Talking to Cerebro

You can tell Cerebro what to do out loud, from anywhere on your PC, and it
answers aloud.

> *Hold **Ctrl+Space** →* "Reply to Priya and tell her the sensor replacement
> ships tomorrow." *→ let go.*

Cerebro:

- finds Priya's latest message;
- drafts the reply in your voice;
- says *"I've drafted a reply to Priya saying the sensor ships tomorrow —
  nothing's sent until you say send it."*

Then say **"Yes, send it"**, or press Approve.

## Two ways to talk

| | How | Best for |
|---|---|---|
| **Push-to-talk** | Hold **Ctrl+Space** in any app, speak, let go | Hands on the keyboard, window closed |
| **Mic button** | Click the 🎙 in the Ask box, speak, click again | Checking the words before sending |

**While you hold the keys:**

- The tray brain and the desktop buddy show **Listening…**.
- Pressing the keys again while Cerebro is talking stops it.

**After you let go:**

- The answer is spoken.
- It also arrives as a notification that opens the chat, so the Cerebro
  window doesn't have to be open.

**The mic button** puts the words in the box for you to check. To send them
straight away instead, set **Settings → Voice → Mic button** to **Send
straight away**.

## What you can say

Talk to it as you would type to it. A spoken request is treated as an
instruction: Cerebro acts on it rather than asking questions back, unless
something essential is missing.

**Resolving "this", "him" and "that case".** Cerebro uses what you have open:

- the case on screen;
- the chat you're in;
- the person you last messaged.

**Bigger jobs.** A job that takes several steps, such as "find every open
case for Smile Dental and summarise them", shows a short **plan** first. It
then gets more steps to finish in.

**Spoken approvals:**

| Say | What happens |
|---|---|
| "Yes, send it" · "Approve" · "Go ahead" | Approves the change or message waiting for approval |
| "Cancel that" · "Discard" · "No" | Discards it |
| "Stop" | Stops Cerebro talking |

**How spoken approvals stay safe:**

- A spoken "yes" only ever answers something prepared in the last 30
  minutes. With nothing waiting, it's just ordinary speech.
- Before you approve a message, Cerebro reads back who it's to and the gist.

## Talking to a particular agent

Push-to-talk goes to the agent on screen. Start with an agent's name to talk
to another one: *"Inbox, reply to Priya…"*. Each agent can have its own voice
(**⚙ → Voice**), and its face moves its mouth while it speaks.

## Privacy: speech stays on your PC

Speech is turned into text **on your PC** with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper). Nothing you say
is uploaded to be transcribed.

- **The speech model.** It's downloaded once, the first time you talk, to
  `%LOCALAPPDATA%\Cerebro\models`. You choose it in **Settings → Voice →
  Speech recognition**:
  - `tiny.en`: fastest;
  - `base.en`: the default;
  - `small.en`: most accurate.
- **Silence** is trimmed before transcribing.
- **Words Cerebro knows** are given to the model as hints so they come out
  spelled right: the open case, recent case numbers, people you message, and
  the systems and add-ons you're connected to. For example, "CAS zero one two
  three four" becomes `CAS-01234`.

## Cerebro's voice

Only a short summary of each answer is spoken, without links or citations.
The full answer is in the chat.

**Settings → Voice → Voice:**

| Choice | What it is |
|---|---|
| **Best available** (default) | Polly if it's set up, else Edge voices, else Windows |
| **Amazon Polly** | Natural AWS voices, with the most control over effects (see below). Uses the AWS access keys, profile or default identity set for Bedrock. A *Bedrock API key* can't call Polly. |
| **Amazon Nova Sonic** | AWS's speech-to-speech model: the most expressive voice, and the one behind **Talk live**. Same AWS sign-in as Polly. |
| **Microsoft Edge online voices** | Free, natural, no key. An unofficial Microsoft service, so it can change. |
| **Windows (offline)** | The voice built into Windows. Always works, sounds robotic. |
| **Don't speak** | Answers stay silent |

If the chosen voice fails, the next one speaks, so an answer is never
silent because of one service.

Each agent can have its own voice: open the agent editor's **Voice &
personality** section. **▶ Preview voice** speaks a sample line with the
settings on screen before you save them. **Settings → Voice → Test voice**
says a line and reports exactly what AWS (or the other service) answered.

## Personality

Each agent has a personality. It shapes how the agent talks in the chat and
how it sounds aloud.

| Preset | What it's like |
|---|---|
| **Sweetheart** (default) | Cute, cheerful, a little alluring and teasing: bubbly reactions, playful banter, a smug "told you so" when a fix works, a pout when you keep her waiting |
| **Warm** | Friendly and encouraging, gently playful |
| **Energetic** | Upbeat and fast, lots of enthusiasm |
| **Calm** | Soft-spoken and soothing |
| **Professional** | Crisp and to the point |
| **Custom** | Your own description |

Three sliders fine-tune any preset:
- **Playfulness:** how much teasing and banter;
- **Expressiveness:** how often she giggles, gasps or whispers;
- **Formality:** casual or formal wording.

**Always:**
- She stays kind and work-appropriate: never sexual, never crude, never
  belittling.
- She drops the teasing when you're stressed, something failed, or the
  topic is serious.
- The personality is only for the agent's own replies to you. Anything
  drafted for someone else (emails, Teams replies, case notes, documents)
  keeps **your** writing style.

## Expressive speech

When **Expressive speech** is on (Settings → Voice), spoken replies carry
small cues. You never see them in the chat; each voice performs them as
well as it can.

| Cue | What you hear |
|---|---|
| `[giggle]` `[laugh]` `[gasp]` `[sigh]` `[hmm]` `[yay]` | a little "hehe", "oh!", "haah…", a cheer |
| `[pause]` | a short pause |
| `[whisper]…[/whisper]` | whispered |
| `[soft]…[/soft]` | softer |
| `[excited]…[/excited]` | brighter and faster |
| `[slow]…[/slow]` | slower |
| `[emphasis]…[/emphasis]` | stressed |
| `[tease]…[/tease]` | a playful, sing-song aside |

Spoken requests ask the agent for up to three cues, by its expressiveness.
Typed replies that are read aloud get a few gentle ones from how they read:
"haha" becomes a giggle, "oops" a gasp, an exclamation a lift.

### Amazon Polly engines

Polly has four engines, and each accepts different SSML. Cerebro builds
SSML only from tags the chosen engine accepts, following the "Supported SSML
tags" table in AWS's Polly developer guide.

| | Generative | Neural | Long-form | Standard |
|---|---|---|---|---|
| Sound | Most natural and expressive | Natural | Natural, for long reading | Older, more robotic |
| Speed and loudness | ✓ | ✓ | ✓ | ✓ |
| Pauses | ✓ | ✓ | ✓ | ✓ |
| Pitch | – | – | – | ✓ |
| Real whisper, soft voice | – (said quietly instead) | – (said quietly instead) | – | ✓ |
| Emphasis | – | – | – | ✓ |
| Timbre (vocal-tract length) | – | – | – | ✓ |
| Breathing | – | – | – | ✓ |
| Loudness compression (DRC) | – | ✓ | – | ✓ |
| Newscaster style | – | Matthew, Joanna, Amy, Lupe | – | – |

**Generative** is the default. Not every voice has every engine. Cerebro
reads which engines each voice has from Polly and steps down (generative →
neural → standard) when it must. **Standard** is the "effects" engine: pick
it for real whispers, pitch, timbre and breaths.

If Polly ever refuses Cerebro's SSML, Cerebro tries plainer SSML, then plain
text, so a tag never stops an answer being spoken.

### Amazon Nova Sonic

Nova Sonic speaks in character: it gets the agent's personality and reads
the reply expressively, performing the cues as stage directions.

| Setting | Default |
|---|---|
| Model | `amazon.nova-2-sonic-v1:0` (or `amazon.nova-sonic-v1:0`) |
| Region | `us-east-1` |
| Voice | `tiffany` (also `matthew`, `amy`, `olivia`, `lupe`, `carlos`, `ambre`, `florian`, …) |

**Needs:**
- Cerebro's Python 3.12 build (the Windows installer is) with AWS's
  `aws-sdk-bedrock-runtime` package and the AWS CRT (both included in the
  installer);
- AWS access keys, a profile or the default AWS identity under **Settings →
  AI Provider** (a Bedrock API key can't sign it);
- access to the Nova Sonic model in the Bedrock console, in that region.

Settings → Voice says which of these is missing.

## Talk live

With Nova Sonic set up, a **Talk live** button appears next to the mic in
the app window. Press it and just talk:
- She answers straight away, out loud, in her personality.
- Talk over her to interrupt; she stops and listens.
- Real work goes to the agent: the same tools and scope, and changes still
  wait for your approval. "Yes, send it" approves aloud, as with
  push-to-talk.
- Both sides are written into the chat, and cards (drafts, approvals) appear
  as usual.
- Her face follows her voice and reacts to how things go.

Press **End** or the button again to stop. AWS ends a live session after
about 8 minutes; press it again to carry on. Push-to-talk and the tray keep
working as before.

## Settings

All of these are in **Settings → Voice**:

| Setting | Default | |
|---|---|---|
| Talk to Cerebro | On | Push-to-talk, the mic button and Talk live |
| Push-to-talk keys | `ctrl+space` | e.g. `ctrl+shift+space`, `f9` |
| Speech recognition | `base.en` | `tiny.en` · `base.en` · `small.en` |
| Speak replies | On | |
| Voice | Best available | |
| Default personality | Sweetheart | for agents that haven't chosen one |
| Expressive speech | On | sound cues in spoken replies |
| Polly engine | Generative | |
| Nova Sonic voice, model, region | `tiffany`, `amazon.nova-2-sonic-v1:0`, `us-east-1` | |
| Mic button | Put the words in the box | or Send straight away |

## What hasn't been tried against AWS yet

Cerebro's Polly and Nova Sonic code is tested against stand-ins built from
AWS's documented shapes and AWS's official Nova Sonic sample. Against the
real service, Cerebro's Nova Sonic connection opens, signs and sends, and
AWS answers. But no audio has been spoken with real credentials yet.

The first real run happens on your PC. **Test voice** shows exactly what AWS
says. In particular:
- whether Nova Sonic reads a reply aloud from text alone (AWS calls this
  "cross-modal input");
- whether Polly's generative engine accepts every tag Cerebro sends.

If either fails, the next voice speaks instead.

## Troubleshooting

**Nothing happens when I hold the keys.**

- Check **Talk to Cerebro** is on.
- Another app may use Ctrl+Space (some keyboard layouts switch input
  language with it). Pick other keys under **Push-to-talk keys**. A change is
  picked up within a minute.

**"The microphone couldn't be opened."** Windows **Settings → Privacy &
security → Microphone** must allow desktop apps.

**The first request is slow.** The speech model is downloading, about
150 MB for `base.en`. Later requests start at once.

**It mishears names.** Try `small.en`. Names Cerebro has seen in your
messages and cases are recognised better over time.

**Talk live doesn't appear.** It needs Nova Sonic: Settings → Voice says
what's missing (the AWS sign-in, the 3.12 build, or model access).

**She sounds flat.** Turn on **Expressive speech**, raise **Expressiveness**
in the agent editor, or pick the Polly **Standard** engine for whispers and
pitch.
