// Faces for Cerebro's agents: the pixel brain, an original idol-style anime
// girl, or a Live2D / VRM model the user loads. Every face takes the same calls:
//
//   const face = createFace(host, {face: 'anime', style: {palette: 'pink', hair: 'twintails'}});
//   face.setState('thinking');   // an activity state (see brain.js)
//   face.setMouth(0.6);          // 0..1 — how open, for lip sync
//   face.react('happy');         // a passing reaction: happy, wink, love, sorry, smug, surprised, pout
//   face.destroy();
//
// The anime face is drawn here as layered SVG (an original character — not a
// copy of any existing one), animated by time so the same drawing can be
// rendered frame by frame for the desktop buddy (packaging/make_face.py).
import { mascot, setBrainState } from './brain.js';

// ------------------------------------------------------------- palettes
export const PALETTES = {
  pink:   { hair: '#f7a8cc', shade: '#d9719f', light: '#ffe0ee', eye: '#7c4dff', eye2: '#d6c6ff', accent: '#ff6fae', coat: '#fff4f9' },
  black:  { hair: '#33354f', shade: '#1a1b2b', light: '#6a6f9c', eye: '#c2410c', eye2: '#ffd27a', accent: '#e64980', coat: '#f6f4ff' },
  silver: { hair: '#e4e7f3', shade: '#a9b0cb', light: '#ffffff', eye: '#0284c7', eye2: '#a5e8ff', accent: '#8b7cf6', coat: '#f5f6ff' },
  blue:   { hair: '#7fb2ff', shade: '#4677db', light: '#c8ddff', eye: '#e8590c', eye2: '#ffe08a', accent: '#3fa9f5', coat: '#f2f8ff' },
  brown:  { hair: '#b07a52', shade: '#7a4e2f', light: '#e2b38b', eye: '#2f9e44', eye2: '#b2f2bb', accent: '#f08c00', coat: '#fffaf2' },
  green:  { hair: '#8fe0b8', shade: '#3fae80', light: '#d3f9e6', eye: '#d6336c', eye2: '#ffc0d9', accent: '#20c997', coat: '#f3fff9' },
};
export const PALETTE_NAMES = Object.keys(PALETTES);
export const HAIRSTYLES = { long: 'Long', twintails: 'Twin-tails', bob: 'Bob', sideponytail: 'Side ponytail' };
export const HAIR_NAMES = Object.keys(HAIRSTYLES);
export const EXPRESSIONS = ['calm', 'happy', 'wink', 'love', 'thinking', 'reading', 'focused', 'listening',
  'surprised', 'sorry', 'pout', 'dizzy', 'smug', 'asleep'];
export const REACTIONS = ['happy', 'wink', 'love', 'sorry', 'smug', 'surprised', 'pout'];

// Activity state -> expression.
const EXPRESSION = {
  idle: 'calm', thinking: 'thinking', searching: 'reading', browsing: 'focused',
  writing: 'focused', syncing: 'focused', listening: 'listening',
  awaiting_approval: 'surprised', error: 'dizzy', offline: 'asleep', speaking: 'calm',
};
// Older names still accepted.
const ALIASES = { alert: 'surprised' };

let uid = 0;

/** The parts that differ between hairstyles: what's behind the head, and the side locks. */
function hairParts(hair, id, p) {
  const fill = `url(#${id}hair)`;
  const tie = (x, y, flip = 1) => `
    <g class="ribbon" transform="translate(${x} ${y}) scale(${flip} 1)">
      <path d="M0 0 C-10 -10 -18 -4 -16 4 C-14 10 -6 6 0 0 C6 6 14 10 16 4 C18 -4 10 -10 0 0 Z" fill="${p.accent}" stroke="#fff" stroke-width="1.2"/>
      <path d="M-2 2 L-8 18 L-3 15 L0 20 L2 3 Z" fill="${p.accent}" opacity=".9"/>
      <circle r="3.4" fill="#fff" opacity=".9"/><circle r="2" fill="${p.accent}"/>
    </g>`;
  if (hair === 'twintails') {
    return {
      back: `<path class="hair-back" d="M44 92 C40 42 70 18 100 18 C130 18 160 42 156 92 L152 128 L48 128 Z" fill="${fill}"/>
        <g class="tail tail-l"><path d="M50 64 C24 74 14 112 20 150 C24 172 30 186 40 196 C40 176 46 158 50 138 C54 116 58 92 60 74 Z" fill="${fill}"/>
          <path d="M34 98 C28 120 28 148 36 172" fill="none" stroke="${p.light}" stroke-width="2.4" opacity=".55" stroke-linecap="round"/></g>
        <g class="tail tail-r"><path d="M150 64 C176 74 186 112 180 150 C176 172 170 186 160 196 C160 176 154 158 150 138 C146 116 142 92 140 74 Z" fill="${fill}"/>
          <path d="M166 98 C172 120 172 148 164 172" fill="none" stroke="${p.light}" stroke-width="2.4" opacity=".55" stroke-linecap="round"/></g>`,
      locks: `<path class="lock lock-l" d="M57 78 C50 100 51 120 58 138 C61 120 64 102 67 86 Z" fill="${fill}"/>
        <path class="lock lock-r" d="M143 78 C150 100 149 120 142 138 C139 120 136 102 133 86 Z" fill="${fill}"/>`,
      extra: tie(52, 62, 1) + tie(148, 62, -1),
    };
  }
  if (hair === 'bob') {
    return {
      back: `<path class="hair-back" d="M40 96 C34 44 66 16 100 16 C134 16 166 44 160 96 C162 120 158 138 150 146 L50 146 C42 138 38 120 40 96 Z" fill="${fill}"/>`,
      locks: `<path class="lock lock-l" d="M56 76 C46 102 46 128 58 146 C62 146 66 142 66 136 C62 118 64 98 68 84 Z" fill="${fill}"/>
        <path class="lock lock-r" d="M144 76 C154 102 154 128 142 146 C138 146 134 142 134 136 C138 118 136 98 132 84 Z" fill="${fill}"/>`,
      extra: `<path class="clip" d="M136 52 l3 -6 l3 6 l6 1 l-5 4 l2 6 l-6 -3 l-6 3 l2 -6 l-5 -4 Z" fill="${p.accent}" stroke="#fff" stroke-width="1"/>`,
    };
  }
  if (hair === 'sideponytail') {
    return {
      back: `<path class="hair-back" d="M42 96 C36 44 68 16 100 16 C132 16 164 44 158 96 L156 136 L44 136 Z" fill="${fill}"/>
        <g class="tail tail-r"><path d="M146 52 C178 56 190 92 186 128 C184 154 176 174 164 190 C166 166 164 146 160 126 C156 106 150 84 140 66 Z" fill="${fill}"/>
          <path d="M172 82 C180 104 180 132 172 160" fill="none" stroke="${p.light}" stroke-width="2.4" opacity=".55" stroke-linecap="round"/></g>`,
      locks: `<path class="lock lock-l" d="M57 76 C48 104 48 134 58 162 C62 136 64 108 68 84 Z" fill="${fill}"/>
        <path class="lock lock-r" d="M143 78 C149 98 148 116 142 130 C139 114 136 100 133 86 Z" fill="${fill}"/>`,
      extra: `<g class="scrunchie" transform="translate(148 56)"><ellipse rx="9" ry="7" fill="${p.accent}" stroke="#fff" stroke-width="1.2"/>
        <path d="M-6 -3 Q0 -7 6 -3 M-6 3 Q0 7 6 3" stroke="#fff" stroke-width="1.1" fill="none" opacity=".8"/></g>`,
    };
  }
  return {                                     // long (default)
    back: `<path class="hair-back" d="M40 96 C34 42 66 14 100 14 C134 14 166 42 160 96 L170 176 C158 194 138 186 134 160 L66 160 C62 186 42 194 30 176 Z" fill="${fill}"/>
      <path d="M44 120 C42 146 40 164 34 176 M156 120 C158 146 160 164 166 176" fill="none" stroke="${p.light}" stroke-width="2.4" opacity=".5" stroke-linecap="round"/>`,
    locks: `<path class="lock lock-l" d="M57 76 C48 104 48 136 58 166 C62 138 64 108 68 84 Z" fill="${fill}"/>
      <path class="lock lock-r" d="M143 76 C152 104 152 136 142 166 C138 138 136 108 132 84 Z" fill="${fill}"/>`,
    extra: `<path class="clip" d="M136 50 l3 -6 l3 6 l6 1 l-5 4 l2 6 l-6 -3 l-6 3 l2 -6 l-5 -4 Z" fill="${p.accent}" stroke="#fff" stroke-width="1"/>`,
  };
}

