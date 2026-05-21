const SESSION_KEY = 'atenea_session_id';
let sessionId = localStorage.getItem(SESSION_KEY) || createSessionId();
localStorage.setItem(SESSION_KEY, sessionId);

function createSessionId() {
  return 'sess_' + Math.random().toString(36).slice(2, 11);
}

const messagesEl = document.getElementById('messages');
const inputEl    = document.getElementById('input');
const sendBtn    = document.getElementById('send-btn');

function getCourse() {
  const sel = document.getElementById('course-select');
  return sel ? sel.value || null : null;
}

function appendMessage(role, html) {
  const wrap = document.createElement('div');
  wrap.className = `message ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = html;
  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrap;
}

function formatBot(text) {
  const safe = text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return safe
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>');
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  appendMessage('user', text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'));
  inputEl.value = '';
  inputEl.style.height = 'auto';
  sendBtn.disabled = true;

  const loading = appendMessage('assistant loading', 'Pensando...');

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: text, course: getCourse() }),
    });
    const data = await res.json();
    loading.remove();
    appendMessage('assistant', data.error ? `Error: ${data.error}` : formatBot(data.response));
  } catch {
    loading.remove();
    appendMessage('assistant', 'Error al conectar con el servidor.');
  } finally {
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

async function newChat() {
  await fetch(`/api/session/${sessionId}`, { method: 'DELETE' });
  sessionId = createSessionId();
  localStorage.setItem(SESSION_KEY, sessionId);
  messagesEl.innerHTML = '';
  appendMessage('assistant', '¡Nueva sesion iniciada! ¿Sobre que quieres estudiar hoy?');
}

// Submit on Enter (Shift+Enter = newline)
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// Auto-resize textarea
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = inputEl.scrollHeight + 'px';
});

// Greeting on load
appendMessage('assistant', '¡Hola! Soy Atenea, tu asistente de estudio. ¿Que tema quieres explorar hoy?');
