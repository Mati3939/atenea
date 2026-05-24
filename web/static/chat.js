const SESSION_KEY = 'atenea_session_id';
let sessionId = localStorage.getItem(SESSION_KEY) || createSessionId();
localStorage.setItem(SESSION_KEY, sessionId);

function createSessionId() {
  return 'sess_' + Math.random().toString(36).slice(2, 11);
}

const messagesEl  = document.getElementById('messages');
const inputEl     = document.getElementById('input');
const sendBtn     = document.getElementById('send-btn');
const courseEl    = document.getElementById('course-select');
const unitEl      = document.getElementById('unit-select');
const diffEl      = document.getElementById('difficulty-select');

function getCourse()     { return courseEl ? courseEl.value || null : null; }
function getUnit()       { return unitEl   ? unitEl.value   || null : null; }
function getDifficulty() { return diffEl   ? diffEl.value   || 'practicando' : 'practicando'; }

// ── Math rendering ─────────────────────────────────────────────────────────────

const KATEX_OPTS = {
  delimiters: [
    { left: '$$', right: '$$', display: true  },
    { left: '$',  right: '$',  display: false },
    { left: '\\(', right: '\\)', display: false },
    { left: '\\[', right: '\\]', display: true  },
  ],
  throwOnError: false,
};

function renderMath(el) {
  if (window.renderMathInElement) renderMathInElement(el, KATEX_OPTS);
}

// ── Message rendering ──────────────────────────────────────────────────────────

function appendMessage(role, html) {
  const wrap = document.createElement('div');
  wrap.className = `message ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = html;
  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  // Render math after inserting into DOM so element dimensions are available
  if (role === 'assistant') renderMath(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrap;
}

function appendOptions(options) {
  if (!options || !options.length) return;
  const row = document.createElement('div');
  row.className = 'quick-replies';
  options.forEach(opt => {
    const btn = document.createElement('button');
    btn.className = 'quick-reply-btn';
    btn.textContent = opt;
    btn.onclick = () => { clearOptions(); doSend(opt); };
    row.appendChild(btn);
  });
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function clearOptions() {
  document.querySelectorAll('.quick-replies').forEach(r => r.remove());
}

function formatBot(text) {
  // HTML-escape first, then apply markdown. No \n→<br>: CSS white-space:pre-wrap handles newlines.
  const safe = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return safe
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g,     '<em>$1</em>')
    .replace(/`(.*?)`/g,       '<code>$1</code>');
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
  clearOptions();
  appendMessage('user', text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'));
  sendBtn.disabled = true;

  const loading = appendMessage('assistant loading', 'Pensando...');

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        message:    text,
        course:     getCourse(),
        unit:       getUnit(),
        difficulty: getDifficulty(),
      }),
    });
    const data = await res.json();
    loading.remove();
    appendMessage('assistant', data.error ? `Error: ${data.error}` : formatBot(data.response));
    appendOptions(data.options);
  } catch {
    loading.remove();
    appendMessage('assistant', 'Error al conectar con el servidor.');
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

// ── New chat ───────────────────────────────────────────────────────────────────

async function newChat() {
  await fetch(`/api/session/${sessionId}`, { method: 'DELETE' });
  sessionId = createSessionId();
  localStorage.setItem(SESSION_KEY, sessionId);
  messagesEl.innerHTML = '';
  showGreeting();
}

function showGreeting() {
  appendMessage('assistant', '¡Hola! Soy Atenea. Selecciona un curso y unidad, luego elige cómo quieres comenzar.');
  appendOptions(['Dame un ejercicio', 'Explícame el tema']);
}

// ── Unit selector ──────────────────────────────────────────────────────────────

if (courseEl) {
  courseEl.addEventListener('change', async () => {
    if (!unitEl) return;
    const course = getCourse();
    unitEl.innerHTML = '<option value="">Todas las unidades</option>';

    if (!course) {
      unitEl.style.display = 'none';
      return;
    }

    try {
      const res  = await fetch(`/api/units/${encodeURIComponent(course)}`);
      const data = await res.json();
      if (data.units && data.units.length > 0) {
        data.units.forEach(u => {
          const opt = document.createElement('option');
          opt.value = u;
          opt.textContent = u;
          unitEl.appendChild(opt);
        });
        unitEl.style.display = '';
      } else {
        unitEl.style.display = 'none';
      }
    } catch {
      unitEl.style.display = 'none';
    }
  });
}

// ── Input shortcuts ────────────────────────────────────────────────────────────

inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = inputEl.scrollHeight + 'px';
});

// ── Init ───────────────────────────────────────────────────────────────────────

showGreeting();
