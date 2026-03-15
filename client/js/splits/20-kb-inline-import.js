/* ── 内联导入区（卡片体内嵌） ─────────────────────────────────────────────── */
const _kbInlineFiles = new Map(); // sid → [{path,status,chunks,addedAt,error}]

function kbToggleInline(sid, name, btnEl) {
  if (btnEl) btnEl.blur();
  const el = document.getElementById('kbInline_' + sid);
  if (!el) return;
  const opening = el.hidden;
  el.hidden = !el.hidden;
  if (opening) {
    _kbInlineFiles.set(sid, []);
    kbInlineRenderFiles(sid);
    const al = document.getElementById('kbInlineAlert_' + sid);
    if (al) al.innerHTML = '';
  }
}

function kbHideInline(sid) {
  const el = document.getElementById('kbInline_' + sid);
  if (el) el.hidden = true;
  _kbInlineFiles.delete(sid);
}

function kbInlineDragOver(e, sid) {
  e.preventDefault();
  document.getElementById('kbInlineDrop_' + sid)?.classList.add('drag-over');
}

function kbInlineDragLeave(e, sid) {
  document.getElementById('kbInlineDrop_' + sid)?.classList.remove('drag-over');
}

async function kbInlineDrop(e, sid, name) {
  e.preventDefault();
  document.getElementById('kbInlineDrop_' + sid)?.classList.remove('drag-over');

  // 第一级：标准 dataTransfer.files
  let fileArr = Array.from(e.dataTransfer.files || []);
  // 第二级：部分 WebView2 版本 files 为空但 items 有内容
  if (!fileArr.length && e.dataTransfer.items) {
    fileArr = Array.from(e.dataTransfer.items)
      .filter(i => i.kind === 'file')
      .map(i => i.getAsFile())
      .filter(Boolean);
  }
  // 第三级：WebView2 完全拦截外部拖拽 → 自动弹出原生文件选择框
  if (!fileArr.length) {
    await kbInlineClickDrop(sid, name);
    return;
  }

  const allowed = ['.txt', '.md', '.pdf', '.docx'];
  const cur     = _kbInlineFiles.get(sid) || [];
  fileArr.forEach(f => {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
    if (!allowed.includes(ext)) return;
    // f.path 在 WebView2 中可能为 undefined（浏览器安全限制），退回到 f.name 作 key
    const key = f.path || f.name;
    if (!cur.some(x => x.path === key))
      cur.unshift({ path: key, _fileObj: f.path ? null : f,
                   status: 'pending', chunks: 0, addedAt: '', error: '', done: 0, total: 0 });
  });
  _kbInlineFiles.set(sid, cur);
  kbInlineRenderFiles(sid);
}

async function kbInlineClickDrop(sid, name) {
  const paths = await api()?.open_file_dialog();
  if (!paths || paths.length === 0) return;
  const cur = _kbInlineFiles.get(sid) || [];
  paths.forEach(p => {
    if (p && !cur.some(x => x.path === p))
      cur.unshift({ path: p, status: 'pending', chunks: 0, addedAt: '', error: '', done: 0, total: 0 });
  });
  _kbInlineFiles.set(sid, cur);
  kbInlineRenderFiles(sid);
}

function kbInlineRenderFiles(sid) {
  const el = document.getElementById('kbInlineFileList_' + sid);
  if (!el) return;
  const files = _kbInlineFiles.get(sid) || [];
  if (files.length === 0) { el.innerHTML = ''; return; }
  el.innerHTML = files.map((f, i) => {
    const fname = f.path.split(/[\/\\]/).pop() || f.path;
    let below = '';
    if (f.status === 'loading') {
      if (f.total > 0) {
        const pct = Math.round(f.done / f.total * 100);
        below = `<div class="kb-file-bar"><div class="kb-file-bar-fill" style="width:${pct}%;background:var(--accent);transition:width .25s"></div></div>
                 <div class="kb-file-meta">${f.done} / ${f.total} 块</div>`;
      } else {
        below = `<div class="kb-file-bar"><div class="kb-file-bar-fill loading"></div></div>`;
      }
    } else if (f.status === 'done')
      below = `<div class="kb-file-bar"><div class="kb-file-bar-fill done"></div></div>
               <div class="kb-file-meta">${esc(f.addedAt)} &middot; ${f.chunks} 块</div>`;
    else if (f.status === 'error')
      below = `<div class="kb-file-err">&#10007; ${esc(f.error)}</div>`;
    const delBtn = f.status !== 'loading'
      ? `<button class="kb-file-row-del" onclick="kbInlineRemoveFile('${sid}',${i})" title="移除">&times;</button>` : '';
    return `<div class="kb-file-row">
      <div class="kb-file-row-top">
        ${delBtn}
        <span class="kb-file-row-name" title="${esc(f.path)}">${esc(fname)}</span>
      </div>${below}</div>`;
  }).join('');
}

function kbInlineRemoveFile(sid, idx) {
  const cur = _kbInlineFiles.get(sid) || [];
  cur.splice(idx, 1);
  _kbInlineFiles.set(sid, cur);
  kbInlineRenderFiles(sid);
}

