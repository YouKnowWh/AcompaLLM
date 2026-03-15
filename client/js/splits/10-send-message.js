/* ── Send message ────────────────────────────────────────────────────────── */
async function sendMessage() {
  const input = document.getElementById('msgInput');
  const text  = input.value.trim();
  const attachments = (typeof getPendingAttachments === 'function') ? getPendingAttachments() : [];
  if (!text && !attachments.length) return;

  if (S.streaming) {
    S._pendingSend = { text, attachments };
    input.value = '';
    input.style.height = 'auto';
    if (typeof clearPendingAttachments === 'function') clearPendingAttachments();
    cancelStream();
    return;
  }

  // Ensure conversation exists
  if (!S.convId) {
    const conv = await api().new_conversation();
    S.convs.unshift(conv);
    S.convId = conv.id;
    renderConvList();
    updateTitle(conv.title);
    showEmpty(false);
    document.getElementById('headerClearBtn').hidden = false;
  }

  input.value = '';
  input.style.height = 'auto';
  showEmpty(false);

  // Options from toolbar
  const wsActive  = document.getElementById('webSearchBtn').classList.contains('active');
  const dtBtn     = document.getElementById('deepThinkBtn');
  const deepThink = dtBtn && dtBtn.style.display !== 'none' && dtBtn.classList.contains('active');
  const options = {
    tool_web_search: wsActive ? 'true' : undefined,
    kb_names: S.kbNames.length ? S.kbNames : undefined,
    deep_think: deepThink || undefined,
    attachments: attachments.length ? attachments : undefined,
  };
  // Regen mode: skip re-saving the user message (it already exists in the conv)
  let _isRegen = false;
  if (S._regenUserMsgId) {
    options.skip_user_save       = true;
    options.existing_user_msg_id = S._regenUserMsgId;
    _isRegen = true;
    S._regenUserMsgId = null;
  }

  // Append user bubble immediately (skip in regen mode — existing bubble stays)
  if (!_isRegen) {
    const userEntry = { id: null, role: 'user', content: text, attachments };
    const userEl = buildMsgEl(userEntry);
    document.getElementById('msgList').appendChild(userEl);
    scrollBottom(true);
  }

  if (typeof clearPendingAttachments === 'function') clearPendingAttachments();

  // Lock UI
  S.streaming = true;
  document.getElementById('cancelBtn').classList.add('show');

  await api().send_message(S.convId, text, options);
}

function cancelStream() {
  if (S.convId) api().cancel_stream(S.convId);
}

function toggleTool(btn) {
  btn.classList.toggle('active');
  // 持久化工具按钮状态
  const id = btn.id;
  if (id) localStorage.setItem('toolActive_' + id, btn.classList.contains('active') ? '1' : '0');
}

