// Faces for Cerebro's agents: the pixel brain, an original anime-style girl,
// or a Live2D / VRM model the user loads. Every face takes the same calls:
//
//   const face = createFace(host, {face: 'anime', style: {palette: 'pink'}});
//   face.setState('thinking');   // an activity state (see brain.js)
//   face.setMouth(0.6);          // 0..1 — how open, for lip sync
//   face.destroy();
//
// The anime face is drawn here as layered SVG (an original character — not a
// copy of any existing one), animated by time so the same drawing can be
// rendered frame by frame for the desktop buddy (packaging/make_face.py).
import { mascot, setBrainState } from './brain.js';

// ------------------------------------------------------------- palettes
export const PALETTES = {
  pink:   { hair: '#f6a6c9', shade: '#dc7aa6', light: '#ffd6e8', eye: '#8b5cf6', eye2: '#c4b5fd', accent: '#f472b6' },
  black:  { hair: '#2e3047', shade: '#1b1c2c', light: '#575c80', eye: '#b45309', eye2: '#fbbf24', accent: '#ef4444' },
  silver: { hair: '#dfe2ee', shade: '#aab0c8', light: '#f7f8fc', eye: '#0ea5e9', eye2: '#7dd3fc', accent: '#a78bfa' },
  blue:   { hair: '#74a9ff', shade: '#4479de', light: '#b4d0ff', eye: '#d97706', eye2: '#fcd34d', accent: '#38bdf8' },
  brown:  { hair: '#a9714a', shade: '#7c4f30', light: '#d39a6c', eye: '#15803d', eye2: '#86efac', accent: '#f59e0b' },
  green:  { hair: '#86dcb2', shade: '#45b382', light: '#c4f3dc', eye: '#db2777', eye2: '#f9a8d4', accent: '#34d399' },
};
export const PALETTE_NAMES = Object.keys(PALETTES);

// Activity state -> expression.
const EXPRESSION = {
  idle: 'calm', thinking: 'thinking', searching: 'reading', browsing: 'focused',
  writing: 'focused', syncing: 'focused', listening: 'listening',
  awaiting_approval: 'alert', error: 'dizzy', offline: 'asleep', speaking: 'calm',
};

let uid = 0;