/** The anime face as an SVG string. ``style`` is a palette name or {palette, hair}. */
export function animeSVG(style = 'pink', expr = 'calm') {
  const opts = typeof style === 'string' ? { palette: style } : (style || {});
  const paletteName = PALETTES[opts.palette] ? opts.palette : 'pink';
  const hair = HAIRSTYLES[opts.hair] ? opts.hair : 'long';
  const p = PALETTES[paletteName];
  const id = `af${++uid}`;
  const parts = hairParts(hair, id, p);
  expr = ALIASES[expr] || expr;
  const eye = (cx, side) => `
    <g class="eye eye-${side}" transform="translate(${cx} 106)">
      <g class="lid">
        <path d="M-15 -2 C-15 -14 -8 -19 0 -19 C9 -19 15 -13 15 -2 C15 11 8 17 0 17 C-8 17 -15 11 -15 -2 Z" fill="#fff"/>
        <clipPath id="${id}c${side}"><path d="M-15 -2 C-15 -14 -8 -19 0 -19 C9 -19 15 -13 15 -2 C15 11 8 17 0 17 C-8 17 -15 11 -15 -2 Z"/></clipPath>
        <g clip-path="url(#${id}c${side})">
          <g class="iris">
            <ellipse cy="2" rx="11.5" ry="14.5" fill="url(#${id}iris)"/>
            <ellipse cy="-9" rx="11.5" ry="6" fill="#120b24" opacity=".35"/>
            <ellipse cy="3" rx="5.4" ry="7.6" fill="#1b1033" opacity=".85"/>
            <g class="pupil-heart"><path d="M0 9 C-9 2 -8 -6 -3 -6 C-1 -6 0 -4 0 -3 C0 -4 1 -6 3 -6 C8 -6 9 2 0 9 Z" fill="#ff4d8d"/></g>
            <ellipse class="shine" cx="-4.5" cy="-6" rx="4.2" ry="5" fill="#fff"/>
            <circle cx="5" cy="8" r="2.1" fill="#fff" opacity=".9"/>
            <path class="star" d="M5 -9 l1 2.6 l2.6 1 l-2.6 1 l-1 2.6 l-1 -2.6 l-2.6 -1 l2.6 -1 Z" fill="#fff" opacity=".95"/>
          </g>
          <path class="tearline" d="M-13 11 Q0 18 13 11 L13 20 L-13 20 Z" fill="#9fdcff" opacity=".55"/>
        </g>
        <path d="M-16 -5 C-13 -16 -6 -20 1 -20 C9 -20 14 -15 16 -7 C10 -12 -8 -13 -16 -5 Z" fill="${p.accent}" opacity=".18"/>
        <path class="lash" d="M-17 -4 C-14 -17 -6 -21 1 -21 C9 -21 15 -16 17 -6" fill="none" stroke="#2a1530" stroke-width="4" stroke-linecap="round"/>
        <path d="M${side === 'l' ? '-17 -4 L-22 -8 M-14 -11 L-19 -15' : '17 -6 L22 -10 M14 -12 L19 -16'}" stroke="#2a1530" stroke-width="2.4" stroke-linecap="round"/>
        <path d="M-9 15.5 Q0 18.5 9 15.5" fill="none" stroke="#2a1530" stroke-width="1.2" opacity=".5"/>
      </g>
      <path class="arc" d="M-13 4 Q0 -10 13 4" fill="none" stroke="#2a1530" stroke-width="3.6" stroke-linecap="round"/>
      <path class="shut" d="M-14 2 Q0 11 14 2" fill="none" stroke="#2a1530" stroke-width="3.2" stroke-linecap="round"/>
      <g class="swirl" fill="none" stroke="#2a1530" stroke-width="2" stroke-linecap="round">
        <path d="M0 0 m-1 0 a1 1 0 1 1 2 0 a3 3 0 1 1 -6 0 a5 5 0 1 1 10 0 a7 7 0 1 1 -14 0 a9 9 0 1 1 18 0"/>
      </g>
    </g>`;
  return `
<svg class="anime-face" viewBox="0 0 200 200" data-expr="${expr}" data-palette="${paletteName}" data-hair="${hair}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <linearGradient id="${id}iris" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${p.eye}"/><stop offset=".6" stop-color="${p.eye}"/><stop offset="1" stop-color="${p.eye2}"/>
    </linearGradient>
    <linearGradient id="${id}hair" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${p.light}"/><stop offset=".3" stop-color="${p.hair}"/><stop offset="1" stop-color="${p.shade}"/>
    </linearGradient>
    <linearGradient id="${id}skin" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffe9df"/><stop offset="1" stop-color="#ffdcd0"/>
    </linearGradient>
    <radialGradient id="${id}glow"><stop offset="0" stop-color="${p.accent}" stop-opacity=".9"/><stop offset="1" stop-color="${p.accent}" stop-opacity="0"/></radialGradient>
    <radialGradient id="${id}blush"><stop offset="0" stop-color="#ff7aa0" stop-opacity=".75"/><stop offset="1" stop-color="#ff7aa0" stop-opacity="0"/></radialGradient>
  </defs>
  <style>
    .anime-face .arc, .anime-face .shut, .anime-face .swirl, .anime-face .glasses, .anime-face .bubble, .anime-face .bang,
    .anime-face .zz, .anime-face .sweat, .anime-face .tear, .anime-face .tearline, .anime-face .sparkles, .anime-face .hearts,
    .anime-face .hand, .anime-face .puff, .anime-face .pupil-heart, .anime-face .mic-glow,
    .anime-face .m-open, .anime-face .m-o, .anime-face .m-flat, .anime-face .m-wave, .anime-face .m-three, .anime-face .m-cat,
    .anime-face .m-tongue, .anime-face .m-sad, .anime-face .m-grin { display: none }
    .anime-face[data-expr="happy"] .eye .lid, .anime-face[data-expr="wink"] .eye-r .lid { display: none }
    .anime-face[data-expr="happy"] .eye .arc, .anime-face[data-expr="wink"] .eye-r .arc { display: inline }
    .anime-face[data-expr="happy"] .sparkles, .anime-face[data-expr="wink"] .sparkles { display: inline }
    .anime-face[data-expr="happy"] .m-grin { display: inline }
    .anime-face[data-expr="wink"] .m-tongue { display: inline }
    .anime-face[data-expr="love"] .pupil-heart, .anime-face[data-expr="love"] .hearts, .anime-face[data-expr="love"] .m-grin { display: inline }
    .anime-face[data-expr="love"] .iris .shine { opacity: .6 }
    .anime-face[data-expr="asleep"] .lid, .anime-face[data-expr="dizzy"] .lid { display: none }
    .anime-face[data-expr="asleep"] .shut, .anime-face[data-expr="asleep"] .zz { display: inline }
    .anime-face[data-expr="dizzy"] .swirl, .anime-face[data-expr="dizzy"] .sweat, .anime-face[data-expr="dizzy"] .m-wave { display: inline }
    .anime-face[data-expr="reading"] .glasses { display: inline }
    .anime-face[data-expr="thinking"] .bubble, .anime-face[data-expr="thinking"] .hand { display: inline }
    .anime-face[data-expr="surprised"] .bang, .anime-face[data-expr="surprised"] .m-o { display: inline }
    .anime-face[data-expr="listening"] .mic-glow { display: inline }
    .anime-face[data-expr="thinking"] .m-flat, .anime-face[data-expr="reading"] .m-flat, .anime-face[data-expr="focused"] .m-flat { display: inline }
    .anime-face[data-expr="sorry"] .tear, .anime-face[data-expr="sorry"] .tearline, .anime-face[data-expr="sorry"] .m-sad { display: inline }
    .anime-face[data-expr="pout"] .puff, .anime-face[data-expr="pout"] .m-three { display: inline }
    .anime-face[data-expr="smug"] .m-cat { display: inline }
    .anime-face:not([data-expr="calm"]):not([data-expr="listening"]):not([data-expr="asleep"]) .m-smile,
    .anime-face:not([data-expr="calm"]):not([data-expr="listening"]):not([data-expr="asleep"]) .gloss { display: none }
    .anime-face[data-expr="asleep"] { filter: saturate(.5) brightness(.93) }
    .anime-face[data-expr="love"] .blush, .anime-face[data-expr="pout"] .blush, .anime-face[data-expr="smug"] .blush { opacity: 1 }
  </style>
  ${parts.back}
  <g class="body">
    <path d="M36 200 C40 172 66 158 100 158 C134 158 160 172 164 200 Z" fill="${p.coat}"/>
    <path d="M36 200 C40 176 58 164 76 160 L100 186 L124 160 C142 164 160 176 164 200 Z" fill="${p.accent}" opacity=".92"/>
    <path d="M76 160 L100 186 L124 160" fill="none" stroke="#fff" stroke-width="2.6" stroke-linejoin="round"/>
    <path d="M80 164 L100 184 L120 164" fill="none" stroke="#fff" stroke-width="1.2" opacity=".7"/>
    <g class="bow" transform="translate(100 184)">
      <path d="M0 0 C-12 -9 -20 -3 -18 5 C-16 11 -7 7 0 0 C7 7 16 11 18 5 C20 -3 12 -9 0 0 Z" fill="#ff4d8d" stroke="#fff" stroke-width="1.1"/>
      <circle r="3" fill="#fff"/>
    </g>
  </g>
  <g class="head">
    <path d="M90 138 h20 v24 c-6 4 -14 4 -20 0 Z" fill="#f6c8b8"/>
    <path class="face-shape" d="M58 78 C57 116 74 144 100 150 C126 144 143 116 142 78 Z" fill="url(#${id}skin)"/>
    <path d="M63 126 C72 140 86 148 100 150 C114 148 128 140 137 126" fill="none" stroke="#f0b4a2" stroke-width="1.1" opacity=".55"/>
    <g class="blush" opacity=".75">
      <ellipse cx="72" cy="124" rx="11" ry="6" fill="url(#${id}blush)"/>
      <ellipse cx="128" cy="124" rx="11" ry="6" fill="url(#${id}blush)"/>
      <path d="M66 123 l3 -3 M71 123 l3 -3 M76 123 l3 -3 M122 123 l3 -3 M127 123 l3 -3 M132 123 l3 -3" stroke="#ff6f96" stroke-width="1.2" stroke-linecap="round" opacity=".7"/>
    </g>
    <ellipse class="puff" cx="129" cy="126" rx="9" ry="7.5" fill="#ffd6cc" stroke="#f0a493" stroke-width="1"/>
    <g class="brows" fill="none" stroke="${p.shade}" stroke-width="2.4" stroke-linecap="round">
      <path class="brow brow-l" d="M68 84 Q79 79 90 83"/><path class="brow brow-r" d="M110 83 Q121 79 132 84"/>
    </g>
    ${eye(79, 'l')}${eye(121, 'r')}
    <path d="M100 121 l1.2 2.4" stroke="#e39b88" stroke-width="1.5" stroke-linecap="round"/>
    <g class="mouth" transform="translate(100 135)">
      <path class="m-smile" d="M-6 -1 Q0 4.5 6 -1" fill="none" stroke="#c2465a" stroke-width="2.1" stroke-linecap="round"/>
      <path class="gloss" d="M-2 4.5 q2 1 4 0" fill="none" stroke="#fff" stroke-width="1" stroke-linecap="round" opacity=".7"/>
      <g class="m-grin"><path d="M-8 -2 Q0 -1 8 -2 Q7 7 0 8 Q-7 7 -8 -2 Z" fill="#a83249"/><path d="M-4 4.5 Q0 2 4 4.5 Q2 7.4 0 7.4 Q-2 7.4 -4 4.5 Z" fill="#ff8fa3"/></g>
      <g class="m-tongue"><path d="M-7 -1 Q0 4 7 -1" fill="none" stroke="#c2465a" stroke-width="2" stroke-linecap="round"/><path d="M1 1.5 q3 0 3 3.5 q-1.5 2.5 -3 0 Z" fill="#ff7f97"/></g>
      <path class="m-flat" d="M-5 0.5 Q0 1.5 5 0" fill="none" stroke="#c2465a" stroke-width="2.1" stroke-linecap="round"/>
      <ellipse class="m-o" cx="0" cy="1" rx="3.4" ry="4.4" fill="#a83249"/>
      <path class="m-wave" d="M-8 1 q2 -3 4 0 t4 0 t4 0 t4 0" fill="none" stroke="#c2465a" stroke-width="1.9" stroke-linecap="round"/>
      <path class="m-three" d="M-1 -4 q4 1.5 0 3.5 q4 1.5 0 3.5" fill="none" stroke="#c2465a" stroke-width="2" stroke-linecap="round"/>
      <path class="m-cat" d="M-8 -1 q4 4 8 0 q4 4 8 0" fill="none" stroke="#c2465a" stroke-width="2" stroke-linecap="round"/>
      <path class="m-sad" d="M-6 3 Q0 -2.5 6 3" fill="none" stroke="#c2465a" stroke-width="2.1" stroke-linecap="round"/>
      <g class="m-open" opacity="0">
        <path d="M-7 -1 Q0 -2 7 -1 Q6 9 0 10 Q-6 9 -7 -1 Z" fill="#8f2a40"/>
        <path d="M-4 6 Q0 3 4 6 Q2 9.5 0 9.5 Q-2 9.5 -4 6 Z" fill="#ff8a9e"/>
      </g>
    </g>
    <g class="glasses" fill="none" stroke="#3b3f5c" stroke-width="2.2">
      <circle cx="79" cy="106" r="17"/><circle cx="121" cy="106" r="17"/><path d="M96 104 Q100 101 104 104"/>
      <path d="M67 97 L73 101" stroke="#fff" stroke-width="1.5" opacity=".7"/>
    </g>
    <g class="hair-front">
      ${parts.locks}
      <path d="M50 94 C46 50 72 26 100 26 C128 26 154 50 150 94 C148 84 146 79 142 75 Q140 86 134 93 Q133 77 125 66 Q123 81 117 89 Q113 71 105 61 Q103 77 99 87 Q95 72 90 62 Q85 78 80 89 Q77 75 71 66 Q66 81 62 93 Q58 83 54 79 Z" fill="url(#${id}hair)"/>
      <path d="M71 66 Q74 74 74 84 M105 61 Q107 70 106 80 M125 66 Q127 74 126 84" fill="none" stroke="${p.shade}" stroke-width="1.1" opacity=".45" stroke-linecap="round"/>
      <path d="M66 46 Q100 32 134 46" fill="none" stroke="${p.light}" stroke-width="6" stroke-linecap="round" opacity=".7"/>
      <path d="M78 40 q6 -3 12 -2 M110 38 q6 -1 12 2" stroke="#fff" stroke-width="2.2" stroke-linecap="round" opacity=".75"/>
        <path class="ahoge" d="M100 28 C96 12 106 4 114 8 C106 8 102 16 104 28 Z" fill="url(#${id}hair)"/>
      ${parts.extra}
    </g>
    <g class="headset">
      <path d="M48 94 C44 30 156 30 152 94" fill="none" stroke="#454a6e" stroke-width="5" stroke-linecap="round"/>
      <rect x="39" y="82" width="15" height="28" rx="7" fill="#454a6e"/><rect x="42" y="86" width="9" height="20" rx="4.5" fill="${p.accent}"/>
      <rect x="146" y="82" width="15" height="28" rx="7" fill="#454a6e"/><rect x="149" y="86" width="9" height="20" rx="4.5" fill="${p.accent}"/>
      <path d="M46 108 Q53 138 79 143" fill="none" stroke="#454a6e" stroke-width="2.6" stroke-linecap="round"/>
      <circle class="mic-glow" cx="82" cy="143" r="10" fill="url(#${id}glow)"/>
      <path d="M82 148 C76 144 76 139 79 138 C80.5 137.6 82 138.6 82 140 C82 138.6 83.5 137.6 85 138 C88 139 88 144 82 148 Z" fill="${p.accent}" stroke="#454a6e" stroke-width="1.4"/>
    </g>
    <g class="hand">
      <path d="M118 150 C116 140 122 134 128 136 C132 132 138 136 136 142 C140 144 138 152 132 154 C126 158 120 156 118 150 Z" fill="#ffe2d6" stroke="#f0b4a2" stroke-width="1"/>
      <path d="M124 136 C124 128 128 124 131 127 C132 132 130 136 129 138" fill="#ffe2d6" stroke="#f0b4a2" stroke-width="1"/>
    </g>
  </g>
  <g class="sparkles" fill="#fff" stroke="${p.accent}" stroke-width=".8">
    <path d="M30 60 l2.5 6 l6 2.5 l-6 2.5 l-2.5 6 l-2.5 -6 l-6 -2.5 l6 -2.5 Z"/>
    <path d="M168 50 l2 5 l5 2 l-5 2 l-2 5 l-2 -5 l-5 -2 l5 -2 Z"/>
    <path d="M160 128 l1.6 4 l4 1.6 l-4 1.6 l-1.6 4 l-1.6 -4 l-4 -1.6 l4 -1.6 Z"/>
  </g>
  <g class="hearts" fill="#ff4d8d">
    <path d="M30 60 C22 52 24 44 30 46 C32 46.5 33 48 33 49 C33 48 34 46.5 36 46 C42 44 44 52 36 60 L33 63 Z"/>
    <path d="M166 44 C160 38 162 32 166 33.5 C167.5 34 168 35 168 36 C168 35 168.5 34 170 33.5 C174 32 176 38 170 44 L168 46 Z"/>
  </g>
  <g class="bubble" transform="translate(160 40)">
    <ellipse rx="22" ry="14" fill="#fff" stroke="#c9cbe0" stroke-width="1.5"/>
    <circle cx="-26" cy="18" r="4" fill="#fff" stroke="#c9cbe0" stroke-width="1.2"/>
    <circle class="dot" cx="-9" cy="0" r="3" fill="#8b7cf6"/><circle class="dot" cx="0" cy="0" r="3" fill="#8b7cf6"/><circle class="dot" cx="9" cy="0" r="3" fill="#8b7cf6"/>
  </g>
  <g class="bang" transform="translate(164 38)">
    <circle r="15" fill="#ffb02e"/><rect x="-2.4" y="-9" width="4.8" height="11" rx="2.4" fill="#fff"/><circle cy="6.5" r="2.6" fill="#fff"/>
  </g>
  <g class="zz" fill="#8b7cf6" font-family="Inter, Arial, sans-serif" font-weight="800">
    <text x="146" y="56" font-size="18">z</text><text x="160" y="40" font-size="13">z</text>
  </g>
  <path class="sweat" d="M148 72 Q154 82 148 86 Q142 82 148 72 Z" fill="#7dd3fc" stroke="#fff" stroke-width="1"/>
  <path class="tear" d="M66 118 Q70 128 66 133 Q62 128 66 118 Z" fill="#8fd3ff" stroke="#fff" stroke-width="1"/>
</svg>`;
}

