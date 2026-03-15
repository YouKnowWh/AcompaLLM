/* ── Settings ────────────────────────────────────────────────────────────── */
const PROVIDER_URLS = {
  deepseek: 'https://api.deepseek.com',
  openai:   'https://api.openai.com',
  gemini:   'https://generativelanguage.googleapis.com/v1beta/openai',
  qwen:     'https://dashscope.aliyuncs.com/compatible-mode/v1',
  ollama:   'http://localhost:11434/v1',
  custom:   '',
};
const PROVIDER_COLORS = {
  deepseek: '#58a6ff', openai: '#10a37f', gemini: '#8b5cf6',
  qwen: '#f0883e', ollama: '#4ec9b0', custom: '#8b949e',
};
const PROVIDER_NAMES = {
  deepseek: 'DeepSeek', openai: 'OpenAI', gemini: 'Gemini',
  qwen: '通义千问', ollama: 'Ollama', custom: '自定义',
};
const PRESET_MODELS = {
  deepseek: ['deepseek-chat', 'deepseek-reasoner'],
  openai:   ['gpt-4.1', 'gpt-4.1-mini', 'gpt-4o', 'gpt-4o-mini', 'o3', 'o3-mini', 'o4-mini'],
  gemini:   ['gemini-2.5-pro', 'gemini-2.0-flash', 'gemini-2.0-flash-lite', 'gemini-1.5-pro', 'gemini-1.5-flash'],
  qwen:     ['qwen-max', 'qwen-max-latest', 'qwen-plus', 'qwen-turbo', 'qwen-long'],
  ollama:   [],
  custom:   [],
};
let _fetchedModels = {};  // { pid: [model, ...] }
let _providerApiKeys = {};

function _rememberCurrentProviderApiKey() {
  const pid = document.getElementById('sPickedProvider')?.value;
  if (!pid || pid === 'ollama') return;
  const key = document.getElementById('sApiKey')?.value || '';
  _providerApiKeys[pid] = key.trim();
}

function _onProviderChange(pid) {
  const isOllama   = pid === 'ollama';
  const isCustom   = pid === 'custom';
  const isDeepSeek = pid === 'deepseek';
  document.getElementById('customUrlField').style.display = isCustom ? '' : 'none';
  if (!isCustom) document.getElementById('sBaseUrl').value = PROVIDER_URLS[pid] || '';
  const keyInput = document.getElementById('sApiKey');
  const keyHint  = document.getElementById('sApiKeyHint');
  keyInput.disabled = isOllama;
  keyHint.style.display = isOllama ? '' : 'none';
  keyInput.value = isOllama ? '' : (_providerApiKeys[pid] || '');
  // 深思按钮：支持原生思考模式的厂商显示
  const _THINK_VENDORS = new Set(['deepseek', 'openai', 'qwen', 'gemini']);
  const dtBtn = document.getElementById('deepThinkBtn');
  if (dtBtn) {
    dtBtn.style.display = _THINK_VENDORS.has(pid) ? '' : 'none';
    if (!_THINK_VENDORS.has(pid)) dtBtn.classList.remove('active');
  }
}

function _setPickerDisplay(pid, model) {
  document.getElementById('mpDot').style.background = PROVIDER_COLORS[pid] || '#8b949e';
  document.getElementById('mpLabel').textContent = PROVIDER_NAMES[pid] || pid;
}

