/* ── Message rendering ───────────────────────────────────────────────────── */
function clearMsgList() {
  document.getElementById('msgList').innerHTML = '';
  // 清空版本导航状态，避免切换对话后的 stale DOM 引用
  S.turnVersions = {};
  S._verChoice   = {};
}

function showEmpty(show) {
  let el = document.getElementById('emptyState');
  if (!el) return;
  if (show) {
    document.getElementById('msgList').appendChild(el);
  } else if (el.parentNode) {
    el.parentNode.removeChild(el);
  }
}

function renderMessages(messages) {
  const list = document.getElementById('msgList');
  list.innerHTML = '';
  showEmpty(false);
  S.turnVersions = {};
  S._verChoice = {};
  const turnAssistRows = {};
  let anonUserSeq = 0;
  let currentUserId = '';
  const addAssistToTurn = (turnId, row) => {
    if (!turnId) return;
    if (!turnAssistRows[turnId]) turnAssistRows[turnId] = [];
    turnAssistRows[turnId].push(row);
  };

  for (const m of messages) {
    if (m.role === 'system') continue;
    const row = buildMsgEl(m);
    list.appendChild(row);
    if (m.role === 'user') {
      currentUserId = m.id || row.dataset.msgId || (`__u_${++anonUserSeq}`);
      row.dataset.turnKey = currentUserId;
    } else if (m.role === 'assistant') {
      const explicitTurn = m.for_user_id || row.dataset.forUserId || '';
      const inferredTurn = explicitTurn || currentUserId;
      addAssistToTurn(inferredTurn, row);
    } else {
      // keep currentUserId unchanged for non user/assistant roles
    }
  }

  for (const [turnId, rows] of Object.entries(turnAssistRows)) {
    if (!turnId || rows.length <= 1) continue;
    S.turnVersions[turnId] = rows.map(row => ({ row, msgId: row.dataset.msgId || '' }));
    delete S._verChoice[turnId];
    _attachVersionNav(turnId);
  }
  scrollBottom(true);
}

