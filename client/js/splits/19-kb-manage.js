/* ── 知识库管理 ───────────────────────────────────────────────────────────── */
let _kbTargetName = null;   // null=新建  string=追加到已有集合
let _kbEmbedModel  = 'BAAI/bge-small-zh-v1.5';
let _kbFiles       = [];    // [{path, status:'pending'|'loading'|'done'|'error', chunks, addedAt, error}]

function kbShowAddForm(colName, evt) {
  if (evt) evt.stopPropagation();
  _kbTargetName = colName;
  _kbFiles = [];
  kbRenderFileList();
  document.getElementById('kbAlert').innerHTML = '';
  document.getElementById('kbUrl').value = '';
  const title = document.getElementById('kbAddFormTitle');
  if (colName) {
    title.textContent = `导入文档到"${colName}"`;
    document.getElementById('kbStep1').hidden = true;
    document.getElementById('kbStep2').hidden = false;
  } else {
    title.textContent = '新建知识库';
    document.getElementById('kbAddName').value = '';
    _kbEmbedModel = (typeof getSelectedEmbedModel === 'function' ? getSelectedEmbedModel() : (document.getElementById('kbEmbedModel').value || 'BAAI/bge-small-zh-v1.5'));
    document.getElementById('kbStep1Alert').innerHTML = '';
    document.getElementById('kbStep1').hidden = false;
    document.getElementById('kbStep2').hidden = true;
  }
  const form = document.getElementById('kbAddForm');
  form.hidden = false;
  form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function kbNextStep() {
  const name    = document.getElementById('kbAddName').value.trim();
  const alertEl = document.getElementById('kbStep1Alert');
  if (!name) {
    alertEl.innerHTML = `<span style="color:var(--danger)">请填写知识库名称</span>`;
    return;
  }
  alertEl.innerHTML = '';
  _kbEmbedModel = (typeof getSelectedEmbedModel === 'function' ? getSelectedEmbedModel() : document.getElementById('kbEmbedModel').value);
  document.getElementById('kbAddFormTitle').textContent = `导入文档到"${name}"`;
  document.getElementById('kbStep1').hidden = true;
  document.getElementById('kbStep2').hidden = false;
}

function kbHideAddForm() {
  document.getElementById('kbAddForm').hidden = true;
  _kbTargetName = null;
  _kbFiles = [];
}

/* 拖拽区事件 */
function kbDragOver(e) {
  e.preventDefault();
  document.getElementById('kbDropZone').classList.add('drag-over');
}
function kbDragLeave(e) {
  document.getElementById('kbDropZone').classList.remove('drag-over');
}
function kbDrop(e) {
  e.preventDefault();
  document.getElementById('kbDropZone').classList.remove('drag-over');
  const files   = Array.from(e.dataTransfer.files || []);
  const allowed = ['.txt', '.md', '.pdf', '.docx'];
  files.forEach(f => {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
    if (allowed.includes(ext) && f.path && !_kbFiles.some(x => x.path === f.path)) {
      _kbFiles.push({ path: f.path, status: 'pending', chunks: 0, addedAt: '', error: '' });
    }
  });
  kbRenderFileList();
  _kbAutoFillName();
}

/* 点击区域 → 打开文件对话框 */
async function kbClickDrop() {
  const paths = await api().open_file_dialog();
  if (!paths || paths.length === 0) return;
  paths.forEach(p => {
    if (p && !_kbFiles.some(x => x.path === p))
      _kbFiles.push({ path: p, status: 'pending', chunks: 0, addedAt: '', error: '' });
  });
  kbRenderFileList();
  _kbAutoFillName();
}

function _kbAutoFillName() {
  if (_kbTargetName) return;
  const nameEl = document.getElementById('kbAddName');
  if (nameEl && !nameEl.value.trim() && _kbFiles.length) {
    const base = _kbFiles[0].path.split(/[/\\]/).pop() || '';
    nameEl.value = base.replace(/\.[^.]+$/, '');
  }
}

function kbRenderFileList() {
  const el = document.getElementById('kbFileList');
  if (!el) return;
  if (_kbFiles.length === 0) { el.innerHTML = ''; return; }
  el.innerHTML = _kbFiles.map((f, i) => {
    const name      = f.path.split(/[/\\]/).pop() || f.path;
    const isDone    = f.status === 'done';
    const isLoading = f.status === 'loading';
    const isError   = f.status === 'error';
    const icon      = isDone    ? `<span style="color:var(--green,#2da44e)">&#10003;</span>`
                    : isError   ? `<span style="color:var(--danger)">&#9888;</span>`
                    : '&#128196;';
    const delBtn    = f.status === 'pending'
      ? `<button class="kb-file-row-del" onclick="kbRemoveFile(${i})" title="移除">&times;</button>` : '';
    let below = '';
    if (isLoading) {
      below = `<div class="kb-file-bar"><div class="kb-file-bar-fill loading"></div></div>`;
    } else if (isDone) {
      below = `<div class="kb-file-bar"><div class="kb-file-bar-fill done"></div></div>
               <div class="kb-file-meta">${esc(f.addedAt)} &middot; ${f.chunks} 块</div>`;
    } else if (isError) {
      below = `<div class="kb-file-err">&#10007; ${esc(f.error)}</div>`;
    }
    return `<div class="kb-file-row">
      <div class="kb-file-row-top">
        <span style="font-size:13px;flex-shrink:0">${icon}</span>
        <span class="kb-file-row-name" title="${esc(f.path)}">${esc(name)}</span>
        ${delBtn}
      </div>${below}
    </div>`;
  }).join('');
}

function kbRemoveFile(idx) {
  _kbFiles.splice(idx, 1);
  kbRenderFileList();
}

async function kbIngest() {
  const al   = document.getElementById('kbAlert');
  const btn  = document.getElementById('kbIngestBtn');
  const name = _kbTargetName || document.getElementById('kbAddName').value.trim();
  const url  = document.getElementById('kbUrl').value.trim();
  const embedModel = _kbTargetName ? '' : _kbEmbedModel;
  if (!name && !url) { al.innerHTML = `<span style="color:var(--danger)">请填写知识库名称</span>`; return; }
  if (!_kbFiles.length && !url) { al.innerHTML = `<span style="color:var(--danger)">请选择文件或填写 URL</span>`; return; }
  let allOk = true, totalChunks = 0, kbName = name;
  try {
    btn.disabled = true;
    al.innerHTML = '';
    for (let i = 0; i < _kbFiles.length; i++) {
      if (_kbFiles[i].status !== 'pending') continue;
      _kbFiles[i].status = 'loading';
      kbRenderFileList();
      const res = await api().kb_ingest_file(_kbFiles[i].path, name, embedModel);
      if (res.ok) {
        _kbFiles[i].status  = 'done';
        _kbFiles[i].chunks  = res.chunks || 0;
        _kbFiles[i].addedAt = res.added_at || new Date().toLocaleDateString('zh-CN');
        totalChunks += _kbFiles[i].chunks;
        kbName = res.name;
      } else {
        _kbFiles[i].status = 'error';
        _kbFiles[i].error  = res.error || '导入失败';
        allOk = false;
      }
      kbRenderFileList();
    }
    // URL 导入
    if (url) {
      al.innerHTML = '&#9203; 正在导入 URL…';
      const res = await api().kb_ingest_url(url, name || url, embedModel);
      if (res.ok) { totalChunks += (res.chunks || 0); kbName = res.name; }
      else { allOk = false; al.innerHTML = `<span style="color:var(--danger)">✗ ${esc(res.error)}</span>`; }
    }
    if (allOk) {
      al.innerHTML = totalChunks > 0
        ? `<span style="color:var(--green,#2da44e)">✓ 全部导入完成，共 ${totalChunks} 块</span>`
        : `<span style="color:var(--green,#2da44e)">✓ 导入完成</span>`;
      if (url) document.getElementById('kbUrl').value = '';
      await kbLoadList();
      if (kbName) {
        const sid  = kbSafeId(kbName);
        const body = document.getElementById('kbBody_' + sid);
        const hd   = document.querySelector(`#kbCard_${sid} .kb-card-hd`);
        if (body && body.hidden) { body.hidden = false; if (hd) hd.classList.add('open'); }
        if (body) await kbLoadSources(kbName, sid);
      }
    }
  } catch(e) {
    al.innerHTML = `<span style="color:var(--danger)">✗ ${esc(String(e))}</span>`;
  } finally {
    btn.disabled = false;
  }
}

function kbSafeId(name) {
  return name.replace(/[^a-zA-Z0-9]/g, '_');
}

