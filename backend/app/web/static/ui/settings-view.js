/*
 * Settings, inside the app window (and the /settings page, which mounts the same view).
 *
 *   mountSettings(root, { api, toast, esc, initialGroup })
 *
 * Everything is scoped to ``root``. The form is built from GET /api/system/settings
 * (groups and fields described by the server) and saved with PUT.
 */

const GROUP_ICONS = {
  general: '<path d="M12 15.5A3.5 3.5 0 1 0 12 8.5a3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-2.82 1.17V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 7.2 19.73l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 3.09 14H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.27 7.18l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9.92 3.09V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 2.82 1.17l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 20.91 10H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/>',
  database: '<path d="M12 8c4.42 0 8-1.34 8-3s-3.58-3-8-3-8 1.34-8 3 3.58 3 8 3Z"/><path d="M20 5v6c0 1.66-3.58 3-8 3s-8-1.34-8-3V5"/><path d="M20 11v6c0 1.66-3.58 3-8 3s-8-1.34-8-3v-6"/>',
  ai: '<path d="M12 3v18"/><path d="M9 5.5A3 3 0 0 0 6 8.5a3 3 0 0 0-2 5.3A3 3 0 0 0 6.5 19a3 3 0 0 0 5.5.9"/><path d="M15 5.5a3 3 0 0 1 3 3 3 3 0 0 1 2 5.3A3 3 0 0 1 17.5 19a3 3 0 0 1-5.5.9"/>',
  knowledge: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/>',
  enterprise: '<path d="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="m22 7-10 6L2 7"/>',
  documents: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/><path d="M9 13h6"/><path d="M9 17h6"/>',
  integrations: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10Z"/>',
  voice: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
  secretary: '<path d="M8 2v4"/><path d="M16 2v4"/><path d="M3 10h18"/><path d="M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/>',
  copilot: '<path d="M12 2a5 5 0 0 1 5 5v1a4 4 0 0 1 0 8v1a5 5 0 0 1-10 0v-1a4 4 0 0 1 0-8V7a5 5 0 0 1 5-5Z"/><path d="M12 8v8"/><path d="M9 11h6"/>',
  capture: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/>',
  desktop: '<path d="M3 4h18a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z"/><path d="M8 21h8"/><path d="M12 17v4"/>',
  logging: '<path d="M4 4h10l6 6v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="M14 4v6h6"/><path d="M8 14h6"/><path d="M8 18h4"/>',
};
const TEST_TARGETS = { database: 'database', ai: 'ai', knowledge: 'knowledge', enterprise: 'enterprise',
                       documents: 'documents', desktop: 'screenpipe', copilot: 'copilot', capture: 'activity',
                       integrations: 'integrations' };

function icon(group, esc) {
  const paths = GROUP_ICONS[group.id];
  return paths
    ? `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`
    : `<span>${esc(group.icon || '')}</span>`;
}

