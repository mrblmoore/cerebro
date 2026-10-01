// Cerebro's brain mascot: the logo mark with a face, animated per activity.
//
// States mirror app/core/activity_state.py: idle, thinking, searching,
// browsing, writing, awaiting_approval, syncing, listening, error, offline.
// Every animation is CSS (see .brain rules in app.css), so the SVG is static
// and switching state is a single attribute change.

const HEMISPHERE = 'M128 30C106 30 88 41 80 59 60 58 44 71 42 90 27 98 20 116 26 133 ' +
  '17 149 22 170 38 179 41 199 58 212 78 209 90 221 110 227 128 222Z';

let uid = 0;

export function brainSVG(size = 40) {
  const id = `bg${++uid}`;
  return `
<svg class="brain" data-state="idle" viewBox="-24 -24 304 304" width="${size}" height="${size}" aria-hidden="true">
  <defs>
    <linearGradient id="${id}" x1="24" y1="28" x2="232" y2="228" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#9B8CFF"/><stop offset=".55" stop-color="#6D6AF5"/><stop offset="1" stop-color="#2DD4EE"/>
    </linearGradient>
    <radialGradient id="${id}g" cx="50%" cy="50%" r="50%">
      <stop offset="0" stop-color="#8B7CF6" stop-opacity=".55"/><stop offset="1" stop-color="#8B7CF6" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <circle class="b-glow" cx="128" cy="128" r="140" fill="url(#${id}g)"/>
  <g class="b-ring"><ellipse cx="128" cy="128" rx="138" ry="52" fill="none" stroke="#22D3EE" stroke-width="7" stroke-dasharray="26 18" opacity=".85"/><circle cx="266" cy="128" r="11" fill="#22D3EE"/></g>
  <g class="b-body">
    <g fill="url(#${id})">
      <path d="${HEMISPHERE}"/>
      <path d="${HEMISPHERE}" transform="translate(256,0) scale(-1,1)"/>
    </g>
    <g class="b-folds" fill="none" stroke="#fff" stroke-opacity=".28" stroke-width="7" stroke-linecap="round">
      <path d="M128 40v176"/>
      <path d="M62 92c14-4 26 2 30 12"/><path d="M50 150c16-8 30-4 36 8"/>
      <path d="M194 92c-14-4-26 2-30 12"/><path d="M206 150c-16-8-30-4-36 8"/>
      <path d="M90 196c6-10 18-14 28-10"/><path d="M166 196c-6-10-18-14-28-10"/>
    </g>
    <g class="b-neurons" fill="#fff">
      <circle class="n1" cx="74" cy="78" r="7"/><circle class="n2" cx="186" cy="76" r="7"/>
      <circle class="n3" cx="44" cy="128" r="6"/><circle class="n4" cx="214" cy="132" r="6"/>
      <circle class="n5" cx="96" cy="208" r="6"/><circle class="n6" cx="162" cy="210" r="6"/>
    </g>
    <g class="b-face">
      <g class="b-eyes">
        <g class="b-eye"><ellipse cx="98" cy="126" rx="17" ry="20" fill="#fff"/><circle class="b-pupil" cx="100" cy="129" r="9" fill="#1b1640"/><circle cx="104" cy="123" r="3.2" fill="#fff"/></g>
        <g class="b-eye"><ellipse cx="158" cy="126" rx="17" ry="20" fill="#fff"/><circle class="b-pupil" cx="160" cy="129" r="9" fill="#1b1640"/><circle cx="164" cy="123" r="3.2" fill="#fff"/></g>
      </g>
      <g class="b-closed" fill="none" stroke="#1b1640" stroke-width="7" stroke-linecap="round">
        <path d="M84 130c8 7 20 7 28 0"/><path d="M144 130c8 7 20 7 28 0"/>
      </g>
      <g class="b-dizzy" fill="none" stroke="#1b1640" stroke-width="5" stroke-linecap="round">
        <path d="M98 126m-4 0a4 4 0 1 1 8 0a8 8 0 1 1-16 0a12 12 0 1 1 24 0"/>
        <path d="M158 126m-4 0a4 4 0 1 1 8 0a8 8 0 1 1-16 0a12 12 0 1 1 24 0"/>
      </g>
      <ellipse cx="76" cy="156" rx="12" ry="7" fill="#ff7eb6" opacity=".55"/>
      <ellipse cx="180" cy="156" rx="12" ry="7" fill="#ff7eb6" opacity=".55"/>
      <path class="b-mouth" d="M114 158c8 9 20 9 28 0" fill="none" stroke="#1b1640" stroke-width="7" stroke-linecap="round"/>
      <ellipse class="b-mouth-o" cx="128" cy="162" rx="8" ry="10" fill="#1b1640"/>
    </g>
  </g>
  <g class="b-badge"><circle cx="222" cy="40" r="30" fill="#F59E0B"/><path d="M222 24v20" stroke="#fff" stroke-width="9" stroke-linecap="round"/><circle cx="222" cy="56" r="5" fill="#fff"/></g>
  <g class="b-glass"><circle cx="214" cy="200" r="24" fill="none" stroke="#fff" stroke-width="9"/><path d="M231 217l18 18" stroke="#fff" stroke-width="11" stroke-linecap="round"/></g>
  <g class="b-globe" fill="none" stroke="#22D3EE" stroke-width="6"><circle cx="218" cy="206" r="28" fill="#0b1324"/><ellipse cx="218" cy="206" rx="12" ry="28"/><path d="M190 206h56M194 192h48M194 220h48"/></g>
  <g class="b-pencil"><g transform="translate(206 168) rotate(35)"><rect x="-8" y="-34" width="16" height="52" rx="3" fill="#FBBF24"/><path d="M-8 18L0 34 8 18Z" fill="#fde68a"/><rect x="-8" y="-40" width="16" height="8" rx="2" fill="#f472b6"/></g></g>
  <g class="b-waves" fill="none" stroke="#22D3EE" stroke-width="7" stroke-linecap="round"><path class="w1" d="M244 104c10 14 10 34 0 48"/><path class="w2" d="M262 90c16 22 16 54 0 76"/></g>
  <g class="b-zzz" fill="#a5b4fc" font-family="Inter Variable, sans-serif" font-weight="800"><text class="z1" x="200" y="64" font-size="40">z</text><text class="z2" x="228" y="34" font-size="30">z</text></g>
</svg>`;
}

const LABELS = {
  idle: 'Idle', thinking: 'Thinking', searching: 'Searching sources',
  browsing: 'Working in the browser', writing: 'Making a change',
  awaiting_approval: 'Waiting for your approval', syncing: 'Syncing',
  listening: 'Listening', error: 'Something needs attention', offline: 'Offline',
};

export function setBrainState(root, state) {
  const value = LABELS[state] ? state : 'idle';
  (root.matches?.('svg.brain') ? [root] : root.querySelectorAll('svg.brain'))
    .forEach(svg => svg.setAttribute('data-state', value));
}

export function stateLabel(state) {
  return LABELS[state] || LABELS.idle;
}