/** The anime face as an SVG string (``frozen``: no animation hooks needed). */
export function animeSVG(paletteName = 'pink', expr = 'calm') {
  const p = PALETTES[paletteName] || PALETTES.pink;
  const id = `af${++uid}`;
  const eye = (cx, side) => `
    <g class="eye" data-side="${side}" transform="translate(${cx} 104)">
      <g class="lid">
        <ellipse rx="13" ry="15" fill="#fff"/>
        <clipPath id="${id}c${side}"><ellipse rx="13" ry="15"/></clipPath>
        <g clip-path="url(#${id}c${side})">
          <g class="iris">
            <ellipse cy="1" rx="10.5" ry="13" fill="url(#${id}iris)"/>
            <ellipse cy="2" rx="5.2" ry="7.2" fill="#1e1530" opacity=".85"/>
            <circle cx="-4" cy="-5" r="3.4" fill="#fff"/>
            <circle cx="4" cy="6" r="1.7" fill="#fff" opacity=".85"/>
          </g>
          <path d="M-14 -16 H14 V-9 Q0 -14 -14 -9 Z" fill="#000" opacity=".12"/>
        </g>
        <path class="lash" d="M-15 -8 Q0 -21 15 -9" fill="none" stroke="#2a1b2e" stroke-width="3.6" stroke-linecap="round"/>
        <path d="M${side === 'l' ? '-15 -8 L-19 -11' : '15 -9 L19 -12'}" stroke="#2a1b2e" stroke-width="2.4" stroke-linecap="round"/>
        <path d="M-9 14 Q0 17 9 14" fill="none" stroke="#2a1b2e" stroke-width="1" opacity=".45"/>
      </g>
      <path class="shut" d="M-13 2 Q0 10 13 2" fill="none" stroke="#2a1b2e" stroke-width="3" stroke-linecap="round"/>
      <g class="swirl" fill="none" stroke="#2a1b2e" stroke-width="2" stroke-linecap="round">
        <path d="M0 0 m-1 0 a1 1 0 1 1 2 0 a3 3 0 1 1 -6 0 a5 5 0 1 1 10 0 a7 7 0 1 1 -14 0 a9 9 0 1 1 18 0"/>
      </g>
    </g>`;
  return `
<svg class="anime-face" viewBox="0 0 200 200" data-expr="${expr}" data-palette="${paletteName}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <defs>
    <linearGradient id="${id}iris" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${p.eye}" stop-opacity="1"/><stop offset=".55" stop-color="${p.eye}"/>
      <stop offset="1" stop-color="${p.eye2}"/>
    </linearGradient>
    <linearGradient id="${id}hair" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${p.light}"/><stop offset=".35" stop-color="${p.hair}"/><stop offset="1" stop-color="${p.shade}"/>
    </linearGradient>
    <radialGradient id="${id}glow"><stop offset="0" stop-color="${p.accent}" stop-opacity=".9"/><stop offset="1" stop-color="${p.accent}" stop-opacity="0"/></radialGradient>
  </defs>
  <style>
    .anime-face .shut, .anime-face .swirl, .anime-face .glasses, .anime-face .bubble, .anime-face .bang,
    .anime-face .zz, .anime-face .sweat, .anime-face .mouth-o, .anime-face .mouth-flat, .anime-face .mouth-wave,
    .anime-face .mic-glow { display: none }
    .anime-face[data-expr="asleep"] .lid, .anime-face[data-expr="dizzy"] .lid { display: none }
    .anime-face[data-expr="asleep"] .shut, .anime-face[data-expr="asleep"] .zz { display: inline }
    .anime-face[data-expr="dizzy"] .swirl, .anime-face[data-expr="dizzy"] .sweat, .anime-face[data-expr="dizzy"] .mouth-wave { display: inline }
    .anime-face[data-expr="reading"] .glasses { display: inline }
    .anime-face[data-expr="thinking"] .bubble, .anime-face[data-expr="alert"] .bang { display: inline }
    .anime-face[data-expr="listening"] .mic-glow { display: inline }
    .anime-face[data-expr="alert"] .mouth-o { display: inline }
    .anime-face[data-expr="thinking"] .mouth-flat, .anime-face[data-expr="reading"] .mouth-flat { display: inline }
    .anime-face[data-expr="alert"] .mouth-smile, .anime-face[data-expr="thinking"] .mouth-smile,
    .anime-face[data-expr="reading"] .mouth-smile, .anime-face[data-expr="dizzy"] .mouth-smile { display: none }
    .anime-face[data-expr="asleep"] { filter: saturate(.45) brightness(.92) }
  </style>
  <g class="body">
    <path d="M40 200 C44 170 70 158 100 158 C130 158 156 170 160 200 Z" fill="${p.accent}"/>
    <path d="M40 200 C44 170 70 158 100 158 C130 158 156 170 160 200 Z" fill="#fff" opacity=".18"/>
    <path d="M84 158 L100 176 L116 158" fill="none" stroke="#fff" stroke-width="3" opacity=".85" stroke-linejoin="round"/>
  </g>
  <g class="head">
    <path class="hair-back" d="M40 96 C34 44 66 16 100 16 C134 16 166 44 160 96 L168 168 C154 178 140 166 136 146 L64 146 C60 166 46 178 32 168 Z" fill="url(#${id}hair)"/>
    <rect x="89" y="136" width="22" height="26" rx="8" fill="#f5c9b4"/>
    <path class="face-shape" d="M57 76 C56 118 76 146 100 151 C124 146 144 118 143 76 Z" fill="#ffe4d6"/>
    <path d="M60 122 C68 140 84 150 100 151 C116 150 132 140 140 122" fill="none" stroke="#f0b9a3" stroke-width="1.2" opacity=".6"/>
    <ellipse cx="73" cy="123" rx="8.5" ry="4.2" fill="#ff8fa6" opacity=".38"/>
    <ellipse cx="127" cy="123" rx="8.5" ry="4.2" fill="#ff8fa6" opacity=".38"/>
    <g class="brows" fill="none" stroke="${p.shade}" stroke-width="2.6" stroke-linecap="round">
      <path class="brow" d="M69 83 Q80 78 90 82"/><path class="brow" d="M110 82 Q120 78 131 83"/>
    </g>
    ${eye(80, 'l')}${eye(120, 'r')}
    <path d="M99 117 L101 120.5" stroke="#d99a86" stroke-width="1.6" stroke-linecap="round"/>
    <g class="mouth" transform="translate(100 133)">
      <path class="mouth-smile" d="M-7 -1 Q0 5 7 -1" fill="none" stroke="#b9505e" stroke-width="2.2" stroke-linecap="round"/>
      <path class="mouth-flat" d="M-5 0.5 Q0 1.5 5 0" fill="none" stroke="#b9505e" stroke-width="2.2" stroke-linecap="round"/>
      <ellipse class="mouth-o" cx="0" cy="1" rx="3.6" ry="4.4" fill="#9e3b4b"/>
      <path class="mouth-wave" d="M-8 1 q2 -3 4 0 t4 0 t4 0 t4 0" fill="none" stroke="#b9505e" stroke-width="2" stroke-linecap="round"/>
      <g class="mouth-open" opacity="0">
        <path class="open-shape" d="M-7 -1 Q0 -2 7 -1 Q6 9 0 10 Q-6 9 -7 -1 Z" fill="#8f3343"/>
        <path class="tongue" d="M-4 6 Q0 3 4 6 Q2 9.5 0 9.5 Q-2 9.5 -4 6 Z" fill="#f08a96"/>
      </g>
    </g>
    <g class="glasses" fill="none" stroke="#3b3f5c" stroke-width="2.2">
      <circle cx="80" cy="104" r="16"/><circle cx="120" cy="104" r="16"/><path d="M96 102 Q100 99 104 102"/>
      <path d="M68 96 L74 100" stroke="#fff" stroke-width="1.5" opacity=".7"/>
    </g>
    <g class="hair-front">
      <path class="lock lock-l" d="M58 74 C50 100 50 132 60 158 C64 132 66 106 68 84 Z" fill="url(#${id}hair)"/>
      <path class="lock lock-r" d="M142 74 C150 100 150 132 140 158 C136 132 134 106 132 84 Z" fill="url(#${id}hair)"/>
      <path d="M50 92 C47 50 72 28 100 28 C128 28 153 50 150 92 L143 74 L135 88 L126 66 L116 86 L106 62 L98 84 L88 63 L79 87 L70 67 L60 90 Z" fill="url(#${id}hair)"/>
      <path d="M68 44 Q100 32 132 44" fill="none" stroke="${p.light}" stroke-width="5" stroke-linecap="round" opacity=".75"/>
      <path d="M104 64 L98 84 M79 87 L88 64" stroke="${p.shade}" stroke-width="1.4" opacity=".55"/>
      <path class="clip" d="M134 50 l3 -6 l3 6 l6 1 l-5 4 l2 6 l-6 -3 l-6 3 l2 -6 l-5 -4 Z" fill="${p.accent}" stroke="#fff" stroke-width="1"/>
    </g>
    <g class="headset">
      <path d="M48 92 C44 30 156 30 152 92" fill="none" stroke="#3b3f5c" stroke-width="6" stroke-linecap="round"/>
      <rect x="38" y="80" width="16" height="30" rx="7" fill="#3b3f5c"/><rect x="41" y="85" width="10" height="20" rx="5" fill="${p.accent}"/>
      <rect x="146" y="80" width="16" height="30" rx="7" fill="#3b3f5c"/><rect x="149" y="85" width="10" height="20" rx="5" fill="${p.accent}"/>
      <path d="M46 108 Q54 138 80 141" fill="none" stroke="#3b3f5c" stroke-width="3" stroke-linecap="round"/>
      <circle class="mic-glow" cx="82" cy="141" r="9" fill="url(#${id}glow)"/>
      <circle cx="82" cy="141" r="3.8" fill="#3b3f5c"/><circle cx="82" cy="141" r="1.7" fill="${p.accent}"/>
    </g>
  </g>
  <g class="bubble" transform="translate(160 40)">
    <ellipse rx="22" ry="14" fill="#fff" stroke="#c9cbe0" stroke-width="1.5"/>
    <circle cx="-26" cy="18" r="4" fill="#fff" stroke="#c9cbe0" stroke-width="1.2"/>
    <circle class="dot" cx="-9" cy="0" r="3" fill="#8b7cf6"/><circle class="dot" cx="0" cy="0" r="3" fill="#8b7cf6"/><circle class="dot" cx="9" cy="0" r="3" fill="#8b7cf6"/>
  </g>
  <g class="bang" transform="translate(162 38)">
    <circle r="15" fill="#f59e0b"/><rect x="-2.4" y="-9" width="4.8" height="11" rx="2.4" fill="#fff"/><circle cy="6.5" r="2.6" fill="#fff"/>
  </g>
  <g class="zz" fill="#8b7cf6" font-family="Inter, Arial, sans-serif" font-weight="800">
    <text class="z1" x="146" y="56" font-size="18">z</text><text class="z2" x="160" y="40" font-size="13">z</text>
  </g>
  <path class="sweat" d="M146 74 Q152 84 146 88 Q140 84 146 74 Z" fill="#7dd3fc" stroke="#fff" stroke-width="1"/>
</svg>`;
}

