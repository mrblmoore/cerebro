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
| **Amazon Polly (neural)** | Natural AWS voices. Uses the AWS access keys, profile or default identity set for Bedrock. A *Bedrock API key* can't call Polly. |
| **Microsoft Edge online voices** | Free, natural, no key. An unofficial Microsoft service, so it can change. |
| **Windows (offline)** | The voice built into Windows. Always works, sounds robotic. |
| **Don't speak** | Answers stay silent |

**Voice name** picks a voice for the chosen provider, for example:

- `Ruth` or `Matthew` for Polly;
- `en-US-AriaNeural` or `en-GB-SoniaNeural` for Edge.

**Speaking speed** goes from 0.5 to 2.0.

## Settings

All of these are in **Settings → Voice**:

| Setting | Default | |
|---|---|---|
| Talk to Cerebro | On | Push-to-talk and the mic button |
| Push-to-talk keys | `ctrl+space` | e.g. `ctrl+shift+space`, `f9` |
| Speech recognition | `base.en` | `tiny.en` · `base.en` · `small.en` |
| Speak replies | On | |
| Voice | Best available | |
| Mic button | Put the words in the box | or Send straight away |

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
