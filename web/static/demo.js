// ── Demo tour state ────────────────────────────────────────────────────────────
const TOTAL_SLIDES = 7;
const CHAT_SLIDE = 2; // index of the scripted chat slide
let _demoSlide = 0;

// Scripted chat conversation for the chat slide
// Each entry: { role: 'user'|'assistant', text: string }
const DEMO_SCRIPT = [
  {
    role: 'user',
    text: 'Quiero estudiar la Unidad 2: Cálculo Diferencial',
  },
  {
    role: 'assistant',
    text: 'Genial. En **Cálculo Diferencial** el concepto central es la **derivada**.\n\nRecuerda que la derivada de $f$ en $x$ se define como:\n$$f\'(x) = \\lim_{h \\to 0} \\frac{f(x+h) - f(x)}{h}$$\n¿Qué entiendes tú por "tasa de cambio instantánea"?',
  },
  {
    role: 'user',
    text: 'Dame un ejercicio de derivadas',
  },
  {
    role: 'assistant',
    text: 'Aquí va tu ejercicio:\n\nCalcula la derivada de $f(x) = 3x^2 + 2x - 5$.\n\nRecuerda la regla de potencias: $\\frac{d}{dx}[x^n] = nx^{n-1}$.\n\n¿Cuál es tu enfoque para resolverlo?',
  },
  {
    role: 'user',
    text: 'Creo que es 6x + 2',
  },
  {
    role: 'assistant',
    text: '¡Correcto! $f\'(x) = 6x + 2$. Lo lograste aplicando la regla de potencias término a término:\n- $\\frac{d}{dx}[3x^2] = 6x$\n- $\\frac{d}{dx}[2x] = 2$\n- $\\frac{d}{dx}[-5] = 0$\n\n¿Quieres intentar uno más difícil?',
  },
];

let _chatStep = 0; // how many script messages have been revealed

// ── Helpers ────────────────────────────────────────────────────────────────────
const KATEX_OPTS = {
  delimiters: [
    { left: '$$', right: '$$', display: true  },
    { left: '$',  right: '$',  display: false },
    { left: '\\[', right: '\\]', display: true  },
    { left: '\\(', right: '\\)', display: false },
  ],
  throwOnError: false,
};

function _esc(t) {
  return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _formatBot(text) {
  const blocks = [];
  const stash = m => { blocks.push(m); return `\x00B${blocks.length - 1}\x00`; };
  let t = text
    .replace(/\$\$[\s\S]*?\$\$/g, stash)
    .replace(/\\\[[\s\S]*?\\\]/g, stash)
    .replace(/\$[^$\n]+?\$/g, stash)
    .replace(/\\\([^)\n]*?\\\)/g, stash);
  t = _esc(t);
  let html;
  if (window.marked) {
    html = marked.parse(t, { breaks: true });
  } else {
    html = t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>')
            .replace(/\n/g, '<br>');
  }
  html = html.replace(/\x00B(\d+)\x00/g, (_, i) => _esc(blocks[+i]));
  html = html.replace(/\\\$/g, '$');
  return html;
}

function _renderMath(el) {
  if (window.renderMathInElement) {
    renderMathInElement(el, KATEX_OPTS);
  } else {
    el.classList.add('needs-math');
  }
}

// ── Chat rendering ─────────────────────────────────────────────────────────────
function _appendDemoMsg(role, text) {
  const container = document.getElementById('demo-messages');
  if (!container) return;
  const wrap = document.createElement('div');
  wrap.className = `message ${role} demo-msg-in`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  if (role === 'user') {
    bubble.textContent = text;
  } else {
    bubble.innerHTML = _formatBot(text);
    _renderMath(bubble);
  }
  wrap.appendChild(bubble);
  container.appendChild(wrap);
  container.scrollTop = container.scrollHeight;
}

function _revealNextChatMsg() {
  if (_chatStep < DEMO_SCRIPT.length) {
    const entry = DEMO_SCRIPT[_chatStep++];
    _appendDemoMsg(entry.role, entry.text);
    return true; // more to reveal
  }
  return false; // all revealed
}

function _resetChatSlide() {
  _chatStep = 0;
  const container = document.getElementById('demo-messages');
  if (container) container.innerHTML = '';
}

// ── Slide 1: Login mockup ───────────────────────────────────────────────────────
let _loginTimer = null;

