/* ── Embedding Model Picker ─────────────────────────────────────────────── */
const DEFAULT_EMBED_MODEL = 'BAAI/bge-small-zh-v1.5';
const EMBED_PROVIDERS = [
  { id: 'default', label: '默认', color: 'var(--accent)' },
  { id: 'ollama', label: 'Ollama', color: 'var(--green)' },
  { id: 'custom', label: '自定义', color: 'var(--muted)' },
];
let _embedProviderApiKeys = {};
let _embedFetchedModels = {};
let _embedAutoFetchedProviders = {};

function setEmbedApiKeysMap(mapObj) {
  _embedProviderApiKeys = { ...(mapObj || {}) };
}

function setEmbedFetchedModelsMap(mapObj) {
  const src = mapObj || {};
  const dst = {};
  for (const [pid, arr] of Object.entries(src)) {
    if (!Array.isArray(arr)) continue;
    dst[pid] = arr.filter(Boolean).map(v => String(v).trim()).filter(Boolean);
  }
  _embedFetchedModels = dst;
}

function _renderEmbedModelButtons(models, activeModel) {
  const row = document.getElementById('kbEmbedModelBtnsRow');
  const wrap = document.getElementById('kbEmbedModelBtns');
  if (!row || !wrap) return;
  const arr = Array.isArray(models) ? models.filter(Boolean) : [];
  if (!arr.length) {
    row.style.display = 'none';
    wrap.innerHTML = '';
    return;
  }
  row.style.display = '';
  wrap.innerHTML = '';
  const current = (activeModel || '').trim();
  for (const model of arr) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'embed-model-btn' + (model === current ? ' active' : '');
    btn.textContent = model;
    btn.onclick = async () => {
      const input = document.getElementById('kbEmbedModelInput');
      if (input) input.value = model;
      document.getElementById('kbEmbedModel').value = model;
      await _saveEmbedConfig();
      _renderEmbedModelButtons(arr, model);
    };
    wrap.appendChild(btn);
  }
}

async function _persistEmbedModelsMap() {
  try {
    const payload = { kb_embed_models_map: { ..._embedFetchedModels } };
    await api()?.save_config(payload);
  } catch (_) {
    // ignore persist errors; UI stays usable with in-memory cache
  }
}

function _rememberCurrentEmbedApiKey() {
  const pid = document.getElementById('kbEmbedProvider')?.value || 'default';
  if (!pid || pid === 'default') return;
  const key = document.getElementById('kbEmbedApiKey')?.value || '';
  _embedProviderApiKeys[pid] = key.trim();
}

function toggleEmbedModelPicker(e) {
  e.stopPropagation();
  const panel   = document.getElementById('kbEmbedPanel');
  const trigger = document.getElementById('kbEmbedTrigger');
  if (panel.classList.contains('open')) {
    closeEmbedModelPicker();
  } else {
    // Build embed provider list
    const list = document.getElementById('kbEmbedList');
    list.innerHTML = '';
    const currentProvider = document.getElementById('kbEmbedProvider').value || 'default';
    EMBED_PROVIDERS.forEach(provider => {
      const row = document.createElement('div');
      row.className = 'mp-provider-row' + (provider.id === currentProvider ? ' active' : '');
      row.innerHTML = `<span class="mp-provider-hdr-dot" style="background:${provider.color}"></span>`
                    + `<span>${provider.label}</span>`;
      row.onclick = () => _pickEmbedProvider(provider.id);
      list.appendChild(row);
    });
    panel.classList.add('open');
    trigger.classList.add('open');
    setTimeout(() => list.scrollTop = 0, 10);
  }
}

function closeEmbedModelPicker() {
  document.getElementById('kbEmbedPanel').classList.remove('open');
  document.getElementById('kbEmbedTrigger').classList.remove('open');
}

function _syncEmbedModelFromProvider() {
  const provider = document.getElementById('kbEmbedProvider').value || 'default';
  const connRow = document.getElementById('kbEmbedConnRow');
  const btnRow = document.getElementById('kbEmbedModelBtnsRow');
  const baseInput = document.getElementById('kbEmbedBaseUrl');
  const keyInput = document.getElementById('kbEmbedApiKey');
  const modelInput = document.getElementById('kbEmbedModelInput');
  if (connRow) connRow.style.display = provider === 'default' ? 'none' : '';
  if (btnRow && provider === 'default') btnRow.style.display = 'none';

  if (provider === 'ollama') {
    if (baseInput && !baseInput.value.trim()) baseInput.value = 'http://localhost:11434';
    if (keyInput) keyInput.placeholder = 'Ollama 通常无需 API Key';
    if (modelInput) modelInput.placeholder = '可选：例如 nomic-embed-text';
  } else if (provider === 'custom') {
    if (keyInput) keyInput.placeholder = '按服务要求填写 API Key';
    if (modelInput) modelInput.placeholder = '可选：模型名（留空将仅测试服务可达性）';
  } else {
    if (modelInput) modelInput.placeholder = DEFAULT_EMBED_MODEL;
  }
  if (keyInput) {
    keyInput.value = provider === 'default' ? '' : (_embedProviderApiKeys[provider] || '');
  }

  let model = provider === 'default'
    ? DEFAULT_EMBED_MODEL
    : (modelInput?.value || '').trim();
  document.getElementById('kbEmbedModel').value = model;
  if (provider !== 'default') {
    const _cached = _embedFetchedModels[provider] || [];
    const _fallback = model ? [model] : [];
    _renderEmbedModelButtons(_cached.length ? _cached : _fallback, model);
    if (!_cached.length) _tryAutoRefreshEmbedModels(provider);
  }
  return model;
}