async function openSettings() {
  const cfg      = await api().get_config();
  const provider = cfg.provider || 'deepseek';
  const model    = cfg.model    || 'deepseek-chat';
  document.getElementById('sModel').value          = model;
  document.getElementById('sPickedProvider').value = provider;
  _providerApiKeys = { ...(cfg.api_keys || {}) };
  _fetchedModels = {};
  for (const [pid, arr] of Object.entries(cfg.upstream_models_map || {})) {
    if (!Array.isArray(arr)) continue;
    const cleaned = Array.from(new Set(arr.map(x => String(x || '').trim()).filter(Boolean)));
    if (cleaned.length) _fetchedModels[String(pid || '').trim()] = cleaned;
  }
  if (!_providerApiKeys[provider] && cfg.api_key) {
    _providerApiKeys[provider] = cfg.api_key;
  }
  _setPickerDisplay(provider, model);
  _onProviderChange(provider);
  {
    const _cached = Array.isArray(_fetchedModels[provider]) ? _fetchedModels[provider] : [];
    const _preset = Array.isArray(PRESET_MODELS[provider]) ? PRESET_MODELS[provider] : [];
    const _models = _cached.length ? _cached.slice() : _preset.slice();
    if (model && !_models.includes(model)) _models.unshift(model);
    _populateModelDropdown(_models, model);
  }
  if (provider === 'custom') document.getElementById('sBaseUrl').value = cfg.upstream_base_url || '';
  document.getElementById('sTemp').value         = cfg.temperature ?? 0.7;
  document.getElementById('sSystemPrompt').value = cfg.system_prompt || '';
  document.getElementById('sWebSearch').value    = cfg.tool_web_search || 'auto';
  const ragToggle = document.getElementById('sRag');
  if (ragToggle) ragToggle.checked = !!cfg.tool_rag;
  const _engine = cfg.tool_web_search_engine || 'ddg';
  _pickEngine(_engine);
  document.getElementById('sTavilyKey').value    = cfg.tool_tavily_key || '';
  document.getElementById('sBingKey').value      = cfg.tool_bing_key   || '';
  document.getElementById('sBraveKey').value     = cfg.tool_brave_key  || '';
  document.getElementById('sSerpKey').value      = cfg.tool_serp_key   || '';
  if (typeof setEmbedApiKeysMap === 'function') {
    setEmbedApiKeysMap(cfg.kb_embed_api_keys || {});
  }
  if (typeof setEmbedFetchedModelsMap === 'function') {
    setEmbedFetchedModelsMap(cfg.kb_embed_models_map || {});
  }
  const _ep = cfg.kb_embed_provider || 'default';
  const _ekMap = cfg.kb_embed_api_keys || {};
  const _ek = (_ep !== 'default' ? (_ekMap[_ep] || '') : '') || cfg.kb_embed_api_key || '';
  setEmbedModelPicker(
    _ep,
    (cfg.kb_embed_model || ''),
    cfg.kb_embed_base_url || '',
    _ek,
  );
  document.getElementById('connAlert').innerHTML      = '';
  const _cat = document.getElementById('connToolAlert');
  if (_cat) _cat.innerHTML = '';
  document.getElementById('fetchModelsStatus').innerHTML = '';
  _loadAppearanceFromConfig(cfg);
  _bindAppearanceInputs();
  _updateAppearanceUI();
  _applyAppearanceToApp();
  // 默认选中第一个子菜单
  const firstNav = document.querySelector('#sbPanelSettings .sp-nav-item');
  switchSettingsTab('api', firstNav);
  selectSidebarPanel('settings');
}

function closeSettings() {
  closeModelPicker();
  closeEmbedModelPicker();
  selectSidebarPanel('conv');
}

function selectSidebarPanel(id) {
  document.getElementById('sbPanelConv').style.display     = id === 'conv'     ? '' : 'none';
  document.getElementById('sbPanelSettings').style.display = id === 'settings' ? '' : 'none';
  // main 区切换
  const sv = document.getElementById('settingsView');
  const ch = document.getElementById('chatView');
  if (id === 'settings') {
    sv.classList.add('visible');
    if (ch) ch.style.display = 'none';
  } else {
    sv.classList.remove('visible');
    if (ch) ch.style.display = '';
  }
  // 同步 icon bar 激活状态
  document.querySelectorAll('#iconBar .ib-btn[data-panel]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.panel === id);
  });
}

function switchSettingsTab(name, btn) {
  // 导航项选中状态
  document.querySelectorAll('#sbPanelSettings .sp-nav-item').forEach(b => b.classList.remove('active'));
  btn?.classList.add('active');
  // 标题
  const titles = { api: 'API 配置', tools: '工具', memory: '知识库', appearance: '外观' };
  const hdr = document.getElementById('svHeader');
  if (hdr) hdr.textContent = titles[name] || name;
  // 内容面板
  document.getElementById('tabApi').hidden        = name !== 'api';
  document.getElementById('tabTools').hidden      = name !== 'tools';
  document.getElementById('tabMemory').hidden     = name !== 'memory';
  document.getElementById('tabAppearance').hidden = name !== 'appearance';
  // 切换到知识库 tab 时自动刷新集合列表
  if (name === 'memory') kbLoadList();
  // 连接/保存按钮：api tab 才显示测试连接，memory tab 隐藏整个footer
  const footer = document.querySelector('.sv-footer');
  if (footer) footer.style.display = name === 'memory' ? 'none' : '';
  const testConnBtn = document.getElementById('testConnBtn');
  if (testConnBtn) testConnBtn.style.display = name === 'api' ? '' : 'none';
}

// 兼容旧调用名
function switchTab(name, btn) { switchSettingsTab(name, btn); }