function buildMsgEl(msg) {
  const row = document.createElement('div');
  row.className = `msg-row ${msg.role}`;
  row.dataset.msgId = msg.id || '';
  if (msg.role === 'assistant' && msg.for_user_id) {
    row.dataset.forUserId = msg.for_user_id;
  }

  const avatarIcon = msg.role === 'user' ? 'U' : 'A';

  if (msg.role === 'user') {
    const attachItems = Array.isArray(msg.attachments) ? msg.attachments : [];
    const _imgSrc = (att) => {
      if (!att || att.kind !== 'image') return '';
      if (att.data_url) return att.data_url;
      const p = String(att.path || '').trim();
      if (!p) return '';
      const norm = p.replace(/\\/g, '/');
      if (/^[a-zA-Z]:\//.test(norm)) return `file:///${norm}`;
      if (norm.startsWith('/')) return `file://${norm}`;
      return '';
    };
    const attachHtml = attachItems.length
      ? `<div class="msg-user-attachments">${attachItems.map(att => {
          const src = _imgSrc(att);
          const icon = src
            ? `<img class="attach-chip-thumb" src="${esc(src)}" alt="preview" />`
            : (att?.kind === 'image' ? '🖼️' : '📎');
          const name = esc(att?.name || '附件');
          return `<span class="attach-chip"><span>${icon}</span><span class="attach-chip-name">${name}</span></span>`;
        }).join('')}</div>`
      : '';
    row.innerHTML = `
      <div class="msg-avatar">${avatarIcon}</div>
      <div class="msg-body">
        <div class="msg-bubble">${esc(msg.content || '')}</div>
        ${attachHtml}
        <div class="msg-actions">
          <button class="msg-act-btn" onclick="copyMsg(this)">复制</button>
          <button class="msg-act-btn" onclick="regenFromUserMsg(this,'${esc(msg.id||'')}')">重新发送</button>
          <button class="msg-act-btn" onclick="deleteMsg(this)">删除</button>
        </div>
      </div>`;
  } else {
    const thinking = msg.reasoning_content
      ? `<div class="think-block">
           <div class="think-header" onclick="this.closest('.think-block').classList.toggle('open')">
             <span class="think-arrow" style="margin-left:0;margin-right:4px">▶</span><span>思考过程</span>
           </div>
           <div class="think-body">${esc(msg.reasoning_content)}</div>
         </div>`
      : '';
    const usage = (msg.usage && msg.usage.total_tokens)
      ? `<span style="font-size:11px;color:var(--muted);margin-top:6px;display:inline-block">
           ${msg.model || ''} · ${msg.usage.total_tokens} tokens
         </span>`
      : '';

    // ── 历史工具块还原 ────────────────────────────────────────────────────
    let toolBlocksHtmlParts = [];
    let _lastToolPartIdx = -1;
    for (const tr of (msg.tool_results || [])) {
      if (tr.tool === 'web_search') {
        const cards = (tr.results || []).map((r, i) => {
          const _url   = r.url || '';
          const _title = esc((r.title || '未知标题').slice(0, 46));
          const _domain = _url.replace(/^https?:\/\//, '').split('/')[0].replace(/^www\./, '').split('?')[0];
          return `<div class="tb-result-card" onclick="window.open(${JSON.stringify(_url)},'_blank')">
            <span class="rc-idx">${i+1}.</span><span class="rc-title">${_title}</span>
            <span class="rc-url">${esc(_domain || _url)}</span>
          </div>`;
        }).join('');
        toolBlocksHtmlParts.push(`
          <div class="tool-block" data-tool="web_search">
            <div class="tool-block-hd" onclick="this.closest('.tool-block').classList.toggle('open')">
              <span class="tb-icon">&#128269;</span>
              <span class="tb-label">联网搜索</span>
              <span class="tb-ok"><svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><polyline points="1.5,6 4.5,9.5 10.5,2.5"/></svg></span>
              <span class="tb-arrow">&#9656;</span>
            </div>
            <div class="tool-block-body">
              <div class="tb-query">搜索查询：<em>${esc(tr.query || '')}</em></div>
              <div class="tb-results">${cards}</div>
            </div>
          </div>`);
        _lastToolPartIdx = toolBlocksHtmlParts.length - 1;
      } else if (tr.tool === 'kb_search') {
        let roundsHtml = '';
        for (let ri = 0; ri < (tr.rounds || []).length; ri++) {
          const rd = tr.rounds[ri];
          const hitCards = (rd.hits || []).map((h, i) => {
            const _src = esc((h.source || '').split(/[\/\\]/).pop() || h.source || '');
            const _ci  = h.chunk_index != null ? ` #${h.chunk_index}` : '';
            const _bt  = esc((h.body || '').slice(0, 120));
            return `<div class="tb-result-card">
              <span class="rc-idx">${i+1}.</span>
              <span class="rc-title">${_src}${_ci}</span>
              <span class="rc-url">${esc(h.kb_name || '')}</span>
              <span class="rc-body">${_bt}</span>
            </div>`;
          }).join('');
          roundsHtml += `
            <div class="tb-round">
              <div class="tb-round-hd" onclick="this.closest('.tb-round').classList.toggle('open')">
                <span style="opacity:.7;font-size:11px">&#128269;</span>
                <span class="tb-round-label">第${ri+1}轮 &middot; ${esc(rd.query || '')}</span>
                <span class="tb-round-sub">${esc(rd.kb_name || '')} &rarr; ${rd.hit_count || 0} 条</span>
                <span class="tb-round-arrow">&#9656;</span>
              </div>
              ${hitCards ? `<div class="tb-round-body"><div class="tb-results">${hitCards}</div></div>` : ''}
            </div>`;
        }
        toolBlocksHtmlParts.push(`
          <div class="tool-block" data-tool="kb_search">
            <div class="tool-block-hd" onclick="this.closest('.tool-block').classList.toggle('open')">
              <span class="tb-icon">&#9744;</span>
              <span class="tb-label">知识库检索</span>
              <span class="tb-ok"><svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><polyline points="1.5,6 4.5,9.5 10.5,2.5"/></svg></span>
              <span class="tb-arrow">&#9656;</span>
            </div>
            <div class="tool-block-body">${roundsHtml}</div>
          </div>`);
        _lastToolPartIdx = toolBlocksHtmlParts.length - 1;
      } else if (tr.tool === 'reflect') {
        const result = String(tr.result || tr.query || '').toUpperCase();
        const verdict = result.includes('NEED_MORE') ? '再查查' : '够了！';
        const line = `<div class="reflect-line">${verdict}</div>`;
        if (_lastToolPartIdx >= 0) {
          toolBlocksHtmlParts.splice(_lastToolPartIdx + 1, 0, line);
          _lastToolPartIdx += 1;
        } else {
          toolBlocksHtmlParts.push(line);
        }
      }
    }
    const toolBlocksHtml = toolBlocksHtmlParts.join('');
    const toolWrap = toolBlocksHtml
      ? `<div class="tool-blocks-wrap">${toolBlocksHtml}</div>`
      : '';
    const processingHtml = toolWrap
      ? `<div class="decision-round">
           <div class="decision-round-hd" onclick="this.closest('.decision-round').classList.toggle('open')">
             <span class="decision-round-icon">🔧</span>
             <span class="decision-round-label">信息处理</span>
             <span class="decision-round-arrow">▶</span>
           </div>
           <div class="decision-round-body">${toolWrap}</div>
         </div>`
      : '';

    row.innerHTML = `
      <div class="msg-avatar">${avatarIcon}</div>
      <div class="msg-body">
        ${processingHtml}
        ${thinking}
        <div class="msg-bubble">
          <div class="md">${renderMd(msg.content || '')}</div>
          ${usage}
        </div>
        <div class="msg-actions">
          <button class="msg-act-btn" onclick="copyMsg(this)">复制</button>
          <button class="msg-act-btn" onclick="regenMsg(this,'${esc(msg.id||'')}')">重新生成</button>
          <button class="msg-act-btn" onclick="deleteMsg(this,'${esc(msg.id||'')}')">删除</button>
        </div>
      </div>`;
  }
  return row;
}

function copyMsg(btn) {
  const bubble = btn.closest('.msg-body')?.querySelector('.msg-bubble');
  const text = bubble?.innerText || '';
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '✓ 已复制';
    setTimeout(() => btn.textContent = '复制', 2000);
  });
}

