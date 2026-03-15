/* ── KB selector popup ─────────────────────────────────────────────────────── */
function _updateRagBtn() {
  const btn = document.getElementById('ragBtn');
  if (!btn) return;
  btn.classList.toggle('active', S.kbNames.length > 0);
  btn.title = S.kbNames.length
    ? `知识库检索（已绑定：${S.kbNames.join('、')}）`
    : '知识库检索';
}

async function kbSelectorToggle() {
  const popup = document.getElementById('kbSelectorPopup');
  if (popup.style.display !== 'none') { kbSelectorClose(); return; }

  // 加载所有知识库
  const list = document.getElementById('kbSelList');
  list.innerHTML = '<div class="kb-sel-empty">加载中…</div>';
  popup.style.display = 'block';

  const cols = await api().kb_list();
  if (!cols || !cols.length) {
    list.innerHTML = '<div class="kb-sel-empty">暂无知识库，请先在设置中导入文档</div>';
    return;
  }
  list.innerHTML = cols.map(c => {
    const checked = S.kbNames.includes(c.display_name) ? 'checked' : '';
    const nm = esc(c.display_name);
    return `<label class="kb-sel-item">
      <input type="checkbox" value="${nm}" ${checked}>
      <span title="${nm}">${nm}</span>
      <span style="color:var(--muted);font-size:11px;margin-left:auto;flex-shrink:0">${c.count}块</span>
    </label>`;
  }).join('');

  // 点击外部关闭
  setTimeout(() => document.addEventListener('click', _kbSelOutside, { once: true }), 0);
}

function _kbSelOutside(e) {
  const popup = document.getElementById('kbSelectorPopup');
  if (popup && !popup.contains(e.target) && e.target.id !== 'ragBtn') {
    popup.style.display = 'none';
  }
}

function kbSelectorClose() {
  document.getElementById('kbSelectorPopup').style.display = 'none';
  document.removeEventListener('click', _kbSelOutside);
}

async function kbSelectorSave() {
  const checks = document.querySelectorAll('#kbSelList input[type=checkbox]:checked');
  S.kbNames = Array.from(checks).map(c => c.value);
  kbSelectorClose();
  _updateRagBtn();
  // 持久化到对话 JSON（对话不存在时跳过，发消息时会随 options 传入）
  if (S.convId) {
    await api().conv_set_kb_names(S.convId, S.kbNames);
  }
}

function insertTip(el) {
  document.getElementById('msgInput').value = el.textContent;
  document.getElementById('msgInput').focus();
}

