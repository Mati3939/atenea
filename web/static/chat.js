// ── Session ───────────────────────────────────────────────────────────────────
const SESSION_KEY = 'atenea_session_id';
let sessionId = localStorage.getItem(SESSION_KEY) || _mkSessionId();
localStorage.setItem(SESSION_KEY, sessionId);

function _mkSessionId() {
  return 'sess_' + Math.random().toString(36).slice(2, 11);
}

// ── App state ─────────────────────────────────────────────────────────────────
let _flowState       = 'greeting'; // 'greeting' | 'choosing_mode' | 'choosing_course' | 'chatting'
let _activeCourse    = null;       // safe_name para la API
let _activeDifficulty = 'practicando';
let _canvasCourses   = [];         // [{canvas_id, label, safe_name, indexed}]
let _selectedMode    = null;       // 'estudiar' | 'ejercitar' | 'preguntar'

// ── DOM refs ──────────────────────────────────────────────────────────────────
const messagesEl = document.getElementById('messages');
const inputEl    = document.getElementById('input');
const sendBtn    = document.getElementById('send-btn');
const badgeEl    = document.getElementById('course-badge');

// ── Math rendering ─────────────────────────────────────────────────────────────
const KATEX_OPTS = {
  delimiters: [
    { left: '$$', right: '$$', display: true  },
    { left: '$',  right: '$',  display: false },
    { left: '\\[', right: '\\]', display: true  },
    { left: '\\(', right: '\\)', display: false },
  ],
  throwOnError: false,
};

function renderMath(el) {
  if (window.renderMathInElement) {
    renderMathInElement(el, KATEX_OPTS);
  } else {
    // KaTeX deferred — queue for when it loads
    window._pendingMathRender = () => {
      document.querySelectorAll('.bubble.needs-math').forEach(b => {
        renderMathInElement(b, KATEX_OPTS);
        b.classList.remove('needs-math');
      });
      window._pendingMathRender = null;
    };
    el.classList.add('needs-math');
  }
}

