/* ── Fetch / validate models ──────────────────────────────────────────────── */
async function _persistUpstreamModelsMap() {
  const normalized = {};
  for (const [pid, arr] of Object.entries(_fetchedModels || {})) {
    if (!Array.isArray(arr)) continue;
    const cleaned = Array.from(new Set(arr.map(x => String(x || '').trim()).filter(Boolean)));
    if (cleaned.length) normalized[String(pid || '').trim()] = cleaned;
  }
  _fetchedModels = normalized;
  try {
    await api().save_config({ upstream_models_map: { ...normalized } });
  } catch (_) {}
}

async function fetchAndShowModels() {
  const btn = document.getElementById('fetchModelsBtn');
  const spinner = document.getElementById('fetchModelsSpinner');
  const status  = document.getElementById('fetchModelsStatus');
  btn.disabled = true;
  spinner.style.display = 'inline-block';
  status.innerHTML = '';
  await _doSaveConfig(false);
  try {
    const res = await api().test_connection();
    if (!res.ok) {
      status.innerHTML = `<span style="color:var(--danger)">✗ ${esc(res.message || '连接失败')}</span>`;
      btn.disabled = false; spinner.style.display = 'none'; return;
    }
  } catch (e) {
    status.innerHTML = `<span style="color:var(--danger)">✗ ${esc(String(e))}</span>`;
    btn.disabled = false; spinner.style.display = 'none'; return;
  }
  try {
    const models = await api().list_upstream_models();
    const pid    = document.getElementById('sPickedProvider').value || 'deepseek';
    _fetchedModels[pid] = models;
    await _persistUpstreamModelsMap();
    _populateModelDropdown(models, document.getElementById('sModel').value);
    status.innerHTML = `<span style="color:var(--green)">✓ 已获取 ${models.length} 个模型</span>`;
  } catch (_) {
    status.innerHTML = `<span style="color:var(--muted)">已连接，但获取模型列表失败</span>`;
  } finally {
    btn.disabled = false; spinner.style.display = 'none';
  }
}

function togglePw(id, btn) {
  const el = document.getElementById(id);
  el.type = el.type === 'password' ? 'text' : 'password';
  const isHidden = el.type === 'password';
  btn.innerHTML = isHidden
    ? `<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"><path d="M8 2.5C4 2.5 1 8 1 8s3 5.5 7 5.5S15 8 15 8 12 2.5 8 2.5zm0 9A3.5 3.5 0 1 1 8 4.5a3.5 3.5 0 0 1 0 7zm0-5.5a2 2 0 1 0 0 4 2 2 0 0 0 0-4z"/></svg>`
    : `<svg viewBox="0 0 16 16" fill="currentColor" width="14" height="14"><path d="M13.36 2.64a.75.75 0 0 0-1.06 0L2.64 12.3a.75.75 0 1 0 1.06 1.06l1.28-1.28A7.7 7.7 0 0 0 8 13.5c4 0 7-5.5 7-5.5a13.1 13.1 0 0 0-2.4-2.98l1.76-1.76a.75.75 0 0 0 0-1.06zM8 11a3 3 0 0 1-2.6-1.5l1.14-1.14a1.5 1.5 0 0 0 2 2L9.5 9.22A3 3 0 0 1 8 11zm5.6-3S11 10.5 8 10.5c-.52 0-1-.07-1.47-.2l1.12-1.12a1.5 1.5 0 0 0 1.87-1.87l1.12-1.12c.8.7 1.48 1.55 1.96 2.31zM8 4.5c.52 0 1 .07 1.47.2L8.35 5.82A1.5 1.5 0 0 0 6.48 7.7L5.36 8.82A3 3 0 0 1 8 4.5z"/></svg>`;
}

async function testConn() {
  const btn = document.getElementById('testConnBtn');
  btn.textContent = '测试中…';
  btn.disabled    = true;
  await _doSaveConfig(false);
  const res = await api().test_connection();
  const al  = document.getElementById('connAlert');
  if (res.ok) {
    al.innerHTML = `<div class="alert alert-ok">✓ ${res.message || '连接成功'}</div>`;
    try {
      const models = await api().list_upstream_models();
      const pid = document.getElementById('sPickedProvider').value || 'deepseek';
      _fetchedModels[pid] = models;
      await _persistUpstreamModelsMap();
      _populateModelDropdown(models, document.getElementById('sModel').value);
    } catch (_) {}
  } else {
    al.innerHTML = `<div class="alert alert-err">✗ ${esc(res.message || '连接失败')}</div>`;
  }
  setTimeout(() => { btn.textContent = '测试连接'; btn.disabled = false; }, 2000);
}

async function testWebSearch() {
  const btn = document.getElementById('testWebBtn');
  btn.textContent = '测试中…';
  btn.disabled    = true;
  await _doSaveConfig(false);
  const res = await api().test_web_search();
  const al  = document.getElementById('connToolAlert');
  if (res.ok) {
    al.innerHTML = `<div class="alert alert-ok">✓ ${esc(res.message || '联网可用')}</div>`;
  } else {
    al.innerHTML = `<div class="alert alert-err">✗ ${esc(res.message || '联网失败')}</div>`;
  }
  setTimeout(() => { btn.textContent = '测试联网'; btn.disabled = false; }, 2000);
}