async function _tryAutoRefreshEmbedModels(provider) {
  if (!provider || provider === 'default') return;
  if (_embedAutoFetchedProviders[provider]) return;
  if (typeof api()?.list_embed_models !== 'function') return;
  _embedAutoFetchedProviders[provider] = true;
  try {
    const payload = {
      provider,
      base_url: (document.getElementById('kbEmbedBaseUrl')?.value || '').trim(),
      api_key: (document.getElementById('kbEmbedApiKey')?.value || '').trim(),
      model: getSelectedEmbedModel(),
    };
    const modelsRes = await api().list_embed_models(payload);
    const models = Array.isArray(modelsRes?.models) ? modelsRes.models : [];
    if (models.length) {
      _embedFetchedModels[provider] = models;
      _renderEmbedModelButtons(models, getSelectedEmbedModel());
      await _persistEmbedModelsMap();
    }
  } catch (_) {
    // silent best-effort auto refresh
  }
}

function setEmbedModelPicker(providerId, modelId, baseUrl, apiKey) {
  const picked = EMBED_PROVIDERS.find(m => m.id === providerId) || EMBED_PROVIDERS[0];
  if (picked.id !== 'default' && (apiKey || '').trim()) {
    _embedProviderApiKeys[picked.id] = (apiKey || '').trim();
  }
  document.getElementById('kbEmbedProvider').value = picked.id;
  document.getElementById('kbEmbedLabel').textContent = picked.label;
  document.getElementById('kbEmbedDot').style.background = picked.color;
  const baseInput = document.getElementById('kbEmbedBaseUrl');
  const keyInput = document.getElementById('kbEmbedApiKey');
  const modelInput = document.getElementById('kbEmbedModelInput');
  if (baseInput) baseInput.value = (baseUrl || '').trim();
  if (keyInput) keyInput.value = (apiKey || '').trim();
  if (modelInput) modelInput.value = (modelId || '').trim();
  _syncEmbedModelFromProvider();
}

async function _pickEmbedProvider(id) {
  _rememberCurrentEmbedApiKey();
  setEmbedModelPicker(
    id,
    document.getElementById('kbEmbedModelInput')?.value || '',
    document.getElementById('kbEmbedBaseUrl')?.value || '',
    _embedProviderApiKeys[id] || document.getElementById('kbEmbedApiKey')?.value || '',
  );
  const model = _syncEmbedModelFromProvider();
  const currentProvider = document.getElementById('kbEmbedProvider')?.value || 'default';
  const currentKey = (document.getElementById('kbEmbedApiKey')?.value || '').trim();
  if (currentProvider !== 'default') _embedProviderApiKeys[currentProvider] = currentKey;
  await api()?.save_config({
    kb_embed_provider: id,
    kb_embed_model: model,
    kb_embed_base_url: (document.getElementById('kbEmbedBaseUrl')?.value || '').trim(),
    kb_embed_api_key: currentKey,
    kb_embed_api_keys: { ..._embedProviderApiKeys },
    kb_embed_models_map: { ..._embedFetchedModels },
  });
  closeEmbedModelPicker();
}

function getSelectedEmbedModel() {
  return _syncEmbedModelFromProvider();
}

async function _saveEmbedConfig() {
  _rememberCurrentEmbedApiKey();
  const model = _syncEmbedModelFromProvider();
  const provider = document.getElementById('kbEmbedProvider')?.value || 'default';
  const currentKey = (document.getElementById('kbEmbedApiKey')?.value || '').trim();
  if (provider !== 'default') _embedProviderApiKeys[provider] = currentKey;
  await api()?.save_config({
    kb_embed_provider: provider,
    kb_embed_model: model,
    kb_embed_base_url: (document.getElementById('kbEmbedBaseUrl')?.value || '').trim(),
    kb_embed_api_key: currentKey,
    kb_embed_api_keys: { ..._embedProviderApiKeys },
    kb_embed_models_map: { ..._embedFetchedModels },
  });
}

document.getElementById('kbEmbedBaseUrl')?.addEventListener('change', _saveEmbedConfig);
document.getElementById('kbEmbedApiKey')?.addEventListener('change', _saveEmbedConfig);
document.getElementById('kbEmbedModelInput')?.addEventListener('change', _saveEmbedConfig);

async function testEmbedConnection() {
  const btn = document.getElementById('testEmbedBtn');
  const alert = document.getElementById('embedConnAlert');
  const provider = document.getElementById('kbEmbedProvider')?.value || 'default';
  const payload = {
    provider,
    base_url: (document.getElementById('kbEmbedBaseUrl')?.value || '').trim(),
    api_key: (document.getElementById('kbEmbedApiKey')?.value || '').trim(),
    model: getSelectedEmbedModel(),
  };
  btn.disabled = true;
  btn.textContent = '验证中…';
  alert.textContent = '';
  try {
    const res = await api()?.test_embed_connection(payload);
    if (res?.ok) {
      alert.style.color = 'var(--green)';
      alert.textContent = `✓ ${res.message || '连接成功'}`;
      if (provider !== 'default' && typeof api()?.list_embed_models === 'function') {
        const modelsRes = await api().list_embed_models(payload);
        const models = Array.isArray(modelsRes?.models) ? modelsRes.models : [];
        if (models.length) {
          _embedFetchedModels[provider] = models;
          _renderEmbedModelButtons(models, getSelectedEmbedModel());
          await _persistEmbedModelsMap();
        }
      }
    } else {
      alert.style.color = 'var(--danger)';
      alert.textContent = `✗ ${res?.message || '连接失败'}`;
    }
  } catch (e) {
    alert.style.color = 'var(--danger)';
    alert.textContent = `✗ ${String(e)}`;
  } finally {
    btn.disabled = false;
    btn.textContent = '验证并刷新模型列表';
  }
}