// ── Message rendering ──────────────────────────────────────────────────────────
function appendMessage(role, text) {
  const wrap   = document.createElement('div');
  wrap.className = `message ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  if (role.includes('user')) {
    bubble.textContent = text;
  } else if (role === 'greeting') {
    // Large greeting format
    bubble.innerHTML = `<span class="greeting-title">${_esc(text)}</span>`;
  } else {
    bubble.innerHTML = _formatBot(text);
    renderMath(bubble);
  }

  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrap;
}

function _updateMessage(wrap, text) {
  const bubble = wrap.querySelector('.bubble');
  bubble.innerHTML = _formatBot(text);
  renderMath(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function appendOptions(options, handler = null) {
  clearOptions();
  if (!options || !options.length) return;
  const row = document.createElement('div');
  row.className = 'quick-replies';
  options.forEach((opt, i) => {
    const btn = document.createElement('button');
    btn.className = 'quick-reply-btn';
    btn.style.animationDelay = `${i * 0.05}s`;
    btn.textContent = opt;
    btn.onclick = () => {
      clearOptions();
      if (handler) handler(opt);
      else doSend(opt);
    };
    row.appendChild(btn);
  });
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function clearOptions() {
  document.querySelectorAll('.quick-replies').forEach(r => r.remove());
}

// ── Text formatting ────────────────────────────────────────────────────────────
function _esc(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function _formatBot(text) {
  // Extract LaTeX blocks before HTML-escaping so delimiters are preserved
  const blocks = [];
  let t = text
    .replace(/\$\$[\s\S]*?\$\$/g,  m => { blocks.push(m); return `\x00B${blocks.length-1}\x00`; })
    .replace(/\\\[[\s\S]*?\\\]/g,  m => { blocks.push(m); return `\x00B${blocks.length-1}\x00`; })
    .replace(/\$[^$\n]+?\$/g,      m => { blocks.push(m); return `\x00B${blocks.length-1}\x00`; })
    .replace(/\\\([^)]+?\\\)/g,    m => { blocks.push(m); return `\x00B${blocks.length-1}\x00`; });

  // HTML-escape non-LaTeX content
  t = _esc(t);

  // Markdown: bold, italic, inline code
  t = t
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g,     '<em>$1</em>')
    .replace(/`([^`\n]+)`/g,   '<code>$1</code>');

  // Restore LaTeX blocks (unescaped)
  t = t.replace(/\x00B(\d+)\x00/g, (_, i) => blocks[+i]);

  return t;
}

// ── Greeting flow ──────────────────────────────────────────────────────────────
function showGreeting() {
  _flowState    = 'greeting';
  _activeCourse = null;
  _selectedMode = null;
  messagesEl.innerHTML = '';

  if (badgeEl) { badgeEl.textContent = ''; badgeEl.classList.remove('visible'); }

  _setInputEnabled(false);

  appendMessage('greeting', '¿Qué haremos hoy?');
  appendOptions(
    ['Estudiar 📚', 'Ejercitar ✏️', 'Preguntar 💬'],
    _handleModeSelection
  );
}

async function _handleModeSelection(mode) {
  _selectedMode = mode;
  appendMessage('user', mode);
  _flowState = 'choosing_course';

  const loading = appendMessage('assistant loading', 'Cargando tus ramos de Canvas...');

  try {
    const res  = await fetch('/api/canvas/courses');
    const data = await res.json();
    loading.remove();

    if (data.error) {
      appendMessage('assistant', `⚠️ ${data.error}`);
      return;
    }

    _canvasCourses = data.courses || [];

    if (!_canvasCourses.length) {
      appendMessage('assistant', 'No encontré cursos activos en Canvas. Verifica tu token en `.env`.');
      return;
    }

    appendMessage('assistant', '¿En qué ramo estamos trabajando?');
    appendOptions(
      _canvasCourses.map(c => c.label),
      label => _handleCourseSelection(label)
    );
  } catch {
    loading.remove();
    appendMessage('assistant', 'No pude conectar con Canvas. Verifica la configuración en `.env`.');
  }
}

async function _handleCourseSelection(label) {
  const course = _canvasCourses.find(c => c.label === label);
  if (!course) return;

  appendMessage('user', label);
  _activeCourse = course.safe_name;

  if (badgeEl) { badgeEl.textContent = label; badgeEl.classList.add('visible'); }

  if (!course.indexed) {
    await _autoFetchCourse(course);
    // Mark it as indexed locally so new chat doesn't re-fetch
    course.indexed = true;
  }

  _flowState = 'chatting';
  _setInputEnabled(true);

  const modeVerb = _selectedMode.includes('Estudiar') ? 'estudiar'
    : _selectedMode.includes('Ejercitar') ? 'practicar ejercicios en'
    : 'resolver dudas sobre';

  appendMessage('assistant', `¡Listo! Estamos para ${modeVerb} **${label}**. ¿Por dónde empezamos?`);

  if (_selectedMode.includes('Ejercitar')) {
    appendOptions(['Dame un ejercicio', 'Quiero el ejercicio más difícil']);
  } else if (_selectedMode.includes('Estudiar')) {
    appendOptions(['Explícame el tema central', 'Quiero un resumen de la unidad']);
  } else {
    appendOptions(['Tengo una duda específica', 'Explícame desde el principio']);
  }
}

async function _autoFetchCourse(course) {
  const loadWrap = appendMessage('assistant', 'Buscando tu material en Canvas...');
  const bubble   = loadWrap.querySelector('.bubble');

  try {
    await fetch(
      `/api/fetch/course/${course.canvas_id}?name=${encodeURIComponent(course.label)}`,
      { method: 'POST' }
    );
  } catch {
    bubble.innerHTML = _formatBot('⚠️ No pude iniciar la descarga. Continuaré sin material local.');
    return;
  }

  for (let attempt = 0; attempt < 120; attempt++) {
    await new Promise(r => setTimeout(r, 1500));
    let status;
    try {
      status = await fetch(`/api/fetch/course/${course.canvas_id}/status`).then(r => r.json());
    } catch {
      break;
    }

    const pct    = Math.floor(status.percentage || 0);
    const filled = Math.floor(pct / 10);
    const bar    = '▓'.repeat(filled) + '░'.repeat(10 - filled);
    bubble.innerHTML = _formatBot(
      `Buscando material en Canvas...\n${bar} ${pct}%\n*${status.phase || ''}*`
    );
    messagesEl.scrollTop = messagesEl.scrollHeight;

    if (status.done) {
      bubble.innerHTML = _formatBot('✅ ¡Material cargado! Ya tengo tus documentos listos.');
      break;
    }
    if (status.error) {
      bubble.innerHTML = _formatBot(`⚠️ Hubo un problema al cargar el material: ${status.error}\n\nIntentaré responder con lo que tengo.`);
      break;
    }
  }
}

// ── Send logic ─────────────────────────────────────────────────────────────────
async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;
  inputEl.value = '';
  inputEl.style.height = 'auto';
  await doSend(text);
}

async function doSend(text) {
  if (_flowState !== 'chatting') return;
  clearOptions();
  appendMessage('user', text);
  _setInputEnabled(false);

  const loading = appendMessage('assistant loading', 'Pensando...');

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        message:    text,
        course:     _activeCourse,
        unit:       null,
        difficulty: _activeDifficulty,
      }),
    });
    const data = await res.json();
    loading.remove();
    appendMessage('assistant', data.error ? `❌ Error: ${data.error}` : data.response);
    if (data.options) appendOptions(data.options);
  } catch {
    loading.remove();
    appendMessage('assistant', '❌ Error al conectar con el servidor.');
  } finally {
    _setInputEnabled(true);
    inputEl.focus();
  }
}

// ── New chat ───────────────────────────────────────────────────────────────────
async function newChat() {
  try { await fetch(`/api/session/${sessionId}`, { method: 'DELETE' }); } catch {}
  sessionId = _mkSessionId();
  localStorage.setItem(SESSION_KEY, sessionId);
  showGreeting();
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function _setInputEnabled(enabled) {
  inputEl.disabled = !enabled;
  sendBtn.disabled = !enabled;
  if (enabled) inputEl.placeholder = 'Escribe tu respuesta o pregunta...';
  else         inputEl.placeholder = '';
}

// ── Input shortcuts ────────────────────────────────────────────────────────────
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
});

// ── Init ───────────────────────────────────────────────────────────────────────
showGreeting();