function _fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload  = () => resolve(r.result.split(',')[1]);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

async function kbInlineIngest(sid, name) {
  const al  = document.getElementById('kbInlineAlert_' + sid);
  const btn = document.getElementById('kbInlineIngestBtn_' + sid);
  const files = _kbInlineFiles.get(sid) || [];
  if (!files.length) {
    if (al) al.innerHTML = '<span style="color:var(--danger)">请选择文件</span>';
    return;
  }
  let allOk = true, totalChunks = 0;
  try {
    if (btn) btn.disabled = true;
    if (al) al.innerHTML = '';
    for (let i = 0; i < files.length; i++) {
      if (files[i].status !== 'pending') continue;
      files[i].status = 'loading';
      kbInlineRenderFiles(sid);
      let res;
      if (files[i]._fileObj) {
        // WebView2 不暴露 file.path —— 读取内容传给 Python 写临时文件
        const b64 = await _fileToBase64(files[i]._fileObj);
        res = await api().kb_ingest_bytes(files[i].path, b64, name, '');
      } else {
        res = await api().kb_ingest_file(files[i].path, name, '');
      }
      if (res.ok) {
        files[i].status  = 'done';
        files[i].chunks  = res.chunks || 0;
        files[i].addedAt = res.added_at || new Date().toLocaleDateString('zh-CN');
        totalChunks += files[i].chunks;
      } else {
        files[i].status = 'error';
        files[i].error  = res.error || '导入失败';
        allOk = false;
      }
      kbInlineRenderFiles(sid);
    }
    if (allOk) {
      if (al) al.innerHTML = totalChunks > 0
        ? `<span style="color:var(--green,#2da44e)">✓ 导入完成，共 ${totalChunks} 块</span>`
        : `<span style="color:var(--green,#2da44e)">✓ 导入完成</span>`;
      await kbLoadList();
      const body = document.getElementById('kbBody_' + sid);
      const hd   = document.querySelector(`#kbCard_${sid} .kb-card-hd`);
      if (body && body.hidden) { body.hidden = false; if (hd) hd.classList.add('open'); }
      if (body) await kbLoadSources(name, sid);
    }
  } catch(e) {
    if (al) al.innerHTML = `<span style="color:var(--danger)">✗ ${esc(String(e))}</span>`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

window.__onKbProgress = function(data) {
  for (const [sid, files] of _kbInlineFiles) {
    const f = files.find(x => x.path === data.path);
    if (f) { f.done = data.done; f.total = data.total; kbInlineRenderFiles(sid); break; }
  }
};

async function kbToggleCard(safeId, name, hdEl) {
  const body = document.getElementById('kbBody_' + safeId);
  if (!body) return;
  const isOpen = !body.hidden;
  body.hidden = isOpen;
  if (hdEl) hdEl.classList.toggle('open', !isOpen);
  if (!isOpen) await kbLoadSources(name, safeId);
}

async function kbLoadSources(name, safeId) {
  const body = document.getElementById('kbBody_' + safeId);
  if (!body) return;
  body.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:8px 0">加载中…</div>';
  try {
    const srcs = await api().kb_list_sources(name);
    let html = '';
    if (!srcs || srcs.length === 0) {
      html = '<div style="color:var(--muted);font-size:12px;padding:8px 0">暂无文件记录</div>';
    } else {
      const nameJsonE = esc(JSON.stringify(name));
      html = srcs.map(s => {
        const icon     = s.source && s.source.startsWith('http') ? '🌐' : '📄';
        const dispName = esc(s.name || s.source);
        const srcJsonE = esc(JSON.stringify(s.source));
        const countBadge = `<button class="kb-chunk-badge" onclick="kbShowChunks(${nameJsonE},${srcJsonE},event)">${s.count} 块</button>`;
        const meta     = [s.added_at, countBadge].filter(Boolean).join(' · ');
        return `<div class="kb-src-row">
          <span class="kb-src-icon">${icon}</span>
          <div class="kb-src-info">
            <div class="kb-src-name" title="${esc(s.source)}">${dispName}</div>
            <div class="kb-src-meta">${meta}</div>
          </div>
          <button class="kb-src-del" title="删除此来源的所有块"
            onclick="kbDeleteSrc(${nameJsonE},${srcJsonE},event)">🗑</button>
        </div>`;
      }).join('');
    }
    const nameJsonE = esc(JSON.stringify(name));
    html += `<div class="kb-card-footer">
      <button class="kb-more-btn" onclick="kbToggleInline('${safeId}',${nameJsonE},this)">↑ 导入文档</button>
      <div id="kbInline_${safeId}" hidden class="kb-inline-import">
        <div class="kb-drop-zone" id="kbInlineDrop_${safeId}"
             onclick="kbInlineClickDrop('${safeId}',${nameJsonE})"
             ondragover="kbInlineDragOver(event,'${safeId}')"
             ondragleave="kbInlineDragLeave(event,'${safeId}')"
             ondrop="kbInlineDrop(event,'${safeId}',${nameJsonE})">
          <div class="kb-drop-icon">↑</div>
          <div class="kb-drop-text">拖放文件或点击浏览</div>
          <div class="kb-drop-hint">支持 .txt .md .pdf .docx</div>
        </div>
        <div id="kbInlineFileList_${safeId}" style="margin-top:6px"></div>
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="btn-primary" id="kbInlineIngestBtn_${safeId}" onclick="kbInlineIngest('${safeId}',${nameJsonE})">开始导入</button>
          <button class="btn-secondary" onclick="kbHideInline('${safeId}')">取消</button>
        </div>
        <div id="kbInlineAlert_${safeId}" style="margin-top:6px;font-size:12px"></div>
      </div>
    </div>`;
    body.innerHTML = html;
  } catch(e) {
    body.innerHTML = `<div style="color:var(--muted);font-size:12px;padding:8px 0">${esc(String(e))}</div>`;
  }
}

function kbHideChunkModal() {
  document.getElementById('kbChunkModal').style.display = 'none';
}

async function kbShowChunks(nameJson, srcJson, evt) {
  if (evt) evt.stopPropagation();
  const name   = JSON.parse(nameJson);
  const source = JSON.parse(srcJson);
  const modal  = document.getElementById('kbChunkModal');
  const body   = document.getElementById('kbChunkModalBody');
  const title  = document.getElementById('kbChunkModalTitle');
  if (!modal) return;
  const fileName = String(source).split(/[/\\]/).pop() || source;
  title.textContent = fileName + ' — 分块预览';
  body.innerHTML = '<div style="color:var(--muted);padding:12px">加载中…</div>';
  modal.style.display = 'flex';
  try {
    const chunks = await api().kb_peek_chunks(name, source, 100);
    if (!chunks || chunks.length === 0) {
      body.innerHTML = '<div style="color:var(--muted);padding:12px">无分块数据</div>';
      return;
    }
    body.innerHTML = chunks.map((c, i) => {
      const raw     = (c.body || '');
      const preview = raw.slice(0, 100).replace(/\n/g, ' ');
      const isLong  = raw.length > 100;
      return `<div class="kb-chunk-item">
        <span class="kb-chunk-idx">#${c.chunk_index != null ? c.chunk_index : i}</span>
        <span class="kb-chunk-preview">${esc(preview)}${isLong ? '…' : ''}</span>
      </div>`;
    }).join('');
  } catch(e) {
    body.innerHTML = `<div style="color:var(--danger);padding:12px">${esc(String(e))}</div>`;
  }
}

async function kbDeleteSrc(colName, source, evt) {
  if (evt) evt.stopPropagation();
  const display = String(source).split(/[/\\]/).pop() || source;
  if (!confirm(`确认删除"${display}"的所有文本块？`)) return;
  const res = await api().kb_delete_source(colName, source);
  if (res && res.ok !== false) {
    await kbLoadList();
    const sid  = kbSafeId(colName);
    const body = document.getElementById('kbBody_' + sid);
    if (body && !body.hidden) await kbLoadSources(colName, sid);
  }
}

async function kbDeleteCol(name, evt) {
  if (evt) evt.stopPropagation();
  if (!confirm(`确认删除知识库"${name}"？此操作不可撤销。`)) return;
  const res = await api().kb_delete(name);
  if (res.ok) kbLoadList();
}

async function kbLoadList() {
  const el    = document.getElementById('kbColList');
  const empty = document.getElementById('kbEmpty');
  if (!el) return;
  try {
    const cols = await api().kb_list();
    if (!cols || cols.length === 0) {
      el.innerHTML = '';
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;
    // 记住当前展开的卡片
    const openSids = new Set(
      [...document.querySelectorAll('.kb-card-hd.open')].map(h => h.dataset.sid)
    );
    el.innerHTML = cols.map(c => {
      const sid      = kbSafeId(c.display_name);
      const safeName = esc(c.display_name);
      const nameJson = JSON.stringify(c.display_name);
      const isOpen   = openSids.has(sid);
      const nj = esc(nameJson);
      return `<div class="kb-card" id="kbCard_${sid}">
        <div class="kb-card-hd ${isOpen ? 'open' : ''}" data-sid="${sid}"
             onclick="kbToggleCard('${sid}',${nj},this)">
          <span class="kb-chevron">▶</span>
          <span class="kb-card-name" title="${safeName}">${safeName}</span>
          <span class="kb-card-badge">${c.count} 块</span>
          <div class="kb-card-acts" onclick="event.stopPropagation()">
            <button class="btn-danger" style="font-size:11px;padding:2px 8px"
              onclick="kbDeleteCol(${nj},event)">删除</button>
          </div>
        </div>
        <div class="kb-card-body" id="kbBody_${sid}" ${isOpen ? '' : 'hidden'}></div>
      </div>`;
    }).join('');
    // 重新渲染已展开的卡片内容
    for (const sid of openSids) {
      const info = cols.find(c => kbSafeId(c.display_name) === sid);
      if (info) await kbLoadSources(info.display_name, sid);
    }
  } catch(e) {
    el.innerHTML = `<div style="color:var(--muted);font-size:12px;padding:10px 0">${esc(String(e))}</div>`;
  }
}