// ------------------------------------------------------- anime renderer
/** Pose the anime SVG for time ``t`` (ms). Pure: frames can be rendered from it. */
export function poseAnime(svg, t, mouth = 0, look = null) {
  const expr = svg.dataset.expr;
  const set = (sel, value) => svg.querySelectorAll(sel).forEach(node => node.setAttribute('transform', value));
  // Breathing and a slight sway.
  const breath = Math.sin(t / 900) * 1.2;
  const tilt = expr === 'listening' ? 4 : expr === 'asleep' ? -7 : expr === 'thinking' ? -3 : Math.sin(t / 2300) * 1.2;
  set('.head', `translate(0 ${breath.toFixed(2)}) rotate(${tilt.toFixed(2)} 100 150)`);
  set('.body', `translate(0 ${(breath * 0.4).toFixed(2)})`);
  set('.lock-l', `rotate(${(Math.sin(t / 700) * 1.6).toFixed(2)} 64 76)`);
  set('.lock-r', `rotate(${(-Math.sin(t / 760) * 1.6).toFixed(2)} 136 76)`);
  // Blinking: a quick close every ~4 s (never while alert).
  const cycle = (t + 600) % 4200;
  const blink = expr === 'alert' ? 1 : cycle < 70 ? 1 - cycle / 70 : cycle < 150 ? (cycle - 70) / 80 : 1;
  const squint = expr === 'focused' ? 0.82 : expr === 'alert' ? 1.08 : 1;
  svg.querySelectorAll('.lid').forEach(lid => lid.setAttribute('transform', `scale(1 ${Math.max(0.08, blink * squint).toFixed(3)})`));
  // Where the eyes look.
  const gaze = look || ({ thinking: [3, -4], reading: [0, 3], focused: [-1, 1.5], listening: [0, 0] }[expr]) || [Math.sin(t / 3100) * 1.2, 0];
  set('.iris', `translate(${gaze[0].toFixed(2)} ${gaze[1].toFixed(2)})`);
  set('.brow', expr === 'alert' ? 'translate(0 -4)' : expr === 'thinking' ? 'translate(0 -2)' : expr === 'dizzy' ? 'translate(0 2)' : '');
  // The mouth: closed shape, or open by the speech level.
  const open = Math.max(0, Math.min(1, mouth));
  const opened = svg.querySelector('.mouth-open');
  if (opened) {
    opened.setAttribute('opacity', open > 0.05 ? '1' : '0');
    opened.setAttribute('transform', `scale(${(0.8 + open * 0.35).toFixed(3)} ${(0.25 + open * 0.95).toFixed(3)})`);
    svg.querySelectorAll('.mouth-smile, .mouth-flat, .mouth-o, .mouth-wave').forEach(node => { node.style.opacity = open > 0.05 ? '0' : ''; });
  }
  // Bubbles and z's.
  svg.querySelectorAll('.bubble .dot').forEach((dot, i) => dot.setAttribute('opacity', (0.35 + 0.65 * ((Math.floor(t / 300) + 3 - i) % 3 === 0 ? 1 : 0)).toFixed(2)));
  set('.bang', `translate(162 ${(38 + Math.abs(Math.sin(t / 180)) * -4).toFixed(2)})`);
  const zt = (t % 2400) / 2400;
  set('.zz', `translate(${(zt * 6).toFixed(2)} ${(-zt * 10).toFixed(2)})`);
  set('.swirl', `rotate(${((t / 4) % 360).toFixed(1)})`);
}

