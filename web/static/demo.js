// ── Demo tour state ────────────────────────────────────────────────────────────
const TOTAL_SLIDES = 4;
let _demoSlide = 0;

// Scripted chat conversation for slide 1
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
  // On slide 1 (chat), advance the script first before going forward
  if (_demoSlide === 1 && dir === 1 && _chatStep < DEMO_SCRIPT.length) {
    _revealNextChatMsg();
    _updateNavButtons();
    return;
  }
  demoGoTo(_demoSlide + dir);
}

function demoGoTo(idx) {
  if (idx < 0 || idx >= TOTAL_SLIDES) return;

  // Hide current
  const slides = document.querySelectorAll('.demo-slide');
  slides.forEach(s => s.classList.remove('active'));

  _demoSlide = idx;
  slides[_demoSlide].classList.add('active');

  // On entering slide 1, reveal first message if none yet
  if (_demoSlide === 1 && _chatStep === 0) {
    _revealNextChatMsg();
  }

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
  const chatDone = _demoSlide !== 1 || _chatStep >= DEMO_SCRIPT.length;

  if (next) {
    if (isLast) {
      next.style.display = 'none';
    } else {
      next.style.display = '';
      if (_demoSlide === 1 && _chatStep < DEMO_SCRIPT.length) {
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

// ── Init ───────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  _buildDots();
  _updateNavButtons();
});
