/* ── Appearance settings ─────────────────────────────────────────────────── */
const APPEARANCE_DEFAULTS = {
  theme: 'dark',
  bg_type: 'color',
  bg_color: '#0d1117',
  bg_gradient: 'deep-space',
  bg_image_url: '',
  bg_opacity: 70,
  bg_fit: 'cover',
};

const BG_GRADIENTS = {
  'deep-space': 'linear-gradient(135deg, #0f0c29, #302b63, #24243e)',
  sunset: 'linear-gradient(135deg, #ff7e5f, #feb47b)',
  ocean: 'linear-gradient(135deg, #2193b0, #6dd5ed)',
  forest: 'linear-gradient(135deg, #11998e, #38ef7d)',
  'purple-haze': 'linear-gradient(135deg, #8a2387, #e94057, #f27121)',
};

let _appearance = { ...APPEARANCE_DEFAULTS };
let _appearanceBound = false;

function _loadAppearanceFromConfig(cfg) {
  const opacityRaw = Number(cfg.bg_opacity);
  _appearance = {
    theme: cfg.theme || APPEARANCE_DEFAULTS.theme,
    bg_type: cfg.bg_type || APPEARANCE_DEFAULTS.bg_type,
    bg_color: cfg.bg_color || APPEARANCE_DEFAULTS.bg_color,
    bg_gradient: cfg.bg_gradient || APPEARANCE_DEFAULTS.bg_gradient,
    bg_image_url: cfg.bg_image_url || APPEARANCE_DEFAULTS.bg_image_url,
    bg_opacity: Number.isFinite(opacityRaw) ? Math.max(0, Math.min(100, opacityRaw)) : APPEARANCE_DEFAULTS.bg_opacity,
    bg_fit: cfg.bg_fit || APPEARANCE_DEFAULTS.bg_fit,
  };
}

function _setActiveButtons(selector, dataKey, value) {
  document.querySelectorAll(selector).forEach(btn => {
    btn.classList.toggle('active', btn.dataset[dataKey] === value);
  });
}

function _syncAppearanceVisibility() {
  const showColor = _appearance.bg_type === 'color';
  const showGradient = _appearance.bg_type === 'gradient';
  const showImage = _appearance.bg_type === 'image';
  const colorField = document.getElementById('bgColorField');
  const gradientField = document.getElementById('bgGradientField');
  const imageField = document.getElementById('bgImageField');
  if (colorField) colorField.style.display = showColor ? '' : 'none';
  if (gradientField) gradientField.style.display = showGradient ? '' : 'none';
  if (imageField) imageField.style.display = showImage ? '' : 'none';

  const customField = document.getElementById('customImageField');
  if (customField) {
    customField.style.display = showImage ? '' : 'none';
  }
}

function _resolveImageUrl(raw) {
  if (!raw) return '';
  const value = String(raw).trim().replace(/\\/g, '/');
  if (!value) return '';
  if (value.startsWith('/data/background/')) return value;
  if (value.startsWith('data/background/')) return `/${value}`;
  if (value.startsWith('backgrounds/')) {
    return `/data/background/${encodeURIComponent(value.split('/').pop() || '')}`;
  }
  if (!value.includes('/')) {
    return `/data/background/${encodeURIComponent(value)}`;
  }
  if (/^(https?:|file:|data:)/i.test(raw)) return raw;
  if (/^[a-zA-Z]:[\\/]/.test(value)) {
    const fixed = value.replace(/\\/g, '/');
    return `file:///${fixed}`;
  }
  if (value.startsWith('/')) return `file://${value}`;
  return value;
}