function startLoginAnim() {
  const status = document.getElementById('demo-login-status');
  const spinner = document.getElementById('demo-login-spinner');
  const text = document.getElementById('demo-login-text');
  if (!status) return;
  status.classList.remove('demo-login-success');
  if (spinner) spinner.style.display = '';
  if (text) text.textContent = 'Conectando…';
  _loginTimer = setTimeout(() => {
    if (spinner) spinner.style.display = 'none';
    if (text) text.textContent = '✓ Conectado como Matías';
    status.classList.add('demo-login-success');
    _loginTimer = null;
  }, 1400);
}

function stopLoginAnim() {
  if (_loginTimer) { clearTimeout(_loginTimer); _loginTimer = null; }
  const status = document.getElementById('demo-login-status');
  const spinner = document.getElementById('demo-login-spinner');
  const text = document.getElementById('demo-login-text');
  if (status) status.classList.remove('demo-login-success');
  if (spinner) spinner.style.display = '';
  if (text) text.textContent = 'Conectando…';
}

// ── Slide 3: Gestor de archivos (drag-to-folder) ────────────────────────────────
let _filesInterval = null;
let _filesSubTimers = [];

function _filesCycle() {
  const tile = document.getElementById('demo-drag-tile');
  const folder = document.getElementById('demo-folder-tile');
  if (!tile || !folder) return;
  tile.classList.remove('demo-drag-fly');
  folder.classList.remove('demo-folder-glow');
  void tile.offsetWidth; // force reflow so the animation can restart
  tile.classList.add('demo-drag-fly');
  _filesSubTimers.push(setTimeout(() => folder.classList.add('demo-folder-glow'), 650));
  _filesSubTimers.push(setTimeout(() => folder.classList.remove('demo-folder-glow'), 1450));
}

function startFilesAnim() {
  _filesCycle();
  _filesInterval = setInterval(_filesCycle, 2800);
}

function stopFilesAnim() {
  if (_filesInterval) { clearInterval(_filesInterval); _filesInterval = null; }
  _filesSubTimers.forEach(id => clearTimeout(id));
  _filesSubTimers = [];
  const tile = document.getElementById('demo-drag-tile');
  const folder = document.getElementById('demo-folder-tile');
  if (tile) tile.classList.remove('demo-drag-fly');
  if (folder) folder.classList.remove('demo-folder-glow');
}

// ── Slide 4: Calendario inteligente (typewriter + reveal) ───────────────────────
const CAL_NL_TEXT = 'tengo control de integrales en una semana más';
let _calTimers = [];

function _clearCalTimers() {
  _calTimers.forEach(id => clearTimeout(id));
  _calTimers = [];
}

function _resetCalMock() {
  const input = document.getElementById('demo-nl-input');
  if (input) input.value = '';
  const status = document.getElementById('demo-nl-status');
  if (status) status.style.display = 'none';
  document.querySelectorAll('.demo-cal-reveal').forEach(el => el.classList.remove('shown'));
}

function _runCalCycle() {
  const input = document.getElementById('demo-nl-input');
  let i = 0;
  function typeChar() {
    if (!input) return;
    if (i <= CAL_NL_TEXT.length) {
      input.value = CAL_NL_TEXT.slice(0, i);
      i++;
      _calTimers.push(setTimeout(typeChar, 45));
    } else {
      _calTimers.push(setTimeout(_revealCalEvents, 500));
    }
  }
  typeChar();
}

function _revealCalEvents() {
  const cells = Array.from(document.querySelectorAll('.demo-cal-reveal'));
  cells.forEach((el, idx) => {
    _calTimers.push(setTimeout(() => el.classList.add('shown'), idx * 220));
  });
  const status = document.getElementById('demo-nl-status');
  const afterReveal = cells.length * 220 + 250;
  _calTimers.push(setTimeout(() => {
    if (status) status.style.display = 'block';
  }, afterReveal));
  // Loop the whole sequence while the slide stays visible.
  _calTimers.push(setTimeout(() => {
    _resetCalMock();
    _runCalCycle();
  }, afterReveal + 2800));
}

function startCalendarAnim() {
  _resetCalMock();
  _runCalCycle();
}

function stopCalendarAnim() {
  _clearCalTimers();
  _resetCalMock();
}

// ── Slide 5: Pomodoro countdown ──────────────────────────────────────────────────
let _pomodoroTimer = null;