async function deleteMsg(btn, msgId) {
  const row = btn.closest('.msg-row');
  if (!row || !S.convId) return;

  // 兼容渲染时 msg.id 为空的场景：优先使用运行时 data-msg-id
  const resolvedMsgId = msgId || row.dataset.msgId || '';
  if (!resolvedMsgId) return;

  // 仅删除当前这一条消息，不连带删除上一轮或同轮其它消息
  await api().delete_message(S.convId, resolvedMsgId);

  // 若是助手消息，需要同步维护版本导航状态
  if (row.classList.contains('assistant')) {
    const uid = row.dataset.forUserId || '';
    if (uid && S.turnVersions[uid]) {
      const idx = S.turnVersions[uid].findIndex(v => v.row === row || v.msgId === resolvedMsgId);
      const wasVisible = !row.classList.contains('ver-hidden');
      if (idx !== -1) S.turnVersions[uid].splice(idx, 1);
      if (S.turnVersions[uid].length === 1) {
        const remainRow = S.turnVersions[uid][0].row;
        remainRow.classList.remove('ver-hidden');
        remainRow.querySelector('.ver-nav-bar')?.remove();
        delete S.turnVersions[uid];
        delete S._verChoice[uid];
      } else if (S.turnVersions[uid].length === 0) {
        delete S.turnVersions[uid];
        delete S._verChoice[uid];
      } else {
        const remain = S.turnVersions[uid];
        if (wasVisible) {
          const fallbackIdx = Math.max(0, idx - 1);
          remain.forEach((v, i) => v.row.classList.toggle('ver-hidden', i !== fallbackIdx));
          S._verChoice[uid] = fallbackIdx;
        }
        _attachVersionNav(uid);
      }
    }
  } else if (row.classList.contains('user')) {
    delete S.turnVersions[resolvedMsgId];
    delete S._verChoice[resolvedMsgId];
  }

  row.remove();

  // 关键修复：删除后以会话真实数据重建消息与版本可见性，
  // 避免旧的 ver-hidden / turnVersions 状态污染后续消息显示。
  try {
    const conv = await api().get_conversation(S.convId);
    if (conv && Array.isArray(conv.messages)) {
      renderMessages(conv.messages);
    }
  } catch (_) {
    // ignore and keep current best-effort UI state
  }
}