function animeFace(host, style = {}) {
  const palette = PALETTES[style.palette] ? style.palette : 'pink';
  host.innerHTML = animeSVG(palette);
  const svg = host.querySelector('svg');
  let mouth = 0, target = 0, frame = 0, alive = true, look = null;
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
    setState(state) { svg.dataset.expr = EXPRESSION[state] || 'calm'; },
    setMouth(level) { target = level; },
    destroy() { alive = false; cancelAnimationFrame(frame); removeEventListener('pointermove', onMove); },
  };
}

// --------------------------------------------------------- brain (pixel)
function brainFace(host, style = {}, size = 120) {
  host.innerHTML = mascot(size);
  return {
    kind: 'brain',
    setState(state) { setBrainState(host, state); },
    setMouth() {},
    destroy() {},
  };
}

// --------------------------------------------------------- Live2D / VRM
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
  const face = animeFace(host, { palette: style.palette || 'silver' });
  host.title = problem;
  return face;
}

function live2dFace(host, style = {}) {
  // Rendered into a canvas once the libraries and the model have loaded; the
  // anime face stands in meanwhile (and if anything is missing).
  const stand = animeFace(host, { palette: style.palette || 'silver' });
  let model = null, app = null, mouth = 0, alive = true, state = 'idle';
  (async () => {
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
      const scale = Math.min(size / model.width, size / model.height) * 1.6;
      model.scale.set(scale);
      model.anchor?.set?.(0.5, 0.25);
      model.x = size / 2;
      model.y = size * 0.1;
      app.stage.addChild(model);
      stand.destroy();
      host.innerHTML = '';
      host.append(canvas);
      app.ticker.add(() => {
        const core = model.internalModel?.coreModel;
        core?.setParameterValueById?.('ParamMouthOpenY', mouth);
      });
      play(state);
    } catch (error) {
      host.title = error.message;
      console.warn('Live2D face:', error);
    }
  })();
  const play = name => {
    if (!model) return;
    const group = { thinking: 'Idle', listening: 'Idle', awaiting_approval: 'TapBody', error: 'TapBody' }[name] || 'Idle';
    try { model.motion(group); } catch { /* models without that motion */ }
  };
  return {
    kind: 'live2d',
    setState(next) { state = next; stand.setState(next); play(next); },
    setMouth(level) { mouth = level; stand.setMouth(level); },
    destroy() { alive = false; stand.destroy(); app?.destroy(true); },
  };
}

