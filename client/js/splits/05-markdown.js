/* ── Markdown rendering ──────────────────────────────────────────────────── */
function setupMarked() {
  if (!window.marked) return;
  const renderer = new marked.Renderer();

  renderer.code = (code, lang) => {
    const lbl = lang || 'text';
    let highlighted = esc(code);
    if (window.hljs) {
      try {
        highlighted = lang && hljs.getLanguage(lang)
          ? hljs.highlight(code, {language: lang}).value
          : hljs.highlightAuto(code).value;
      } catch(e) {}
    }
    const id = 'code_' + Math.random().toString(36).slice(2,8);
    return `<div class="md-pre">
      <div class="code-header">
        <span>${esc(lbl)}</span>
        <button class="copy-btn" onclick="copyCode('${id}')">复制</button>
      </div>
      <pre><code id="${id}">${highlighted}</code></pre>
    </div>`;
  };

  marked.setOptions({ renderer, breaks: true, gfm: true });

  // 流式渲染用简化 renderer：代码块跳过 hljs，避免每帧语法高亮
  const streamRenderer = new marked.Renderer();
  streamRenderer.code = (code, lang) => {
    const lbl = lang || 'text';
    return `<div class="md-pre">
      <div class="code-header"><span>${esc(lbl)}</span></div>
      <pre><code>${esc(code)}</code></pre>
    </div>`;
  };
  window._markedStream = txt => _postMd(marked.parse(txt, { renderer: streamRenderer, breaks: true, gfm: true }));
}

function _postMd(html) {
  // marked v9（CommonMark 严格模式）对紧邻标点符号的 **…** 处理有缺陷，
  // 常见于中文弯引号 "…" 包住的加粗文字。做一次后处理兜底转换。
  return html.replace(/\*\*([^*<\n]+?)\*\*/g, '<strong>$1</strong>');
}

function renderMd(text) {
  if (!text) return '';
  try {
    const html = window.marked ? marked.parse(text) : '<pre>' + esc(text) + '</pre>';
    return _postMd(html);
  } catch(e) {
    return '<pre>' + esc(text) + '</pre>';
  }
}

function copyCode(id) {
  const el = document.getElementById(id);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent).then(() => {
    const btn = el.closest('.md-pre')?.querySelector('.copy-btn');
    if (btn) { btn.textContent = '✓ 已复制'; setTimeout(() => btn.textContent = '复制', 2000); }
  });
}