// ------------------------------------------------------- anime renderer
/** A small deterministic hash, so "random" blinks render the same frame by frame. */
function noise(n) {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

/** How open the eyes are at time ``t`` (1 open, 0 shut): blinks at uneven 2.5–5 s gaps, sometimes twice. */
export function blinkAt(t) {
  const period = 3600;
  const k = Math.floor(t / period);
  const local = t - k * period - noise(k) * 1600;
  const dip = at => (at >= 0 && at < 150 ? Math.abs(at - 75) / 75 : 1);
  let open = dip(local);
  if (noise(k + 0.5) < 0.22) open = Math.min(open, dip(local - 260));
  return open;
}

/** Pose the anime SVG for time ``t`` (ms). Pure: frames can be rendered from it. */
export function poseAnime(svg, t, mouth = 0, look = null) {
  const expr = svg.dataset.expr;
  const set = (sel, value) => svg.querySelectorAll(sel).forEach(node => node.setAttribute('transform', value));
  const open = Math.max(0, Math.min(1, mouth));
  // Breathing, a little sway, and a lively bob while talking.
  const breath = Math.sin(t / 900) * 1.2;
  const talk = open > 0.05 ? Math.sin(t / 110) * 0.8 * open : 0;
  const tilt = { listening: 5, asleep: -7, thinking: -4, sorry: -3, smug: 3, love: 4, pout: -2 }[expr] ?? Math.sin(t / 2300) * 1.4;
  set('.head', `translate(0 ${(breath + talk).toFixed(2)}) rotate(${(tilt + talk).toFixed(2)} 100 150)`);
  set('.body', `translate(0 ${(breath * 0.4).toFixed(2)})`);
  set('.lock-l', `rotate(${(Math.sin(t / 700) * 1.6).toFixed(2)} 62 78)`);
  set('.lock-r', `rotate(${(-Math.sin(t / 760) * 1.6).toFixed(2)} 138 78)`);
  set('.tail-l', `rotate(${(Math.sin(t / 820) * 2.2).toFixed(2)} 52 64)`);
  set('.tail-r', `rotate(${(-Math.sin(t / 860) * 2.2).toFixed(2)} 148 60)`);
  // The ahoge springs with breathing and speech.
  set('.ahoge', `rotate(${(Math.sin(t / 380) * 4 + open * 8 * Math.sin(t / 90)).toFixed(2)} 102 28)`);
  // Eyes: blinks, and how wide each expression holds them.
  const blink = expr === 'surprised' ? 1 : blinkAt(t);
  const wide = { focused: 0.8, surprised: 1.1, smug: 0.5, sorry: 0.9, pout: 0.85, listening: 1.04 }[expr] || 1;
  svg.querySelectorAll('.lid').forEach(lid => lid.setAttribute('transform', `scale(1 ${Math.max(0.08, blink * wide).toFixed(3)})`));
  const gaze = look || ({ thinking: [3, -5], reading: [0, 3], focused: [-1, 1.5], smug: [3, 0], pout: [-3, 1], sorry: [0, 2] }[expr])
    || [Math.sin(t / 3100) * 1.3, 0];
  set('.iris', `translate(${gaze[0].toFixed(2)} ${gaze[1].toFixed(2)})`);
  const brows = {
    surprised: ['translate(0 -5)', 'translate(0 -5)'], thinking: ['translate(0 -2)', 'translate(0 -3)'],
    dizzy: ['translate(0 2)', 'translate(0 2)'], focused: ['rotate(8 90 83)', 'rotate(-8 110 83)'],
    sorry: ['rotate(-12 90 83)', 'rotate(12 110 83)'], pout: ['rotate(10 90 83)', 'rotate(-10 110 83)'],
    smug: ['', 'translate(0 -3) rotate(-6 121 82)'], love: ['translate(0 -2)', 'translate(0 -2)'],
  }[expr] || ['', ''];
  set('.brow-l', brows[0]);
  set('.brow-r', brows[1]);
  // The mouth: its expression's shape, or open by the speech level; she blushes as she talks.
  const opened = svg.querySelector('.m-open');
  if (opened) {
    opened.setAttribute('opacity', open > 0.05 ? '1' : '0');
    opened.setAttribute('transform', `scale(${(0.8 + open * 0.35).toFixed(3)} ${(0.25 + open * 0.95).toFixed(3)})`);
    svg.querySelectorAll('.mouth > :not(.m-open)').forEach(node => { node.style.opacity = open > 0.05 ? '0' : ''; });
  }
  const blush = svg.querySelector('.blush');
  if (blush && !['love', 'pout', 'smug'].includes(expr)) blush.setAttribute('opacity', (0.7 + open * 0.3).toFixed(2));
  // Bubbles, sparkles, hearts and z's.
  svg.querySelectorAll('.bubble .dot').forEach((dot, i) => dot.setAttribute('opacity', (0.35 + 0.65 * ((Math.floor(t / 300) + 3 - i) % 3 === 0 ? 1 : 0)).toFixed(2)));
  set('.bang', `translate(164 ${(38 + Math.abs(Math.sin(t / 180)) * -4).toFixed(2)})`);
  set('.sparkles', `scale(${(1 + Math.sin(t / 220) * 0.06).toFixed(3)})`);
  svg.querySelectorAll('.sparkles path').forEach((star, i) => star.setAttribute('opacity', (0.55 + 0.45 * Math.abs(Math.sin(t / 260 + i))).toFixed(2)));
  set('.hearts', `translate(0 ${(-Math.abs(Math.sin(t / 400)) * 4).toFixed(2)})`);
  const zt = (t % 2400) / 2400;
  set('.zz', `translate(${(zt * 6).toFixed(2)} ${(-zt * 10).toFixed(2)})`);
  set('.swirl', `rotate(${((t / 4) % 360).toFixed(1)})`);
  set('.tear', `translate(0 ${((t % 1600) / 1600 * 6).toFixed(2)})`);
}

function animeFace(host, style = {}) {
  host.innerHTML = animeSVG(style);
  const svg = host.querySelector('svg');
  let mouth = 0, target = 0, frame = 0, alive = true, look = null, base = 'calm', timer = 0;
  const start = performance.now();
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const onMove = event => {
    const box = svg.getBoundingClientRect();
    if (!box.width) return;
    const dx = (event.clientX - (box.left + box.width / 2)) / innerWidth;
    const dy = (event.clientY - (box.top + box.height * 0.5)) / innerHeight;
    look = [Math.max(-3, Math.min(3, dx * 8)), Math.max(-3, Math.min(3, dy * 8))];
    clearTimeout(onMove.timer);
    onMove.timer = setTimeout(() => { look = null; }, 1500);
  };
  addEventListener('pointermove', onMove);
  const tick = now => {
    if (!alive) return;
    mouth += (target - mouth) * 0.45;
    poseAnime(svg, reduce ? 0 : now - start, mouth, look);
    frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
  return {
    kind: 'anime',
    setState(state) {
      base = EXPRESSION[state] || ALIASES[state] || (EXPRESSIONS.includes(state) ? state : 'calm');
      if (!timer) svg.dataset.expr = base;
    },
    setMouth(level) { target = typeof level === 'number' ? level : (level?.level || 0); },
    react(emotion, ms = 2500) {
      const expr = ALIASES[emotion] || emotion;
      if (!EXPRESSIONS.includes(expr)) return;
      clearTimeout(timer);
      svg.dataset.expr = expr;
      timer = setTimeout(() => { timer = 0; svg.dataset.expr = base; }, ms);
    },
    get expression() { return svg.dataset.expr; },
    destroy() { alive = false; clearTimeout(timer); cancelAnimationFrame(frame); removeEventListener('pointermove', onMove); },
  };
}

// --------------------------------------------------------- brain (pixel)
function brainFace(host, style = {}, size = 120) {
  host.innerHTML = mascot(size);
  return {
    kind: 'brain',
    setState(state) { setBrainState(host, state); },
    setMouth() {},
    react() {},
    destroy() {},
  };
}

// --------------------------------------------------------- Live2D / VRM
// What Cerebro wants a model to show, and the names models usually give it.
// ``matchExpressions`` picks, for each, the first name the model actually has.
export const MODEL_SLOTS = ['calm', 'happy', 'wink', 'love', 'thinking', 'focused', 'listening',
  'surprised', 'sorry', 'pout', 'smug', 'dizzy', 'asleep'];
const SLOT_NAMES = {
  calm: ['relaxed', 'neutral', 'calm', 'smile', 'idle'],
  happy: ['happy', 'joy', 'fun', 'smile', 'f01', 'exp_01'],
  wink: ['blinkLeft', 'winkl', 'wink', 'blink_l', 'happy'],
  love: ['love', 'heart', 'shy', 'blush', 'relaxed', 'happy'],
  thinking: ['think', 'thinking', 'hmm', 'ponder', 'neutral'],
  focused: ['serious', 'focus', 'determined', 'neutral'],
  listening: ['listen', 'curious', 'neutral', 'relaxed'],
  surprised: ['surprised', 'surprise', 'shock', 'oh', 'f03'],
  sorry: ['sad', 'sorrow', 'cry', 'sorry', 'f04'],
  pout: ['angry', 'pout', 'puff', 'sulk', 'f02'],
  smug: ['smug', 'doya', 'relaxed', 'smirk', 'happy'],
  dizzy: ['dizzy', 'confused', 'sad', 'surprised'],
  asleep: ['sleep', 'blink', 'closed'],
};
const STATE_SLOT = {
  idle: 'calm', thinking: 'thinking', searching: 'focused', browsing: 'focused', writing: 'focused',
  syncing: 'focused', listening: 'listening', awaiting_approval: 'surprised', error: 'dizzy',
  offline: 'asleep', speaking: 'calm',
};

/** For each of Cerebro's states/reactions, the model expression that fits best (or null). */
export function matchExpressions(available = []) {
  const names = available.map(String);
  const lower = names.map(n => n.toLowerCase());
  const find = wanted => {
    for (const want of wanted) {
      const w = want.toLowerCase();
      let i = lower.indexOf(w);
      if (i < 0) i = lower.findIndex(n => n.includes(w));
      if (i >= 0) return names[i];
    }
    return null;
  };
  return Object.fromEntries(MODEL_SLOTS.map(slot => [slot, find(SLOT_NAMES[slot])]));
}

/** Expressions a Live2D model declares (model3.json): its expressions and motion groups. */
export function live2dCatalog(settings = {}) {
  const refs = settings.FileReferences || settings.fileReferences || settings;
  const expressions = (refs.Expressions || refs.expressions || []).map(e => e.Name || e.name).filter(Boolean);
  const motions = Object.keys(refs.Motions || refs.motions || {});
  return { expressions, motions };
}

/** The user's mapping on top of the guesses: {slot: {name, weight}}. */
function mapping(guess, chosen = {}) {
  return Object.fromEntries(MODEL_SLOTS.map(slot => {
    const pick = chosen[slot];
    if (pick && pick.name === '') return [slot, null];                 // the user chose "nothing"
    if (pick && pick.name) return [slot, { name: pick.name, weight: Math.max(0, Math.min(1, Number(pick.weight ?? 1))) }];
    return [slot, guess[slot] ? { name: guess[slot], weight: slot === 'calm' ? 0.35 : 1 } : null];
  }));
}

const loaded = {};
function loadScript(src) {
  if (!loaded[src]) {
    loaded[src] = new Promise((resolve, reject) => {
      const tag = document.createElement('script');
      tag.src = src;
      tag.onload = resolve;
      tag.onerror = () => reject(new Error(`Couldn't load ${src}`));
      document.head.append(tag);
    });
  }
  return loaded[src];
}

function fallback(host, style, problem) {
  const face = animeFace(host, { palette: style.palette || 'silver', hair: style.hair });
  host.title = problem;
  return face;
}

/** Where the cursor is, -1..1 on each axis from the host's centre (decays to 0 when still). */
function cursorTracker(host) {
  const state = { x: 0, y: 0, tx: 0, ty: 0, timer: 0 };
  const onMove = event => {
    const box = host.getBoundingClientRect();
    if (!box.width) return;
    state.tx = Math.max(-1, Math.min(1, (event.clientX - (box.left + box.width / 2)) / (innerWidth / 2)));
    state.ty = Math.max(-1, Math.min(1, (event.clientY - (box.top + box.height / 2)) / (innerHeight / 2)));
    clearTimeout(state.timer);
    state.timer = setTimeout(() => { state.tx = 0; state.ty = 0; }, 2500);
  };
  addEventListener('pointermove', onMove);
  state.step = () => { state.x += (state.tx - state.x) * 0.08; state.y += (state.ty - state.y) * 0.08; };
  state.stop = () => { removeEventListener('pointermove', onMove); clearTimeout(state.timer); };
  return state;
}

/** A reaction lasts ``ms``, then the state's look returns. */
function reactor() {
  const r = { emotion: null, until: 0 };
  r.set = (emotion, ms = 2500) => { r.emotion = emotion; r.until = performance.now() + ms; };
  r.current = () => (r.emotion && performance.now() < r.until ? r.emotion : null);
  return r;
}

function live2dFace(host, style = {}) {
  // Rendered into a canvas once the libraries and the model have loaded; the
  // anime face stands in meanwhile (and if anything is missing).
  const stand = animeFace(host, { palette: style.palette || 'silver', hair: style.hair });
  const framing = { zoom: 1, y: 0, mirror: false, ...(style.framing || {}) };
  let model = null, app = null, alive = true, state = 'idle', catalog = { expressions: [], motions: [] };
  let mouth = 0, bands = null, map = {}, shown = null;
  const look = cursorTracker(host);
  const reaction = reactor();
  const ready = (async () => {
    try {
      const info = await (await fetch('/api/faces/live2d/runtime')).json();
      if (!info.installed) throw new Error('The Live2D runtime (Cubism Core) is not installed — choose this face again in the agent editor to set it up.');
      await loadScript('/api/faces/live2d/runtime.js');
      await loadScript('/static/vendor/pixi/pixi.min.js');
      await loadScript('/static/vendor/live2d/cubism4.min.js');
      if (!alive) return;
      const canvas = document.createElement('canvas');
      canvas.className = 'face-canvas';
      const size = Math.max(96, host.clientWidth || 160);
      app = new window.PIXI.Application({ view: canvas, width: size, height: size, backgroundAlpha: 0, autoStart: true });
      model = await window.PIXI.live2d.Live2DModel.from(`/faces/${encodeURIComponent(style.model)}/${style.entry || 'model.model3.json'}`, { autoInteract: false });
      if (!alive) return;
      catalog = live2dCatalog(model.internalModel?.settings?.json || {});
      map = mapping(matchExpressions([...catalog.expressions, ...catalog.motions]), style.expressions);
      const scale = Math.min(size / model.width, size / model.height) * 1.6 * framing.zoom;
      model.scale.set(framing.mirror ? -scale : scale, scale);
      model.anchor?.set?.(0.5, 0.25);
      model.x = size / 2;
      model.y = size * (0.1 + framing.y);
      app.stage.addChild(model);
      // Tapping the model plays its own Tap motion.
      model.interactive = true;
      model.on?.('hit', areas => { try { model.motion(`Tap${areas[0] || ''}`) || model.motion('Tap'); } catch { /* none */ } });
      stand.destroy();
      host.innerHTML = '';
      host.append(canvas);
      const has = new Set();
      const core = model.internalModel?.coreModel;
      try {
        const ids = core?._model?.parameters?.ids || core?.getModel?.()?.parameters?.ids || [];
        ids.forEach(id => has.add(id));
      } catch { /* older runtime: set what we can */ }
      const setParam = (id, value) => { if (!has.size || has.has(id)) core?.setParameterValueById?.(id, value); };
      const start = performance.now();
      app.ticker.add(() => {
        const t = performance.now() - start;
        look.step();
        const slot = reaction.current() || STATE_SLOT[state] || 'calm';
        if (slot !== shown) { shown = slot; play(slot); }
        const blink = state === 'offline' ? 0 : blinkAt(t);
        setParam('ParamEyeLOpen', slot === 'wink' ? blink : blink);
        setParam('ParamEyeROpen', slot === 'wink' ? 0 : blink);
        setParam('ParamEyeBallX', look.x);
        setParam('ParamEyeBallY', -look.y);
        setParam('ParamAngleX', look.x * 22);
        setParam('ParamAngleY', -look.y * 18 + (slot === 'thinking' ? 8 : 0));
        setParam('ParamAngleZ', slot === 'thinking' ? -8 : slot === 'listening' ? 6 : Math.sin(t / 2300) * 3);
        setParam('ParamBodyAngleX', look.x * 6);
        setParam('ParamBreath', (Math.sin(t / 900) + 1) / 2);
        setParam('ParamMouthOpenY', mouth);
        if (bands) setParam('ParamMouthForm', Math.max(-1, Math.min(1, (bands[3] + bands[4]) - (bands[0] + bands[1]) * 0.6)));
        setParam('ParamCheek', ['love', 'happy', 'smug'].includes(slot) ? 1 : 0);
      });
    } catch (error) {
      host.title = error.message;
      console.warn('Live2D face:', error);
    }
  })();
  const play = slot => {
    if (!model) return;
    const pick = map[slot];
    if (!pick) return;
    try {
      if (catalog.expressions.includes(pick.name)) model.expression(pick.name);
      else if (catalog.motions.includes(pick.name)) model.motion(pick.name);
    } catch { /* models without it */ }
  };
  return {
    kind: 'live2d',
    ready,
    setState(next) { state = next; stand.setState(next); },
    setMouth(level, more) { mouth = typeof level === 'number' ? level : (level?.level || 0); bands = more || level?.bands || null; stand.setMouth(mouth); },
    react(emotion, ms) { reaction.set(emotion, ms); stand.react(emotion, ms); },
    listExpressions() { return [...catalog.expressions, ...catalog.motions]; },
    guesses() { return matchExpressions([...catalog.expressions, ...catalog.motions]); },
    preview(name) { try { catalog.expressions.includes(name) ? model?.expression(name) : model?.motion(name); } catch { /* none */ } },
    destroy() { alive = false; look.stop(); stand.destroy(); app?.destroy(true); },
  };
}

const VOWELS = ['aa', 'ih', 'ou', 'ee', 'oh'];
/** Speech bands (low→high energy, 0..1) → vowel weights for VRM's aa/ih/ou/ee/oh. */
export function vowelsFrom(level, bands) {
  const out = Object.fromEntries(VOWELS.map(v => [v, 0]));
  if (!level || level < 0.03) return out;
  if (!bands || bands.length < 5 || bands.every(b => !b)) { out.aa = level; return out; }
  const [b0, b1, b2, b3, b4] = bands;
  const total = b0 + b1 + b2 + b3 + b4 || 1;
  const share = { ou: b0 / total, oh: b1 / total, aa: b2 / total, ee: b3 / total, ih: b4 / total };
  const top = Object.entries(share).sort((a, b) => b[1] - a[1]);
  // Mostly the leading vowel, a little of the next, and "aa" carries unclear sounds.
  out[top[0][0]] = level * 0.8;
  out[top[1][0]] += level * 0.25;
  out.aa = Math.max(out.aa, level * (top[0][1] < 0.3 ? 0.6 : 0.2));
  return out;
}

/** The buddy's strips: name → which expression slot, how many frames, how fast. */
export const STRIP_PLAN = {
  calm: { slot: 'calm', frames: 12, ms: 150, start: 3600 },
  thinking: { slot: 'thinking', frames: 8, ms: 150, start: 0 },
  reading: { slot: 'focused', frames: 8, ms: 160, start: 3600 },
  focused: { slot: 'focused', frames: 8, ms: 140, start: 3600 },
  listening: { slot: 'listening', frames: 8, ms: 150, start: 3600 },
  surprised: { slot: 'surprised', frames: 8, ms: 95, start: 0 },
  dizzy: { slot: 'dizzy', frames: 8, ms: 90, start: 0 },
  asleep: { slot: 'asleep', frames: 12, ms: 220, start: 0 },
  happy: { slot: 'happy', frames: 8, ms: 120, start: 0 },
  sorry: { slot: 'sorry', frames: 8, ms: 140, start: 0 },
  talk: { slot: 'calm', frames: 5, ms: 80, start: 1200, levels: [0, 0.25, 0.5, 0.75, 1] },
};

function vrmFace(host, style = {}) {
  const stand = animeFace(host, { palette: style.palette || 'silver', hair: style.hair });
  const framing = { zoom: 1, y: 0, ...(style.framing || {}) };
  let alive = true, mouth = 0, bands = null, state = 'idle', renderer = null, frame = 0, names = [], map = {};
  let playGesture = () => {}, previewName = null, paused = false, captureImpl = null;
  const look = cursorTracker(host);
  const reaction = reactor();
  const ready = (async () => {
    try {
      const THREE = await import('/static/vendor/three/three.module.js');
      const { GLTFLoader } = await import('/static/vendor/three/addons/loaders/GLTFLoader.js');
      const { VRMLoaderPlugin, VRMUtils } = await import('/static/vendor/three-vrm/three-vrm.module.min.js');
      if (!alive) return;
      const size = Math.max(96, host.clientWidth || 160);
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
      renderer.setPixelRatio(Math.min(2, devicePixelRatio || 1));
      renderer.setSize(size, size);
      renderer.domElement.className = 'face-canvas';
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(22, 1, 0.1, 20);
      scene.add(new THREE.DirectionalLight(0xffffff, 2.2).translateX(1).translateY(1).translateZ(1));
      scene.add(new THREE.AmbientLight(0xffffff, 0.9));
      const loader = new GLTFLoader();
      loader.register(parser => new VRMLoaderPlugin(parser));
      const base = `/faces/${encodeURIComponent(style.model)}/`;
      const gltf = await loader.loadAsync(`${base}${style.entry || 'model.vrm'}`);
      const vrm = gltf.userData.vrm;
      if (!alive || !vrm) throw new Error('That file has no VRM model in it.');
      VRMUtils.rotateVRM0?.(vrm);
      scene.add(vrm.scene);
      const head = vrm.humanoid?.getNormalizedBoneNode('head');
      const neck = vrm.humanoid?.getNormalizedBoneNode('neck');
      const spine = vrm.humanoid?.getNormalizedBoneNode('spine');
      const chest = vrm.humanoid?.getNormalizedBoneNode('chest');
      const headPos = new THREE.Vector3();
      head?.getWorldPosition(headPos);
      const eyeY = (headPos.y || 1.4) + Number(framing.y || 0) * 0.4;
      camera.position.set(0, eyeY, 0.9 / Math.max(0.3, Number(framing.zoom) || 1));
      camera.lookAt(0, eyeY - 0.02, 0);
      const target = new THREE.Object3D();
      scene.add(target);
      if (vrm.lookAt) vrm.lookAt.target = target;
      const expressions = vrm.expressionManager;
      names = (expressions?.expressions || []).map(e => e.expressionName).filter(Boolean);
      map = mapping(matchExpressions(names.filter(n => !VOWELS.includes(n) && !/^(blink|look)/i.test(n) || n === 'blinkLeft')), style.expressions);
      // Gestures (.vrma), played on events and cross-faded back to idle.
      const mixer = new THREE.AnimationMixer(vrm.scene);
      const clips = {};
      const gestures = style.gestures || {};
      if (Object.keys(gestures).length) {
        try {
          const { VRMAnimationLoaderPlugin, createVRMAnimationClip } = await import('/static/vendor/three-vrm-animation/three-vrm-animation.module.min.js');
          const animLoader = new GLTFLoader();
          animLoader.register(parser => new VRMAnimationLoaderPlugin(parser));
          for (const [event, file] of Object.entries(gestures)) {
            if (!file) continue;
            const anim = await animLoader.loadAsync(`${base}${file}`);
            const vrmAnim = anim.userData.vrmAnimations?.[0];
            if (vrmAnim) clips[event] = createVRMAnimationClip(vrmAnim, vrm);
          }
        } catch (error) { console.warn('VRM gestures:', error); }
      }
      let current = null;
      playGesture = event => {
        const clip = clips[event];
        if (!clip) return false;
        const action = mixer.clipAction(clip);
        action.reset().setLoop(THREE.LoopOnce, 1);
        action.clampWhenFinished = false;
        if (current && current !== action) action.crossFadeFrom(current, 0.3, false);
        action.fadeIn(0.25).play();
        current = action;
        return true;
      };
      mixer.addEventListener('finished', e => { e.action.fadeOut(0.4); current = null; });
      if (clips.greeting) setTimeout(() => playGesture('greeting'), 600);
      stand.destroy();
      host.innerHTML = '';
      host.append(renderer.domElement);
      const clock = new THREE.Clock();
      const weights = {};
      let idleAt = performance.now();
      const ease = (name, value) => {
        if (!name || !expressions) return;
        const now = weights[name] ?? 0;
        weights[name] = now + (value - now) * 0.18;                 // ~200 ms to settle
        expressions.setValue(name, weights[name]);
      };
      // Strips for the desktop buddy: the same model, expressions and poses,
      // drawn frame by frame at fixed moments so the result is reproducible.
      captureImpl = async ({ size = 128, strips = STRIP_PLAN } = {}) => {
        paused = true;
        const oldSize = new THREE.Vector2();
        renderer.getSize(oldSize);
        const oldRatio = renderer.getPixelRatio();
        renderer.setPixelRatio(1);
        renderer.setSize(size, size);
        const out = { manifest: { frame: { width: size, height: size }, states: {} }, strips: {} };
        try {
          for (const [name, plan] of Object.entries(strips)) {
            const sheet = document.createElement('canvas');
            sheet.width = size * plan.frames; sheet.height = size;
            const ctx = sheet.getContext('2d');
            for (let i = 0; i < plan.frames; i++) {
              const t = (plan.start + i * plan.ms) / 1000;
              const level = plan.levels ? plan.levels[i] : 0;
              const active = map[plan.slot];
              const mapped = new Set(Object.values(map).filter(Boolean).map(m => m.name));
              mapped.forEach(n => { weights[n] = active && active.name === n ? active.weight : 0; expressions?.setValue(n, weights[n]); });
              const open = plan.slot === 'asleep' ? 0 : blinkAt(t * 1000);
              if (!mapped.has('blink')) expressions?.setValue('blink', 1 - open);
              const vowels = vowelsFrom(level, null);
              VOWELS.forEach(v => expressions?.setValue(v, vowels[v]));
              if (spine) spine.rotation.x = Math.sin(t * 1.1) * 0.015;
              if (chest) chest.rotation.x = Math.sin(t * 1.1 + 0.6) * 0.02;
              if (neck) neck.rotation.y = 0;
              if (head) {
                head.rotation.y = Math.sin(t * 0.6) * 0.05;
                head.rotation.x = (plan.slot === 'thinking' ? -0.12 : plan.slot === 'asleep' ? 0.18 : 0)
                  + (plan.slot === 'happy' ? Math.sin(t * 9) * 0.05 : 0) + Math.sin(t * 0.7) * 0.02;
                head.rotation.z = plan.slot === 'thinking' ? 0.12 : plan.slot === 'listening' ? -0.1
                  : plan.slot === 'dizzy' ? Math.sin(t * 6) * 0.12 : Math.sin(t * 0.5) * 0.02;
              }
              target.position.set(Math.sin(t * 0.4) * 0.05, eyeY, 2);
              vrm.update(1 / 30);
              renderer.render(scene, camera);
              ctx.drawImage(renderer.domElement, i * size, 0, size, size);
            }
            out.strips[name] = await new Promise(resolve => sheet.toBlob(resolve, 'image/png'));
            out.manifest.states[name] = { frames: plan.frames, ms: plan.ms, file: `${name}.png`,
                                          ...(plan.levels ? { levels: plan.levels } : {}) };
          }
        } finally {
          renderer.setPixelRatio(oldRatio);
          renderer.setSize(oldSize.x, oldSize.y);
          paused = false;
        }
        return out;
      };
      const tick = () => {
        if (!alive) return;
        if (paused) { frame = requestAnimationFrame(tick); return; }
        const dt = clock.getDelta(), t = clock.elapsedTime;
        look.step();
        const slot = reaction.current() || STATE_SLOT[state] || 'calm';
        // The mapped expression for now; every other mapped one eases out.
        const active = previewName ? { name: previewName, weight: 1 } : map[slot];
        const mapped = new Set(Object.values(map).filter(Boolean).map(m => m.name));
        if (previewName) mapped.add(previewName);
        mapped.forEach(name => ease(name, active && active.name === name ? active.weight : 0));
        const blink = state === 'offline' ? 0 : blinkAt(t * 1000);
        if (!mapped.has('blink')) expressions?.setValue('blink', 1 - blink);
        if (slot === 'wink' && !(active?.name || '').toLowerCase().includes('blink')) expressions?.setValue('blinkLeft', 1);
        const vowels = vowelsFrom(mouth, bands);
        VOWELS.forEach(v => expressions?.setValue(v, vowels[v]));
        // Life: breathing, gaze and head following the cursor, a nod when happy, a tilt while thinking.
        if (spine) spine.rotation.x = Math.sin(t * 1.1) * 0.015;
        if (chest) chest.rotation.x = Math.sin(t * 1.1 + 0.6) * 0.02;
        const nod = slot === 'happy' && reaction.current() ? Math.sin(t * 9) * 0.06 : 0;
        if (neck) neck.rotation.y = look.x * 0.25;
        if (head) {
          head.rotation.y = look.x * 0.2;
          head.rotation.x = (slot === 'thinking' ? -0.12 : look.y * 0.15) + nod + Math.sin(t * 0.7) * 0.02;
          head.rotation.z = slot === 'thinking' ? 0.12 : slot === 'listening' ? -0.1 : Math.sin(t * 0.5) * 0.02;
        }
        target.position.set(look.x * 1.2 + Math.sin(t * 0.4) * 0.05, eyeY + look.y * -0.6, 2);
        // An idle gesture every few minutes.
        if (clips.idle && performance.now() - idleAt > 180000 && state === 'idle') { idleAt = performance.now(); playGesture('idle'); }
        mixer.update(dt);
        vrm.update(dt);
        renderer.render(scene, camera);
        frame = requestAnimationFrame(tick);
      };
      tick();
    } catch (error) {
      host.title = error.message;
      console.warn('VRM face:', error);
    }
  })();
  const GESTURE_FOR = { happy: 'done', wink: 'done', smug: 'approved', love: 'done', surprised: 'attention' };
  return {
    kind: 'vrm',
    ready,
    setState(next) {
      if (next === 'thinking' && state !== 'thinking') playGesture('thinking');
      state = next; stand.setState(next);
    },
    setMouth(level, more) { mouth = typeof level === 'number' ? level : (level?.level || 0); bands = more || level?.bands || null; stand.setMouth(mouth); },
    react(emotion, ms) { reaction.set(emotion, ms); stand.react(emotion, ms); playGesture(GESTURE_FOR[emotion] || emotion); },
    listExpressions() { return names; },
    guesses() { return matchExpressions(names); },
    preview(name, ms = 1800) { previewName = name; setTimeout(() => { if (previewName === name) previewName = null; }, ms); },
    gesture(event) { return playGesture(event); },
    /** Expression strips for the desktop buddy: {manifest, strips: {state: Blob}}. */
    async capture(options) { await ready; if (!captureImpl) throw new Error('The model didn\'t load.'); return captureImpl(options); },
    destroy() { alive = false; look.stop(); cancelAnimationFrame(frame); stand.destroy(); renderer?.dispose(); },
  };
}

// ------------------------------------------------------------- public API
/** Put an agent's face in ``host``; returns {setState, setMouth, destroy}. */
export function createFace(host, spec = {}, size = 120) {
  host.classList.add('face-host');
  const style = spec.style || spec.face_style || {};
  const kind = spec.face || 'brain';
  if (kind === 'anime') return animeFace(host, style);
  if (kind === 'live2d') return style.model ? live2dFace(host, style) : fallback(host, style, 'No Live2D model chosen yet');
  if (kind === 'vrm') return style.model ? vrmFace(host, style) : fallback(host, style, 'No VRM model chosen yet');
  return brainFace(host, style, size);
}

/** A still avatar (lists, message bubbles): HTML for the agent's face. */
export function faceAvatar(agent, size = 34) {
  const face = agent?.face || 'brain';
  if (face === 'brain') return mascot(size).replace('class="mascot"', 'class="mascot still"');
  const style = { palette: agent?.face_style?.palette || (face === 'anime' ? 'pink' : 'silver'),
                  hair: agent?.face_style?.hair };
  // Framed on the face rather than the whole bust.
  const svg = animeSVG(style).replace('viewBox="0 0 200 200"', 'viewBox="38 34 124 124"');
  return `<span class="face-avatar" style="width:${size}px;height:${size}px">${svg}</span>`;
}

/**
 * Lip sync for an <audio> element: calls ``onLevel(level 0..1, bands)`` about 30
 * times a second while it plays, then ``onLevel(0, null)`` when it stops.
 * ``bands`` is the speech energy in five bands (low → high, 0..1), which VRM and
 * Live2D faces turn into vowel shapes; the anime face just uses the level.
 */
export function lipSync(audio, onLevel) {
  let context, analyser, data, spectrum, frame = 0;
  try {
    context = new (window.AudioContext || window.webkitAudioContext)();
    const source = context.createMediaElementSource(audio);
    analyser = context.createAnalyser();
    analyser.fftSize = 512;
    data = new Uint8Array(analyser.fftSize);
    spectrum = new Uint8Array(analyser.frequencyBinCount);
    source.connect(analyser);
    analyser.connect(context.destination);
  } catch {
    return () => {};
  }
  let last = 0;
  const tick = now => {
    if (now - last > 30) {
      last = now;
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const value of data) sum += ((value - 128) / 128) ** 2;
      const rms = Math.sqrt(sum / data.length);
      analyser.getByteFrequencyData(spectrum);
      onLevel(Math.min(1, rms * 5.5), speechBands(spectrum, context.sampleRate));
    }
    frame = requestAnimationFrame(tick);
  };
  const stop = () => { cancelAnimationFrame(frame); onLevel(0, null); };
  audio.addEventListener('play', () => { context.resume?.(); frame = requestAnimationFrame(tick); });
  audio.addEventListener('pause', stop);
  audio.addEventListener('ended', () => { stop(); context.close?.(); });
  return stop;
}

/** Energy in five speech bands (Hz edges suited to vowel formants), each 0..1. */
export function speechBands(spectrum, sampleRate = 48000) {
  const edges = [80, 300, 700, 1200, 2200, 4000];
  const hzPerBin = sampleRate / 2 / spectrum.length;
  const out = [];
  for (let b = 0; b < 5; b++) {
    const from = Math.max(0, Math.floor(edges[b] / hzPerBin));
    const to = Math.min(spectrum.length, Math.max(from + 1, Math.floor(edges[b + 1] / hzPerBin)));
    let sum = 0;
    for (let i = from; i < to; i++) sum += spectrum[i];
    out.push(sum / (to - from) / 255);
  }
  return out;
}