async function saveSettings() { await _doSaveConfig(true); }

async function _doSaveConfig(close=true) {
  if (typeof _rememberCurrentEmbedApiKey === 'function') {
    _rememberCurrentEmbedApiKey();
  }
  const embedProvider = document.getElementById('kbEmbedProvider')?.value || 'default';
  const embedCurrentKey = (document.getElementById('kbEmbedApiKey')?.value || '').trim();
  const embedKeyMap = (typeof _embedProviderApiKeys === 'object' && _embedProviderApiKeys)
    ? { ..._embedProviderApiKeys }
    : {};
  const embedModelMap = (typeof _embedFetchedModels === 'object' && _embedFetchedModels)
    ? { ..._embedFetchedModels }
    : {};
  if (embedProvider !== 'default') embedKeyMap[embedProvider] = embedCurrentKey;

  const pid     = document.getElementById('sPickedProvider').value || 'deepseek';
  _rememberCurrentProviderApiKey();
  const currentKey = (document.getElementById('sApiKey')?.value || '').trim();
  if (pid !== 'ollama') {
    _providerApiKeys[pid] = currentKey;
  }
  const baseUrl = pid === 'custom'
    ? document.getElementById('sBaseUrl').value.trim()
    : (PROVIDER_URLS[pid] || '');
  const updates = {
    provider:          pid,
    upstream_base_url: baseUrl,
    api_key:           currentKey,
    api_keys:          { ..._providerApiKeys },
    upstream_models_map: { ..._fetchedModels },
    model:             document.getElementById('sModel').value.trim(),
    temperature:       parseFloat(document.getElementById('sTemp').value),
    system_prompt:     document.getElementById('sSystemPrompt').value.trim(),
    tool_web_search:        document.getElementById('sWebSearch').value,
    tool_web_search_engine: document.getElementById('sWebEngine').value,
    tool_tavily_key:        document.getElementById('sTavilyKey').value.trim(),
    tool_bing_key:          document.getElementById('sBingKey').value.trim(),
    tool_brave_key:         document.getElementById('sBraveKey').value.trim(),
    tool_serp_key:          document.getElementById('sSerpKey').value.trim(),
    tool_rag:               document.getElementById('sRag')?.checked ?? false,
    kb_embed_provider:      embedProvider,
    kb_embed_model:         (typeof getSelectedEmbedModel === 'function' ? getSelectedEmbedModel() : (document.getElementById('kbEmbedModel').value || 'BAAI/bge-small-zh-v1.5')),
    kb_embed_base_url:      (document.getElementById('kbEmbedBaseUrl')?.value || '').trim(),
    kb_embed_api_key:       embedCurrentKey,
    kb_embed_api_keys:      embedKeyMap,
    kb_embed_models_map:    embedModelMap,
    theme: _appearance.theme,
    bg_type: _appearance.bg_type,
    bg_color: _appearance.bg_color,
    bg_gradient: _appearance.bg_gradient,
    bg_image_url: _appearance.bg_image_url,
    bg_opacity: _appearance.bg_opacity,
    bg_fit: _appearance.bg_fit,
  };
  await api().save_config(updates);
  updateModelSelector(updates.model);
  if (close) closeSettings();
}

function updateModelSelector(model) {
  const wrap  = document.getElementById('modelSelectorWrap');
  const label = document.getElementById('modelSelectorLabel');
  if (model) {
    label.textContent = model;
    wrap.style.display = '';
    document.querySelectorAll('.model-dropdown-item').forEach(el => {
      el.classList.toggle('active', el.dataset.model === model);
    });
  } else {
    wrap.style.display = 'none';
  }
}

function _populateModelDropdown(models, curModel) {
  const dd = document.getElementById('modelDropdown');
  dd.innerHTML = '';
  if (!models.length) { dd.innerHTML = '<div class="model-dropdown-empty">暂无可用模型</div>'; return; }
  for (const m of models) {
    const el = document.createElement('div');
    el.className   = 'model-dropdown-item' + (m === curModel ? ' active' : '');
    el.dataset.model = m;
    el.textContent  = m;
    el.onclick = () => {
      const pid = document.getElementById('sPickedProvider')?.value || 'deepseek';
      _pickFromPicker(pid, m);
      api().save_config({ model: m });
      closeModelDropdown();
    };
    dd.appendChild(el);
  }
}

function toggleModelDropdown(e) {
  e.stopPropagation();
  document.getElementById('modelDropdown').classList.toggle('open');
}

function closeModelDropdown() {
  document.getElementById('modelDropdown').classList.remove('open');
}

// Close pickers when clicking outside
document.addEventListener('click', () => { closeModelDropdown(); closeModelPicker(); closeSepPicker(); closeEmbedModelPicker(); });

