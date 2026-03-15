/* ── Sidebar resize handle ────────────────────────────────────────────────── */
(function () {
  var MIN_W = 160, MAX_W = 480;
  var handle  = document.getElementById('resizeHandle');
  var sidebar = document.getElementById('sidebar');
  var startX = 0, startW = 0;

  // 恢复上次保存的宽度
  var saved = localStorage.getItem('sb-width');
  if (saved) {
    var sw = Math.min(MAX_W, Math.max(MIN_W, +saved));
    sidebar.style.width = sw + 'px';
    document.documentElement.style.setProperty('--sb-w', sw + 'px');
  }

  // 使用 Pointer Events + setPointerCapture：
  // capture 后 pointermove/pointerup 事件直接发给 handle，
  // 不需要在 document 层监听，在 WSLg/XWayland 下更可靠。
  handle.addEventListener('pointerdown', function(e) {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    startX = e.clientX;
    startW = sidebar.offsetWidth;
    handle.classList.add('dragging');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.body.style.webkitUserSelect = 'none';
  });

  handle.addEventListener('pointermove', function(e) {
    if (!handle.hasPointerCapture(e.pointerId)) return;
    var w = Math.min(MAX_W, Math.max(MIN_W, startW + (e.clientX - startX)));
    sidebar.style.width = w + 'px';
    document.documentElement.style.setProperty('--sb-w', w + 'px');
  });

  handle.addEventListener('pointerup', function(e) {
    if (!handle.hasPointerCapture(e.pointerId)) return;
    handle.releasePointerCapture(e.pointerId);
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    document.body.style.webkitUserSelect = '';
    localStorage.setItem('sb-width', sidebar.offsetWidth);
  });

  handle.addEventListener('pointercancel', function() {
    handle.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    document.body.style.webkitUserSelect = '';
  });
})();

