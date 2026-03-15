/* ── Model picker panel ───────────────────────────────────────────────────── */
function _buildPickerList() {
  const list = document.getElementById('mpList');
  list.innerHTML = '';
  const curPid = document.getElementById('sPickedProvider').value;
  const curModel = document.getElementById('sModel').value || '';
  for (const [pid] of Object.entries(PRESET_MODELS)) {
    const row = document.createElement('div');
    row.className = 'mp-provider-row' + (pid === curPid ? ' active' : '');
    row.innerHTML = `<span class="mp-provider-hdr-dot" style="background:${PROVIDER_COLORS[pid]}"></span>`
                  + `<span>${PROVIDER_NAMES[pid]}</span>`;
    row.onclick = () => {
      const cached = Array.isArray(_fetchedModels[pid]) ? _fetchedModels[pid] : [];
      const preset = Array.isArray(PRESET_MODELS[pid]) ? PRESET_MODELS[pid] : [];
      const modelList = cached.length ? cached : preset;
      const firstModel = (pid === curPid && curModel)
        ? curModel
        : (modelList[0] || '');
      _pickFromPicker(pid, firstModel);
      _populateModelDropdown(modelList, firstModel);
    };
    list.appendChild(row);
  }
}

function filterModels() {} // no-op kept for compat

