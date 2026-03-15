/* ── Version navigation (‹ n / N ›) ─────────────────────────────────────── */

// 用户消息下方"重新发送"按钮：找到对应的 AI 行，复用 regenMsg 逻辑
async function regenFromUserMsg(btn, userMsgId) {
  if (S.streaming || !S.convId) return;
  const userRow = btn.closest('.msg-row');
  if (!userRow) return;
  const userText = userRow.querySelector('.msg-bubble')?.innerText?.trim() || '';
  if (!userText) return;
  const resolvedUserMsgId = userMsgId || userRow.dataset.msgId || '';

  // 向后查找该轮 AI 行（遇到下一条用户消息截止）
  const aiRows = [];
  let probe = userRow.nextElementSibling;
  while (probe) {
    if (probe.classList.contains('user')) break;
    if (probe.classList.contains('assistant')) aiRows.push(probe);
    probe = probe.nextElementSibling;
  }

  // 优先使用当前可见版本；若全隐藏则回退到最后一条（最新）
  const aiRow = aiRows.find(r => !r.classList.contains('ver-hidden')) || aiRows[aiRows.length - 1] || null;

  if (aiRow) {
    const aiMsgId = aiRow.dataset.msgId;
    if (!aiMsgId) return; // 仍在流式中，无法操作
    const fakeBtn = { closest: () => aiRow };
    regenMsg(fakeBtn, aiMsgId);
  } else {
    // 无 AI 回复，直接重发
    if (!resolvedUserMsgId) return;
    delete S._verChoice[resolvedUserMsgId];
    S._regenUserMsgId = resolvedUserMsgId;
    document.getElementById('msgInput').value = userText;
    sendMessage();
  }
}

// Called after a new regen response finishes streaming.
// Ensures every version row has a .ver-nav-bar at the top of its .msg-body.
function _attachVersionNav(userMsgId) {
  const versions = S.turnVersions[userMsgId];
  if (!versions || versions.length < 2) return;
  // Respect user’s manual choice if still valid; otherwise show latest
  const choice = S._verChoice[userMsgId];
  const currentIdx = (typeof choice === 'number' && choice < versions.length) ? choice : versions.length - 1;
  versions.forEach((v, i) => {
    v.row.classList.toggle('ver-hidden', i !== currentIdx);
    _refreshVerNavBar(v.row, userMsgId, i, versions.length);
  });
}

// Create or update the .ver-nav-bar at the TOP of an AI row's .msg-body.
function _refreshVerNavBar(aiRow, userMsgId, idx, total) {
  const msgBody = aiRow.querySelector('.msg-body');
  if (!msgBody) return;
  let nav = msgBody.querySelector('.ver-nav-bar');
  if (!nav) {
    nav = document.createElement('div');
    nav.className = 'ver-nav-bar';
    msgBody.insertBefore(nav, msgBody.firstChild);
  }
  // userMsgId 始终是 "msg_" + hex，不含单引号，可安全嵌入 onclick 属性
  const prev = idx > 0       ? `onclick="_switchVersion('${userMsgId}',${idx - 1})"` : 'disabled';
  const next = idx < total-1 ? `onclick="_switchVersion('${userMsgId}',${idx + 1})"` : 'disabled';
  nav.innerHTML =
    `<button class="ver-nav-btn" ${prev}>‹</button>` +
    `<span>${idx + 1}/${total}</span>` +
    `<button class="ver-nav-btn" ${next}>›</button>`;
}

// Switch to the target version index for a given user turn.
// Also works during streaming: the streaming row is treated as the last implicit version.
function _switchVersion(userMsgId, targetIdx) {
  const versions = S.turnVersions[userMsgId];
  if (!versions) return;

  // The streaming row (if active for this turn) counts as an extra version at the end
  const isStreamingThisTurn = S.streaming && S.streamUserMsgId === userMsgId && S.streamMsgEl;
  const totalCount  = versions.length + (isStreamingThisTurn ? 1 : 0);
  const streamingIdx = versions.length; // index of the streaming row

  if (targetIdx < 0 || targetIdx >= totalCount) return;

  // Remember user’s manual selection
  S._verChoice[userMsgId] = targetIdx;

  // Show/hide committed versions
  versions.forEach((v, i) => {
    const show = (i === targetIdx);
    v.row.classList.toggle('ver-hidden', !show);
    if (show) _refreshVerNavBar(v.row, userMsgId, i, totalCount);
  });

  // Show/hide the currently-streaming row
  if (isStreamingThisTurn) {
    S.streamMsgEl.classList.toggle('ver-hidden', targetIdx !== streamingIdx);
  }
}

