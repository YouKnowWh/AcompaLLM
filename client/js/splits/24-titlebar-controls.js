/* ── Titlebar controls ──────────────────────────────────────────────────── */
let _tbMaximized = false;
function tbToggleMax() {
  _tbMaximized = !_tbMaximized;
  api()?.win_maximize();
  const btn = document.getElementById('tbMaxBtn');
  if (btn) {
    btn.innerHTML = _tbMaximized
      ? `<svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1"><rect x=".5" y="2.5" width="7" height="7"/><path d="M2.5.5h7v7"/></svg>`
      : `<svg viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1"><rect x=".5" y=".5" width="9" height="9"/></svg>`;
  }
}

if (window.pywebview) {
  init();
} else {
  window.addEventListener('pywebviewready', init);
}
