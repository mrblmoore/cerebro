// Cerebro's pixel-art mascot, animated per activity.
//
// The animation strips are built by packaging/make_mascot.py and served from
// /mascot (the same files the tray and desktop buddy use). Each activity
// state from app/core/activity_state.py maps to one pose: the laptop brain
// types while Cerebro thinks, browses or writes; the graduate brain reads
// while it searches. Switching state is one attribute change — the sprite
// and its CSS steps() animation come from rules generated from mascot.json.

const SPRITE_FOR = {
  idle: 'dozing', thinking: 'working', browsing: 'working', writing: 'working',
  syncing: 'working', searching: 'studying', listening: 'studying',
  awaiting_approval: 'alert', error: 'dizzy', offline: 'asleep',
};

const LABELS = {
  idle: 'Idle', thinking: 'Thinking', searching: 'Searching sources',
  browsing: 'Working in the browser', writing: 'Making a change',
  awaiting_approval: 'Waiting for your approval', syncing: 'Syncing',
  listening: 'Listening', error: 'Something needs attention', offline: 'Offline',
};

let ratio = 148 / 128;

/** Generate one animation rule per pose from the manifest, once. */
(async function loadManifest() {
  try {
    const manifest = await (await fetch('/mascot/mascot.json')).json();
    ratio = manifest.frame.width / manifest.frame.height;
    const rules = Object.entries(manifest.states).map(([name, info]) => `
      .mascot[data-sprite="${name}"] {
        background-image: url("/mascot/${info.file}");
        background-size: ${info.frames * 100}% 100%;
        animation: mascot-play ${info.frames * info.ms}ms steps(${info.frames}, jump-none) infinite;
      }`).join('');
    const style = document.createElement('style');
    style.textContent = rules;
    document.head.append(style);
  } catch { /* offline: the first frame of nothing — the layout still holds */ }
})();

/** Markup for a mascot of the given width (px). */
export function mascot(size = 40, state = 'idle') {
  return `<span class="mascot" data-sprite="${SPRITE_FOR[state] || 'dozing'}" data-state="${state}"
    style="width:${size}px;height:${Math.round(size / ratio)}px" aria-hidden="true"></span>`;
}

/** Set the activity state for every mascot inside ``root`` (or ``root`` itself). */
export function setBrainState(root, state) {
  const value = LABELS[state] ? state : 'idle';
  const nodes = root.matches?.('.mascot') ? [root] : root.querySelectorAll('.mascot:not(.still)');
  nodes.forEach(node => {
    node.dataset.state = value;
    node.dataset.sprite = SPRITE_FOR[value];
  });
}

/** Show a specific pose regardless of the global state (e.g. "studying" while a search runs). */
export function setPose(root, sprite) {
  const nodes = root.matches?.('.mascot') ? [root] : root.querySelectorAll('.mascot');
  nodes.forEach(node => { node.dataset.sprite = sprite; });
}

export function stateLabel(state) {
  return LABELS[state] || LABELS.idle;
}