function startPomodoroAnim() {
  let totalSec = 25 * 60;
  const el = document.getElementById('demo-pomodoro-time');
  const render = () => {
    if (!el) return;
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    el.textContent = String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  };
  render();
  _pomodoroTimer = setInterval(() => {
    totalSec = totalSec > 0 ? totalSec - 1 : 25 * 60;
    render();
  }, 1000);
}

function stopPomodoroAnim() {
  if (_pomodoroTimer) { clearInterval(_pomodoroTimer); _pomodoroTimer = null; }
  const el = document.getElementById('demo-pomodoro-time');
  if (el) el.textContent = '25:00';
}

// ── Per-slide animation lifecycle ───────────────────────────────────────────────
const SLIDE_ANIM = {
  1: { enter: startLoginAnim,    exit: stopLoginAnim },
  3: { enter: startFilesAnim,    exit: stopFilesAnim },
  4: { enter: startCalendarAnim, exit: stopCalendarAnim },
  5: { enter: startPomodoroAnim, exit: stopPomodoroAnim },
};

// ── Dots ───────────────────────────────────────────────────────────────────────
function _buildDots() {
  const el = document.getElementById('demo-dots');
  if (!el) return;
  el.innerHTML = '';
  for (let i = 0; i < TOTAL_SLIDES; i++) {
    const d = document.createElement('button');
    d.className = 'demo-dot' + (i === _demoSlide ? ' active' : '');
    d.setAttribute('aria-label', `Ir al paso ${i + 1}`);
    d.onclick = () => demoGoTo(i);
    el.appendChild(d);
  }
}

// ── Navigation ─────────────────────────────────────────────────────────────────
function demoNav(dir) {
  // On the chat slide, advance the script first before moving to the next slide.
  if (_demoSlide === CHAT_SLIDE && dir === 1 && _chatStep < DEMO_SCRIPT.length) {
    _revealNextChatMsg();
    _updateNavButtons();
    return;
  }
  demoGoTo(_demoSlide + dir);
}

function demoGoTo(idx) {
  if (idx < 0 || idx >= TOTAL_SLIDES || idx === _demoSlide) return;

  // Clean up whatever animation belongs to the slide we're leaving.
  const leaving = SLIDE_ANIM[_demoSlide];
  if (leaving && leaving.exit) leaving.exit();
  if (_demoSlide === CHAT_SLIDE) _resetChatSlide();

  // Hide current
  const slides = document.querySelectorAll('.demo-slide');
  slides.forEach(s => s.classList.remove('active'));

  _demoSlide = idx;
  slides[_demoSlide].classList.add('active');

  // On entering the chat slide, reveal the first message.
  if (_demoSlide === CHAT_SLIDE) {
    _revealNextChatMsg();
  }

  // Start whatever animation belongs to the slide we're entering.
  const entering = SLIDE_ANIM[_demoSlide];
  if (entering && entering.enter) entering.enter();

  _buildDots();
  _updateNavButtons();

  const label = document.getElementById('demo-step-label');
  if (label) label.textContent = `Paso ${_demoSlide + 1} de ${TOTAL_SLIDES}`;
}

function _updateNavButtons() {
  const prev = document.getElementById('demo-prev');
  const next = document.getElementById('demo-next');
  const final = document.getElementById('demo-final');

  if (prev) prev.disabled = _demoSlide === 0;

  const isLast = _demoSlide === TOTAL_SLIDES - 1;

  if (next) {
    if (isLast) {
      next.style.display = 'none';
    } else {
      next.style.display = '';
      if (_demoSlide === CHAT_SLIDE && _chatStep < DEMO_SCRIPT.length) {
        next.textContent = 'Siguiente mensaje →';
      } else {
        next.textContent = 'Siguiente →';
      }
    }
  }

  if (final) {
    final.style.display = isLast ? 'flex' : 'none';
  }
}

// ── Keyboard shortcuts ───────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft') {
    e.preventDefault();
    demoGoTo(_demoSlide - 1);
  } else if (e.key === 'ArrowRight') {
    e.preventDefault();
    demoNav(1);
  }
});

// ── Init ───────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  _buildDots();
  _updateNavButtons();
  // Slide 0 (intro) has no scripted animation; reveal the first chat message
  // only happens when the chat slide becomes active via demoGoTo.
});
