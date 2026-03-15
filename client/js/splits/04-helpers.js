/* ── Helpers ─────────────────────────────────────────────────────────────── */
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function relativeTime(ts) {
  if (!ts) return '';
  const d = Date.now() / 1000 - ts;
  if (d < 60)   return '刚刚';
  if (d < 3600) return Math.floor(d/60) + ' 分钟前';
  if (d < 86400) return Math.floor(d/3600) + ' 小时前';
  return Math.floor(d/86400) + ' 天前';
}

function scrollBottom(force=false) {
  const c = document.getElementById('msgContainer');
  if (!c) return;
  // 如果用户已向上滑动（距底部超过 80px）且不是强制模式，不自动滚动
  if (!force && c.scrollHeight - c.scrollTop - c.clientHeight > 80) return;
  c.scrollTo({ top: c.scrollHeight, behavior: 'smooth' });
}

