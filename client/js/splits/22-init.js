/* ── Init ─────────────────────────────────────────────────────────────────── */
async function init() {
  selectSidebarPanel('conv');
  setupMarked();
  await loadConvs();
  const cfg = await api().get_config();
  _fetchedModels = {};
  for (const [pid, arr] of Object.entries(cfg.upstream_models_map || {})) {
    if (!Array.isArray(arr)) continue;
    const cleaned = Array.from(new Set(arr.map(x => String(x || '').trim()).filter(Boolean)));
    if (cleaned.length) _fetchedModels[String(pid || '').trim()] = cleaned;
  }
  updateModelSelector(cfg.model || '');
  _setPickerDisplay(cfg.provider || 'deepseek', cfg.model || 'deepseek-chat');
  _onProviderChange(cfg.provider || 'deepseek');
  {
    const _provider = cfg.provider || 'deepseek';
    const _model = cfg.model || '';
    const _cached = Array.isArray(_fetchedModels[_provider]) ? _fetchedModels[_provider] : [];
    const _preset = Array.isArray(PRESET_MODELS[_provider]) ? PRESET_MODELS[_provider] : [];
    const _models = _cached.length ? _cached.slice() : _preset.slice();
    if (_model && !_models.includes(_model)) _models.unshift(_model);
    _populateModelDropdown(_models, _model);
  }
  setEmbedModelPicker(
    cfg.kb_embed_provider || 'default',
    cfg.kb_embed_model || 'BAAI/bge-small-zh-v1.5',
    cfg.kb_embed_base_url || '',
    cfg.kb_embed_api_key || '',
  );
  _loadAppearanceFromConfig(cfg);
  _applyAppearanceToApp();
  // 恢复工具按钮持久化状态
  for (const id of ['webSearchBtn', 'deepThinkBtn']) {
    const btn = document.getElementById(id);
    if (!btn) continue;
    const saved = localStorage.getItem('toolActive_' + id);
    if (saved === '1') btn.classList.add('active');
    else if (saved === '0') btn.classList.remove('active');
  }
  // Auto-select most recent conversation
  if (S.convs.length) {
    await selectConv(S.convs[0].id);
  }
}

