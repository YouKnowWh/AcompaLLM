/* ── Textarea auto-resize + keyboard shortcut ─────────────────────────────── */
const msgInput = document.getElementById('msgInput');
msgInput.addEventListener('input', () => {
  msgInput.style.height = 'auto';
  msgInput.style.height = Math.min(msgInput.scrollHeight, 200) + 'px';
});

function _attachmentLabel(att) {
  const name = att?.name || '附件';
  const size = Number(att?.size || 0);
  if (!size) return name;
  const kb = Math.max(1, Math.round(size / 1024));
  return `${name} (${kb}KB)`;
}

function _attachmentImageSrc(att) {
  if (!att || att.kind !== 'image') return '';
  if (att.data_url) return att.data_url;
  const p = String(att.path || '').trim();
  if (!p) return '';
  const norm = p.replace(/\\/g, '/');
  if (/^[a-zA-Z]:\//.test(norm)) return `file:///${norm}`;
  if (norm.startsWith('/')) return `file://${norm}`;
  return '';
}

function renderPendingAttachments() {
  const box = document.getElementById('attachList');
  if (!box) return;
  const items = S.pendingAttachments || [];
  if (!items.length) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  box.hidden = false;
  box.innerHTML = items.map((att, idx) => {
    const thumb = _attachmentImageSrc(att)
      ? `<img class="attach-chip-thumb" src="${esc(_attachmentImageSrc(att))}" alt="preview" />`
      : `<span>${att.kind === 'image' ? '🖼️' : '📎'}</span>`;
    return `<span class="attach-chip" title="${esc(att.name || '附件')}">
      ${thumb}
      <span class="attach-chip-name">${esc(_attachmentLabel(att))}</span>
      <button class="attach-chip-del" onclick="removePendingAttachment(${idx})">×</button>
    </span>`;
  }).join('');
}

function removePendingAttachment(idx) {
  if (!Array.isArray(S.pendingAttachments)) S.pendingAttachments = [];
  if (idx < 0 || idx >= S.pendingAttachments.length) return;
  S.pendingAttachments.splice(idx, 1);
  renderPendingAttachments();
}

function clearPendingAttachments() {
  S.pendingAttachments = [];
  renderPendingAttachments();
}

function getPendingAttachments() {
  return Array.isArray(S.pendingAttachments) ? [...S.pendingAttachments] : [];
}

function setPendingAttachments(items) {
  S.pendingAttachments = Array.isArray(items) ? [...items] : [];
  renderPendingAttachments();
}

async function pickAttachments() {
  const paths = await api()?.open_attach_dialog?.();
  if (!paths || !paths.length) return;
  if (!Array.isArray(S.pendingAttachments)) S.pendingAttachments = [];
  for (const p of paths) {
    if (!p) continue;
    const name = String(p).split(/[\\/]/).pop() || '附件';
    const lower = name.toLowerCase();
    const isImage = /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(lower);
    if (S.pendingAttachments.some(a => a.path && a.path === p)) continue;
    const _item = {
      kind: isImage ? 'image' : 'file',
      name,
      path: p,
      mime: isImage ? 'image/*' : 'application/octet-stream',
      size: 0,
      data_url: '',
    };
    if (typeof api()?.get_attachment_preview === 'function') {
      try {
        const meta = await api().get_attachment_preview(p);
        if (meta?.ok) {
          _item.name = meta.name || _item.name;
          _item.size = Number(meta.size || 0);
          _item.mime = meta.mime || _item.mime;
          _item.data_url = meta.data_url || '';
        }
      } catch (_) {}
    }
    S.pendingAttachments.push(_item);
  }
  renderPendingAttachments();
}

async function _addPastedImage(file) {
  const maxSize = 2 * 1024 * 1024;
  if (!file || !file.type.startsWith('image/')) return;
  if (file.size > maxSize) return;
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  if (!dataUrl) return;
  if (!Array.isArray(S.pendingAttachments)) S.pendingAttachments = [];
  S.pendingAttachments.push({
    kind: 'image',
    name: file.name || `pasted_${Date.now()}.png`,
    mime: file.type,
    size: file.size || 0,
    data_url: dataUrl,
  });
  renderPendingAttachments();
}

msgInput.addEventListener('paste', async e => {
  const items = Array.from(e.clipboardData?.items || []);
  const imageItem = items.find(it => it.kind === 'file' && String(it.type || '').startsWith('image/'));
  if (!imageItem) return;
  const f = imageItem.getAsFile();
  if (!f) return;
  e.preventDefault();
  try { await _addPastedImage(f); } catch (_) {}
});

msgInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault();
    sendMessage();
  }
  // Shift+Enter：浏览器默认行为（插入换行），不拦截
});

renderPendingAttachments();