function _hexToRgb(hex) {
  const h = String(hex || '').trim().replace('#', '');
  if (!/^[0-9a-fA-F]{3,8}$/.test(h)) return null;
  let full = h;
  if (h.length === 3) full = h.split('').map(x => x + x).join('');
  if (full.length === 8) full = full.slice(0, 6);
  if (full.length !== 6) return null;
  const n = parseInt(full, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function _getAppearanceBackground() {
  if (_appearance.bg_type === 'gradient') {
    return {
      base: BG_GRADIENTS[_appearance.bg_gradient] || BG_GRADIENTS['deep-space'],
      image: '',
      size: '',
      position: '',
      repeat: '',
    };
  }
  if (_appearance.bg_type === 'image') {
    const imageUrl = _resolveImageUrl(_appearance.bg_image_url);
    if (imageUrl) {
      const fit = _appearance.bg_fit || 'cover';
      const fitMap = {
        cover: { size: 'cover', position: 'center', repeat: 'no-repeat' },
        contain: { size: 'contain', position: 'center', repeat: 'no-repeat' },
        stretch: { size: '100% 100%', position: 'center', repeat: 'no-repeat' },
        tile: { size: 'auto', position: 'left top', repeat: 'repeat' },
      };
      const picked = fitMap[fit] || fitMap.cover;
      const opacity = Math.max(0, Math.min(100, Number(_appearance.bg_opacity ?? 70))) / 100;
      const rgb = _hexToRgb(_appearance.bg_color) || { r: 13, g: 17, b: 23 };
      const overlayAlpha = 1 - opacity;
      const overlay = `linear-gradient(rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${overlayAlpha}), rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${overlayAlpha}))`;
      return {
        base: _appearance.bg_color,
        image: `${overlay}, url("${imageUrl}")`,
        size: `100% 100%, ${picked.size}`,
        position: `center, ${picked.position}`,
        repeat: `no-repeat, ${picked.repeat}`,
      };
    }
    return { base: _appearance.bg_color, image: '', size: '', position: '', repeat: '' };
  }
  return {
    base: _appearance.bg_color,
    image: '',
    size: '',
    position: '',
    repeat: '',
  };
}

function _applyAppearanceToApp() {
  const bg = _getAppearanceBackground();
  document.documentElement.setAttribute('data-theme', _appearance.theme);
  document.documentElement.style.setProperty('--bg', bg.base);
  document.documentElement.style.setProperty('--bg-image-opacity', String(Math.max(0, Math.min(100, Number(_appearance.bg_opacity ?? 70))) / 100));
  document.body.style.backgroundImage = bg.image || 'none';
  document.body.style.backgroundSize = bg.size || '';
  document.body.style.backgroundPosition = bg.position || '';
  document.body.style.backgroundRepeat = bg.repeat || '';
}

function _updateAppearancePreview() {
  const preview = document.getElementById('appearancePreview');
  if (!preview) return;
  const bg = _getAppearanceBackground();
  preview.style.background = bg.base;
  preview.style.backgroundImage = bg.image || 'none';
  preview.style.backgroundSize = bg.size || '';
  preview.style.backgroundPosition = bg.position || '';
  preview.style.backgroundRepeat = bg.repeat || '';
}

function _updateAppearanceUI() {
  _setActiveButtons('.theme-option', 'theme', _appearance.theme);
  _setActiveButtons('.bg-type-option', 'type', _appearance.bg_type);
  _setActiveButtons('.gradient-option', 'gradient', _appearance.bg_gradient);

  const picker = document.getElementById('bgColorPicker');
  const value = document.getElementById('bgColorValue');
  if (picker) picker.value = _appearance.bg_color;
  if (value) value.value = _appearance.bg_color;

  const customUrl = document.getElementById('customImageUrl');
  if (customUrl) customUrl.value = _appearance.bg_image_url || '';

  const opacityRange = document.getElementById('bgOpacityRange');
  const opacityValue = document.getElementById('bgOpacityValue');
  const fitMode = document.getElementById('bgFitMode');
  if (opacityRange) opacityRange.value = String(_appearance.bg_opacity);
  if (opacityValue) opacityValue.value = String(_appearance.bg_opacity);
  if (fitMode) fitMode.value = _appearance.bg_fit || 'cover';

  _syncAppearanceVisibility();
  _updateAppearancePreview();
}

function _bindAppearanceInputs() {
  if (_appearanceBound) return;
  _appearanceBound = true;

  const picker = document.getElementById('bgColorPicker');
  const value = document.getElementById('bgColorValue');
  if (picker && value) {
    const applyColor = val => {
      if (!val) return;
      _appearance.bg_color = val;
      picker.value = val;
      value.value = val;
      _applyAppearanceToApp();
      _updateAppearancePreview();
    };
    picker.addEventListener('input', () => applyColor(picker.value));
    value.addEventListener('change', () => applyColor(value.value.trim()));
  }

  const customUrl = document.getElementById('customImageUrl');
  if (customUrl) {
    customUrl.addEventListener('input', () => {
      _appearance.bg_image_url = customUrl.value.trim();
      _applyAppearanceToApp();
      _updateAppearancePreview();
    });
  }

  const opacityRange = document.getElementById('bgOpacityRange');
  const opacityValue = document.getElementById('bgOpacityValue');
  if (opacityRange && opacityValue) {
    const applyOpacity = val => {
      const n = Number(val);
      if (!Number.isFinite(n)) return;
      const clamped = Math.max(0, Math.min(100, n));
      _appearance.bg_opacity = clamped;
      opacityRange.value = String(clamped);
      opacityValue.value = String(clamped);
      _applyAppearanceToApp();
      _updateAppearancePreview();
    };
    opacityRange.addEventListener('input', () => applyOpacity(opacityRange.value));
    opacityValue.addEventListener('change', () => applyOpacity(opacityValue.value));
  }

  const fitMode = document.getElementById('bgFitMode');
  if (fitMode) {
    fitMode.addEventListener('change', () => {
      _appearance.bg_fit = fitMode.value || 'cover';
      _applyAppearanceToApp();
      _updateAppearancePreview();
    });
  }
}

function selectTheme(theme) {
  _appearance.theme = theme;
  _setActiveButtons('.theme-option', 'theme', theme);
  _applyAppearanceToApp();
  _updateAppearancePreview();
}

function selectBgType(type) {
  _appearance.bg_type = type;
  _setActiveButtons('.bg-type-option', 'type', type);
  _syncAppearanceVisibility();
  _applyAppearanceToApp();
  _updateAppearancePreview();
}

function selectGradient(preset) {
  _appearance.bg_gradient = preset;
  _setActiveButtons('.gradient-option', 'gradient', preset);
  _applyAppearanceToApp();
  _updateAppearancePreview();
}

async function pickCustomImageFile() {
  const paths = await api()?.open_image_dialog();
  if (!paths || paths.length === 0) return;
  const path = Array.isArray(paths) ? paths[0] : paths;
  if (!path) return;

  let finalPath = path;
  try {
    const imported = await api()?.import_background_image(path);
    if (typeof imported === 'string' && imported.trim()) {
      finalPath = imported.trim();
    } else if (imported && typeof imported.path === 'string' && imported.path.trim()) {
      finalPath = imported.path.trim();
    }
  } catch (_) {}

  _appearance.bg_type = 'image';
  _appearance.bg_image_url = finalPath;
  const customUrl = document.getElementById('customImageUrl');
  if (customUrl) customUrl.value = finalPath;
  _setActiveButtons('.bg-type-option', 'type', 'image');
  _syncAppearanceVisibility();
  _applyAppearanceToApp();
  _updateAppearancePreview();
}

