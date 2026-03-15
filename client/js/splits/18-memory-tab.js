/* ── Memory tab ──────────────────────────────────────────────────────────── */
async function addMemory() {
  const text   = document.getElementById('mText')?.value.trim();
  const title  = document.getElementById('mTitle')?.value.trim();
  const source = document.getElementById('mSource')?.value.trim();
  const al = document.getElementById('memAlert');
  if (!text) { if (al) al.innerHTML = '<div class="alert alert-err">请输入内容</div>'; return; }
  const res = await api().add_to_memory(text, title, source);
  if (res.ok) {
    if (al) al.innerHTML = '<div class="alert alert-ok">✓ 已添加到知识库</div>';
  } else {
    if (al) al.innerHTML = `<div class="alert alert-err">✗ ${esc(res.error)}</div>`;
  }
  setTimeout(() => { if (al) al.innerHTML = ''; }, 4000);
}