export function mountSettings(root, { api, toast, esc, initialGroup = null, pickFile = null }) {
  let description = null;
  let active = initialGroup || 'general';
  const pending = {};
  const liveModels = {};
  const customModel = {};
  const $ = selector => root.querySelector(selector);
  const $$ = selector => [...root.querySelectorAll(selector)];
  const put = (path, body) => (api.put ? api.put(path, body) : api.request('PUT', path, body));

  root.classList.add('settings-view');
  root.innerHTML = `
    <div class="st-head">
      <h2>Settings</h2>
      <label class="st-switch" title="Show rarely-needed options"><input type="checkbox" data-advanced><span></span>Advanced</label>
    </div>
    <p class="st-path" data-env></p>
    <div class="st-layout">
      <nav class="st-groups" data-groups></nav>
      <div class="st-panel" data-panel><div class="st-empty">Loading…</div></div>
    </div>
    <div class="st-bar">
      <button type="button" class="st-btn ghost" data-discard>Discard changes</button>
      <span class="st-grow"></span>
      <span class="st-dirty" data-dirty hidden></span>
      <button type="button" class="st-btn primary" data-save>Save changes</button>
    </div>`;

  const isDirty = () => Object.keys(pending).length > 0;
  function updateDirty() {
    const badge = $('[data-dirty]');
    const n = Object.keys(pending).length;
    badge.hidden = !n;
    badge.textContent = `${n} unsaved change${n === 1 ? '' : 's'}`;
  }
  function valueOf(key) {
    if (pending[key] !== undefined) return pending[key];
    const field = description.fields.find(f => f.key === key);
    return field ? field.value : undefined;
  }
  function isVisible(field) {
    if (field.show_if && !field.show_if.values.map(String).includes(String(valueOf(field.show_if.key)))) return false;
    return (field.show_if_all || []).every(c => c.values.map(String).includes(String(valueOf(c.key))));
  }

  function fieldControl(field) {
    const value = pending[field.key] !== undefined ? pending[field.key] : field.value;
    const help = field.help ? `<span class="st-help">${esc(field.help)}</span>` : '';
    if (field.type === 'bool') {
      return `<label class="st-switch"><input type="checkbox" data-key="${esc(field.key)}" ${value === true || value === 'true' ? 'checked' : ''}><span></span>${esc(field.label)}</label>${help}`;
    }
    if (field.type === 'model') {
      const live = liveModels[field.model_provider];
      const options = live ? live.models : field.options;
      const known = options.some(o => o.id === value);
      const custom = customModel[field.key] || (value && !known);
      return `<span class="st-name">${esc(field.label)}</span>${help}
        <div class="st-row"><select data-model-select="${esc(field.key)}">
          ${options.map(o => `<option value="${esc(o.id)}"${o.id === value && !custom ? ' selected' : ''}>${esc(o.label || o.id)}${o.note ? ` — ${esc(o.note)}` : ''}</option>`).join('')}
          <option value="__custom__"${custom ? ' selected' : ''}>Custom model ID…</option></select>
          <button type="button" class="st-btn sm" data-model-refresh="${esc(field.model_provider)}" title="Ask the provider which models this account can use">Refresh</button></div>
        ${custom ? `<input type="text" data-key="${esc(field.key)}" value="${esc(value)}" placeholder="${esc(field.placeholder || '')}" autocomplete="off">`
                 : `<input type="hidden" data-key="${esc(field.key)}" value="${esc(value)}">`}
        ${live ? `<span class="st-help">${live.live ? `✓ ${live.models.length - 1} model${live.models.length === 2 ? '' : 's'} available to your account.` : esc(live.error)}</span>` : ''}`;
    }
    if (field.type === 'select') {
      return `<span class="st-name">${esc(field.label)}</span>${help}
        <select data-key="${esc(field.key)}">${field.options.map(o => `<option value="${esc(o.value)}"${String(o.value) === String(value) ? ' selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`;
    }
    const type = { password: 'password', number: 'number', url: 'url' }[field.type] || 'text';
    const set = field.secret && field.is_set ? ' <span class="st-pill ok">set</span>' : '';
    return `<span class="st-name">${esc(field.label)}${set}</span>${help}
      <input type="${type}" data-key="${esc(field.key)}" value="${esc(value)}" placeholder="${esc(field.placeholder || '')}" autocomplete="off" ${field.secret ? 'data-secret="1"' : ''}>`;
  }

  async function voiceExtras() {
    const host = $('[data-voice-extra]');
    if (!host) return;
    const status = await api.get('/api/voice/status').catch(() => null);
    if (!status) { host.innerHTML = ''; return; }
    const v = status.voice || {};
    const row = (ok, title, detail) => `<div class="st-status"><span class="st-dot ${ok ? 'ok' : 'off'}"></span><b>${esc(title)}</b><span>${esc(detail)}</span></div>`;
    host.innerHTML = `<div class="st-card"><h3>What works on this PC</h3>
      ${row(status.model_ready, 'Speech recognition', status.model_ready ? `Ready (${(status.models || []).join(', ')}) — runs offline.` : 'The chosen model isn\'t installed — import it below, or pick Balanced (base).')}
      ${row(v.kokoro, 'Offline voice (Kokoro)', v.kokoro ? 'Ready — natural, and works on any network.' : (v.kokoro_detail || 'Not installed.'))}
      ${row(v.sonic, 'Amazon Nova Sonic', v.sonic ? 'Ready — goes through Amazon Bedrock. Also powers Talk live.' : (v.sonic_detail || 'Not set up.'))}
      ${row(v.windows, 'Windows voice', v.windows ? 'Available — offline, robotic.' : 'Only on Windows.')}
      <div class="st-actions"><button type="button" class="st-btn sm" data-import-model>Import speech model…</button>
        <span class="st-help">A .zip of a faster-whisper model folder (model.bin, config.json…), e.g. small.en from another PC.</span></div></div>`;
    $('[data-import-model]').onclick = () => {
      const choose = pickFile || ((accept, done) => {
        const input = document.createElement('input');
        input.type = 'file'; input.accept = accept;
        input.onchange = () => input.files[0] && done(input.files[0]);
        input.click();
      });
      choose('.zip', async file => {
        const form = new FormData();
        form.append('file', file, file.name);
        try {
          const result = await api.post('/api/voice/models', form);
          toast(`Speech model ${result.name} installed.`, 'ok');
          voiceExtras();
        } catch (error) { toast(error.message, 'err'); }
      });
    };
  }

  function renderPanel() {
    const showAdvanced = $('[data-advanced]').checked;
    const group = description.groups.find(g => g.id === active) || description.groups[0];
    active = group.id;
    const fields = description.fields.filter(f => f.group === active && isVisible(f) && (showAdvanced || !f.advanced));
    const hidden = description.fields.filter(f => f.group === active && isVisible(f) && f.advanced).length;
    const testTarget = TEST_TARGETS[active];
    $('[data-panel]').innerHTML = `
      <div class="st-card">
        <header>${icon(group, esc)}<h3>${esc(group.title)}</h3><span class="st-grow"></span>
          ${testTarget ? '<button type="button" class="st-btn sm" data-test>Test connection</button>' : ''}
          ${active === 'voice' ? '<button type="button" class="st-btn sm" data-test-voice>Test voice</button>' : ''}</header>
        <p class="st-desc">${esc(group.description || '')}</p>
        ${fields.map(f => `<label class="st-field">${fieldControl(f)}</label>`).join('') || '<div class="st-empty">No basic options here — turn on Advanced.</div>'}
        ${!showAdvanced && hidden ? `<p class="st-help">${hidden} advanced option${hidden === 1 ? '' : 's'} hidden.</p>` : ''}
      </div>
      ${active === 'voice' ? '<div data-voice-extra></div>' : ''}
      ${active === 'knowledge' ? `<div class="st-card"><h3>Maintenance</h3>
        <p class="st-desc">Re-embed every indexed document. Run this after switching embedding provider.</p>
        <div class="st-actions"><button type="button" class="st-btn" data-reindex>Reindex all documents</button></div></div>` : ''}`;

    const controllers = new Set(description.fields.flatMap(f => [...(f.show_if ? [f.show_if.key] : []),
      ...(f.show_if_all || []).map(c => c.key)]));
    $$('[data-key]').forEach(input => {
      input.oninput = input.onchange = () => {
        pending[input.dataset.key] = input.type === 'checkbox' ? input.checked : input.value;
        updateDirty();
        if (controllers.has(input.dataset.key)) renderPanel();
      };
    });
    $$('[data-model-select]').forEach(select => {
      select.onchange = () => {
        const key = select.dataset.modelSelect;
        if (select.value === '__custom__') customModel[key] = true;
        else { delete customModel[key]; pending[key] = select.value; }
        updateDirty();
        renderPanel();
      };
    });
    $$('[data-model-refresh]').forEach(button => {
      button.onclick = async () => {
        if (isDirty() && !(await save())) return;
        button.disabled = true; button.textContent = 'Loading…';
        try {
          const result = await api.get(`/api/system/models/${button.dataset.modelRefresh}`);
          liveModels[button.dataset.modelRefresh] = result;
          if (!result.live) toast(result.error, 'err');
          renderPanel();
        } catch (error) { toast(error.message, 'err'); } finally { button.disabled = false; button.textContent = 'Refresh'; }
      };
    });
    const test = $('[data-test]');
    if (test) test.onclick = async () => {
      if (isDirty() && !(await save())) return;
      test.disabled = true; test.textContent = 'Testing…';
      try {
        const result = await api.post(`/api/system/test/${testTarget}`);
        toast(result.detail || (result.ok ? 'OK' : 'Failed'), result.ok ? 'ok' : 'err');
      } catch (error) { toast(error.message, 'err'); } finally { test.disabled = false; test.textContent = 'Test connection'; }
    };
    const testVoice = $('[data-test-voice]');
    if (testVoice) testVoice.onclick = async () => {
      if (isDirty() && !(await save())) return;
      testVoice.disabled = true; testVoice.textContent = 'Speaking…';
      try {
        const result = await api.post('/api/voice/test', {});
        if (result.ok) {
          new Audio(`data:${result.mime};base64,${result.audio}`).play().catch(() => {});
          toast(`Spoken by ${result.provider}.`, 'ok');
        } else {
          toast(result.detail, 'err');
        }
      } catch (error) { toast(error.message, 'err'); } finally { testVoice.disabled = false; testVoice.textContent = 'Test voice'; }
    };
    const reindex = $('[data-reindex]');
    if (reindex) reindex.onclick = async () => {
      reindex.disabled = true;
      try {
        const result = await api.post('/api/system/reindex');
        toast(`Reindexed ${result.updated} of ${result.total} documents.`, 'ok');
      } catch (error) { toast(error.message, 'err'); } finally { reindex.disabled = false; }
    };
    if (active === 'voice') voiceExtras();
  }

  function renderGroups() {
    $('[data-groups]').innerHTML = description.groups.map(g =>
      `<button type="button" data-group="${esc(g.id)}" aria-selected="${g.id === active}">${icon(g, esc)}<span>${esc(g.title)}</span></button>`).join('');
    $$('[data-group]').forEach(b => { b.onclick = () => { active = b.dataset.group; renderGroups(); renderPanel(); }; });
  }

  async function save() {
    if (!isDirty()) { toast('Nothing to save.'); return true; }
    const button = $('[data-save]');
    button.disabled = true;
    try {
      const result = await put('/api/system/settings', pending);
      Object.keys(pending).forEach(k => delete pending[k]);
      updateDirty();
      await load();
      toast(result.restart_required ? 'Saved. Restart Cerebro for these changes to take effect.'
        : `Saved ${result.applied.length} change${result.applied.length === 1 ? '' : 's'}.`, 'ok');
      return true;
    } catch (error) {
      toast(`Could not save: ${error.message}`, 'err');
      return false;
    } finally { button.disabled = false; }
  }

  async function load() {
    description = await api.get('/api/system/settings');
    $('[data-env]').innerHTML = `Stored in <code>${esc(description.env_file)}</code> — safe to edit by hand.`;
    renderGroups();
    renderPanel();
  }

  $('[data-save]').onclick = save;
  $('[data-discard]').onclick = () => { Object.keys(pending).forEach(k => delete pending[k]); updateDirty(); renderPanel(); };
  $('[data-advanced]').onchange = renderPanel;
  const ready = load().catch(error => {
    $('[data-panel]').innerHTML = `<div class="st-empty">Could not load settings: ${esc(error.message)}</div>`;
  });

  return {
    ready,
    isDirty,
    save,
    show(group) { if (group && description) { active = group; renderGroups(); renderPanel(); } else if (group) active = group; },
    reload: load,
  };
}
