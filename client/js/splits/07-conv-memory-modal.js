/* ── 对话设置弹窗——记忆人内联管理 ──────────────────────────────────────────── */
function csMemPersonChanged() {
  const val = document.getElementById('csMemPerson').value;
  const btn = document.getElementById('csMemDeleteBtn');
  if (btn) btn.style.display = val ? '' : 'none';
}

function csMemToggleCreate() {
  const box = document.getElementById('csMemCreateInline');
  if (!box) return;
  box.hidden = !box.hidden;
  if (!box.hidden) {
    document.getElementById('csMemNewName').value = '';
    document.getElementById('csMemCreateAlert').textContent = '';
    document.getElementById('csMemNewName').focus();
  }
}

async function csMemCreate() {
  const nameEl  = document.getElementById('csMemNewName');
  const alertEl = document.getElementById('csMemCreateAlert');
  const name = nameEl.value.trim();
  if (!name) { alertEl.textContent = '请输入名称'; alertEl.style.color = 'var(--danger)'; return; }
  alertEl.textContent = '创建中…'; alertEl.style.color = 'var(--muted)';
  try {
    const res = await api().mem_create_person(name);
    if (!res?.ok) { alertEl.textContent = '创建失败'; alertEl.style.color = 'var(--danger)'; return; }
    // 刷新下拉并选中新记忆人
    const sel = document.getElementById('csMemPerson');
    const opt = document.createElement('option');
    opt.value = name; opt.textContent = name;
    sel.appendChild(opt);
    sel.value = name;
    csMemPersonChanged();
    document.getElementById('csMemCreateInline').hidden = true;
  } catch(e) {
    alertEl.textContent = String(e); alertEl.style.color = 'var(--danger)';
  }
}

async function csMemDeleteCurrent() {
  const sel  = document.getElementById('csMemPerson');
  const name = sel.value;
  if (!name) return;
  if (!confirm(`确定删除记忆人「${name}」及其全部记忆？此操作无法撤销。`)) return;
  try {
    await api().mem_delete_person(name);
    // 移除下拉里的选项
    const opt = sel.querySelector(`option[value="${CSS.escape(name)}"]`);
    if (opt) opt.remove();
    sel.value = '';
    csMemPersonChanged();
  } catch(e) {
    alert(String(e));
  }
}

async function deleteConvFromModal() {
  const id = S._editingConvId;
  if (!id) return;
  if (!confirm('确定删除这条对话？此操作无法撤销。')) return;
  closeConvSettings();
  await api().delete_conversation(id);
  S.convs = S.convs.filter(c => c.id !== id);
  renderConvList();
  if (id === S.convId) {
    S.convId = null;
    clearMsgList();
    showEmpty(true);
    updateTitle('AcompaLLM');
    document.getElementById('headerClearBtn').hidden = true;
  }
}

async function clearCurrentConv() {
  if (!S.convId || !confirm('确定清空当前对话内容？')) return;
  await api().clear_conversation(S.convId);
  clearMsgList();
  showEmpty(true);
  const conv = S.convs.find(c => c.id === S.convId);
  if (conv) { conv.message_count = 0; }
  renderConvList();
}

