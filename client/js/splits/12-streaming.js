/* ── Streaming callbacks (called from Python via evaluate_js) ─────────────── */
window.__onStreamEvent = function(event) {
  const { type, conv_id } = event;

  function _renderStreamContentNow() {
    if (!S.streamContentEl) return;
    const _renderFn = window._markedStream || (t => '<pre>' + esc(t) + '</pre>');
    try {
      S.streamContentEl.innerHTML = _renderFn(S.streamBuf);
    } catch(e) {
      S.streamContentEl.innerHTML = esc(S.streamBuf);
    }
    const _cur = document.createElement('span');
    _cur.className = 'cursor';
    _cur.textContent = '▋';
    S.streamContentEl.appendChild(_cur);
    scrollBottom();
  }

  function _scheduleStreamContentRender() {
    if (S._streamContentRenderTimer) {
      S._streamContentRenderDirty = true;
      return;
    }
    S._streamContentRenderTimer = setTimeout(() => {
      S._streamContentRenderTimer = null;
      _renderStreamContentNow();
      if (S._streamContentRenderDirty) {
        S._streamContentRenderDirty = false;
        _scheduleStreamContentRender();
      }
    }, 16);
  }

  function _scheduleReasoningRender() {
    if (S._streamReasonRenderTimer) {
      S._streamReasonRenderDirty = true;
      return;
    }
    S._streamReasonRenderTimer = setTimeout(() => {
      S._streamReasonRenderTimer = null;
      if (S.streamReasonEl) {
        S.streamReasonEl.textContent = S.reasonBuf;
        scrollBottom();
      }
      if (S._streamReasonRenderDirty) {
        S._streamReasonRenderDirty = false;
        _scheduleReasoningRender();
      }
    }, 16);
  }

  function _ensureDecisionRound() {
    if (!S.toolBlocksEl) return null;
    if (S.decisionRoundEl) return S.decisionRoundEl;
    const _decisionRound = document.createElement('div');
    _decisionRound.className = 'decision-round';
    _decisionRound.innerHTML = `
      <div class="decision-round-hd" onclick="this.closest('.decision-round').classList.toggle('open')">
        <span class="decision-round-icon">🔧</span>
        <span class="decision-round-label">信息处理</span>
        <span class="decision-round-arrow">▶</span>
      </div>
      <div class="decision-round-body"></div>
    `;
    S.toolBlocksEl.appendChild(_decisionRound);
    S.decisionRoundEl = _decisionRound.querySelector('.decision-round-body');
    return S.decisionRoundEl;
  }

  function _latestToolBlock() {
    const _dr = _ensureDecisionRound();
    if (!_dr) return null;
    const _all = Array.from(_dr.querySelectorAll('.tool-block'));
    return _all.length ? _all[_all.length - 1] : null;
  }

  function _ensureReflectLineFor(targetBlock) {
    const _dr = _ensureDecisionRound();
    if (!_dr) return null;
    const _target = targetBlock || _latestToolBlock();
    if (!_target) return null;
    if (_target._reflectLineEl && _target._reflectLineEl.isConnected) return _target._reflectLineEl;
    const _line = document.createElement('div');
    _line.className = 'reflect-line';
    _target.insertAdjacentElement('afterend', _line);
    _target._reflectLineEl = _line;
    return _line;
  }

  switch (type) {

    case 'start': {
      if (conv_id !== S.convId) break;
      // Remove any pre-stream status indicator
      document.getElementById('preStreamStatus')?.remove();
      // Create assistant bubble placeholder
      const row = document.createElement('div');
      row.className = 'msg-row assistant';
      row.innerHTML = `
        <div class="msg-avatar">A</div>
        <div class="msg-body">
          <div class="msg-bubble">
            <div class="md stream-content"></div>
          </div>
        </div>`;
      document.getElementById('msgList').appendChild(row);
      S.streamMsgEl      = row;
      S.streamContentEl  = row.querySelector('.stream-content');
      S.streamThinkBlock = null;  // created lazily on first reasoning_chunk
      S.streamReasonEl   = null;
      // tool blocks container: inserted before the bubble
      const _toolsCont = document.createElement('div');
      _toolsCont.className = 'tool-blocks-wrap';
      row.querySelector('.msg-body').insertBefore(_toolsCont, row.querySelector('.msg-bubble'));
      S.toolBlocksEl     = _toolsCont;
      // decision round container: wrapper for all tool blocks and thinking blocks in this round
      // We'll create it dynamically when planner tool starts
      S.decisionRoundEl = null;
      // hide bubble until first content arrives
      row.querySelector('.msg-bubble').hidden = true;
      S.streamBuf        = '';
      S.reasonBuf        = '';
      S._streamContentRenderTimer = null;
      S._streamContentRenderDirty = false;
      S._streamReasonRenderTimer  = null;
      S._streamReasonRenderDirty  = false;
      S.streamModel      = event.model || '';
      S.streamUserMsgId  = event.user_message_id || null;
      S.streamErrorMessage = '';
      if (S.streamUserMsgId) {
        row.dataset.forUserId = S.streamUserMsgId;
      }
      // Backfill the user row's data-msg-id if it was just created without one
      // (sendMessage builds user rows with id=null before the backend assigns an id)
      if (S.streamUserMsgId) {
        const _rows = Array.from(document.getElementById('msgList').querySelectorAll('.msg-row.user'));
        const _lastUser = _rows[_rows.length - 1];
        if (_lastUser && !_lastUser.dataset.msgId) {
          _lastUser.dataset.msgId = S.streamUserMsgId;
        }
      }
      // Regen: insert provisional version nav bar ABOVE tool blocks immediately on stream start
      if (S.streamUserMsgId && S.turnVersions[S.streamUserMsgId]?.length > 0) {
        const _vIdx   = S.turnVersions[S.streamUserMsgId].length; // 0-based index of this new response
        const _vTotal = _vIdx + 1;
        // streamUserMsgId 始终是 "msg_" + hex，单引号包裹可安全嵌入 onclick
        const _uid    = S.streamUserMsgId;
        const _vNav   = document.createElement('div');
        _vNav.className = 'ver-nav-bar';
        _vNav.innerHTML =
          `<button class="ver-nav-btn" onclick="_switchVersion('${_uid}',${_vIdx - 1})">&#8249;</button>` +
          `<span>${_vIdx + 1}/${_vTotal}</span>` +
          `<button class="ver-nav-btn" disabled>&#8250;</button>`;
        row.querySelector('.msg-body').insertBefore(_vNav, _toolsCont);
      }
      // 预插入占位按钮：流式期间复制可用，其余等流式结束后激活
      const _phActs = document.createElement('div');
      _phActs.className = 'msg-actions';
      _phActs.innerHTML =
        `<button class="msg-act-btn" onclick="copyMsg(this)">复制</button>` +
        `<button class="msg-act-btn" disabled>重新生成</button>` +
        `<button class="msg-act-btn" disabled>删除</button>`;
      row.querySelector('.msg-body').appendChild(_phActs);
      S.streamContentEl.innerHTML = '';
      scrollBottom(true);
      break;
    }

case 'status': {
  if (conv_id !== S.convId) break;
  if (S.streamMsgEl) {
    // 追加进 decisionRoundEl，保证与工具块的视觉顺序和执行顺序一致
    let chip = S.streamMsgEl.querySelector('.status-chip');
    if (!chip) {
      chip = document.createElement('div');
      chip.className = 'status-chip';
      if (S.decisionRoundEl) {
        S.decisionRoundEl.appendChild(chip);
      } else {
        S.streamMsgEl.querySelector('.msg-body').insertBefore(
          chip, S.streamMsgEl.querySelector('.msg-bubble')
        );
      }
    }
    chip.innerHTML = `<div class="spinner"></div>${esc(event.text)}`;
  } else {
    // Before stream starts — show a standalone pre-stream row
    let statusRow = document.getElementById('preStreamStatus');
    if (!statusRow) {
      statusRow = document.createElement('div');
      statusRow.id = 'preStreamStatus';
      statusRow.className = 'msg-row assistant';
statusRow.innerHTML = '<div class="msg-avatar">A</div><div class="msg-body"><div class="status-chip"><div class="spinner"></div><span></span></div></div>';
      document.getElementById('msgList').appendChild(statusRow);
    }
    statusRow.querySelector('span').textContent = event.text;
    scrollBottom();
  }
  break;
}

    case 'tool_start': {
      if (conv_id !== S.convId) break;
      _ensureDecisionRound();

      if (event.tool === 'reflect') {
        const _line = _ensureReflectLineFor(S._lastDoneToolBlock || null);
        if (_line) _line.textContent = '要再查查信息吗？';
        S._activeReflectLine = _line || null;
        scrollBottom();
        break;
      }

      if (event.tool === 'planner') {
        break;
      }
      
      const _TOOL_META = {
        web_search: {
          label: '联网搜索',
          icon: '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13"><circle cx="5.5" cy="5.5" r="4"/><line x1="8.5" y1="8.5" x2="12" y2="12"/></svg>'
        },
        planner: {
          label: '信息处理',
          icon: '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13"><path d="M2 7h7"/><path d="M7 3l4 4-4 4"/></svg>'
        },
        reflect: {
          label: '信息处理',
          icon: '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13"><circle cx="7" cy="7" r="5"/><path d="M7 4v3"/><circle cx="7" cy="9.5" r=".6" fill="currentColor" stroke="none"/></svg>'
        },
        kb_search: {
          label: '知识库检索',
          icon: '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13"><rect x="2" y="1" width="10" height="12" rx="1"/><line x1="4" y1="5" x2="10" y2="5"/><line x1="4" y1="8" x2="8" y2="8"/></svg>'
        },
        rag: {
          label: '知识库检索',
          icon: '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13"><rect x="2" y="1" width="10" height="12" rx="1"/><line x1="4" y1="5" x2="10" y2="5"/><line x1="4" y1="8" x2="8" y2="8"/></svg>'
        },
        memory_search: {
          label: '记忆检索',
          icon: '<svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13"><rect x="2" y="2" width="10" height="10" rx="1.5"/><path d="M4.5 5.5h5"/><path d="M4.5 8h5"/><path d="M4.5 10.5h3"/></svg>'
        },
      };
      const _meta = _TOOL_META[event.tool] || { label: event.tool, icon: '⚙️' };
      const _block = document.createElement('div');
      _block.className = 'tool-block';
      _block.dataset.tool = event.tool;
      _block.innerHTML = `
        <div class="tool-block-hd" onclick="this.closest('.tool-block').classList.toggle('open')">
          <span class="tb-icon" style="opacity:.65;display:flex;align-items:center">${_meta.icon}</span>
          <span class="tb-label">${_meta.label}</span>
          <div style="width:11px;height:11px;border:1.5px solid rgba(255,255,255,.15);border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite;flex-shrink:0" class="tb-spinner"></div>
          <span class="tb-arrow">▶</span>
        </div>
        <div class="tool-block-body"></div>`;
      // Ensure tool block is NOT expanded by default
      _block.classList.remove('open');
      S.decisionRoundEl.appendChild(_block);
      scrollBottom();
      break;
    }

    case 'tool_done': {
      if (conv_id !== S.convId || !S.decisionRoundEl) break;
      if (event.tool === 'reflect') {
        const _line = S._activeReflectLine || _ensureReflectLineFor(S._lastDoneToolBlock || null);
        if (_line) {
          const _q = String(event.query || '').toUpperCase();
          _line.textContent = _q.includes('NEED_MORE') ? '再查查' : '够了！';
        }
        S._activeReflectLine = null;
        scrollBottom();
        break;
      }
      if (event.tool === 'planner') break;
      const _b_all = Array.from(S.decisionRoundEl.querySelectorAll(`.tool-block[data-tool="${event.tool}"]`));
      const _b = _b_all.slice().reverse().find(b => b.querySelector('.tb-spinner')) || _b_all[_b_all.length - 1];
      if (!_b) break;
      _b.querySelector('.tb-spinner')?.remove();
      S._lastDoneToolBlock = _b;
      const _hd = _b.querySelector('.tool-block-hd');
      const _ok = document.createElement('span');
      _ok.className = 'tb-ok';
      _ok.innerHTML = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><polyline points="1.5,6 4.5,9.5 10.5,2.5"/></svg>';
      _hd.insertBefore(_ok, _hd.querySelector('.tb-arrow'));
      if (event.results && event.results.length) {
        const _body = _b.querySelector('.tool-block-body');
        if (event.tool === 'web_search') {
          const _q = esc(event.query || '');
          const _cards = event.results.map((r, i) => {
            const _url = r.url || '';
            const _title = esc((r.title || '未知标题').slice(0, 46));
            const _domain = _url.replace(/^https?:\/\//, '').split('/')[0].replace(/^www\./, '').split('?')[0];
            return `<div class="tb-result-card" onclick="window.open(${JSON.stringify(_url)},'_blank')">
              <span class="rc-idx">${i+1}.</span><span class="rc-title">${_title}</span>
              <span class="rc-url">${esc(_domain || _url)}</span>
            </div>`;
          }).join('');
          _body.innerHTML = `<div class="tb-query">搜索查询：<em>${_q}</em></div><div class="tb-results">${_cards}</div>`;
        } else if (event.tool === 'kb_search') {
          const _cards = event.results.map((r, i) => {
            const _kb  = esc(r.kb_name || '');
            const _src = esc((r.source || '').split(/[\\/]/).pop() || r.source || '');
            const _ci  = r.chunk_index != null ? ` #${r.chunk_index}` : '';
            const _body_text = esc((r.body || '').slice(0, 120));
            return `<div class="tb-result-card">
              <span class="rc-idx">${i+1}.</span>
              <span class="rc-title">${_src}${_ci}</span>
              <span class="rc-url">${_kb}</span>
              <span class="rc-body">${_body_text}…</span>
            </div>`;
          }).join('');
          // 用 appendChild 追加结果卡片，保留已有的 .tb-round 搜索轮次块
          const _resultsDiv = document.createElement('div');
          _resultsDiv.className = 'tb-results';
          _resultsDiv.innerHTML = _cards;
          _body.appendChild(_resultsDiv);
        } else if (event.tool === 'rag') {
          _body.innerHTML = `<div class="tb-query">已召回 <em>${event.results.length}</em> 个相关片段</div>`;
        }
      } else if (event.query && event.tool !== 'planner' && event.tool !== 'reflect') {
        const _body = _b.querySelector('.tool-block-body');
        _body.innerHTML = `<div class="tb-query">${esc(event.query)}</div>`;
      }
      scrollBottom();
      break;
    }

    case 'tool_error': {
      if (conv_id !== S.convId || !S.toolBlocksEl) break;
      if (event.tool === 'planner' || event.tool === 'reflect') break;
      const _be_all = Array.from(S.toolBlocksEl.querySelectorAll(`.tool-block[data-tool="${event.tool}"]`));
      const _be = _be_all.slice().reverse().find(b => b.querySelector('.tb-spinner')) || _be_all[_be_all.length - 1];
      if (_be) {
        _be.querySelector('.tb-spinner')?.remove();
        const _he = _be.querySelector('.tool-block-hd');
        const _ee = document.createElement('span');
        _ee.className = 'tb-err';
        _ee.textContent = '✗';
        _he.insertBefore(_ee, _he.querySelector('.tb-arrow'));
        if (event.error) {
          const _bdy = _be.querySelector('.tool-block-body');
          _bdy.innerHTML = `<div class="tb-query" style="color:var(--danger)">${esc(event.error)}</div>`;
          _be.classList.add('open');
        }
      }
      break;
    }

    case 'kb_tool_done': {
      // 每次 search_kb 调用追加一个可折叠小工具块到 kb_search 工具块 body
      if (conv_id !== S.convId || !S.decisionRoundEl) break;
      const _kb_b = S.decisionRoundEl.querySelector('.tool-block[data-tool="kb_search"]');
      if (!_kb_b) break;
      const _kb_body = _kb_b.querySelector('.tool-block-body');
      const _roundNum = _kb_body.querySelectorAll('.tb-round').length + 1;
      const _hit    = event.hit_count || 0;
      const _hits   = event.hits || [];
      let _cardsHtml = '';
      if (_hits.length) {
        _cardsHtml = `<div class="tb-results">` +
          _hits.map((h, i) => {
            const _src = esc((h.source || '').split(/[\/\\]/).pop() || h.source || '');
            const _ci  = h.chunk_index != null ? ` #${h.chunk_index}` : '';
            const _bt  = esc((h.body || '').slice(0, 120));
            return `<div class="tb-result-card">` +
              `<span class="rc-idx">${i+1}.</span>` +
              `<span class="rc-title">${_src}${_ci}</span>` +
              `<span class="rc-url">${esc(h.kb_name || '')}</span>` +
              `<span class="rc-body">${_bt}</span>` +
              `</div>`;
          }).join('') +
        `</div>`;
      }
      const _round  = document.createElement('div');
      _round.className = 'tb-round';
      _round.innerHTML =
        `<div class="tb-round-hd" onclick="this.closest('.tb-round').classList.toggle('open')">` +
          `<span style="opacity:.7;font-size:11px">&#128269;</span>` +
          `<span class="tb-round-label">第${_roundNum}轮 &middot; ${esc(event.query || '')}</span>` +
          `<span class="tb-round-sub">${esc(event.kb_name || '')} &rarr; ${_hit} 条</span>` +
          `<span class="tb-round-arrow">&#9656;</span>` +
        `</div>` +
        (_cardsHtml ? `<div class="tb-round-body">${_cardsHtml}</div>` : '');
      _kb_body.appendChild(_round);
      scrollBottom();
      break;
    }

    case 'mem_done': {
      if (conv_id !== S.convId || !S.streamMsgEl) break;
      const _sc = S.streamMsgEl.querySelector('.status-chip');
      if (_sc) {
        _sc.className = 'mem-done-chip';
        _sc.innerHTML = '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" width="11" height="11"><polyline points="1.5,6 4.5,9.5 10.5,2.5"/></svg> 已回忆';
      }
      break;
    }

    case 'fallback_notice': {
      // 工具调用失败时在消息顶部插一行提示
      if (conv_id !== S.convId || !S.streamContentEl) break;
      const _fn = document.createElement('div');
      _fn.className = 'fallback-notice';
      _fn.textContent = event.message || '知识库检索不可用，已直接回答';
      S.streamContentEl.insertBefore(_fn, S.streamContentEl.firstChild);
      break;
    }

case 'reasoning_chunk': {
  if (conv_id !== S.convId) break;
  // 先累计，确保即使未创建 think block 也不会丢失 reasoning
  S.reasonBuf += event.text;
  // Create think-block lazily and always keep it outside tool decision rounds.
  if (!S.streamThinkBlock) {
    const _tb = document.createElement('div');
    _tb.className = 'think-block';
    _tb.innerHTML = `
      <div class="think-header" onclick="this.closest('.think-block').classList.toggle('open')">
        <span class="think-arrow" style="margin-left:0;margin-right:4px">▶</span><span class="think-title">思考中…</span>
      </div>
      <div class="think-body"></div>`;
    const _msgBody = S.streamMsgEl?.querySelector('.msg-body');
    const _bubble = S.streamMsgEl?.querySelector('.msg-bubble');
    if (_msgBody && _bubble) {
      _msgBody.insertBefore(_tb, _bubble);
    }
    S.streamThinkBlock = _tb;
    S.streamReasonEl   = _tb.querySelector('.think-body');
  }
  if (!S.streamReasonEl) break;
  _scheduleReasoningRender();
  break;
}

    case 'content_chunk': {
      if (conv_id !== S.convId || !S.streamContentEl) break;
      // Remove status chip once content starts (mem-done-chip stays)
      S.streamMsgEl?.querySelector('.status-chip')?.remove();
      // Show bubble on first chunk
      const _bubble = S.streamMsgEl?.querySelector('.msg-bubble');
      if (_bubble && _bubble.hidden) _bubble.hidden = false;
      S.streamBuf += event.text;
      _scheduleStreamContentRender();
      break;
    }

    case 'done':
    case 'cancelled': {
      if (conv_id !== S.convId) break;
      _finalizeStream(event.usage, event.model, event.message_id);
      break;
    }

    case 'error': {
      const _errMsg = String(event.message || '未知错误').trim();
      S.streamErrorMessage = _errMsg;
      if (S.streamContentEl) {
        // Error mid-stream: show notice immediately and keep it in final message
        if (S.streamBuf) {
          const marker = `⚠ ${_errMsg}`;
          if (!S.streamBuf.includes(marker)) {
            S.streamBuf += `\n\n${marker}`;
          }
          _renderStreamContentNow();
        } else {
          S.streamContentEl.innerHTML = `⚠ ${esc(_errMsg)}`;
        }
        S.streamMsgEl?.querySelector('.status-chip')?.remove();
        const _bubble = S.streamMsgEl?.querySelector('.msg-bubble');
        if (_bubble) _bubble.hidden = false; // 首包前报错时确保可见
      } else {
        // No stream started: show as a neutral system row (not red !)
        const row = document.createElement('div');
        row.className = 'msg-row system';
        row.innerHTML = `<div class="msg-avatar">⚠</div>
          <div class="msg-body"><div class="msg-bubble">${esc(event.message)}</div></div>`;
        document.getElementById('msgList').appendChild(row);
      }
      _finalizeStream(undefined, undefined, event.message_id || undefined);
      break;
    }

    case 'title_update': {
      const c = S.convs.find(x => x.id === conv_id);
      if (c) c.title = event.title;
      renderConvList();
      if (conv_id === S.convId) updateTitle(event.title);
      break;
    }
  }
};

// 批量流式事件处理器（由 desktop_app.py _stream_thread 30ms 节流调用）
window.__onStreamBatch = function(events) {
  for (const ev of events) window.__onStreamEvent(ev);
};

function _finalizeStream(usage, model, aiMsgId) {
  // Render markdown on final content
  if (S.streamContentEl) {
    const mdEl = S.streamContentEl;
    let finalText = S.streamBuf || '';
    if (S.streamErrorMessage) {
      const marker = `⚠ ${S.streamErrorMessage}`;
      if (!finalText.includes(marker)) {
        finalText = finalText ? `${finalText}\n\n${marker}` : marker;
      }
    }
    if (!finalText && (S.reasonBuf || '').trim()) {
      finalText = '（深度思考已完成，正文为空）';
    }
    mdEl.innerHTML = renderMd(finalText) || '';
    const bubbleEl = mdEl.closest('.msg-bubble');
    if (bubbleEl && bubbleEl.hidden) bubbleEl.hidden = false;
    // Append usage footer inside the bubble
    const mdModel = model || S.streamModel || '';
    if (usage && usage.total_tokens) {
      const foot = document.createElement('span');
      foot.style.cssText = 'font-size:11px;color:var(--muted);margin-top:6px;display:inline-block';
      foot.textContent = (mdModel ? mdModel + ' · ' : '') + usage.total_tokens + ' tokens';
      bubbleEl?.appendChild(foot);
    }
    // Mark think block as done
    if (S.streamThinkBlock && !S.streamThinkBlock.hidden) {
      const tt = S.streamThinkBlock.querySelector('.think-title');
      if (tt) tt.textContent = '思考过程';
    }
  }

  // 清理未完成的工具块 spinner（如 KB 搜索期间用户取消）
  S.toolBlocksEl?.querySelectorAll('.tb-spinner').forEach(s => s.remove());

  // 激活/更新动作按钮
  if (S.streamMsgEl) {
    const msgBody = S.streamMsgEl.querySelector('.msg-body');
    // 取到流式期间预插入的占位 msg-actions，或新建
    let actions = msgBody?.querySelector('.msg-actions');
    if (!actions) {
      actions = document.createElement('div');
      actions.className = 'msg-actions';
      msgBody?.appendChild(actions);
    }
    const _uId = S.streamUserMsgId;
    if (aiMsgId) {
      S.streamMsgEl.dataset.msgId = aiMsgId;
      actions.innerHTML =
        `<button class="msg-act-btn" onclick="copyMsg(this)">复制</button>` +
        `<button class="msg-act-btn" onclick="regenMsg(this,'${aiMsgId}')">重新生成</button>` +
        `<button class="msg-act-btn" onclick="deleteMsg(this,'${aiMsgId}')">删除</button>`;
      // Version navigation: link this response to prior regen versions
      if (_uId && S.turnVersions[_uId] && S.turnVersions[_uId].length > 0) {
        S.turnVersions[_uId].push({ row: S.streamMsgEl, msgId: aiMsgId });
        _attachVersionNav(_uId);
      }
    } else {
      // 已取消：标记父用户消息 ID，供 regenCancelledMsg/deleteCancelledMsg 使用
      if (_uId) S.streamMsgEl.dataset.cancelledForUser = _uId;
      actions.innerHTML =
        `<button class="msg-act-btn" onclick="copyMsg(this)">复制</button>` +
        `<button class="msg-act-btn" onclick="regenCancelledMsg(this)">重新生成</button>` +
        `<button class="msg-act-btn" onclick="deleteCancelledMsg(this)">删除</button>`;
      // 若是 regen 取消，必须把取消行注册到 turnVersions，
      // 否则 _switchVersion 切到旧版本时无法隐藏该行，导致两行同时可见
      if (_uId && S.turnVersions[_uId]?.length > 0) {
        if (!S.turnVersions[_uId].some(v => v.row === S.streamMsgEl)) {
          S.turnVersions[_uId].push({ row: S.streamMsgEl, msgId: null });
        }
        // 保持显示取消行为当前版本（用户可用 ‹ 切回旧版本）
        S._verChoice[_uId] = S.turnVersions[_uId].length - 1;
        _attachVersionNav(_uId);
      }
    }
  }

  S.streamMsgEl      = null;
  S.streamContentEl  = null;
  S.streamThinkBlock = null;
  S.streamReasonEl   = null;
  S._lastDoneToolBlock = null;
  S._activeReflectLine = null;
  S.streamErrorMessage = '';
  if (S._streamContentRenderTimer) {
    clearTimeout(S._streamContentRenderTimer);
    S._streamContentRenderTimer = null;
  }
  if (S._streamReasonRenderTimer) {
    clearTimeout(S._streamReasonRenderTimer);
    S._streamReasonRenderTimer = null;
  }
  S.toolBlocksEl     = null;
  S.streamUserMsgId  = null;
  document.getElementById('preStreamStatus')?.remove();
  S.smoothBuf        = '';
  S.reasonBuf        = '';
  S.streaming        = false;

  // Unlock UI
  document.getElementById('cancelBtn').classList.remove('show');
  document.getElementById('msgInput').focus();

  if (S._pendingSend) {
    const _next = S._pendingSend;
    S._pendingSend = null;
    const _input = document.getElementById('msgInput');
    _input.value = _next.text || '';
    if (typeof setPendingAttachments === 'function') {
      setPendingAttachments(_next.attachments || []);
    }
    sendMessage();
  }

  // Refresh conv list (message_count / updated_at)
  loadConvs().then(() => {
    document.querySelectorAll('.conv-item').forEach(el => {
      el.classList.toggle('active', el.dataset.id === S.convId);
    });
  });
}