function vrmFace(host, style = {}) {
  const stand = animeFace(host, { palette: style.palette || 'silver' });
  let alive = true, mouth = 0, state = 'idle', renderer = null, frame = 0;
  (async () => {
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
      const gltf = await loader.loadAsync(`/faces/${encodeURIComponent(style.model)}/${style.entry || 'model.vrm'}`);
      const vrm = gltf.userData.vrm;
      if (!alive || !vrm) throw new Error('That file has no VRM model in it.');
      VRMUtils.rotateVRM0?.(vrm);
      scene.add(vrm.scene);
      const head = vrm.humanoid?.getNormalizedBoneNode('head');
      const headPos = new THREE.Vector3();
      head?.getWorldPosition(headPos);
      camera.position.set(0, headPos.y || 1.4, 0.9);
      camera.lookAt(0, (headPos.y || 1.4) - 0.02, 0);
      const target = new THREE.Object3D();
      scene.add(target);
      if (vrm.lookAt) vrm.lookAt.target = target;
      stand.destroy();
      host.innerHTML = '';
      host.append(renderer.domElement);
      const clock = new THREE.Clock();
      const expressions = vrm.expressionManager;
      const tick = () => {
        if (!alive) return;
        const dt = clock.getDelta(), t = clock.elapsedTime;
        const cycle = (t * 1000 + 600) % 4200;
        expressions?.setValue('blink', state === 'offline' ? 1 : cycle < 150 ? 1 - Math.abs(cycle - 75) / 75 : 0);
        expressions?.setValue('aa', mouth);
        expressions?.setValue('happy', state === 'idle' ? 0.25 : 0);
        expressions?.setValue('surprised', state === 'awaiting_approval' ? 0.6 : 0);
        const spine = vrm.humanoid?.getNormalizedBoneNode('spine');
        if (spine) spine.rotation.z = Math.sin(t * 0.9) * 0.03;
        if (head) head.rotation.x = state === 'thinking' ? -0.12 : Math.sin(t * 0.7) * 0.03;
        target.position.set(Math.sin(t * 0.4) * 0.3, camera.position.y, 2);
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
  return {
    kind: 'vrm',
    setState(next) { state = next; stand.setState(next); },
    setMouth(level) { mouth = level; stand.setMouth(level); },
    destroy() { alive = false; cancelAnimationFrame(frame); stand.destroy(); renderer?.dispose(); },
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
  const palette = agent?.face_style?.palette || (face === 'anime' ? 'pink' : 'silver');
  // Framed on the face rather than the whole bust.
  const svg = animeSVG(palette).replace('viewBox="0 0 200 200"', 'viewBox="36 40 128 128"');
  return `<span class="face-avatar" style="width:${size}px;height:${size}px">${svg}</span>`;
}

/**
 * Lip sync for an <audio> element: calls ``onLevel(0..1)`` about 30 times a
 * second while it plays, then 0 when it stops.
 */
export function lipSync(audio, onLevel) {
  let context, analyser, data, frame = 0;
  try {
    context = new (window.AudioContext || window.webkitAudioContext)();
    const source = context.createMediaElementSource(audio);
    analyser = context.createAnalyser();
    analyser.fftSize = 512;
    data = new Uint8Array(analyser.fftSize);
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
      onLevel(Math.min(1, rms * 5.5));
    }
    frame = requestAnimationFrame(tick);
  };
  const stop = () => { cancelAnimationFrame(frame); onLevel(0); };
  audio.addEventListener('play', () => { context.resume?.(); frame = requestAnimationFrame(tick); });
  audio.addEventListener('pause', stop);
  audio.addEventListener('ended', () => { stop(); context.close?.(); });
  return stop;
}
