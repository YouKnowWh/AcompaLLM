/* ── Conversation list ───────────────────────────────────────────────────── */
async function loadConvs() {
  const convs = await api().list_conversations();
  S.convs = convs;
  renderConvList();
}

function renderConvList() {
  const el = document.getElementById('convList');
  if (!S.convs.length) {
    el.innerHTML = '<div style="padding:16px;text-align:center;font-size:12px;color:var(--muted)">暂无对话，点击 + 新建</div>';
    return;
  }

  // Group by date
  const today    = new Date(); today.setHours(0,0,0,0);
  const yday     = new Date(today); yday.setDate(yday.getDate()-1);
  const week     = new Date(today); week.setDate(week.getDate()-7);

  const groups = { '今天':[], '昨天':[], '最近 7 天':[], '更早':[] };
  for (const c of S.convs) {
    const d = new Date(c.updated_at * 1000); d.setHours(0,0,0,0);
    if (d >= today)       groups['今天'].push(c);
    else if (d >= yday)   groups['昨天'].push(c);
    else if (d >= week)   groups['最近 7 天'].push(c);
    else                  groups['更早'].push(c);
  }

  let html = '';
  for (const [label, items] of Object.entries(groups)) {
    if (!items.length) continue;
    html += `<div class="conv-section-label">${label}</div>`;
    for (const c of items) {
      const active = c.id === S.convId ? ' active' : '';
      html += `
        <div class="conv-item${active}" data-id="${esc(c.id)}" onclick="selectConv('${esc(c.id)}')">
          <div class="conv-info">
            <div class="conv-title">${esc(c.title)}</div>
            <div class="conv-date">${relativeTime(c.updated_at)}</div>
          </div>
          <div class="conv-actions">
            <button title="对话设置" onclick="openConvSettings(event,'${esc(c.id)}')">⚙</button>
          </div>
        </div>`;
    }
  }
  el.innerHTML = html;
}

async function newChat() {
  const conv = await api().new_conversation();
  S.convs.unshift(conv);
  renderConvList();
  await selectConv(conv.id, true);
}

async function selectConv(id, isNew=false) {
  if (S.streaming && id !== S.convId) {
    api().cancel_stream(S.convId);
    S.streaming = false;
  }
  S.convId = id;

  // Highlight active
  document.querySelectorAll('.conv-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === id);
  });

  if (isNew) {
    clearMsgList();
    showEmpty(true);
    document.getElementById('headerClearBtn').hidden = true;
    updateTitle('新对话');
    return;
  }

  const conv = await api().get_conversation(id);
  if (!conv) return;
  updateTitle(conv.title);
  renderMessages(conv.messages || []);
  document.getElementById('headerClearBtn').hidden = false;
  // 恢复该对话绑定的知识库列表
  S.kbNames = Array.isArray(conv.kb_names) ? conv.kb_names : [];
  _updateRagBtn();
}

function updateTitle(t) {
  document.getElementById('chatTitle').textContent = t || '新对话';
}

async function openConvSettings(e, id) {
  e.stopPropagation();
  S._editingConvId = id;
  // 从后端加载新鲜对话数据
  const conv = await api().get_conversation(id);
  document.getElementById('csTitle').value        = conv?.title || '';
  document.getElementById('csSystemPrompt').value = conv?.system_prompt ?? '';
  document.getElementById('csTemp').value         = conv?.temperature != null ? conv.temperature : '';
  document.getElementById('csContextWindow').value = conv?.context_window ?? '';
  // 加载记忆人下拉选项
  const sel = document.getElementById('csMemPerson');
  sel.innerHTML = '<option value="">不使用记忆库</option>';
  try {
    const persons = await api().mem_list_persons();
    for (const p of (persons || [])) {
      const opt = document.createElement('option');
      opt.value = p.name;
      opt.textContent = `${p.name}  (片段: ${p.count})`;
      sel.appendChild(opt);
    }
  } catch(e) {}
  sel.value = conv?.memory_person || '';
  document.getElementById('convSettingsModal').style.display = 'flex';
  // 初始化删除按钮显隐 & 关闭内联新建表单
  csMemPersonChanged();
  const inlineBox = document.getElementById('csMemCreateInline');
  if (inlineBox) inlineBox.hidden = true;
}

function closeConvSettings() {
  document.getElementById('convSettingsModal').style.display = 'none';
  S._editingConvId = null;
}

async function saveConvSettings() {
  const id = S._editingConvId;
  if (!id) return;
  const title         = document.getElementById('csTitle').value.trim();
  const sysPrompt     = document.getElementById('csSystemPrompt').value;
  const tempStr       = document.getElementById('csTemp').value.trim();
  const temperature   = tempStr !== '' ? parseFloat(tempStr) : null;
  const memPerson     = document.getElementById('csMemPerson').value;
  const cwStr         = document.getElementById('csContextWindow').value.trim();
  const contextWindow = cwStr !== '' ? parseInt(cwStr, 10) : null;

  await api().update_conversation(id, {
    title, system_prompt: sysPrompt, temperature,
    memory_person: memPerson,
    context_window: contextWindow,
  });

  const conv = S.convs.find(c => c.id === id);
  if (conv) {
    if (title) conv.title = title;
    conv.system_prompt  = sysPrompt;
    conv.temperature    = temperature;
    conv.memory_person  = memPerson;
    conv.context_window = contextWindow;
  }
  renderConvList();
  if (id === S.convId) updateTitle(title || conv?.title || '');
  closeConvSettings();
}

