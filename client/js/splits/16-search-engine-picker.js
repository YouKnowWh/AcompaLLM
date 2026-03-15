/* ── Search engine picker ─────────────────────────────────────────────────── */
const SEARCH_ENGINES = {
  ddg:    { name: 'DuckDuckGo', color: '#ff6900', keyId: null,         hint: '免费，无需配置',       rowId: null },
  tavily: { name: 'Tavily',     color: '#0088cc', keyId: 'sTavilyKey', hint: '需要 API Key',        rowId: 'seKeyTavily' },
  bing:   { name: 'Bing',       color: '#008373', keyId: 'sBingKey',   hint: '需要 Azure Key',      rowId: 'seKeyBing' },
  brave:  { name: 'Brave',      color: '#FB542B', keyId: 'sBraveKey',  hint: '需要 API Key',        rowId: 'seKeyBrave' },
  serp:   { name: 'SerpAPI',    color: '#f6c544', keyId: 'sSerpKey',   hint: '需要 API Key',        rowId: 'seKeySerp' },
};

function _buildSepList() {
  const list   = document.getElementById('sepList');
  const curVal = document.getElementById('sWebEngine').value;
  list.innerHTML = '';
  for (const [id, eng] of Object.entries(SEARCH_ENGINES)) {
    const row = document.createElement('div');
    row.className = 'mp-provider-row' + (id === curVal ? ' active' : '');
    row.innerHTML = `<span class="mp-provider-hdr-dot" style="background:${eng.color}"></span>`
                  + `<span>${eng.name}</span>`
                  + `<span class="mp-row-hint">${eng.hint}</span>`;
    row.onclick = () => _pickEngine(id);
    list.appendChild(row);
  }
}

function _pickEngine(id) {
  const eng = SEARCH_ENGINES[id];
  if (!eng) return;
  document.getElementById('sWebEngine').value       = id;
  document.getElementById('sepDot').style.background = eng.color;
  document.getElementById('sepLabel').textContent    = eng.name;
  // show/hide key rows
  for (const [eid, e] of Object.entries(SEARCH_ENGINES)) {
    if (e.rowId) document.getElementById(e.rowId).style.display = eid === id ? '' : 'none';
  }
  closeSepPicker();
}

function toggleSepPicker(e) {
  e.stopPropagation();
  const panel   = document.getElementById('sepPanel');
  const trigger = document.getElementById('sepTrigger');
  if (panel.classList.contains('open')) {
    closeSepPicker();
  } else {
    _buildSepList();
    panel.classList.add('open');
    trigger.classList.add('open');
    setTimeout(() => document.getElementById('sepList').scrollTop = 0, 10);
  }
}

function closeSepPicker() {
  document.getElementById('sepPanel')?.classList.remove('open');
  document.getElementById('sepTrigger')?.classList.remove('open');
}

function _pickFromPicker(pid, model) {
  _rememberCurrentProviderApiKey();
  const cached = Array.isArray(_fetchedModels[pid]) ? _fetchedModels[pid] : [];
  const preset = Array.isArray(PRESET_MODELS[pid]) ? PRESET_MODELS[pid] : [];
  const modelList = cached.length ? cached : preset;
  const pickedModel = model || modelList[0] || '';
  document.getElementById('sModel').value          = pickedModel;
  document.getElementById('sPickedProvider').value = pid;
  _setPickerDisplay(pid, pickedModel);
  _onProviderChange(pid);
  _populateModelDropdown(modelList, pickedModel);
  updateModelSelector(pickedModel);
  closeModelPicker();
}

function toggleModelPicker(e) {
  e.stopPropagation();
  const panel   = document.getElementById('mpPanel');
  const trigger = document.getElementById('mpTrigger');
  if (panel.classList.contains('open')) {
    closeModelPicker();
  } else {
    _buildPickerList();
    panel.classList.add('open');
    trigger.classList.add('open');
    setTimeout(() => document.getElementById('mpList').scrollTop = 0, 10);
  }
}

function closeModelPicker() {
  document.getElementById('mpPanel')?.classList.remove('open');
  document.getElementById('mpTrigger')?.classList.remove('open');
}