// 取消行专用：删除（无 aiMsgId 可删后端，仅移除 DOM 和版本状态）
function deleteCancelledMsg(btn) {
  const row = btn.closest('.msg-row');
  if (!row) return;
  const uid = row.dataset.cancelledForUser;
  if (uid && S.turnVersions[uid]) {
    const idx = S.turnVersions[uid].findIndex(v => v.row === row);
    if (idx !== -1) S.turnVersions[uid].splice(idx, 1);
    // 仅剩 1 条时移除版本导航栏，变回普通单条消息
    if (S.turnVersions[uid].length === 1) {
      S.turnVersions[uid][0].row.querySelector('.ver-nav-bar')?.remove();
      delete S.turnVersions[uid];
    } else if (S.turnVersions[uid].length === 0) {
      delete S.turnVersions[uid];
    }
    delete S._verChoice[uid];
  }
  row.remove();
}

// 取消行专用：重新生成（删除取消行，重跑同一用户问题）
async function regenCancelledMsg(btn) {
  if (S.streaming || !S.convId) return;
  const row = btn.closest('.msg-row');
  if (!row) return;
  const uid = row.dataset.cancelledForUser;
  const userRow = uid ? document.querySelector(`.msg-row.user[data-msg-id="${uid}"]`) : null;
  const userText = userRow?.querySelector('.msg-bubble')?.innerText?.trim() || '';
  if (!userText) return;
  deleteCancelledMsg(btn); // 先清理 DOM 和版本状态
  S._regenUserMsgId = uid || null;
  document.getElementById('msgInput').value = userText;
  sendMessage();
}

async function regenMsg(btn, msgId) {
  if (S.streaming || !S.convId || !msgId) return;
  const aiRow = btn.closest('.msg-row');
  if (!aiRow) return;

  // Find the nearest user message row above this AI response
  let userRow = aiRow.previousElementSibling;
  while (userRow && !userRow.classList.contains('user')) {
    userRow = userRow.previousElementSibling;
  }
  if (!userRow) return;

  const userMsgId = userRow.dataset.msgId;
  const userText  = userRow.querySelector('.msg-bubble')?.innerText?.trim() || '';
  if (!userText) return;

  // Stash this version (hidden) for version navigation，同时保留到后端历史
  // 防止重复 push：aiRow 可能已经在 turnVersions 里（第二次 regen 同一行时）
  if (!S.turnVersions[userMsgId]) S.turnVersions[userMsgId] = [];
  if (!S.turnVersions[userMsgId].some(v => v.row === aiRow)) {
    S.turnVersions[userMsgId].push({ row: aiRow, msgId });
  }
  aiRow.classList.add('ver-hidden');

  // Clear manual choice so the new version auto-shows after stream finishes
  delete S._verChoice[userMsgId];
  // Trigger a new stream - skip re-saving the user message
  S._regenUserMsgId = userMsgId;
  document.getElementById('msgInput').value = userText;
  sendMessage();
}

