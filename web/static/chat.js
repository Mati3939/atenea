// ── Session ───────────────────────────────────────────────────────────────────
const SESSION_KEY = 'atenea_session_id';
let sessionId = localStorage.getItem(SESSION_KEY) || _mkSessionId();
localStorage.setItem(SESSION_KEY, sessionId);

function _mkSessionId() {
  return 'sess_' + Math.random().toString(36).slice(2, 11);
}

// ── App state ─────────────────────────────────────────────────────────────────
let _flowState        = 'greeting'; // 'greeting' | 'choosing_course' | 'choosing_unit' | 'chatting'
let _activeCourse     = null;       // safe_name para la API
let _activeCourseLabel = null;
let _activeUnit       = null;
let _activeDifficulty = 'practicando';
let _canvasCourses    = [];         // [{canvas_id, label, safe_name, indexed}]
let _selectedMode     = null;       // 'estudiar' | 'ejercitar' | 'preguntar'
let _selectedMethod   = null;       // key de método de estudio; '__none__' = decidido sin método
let _methodLabel      = null;       // nombre legible del método activo (para badge/intro)
let _allMethods       = null;       // cache de GET /api/methods
let _transcript         = [];         // [{role, text}] para repintar al recargar
let _lastOptions        = null;
let _recording          = true;       // off durante el repintado
let _pendingTopicCapture = null;      // Feature E: one-shot topic input intercept

// ── DOM refs ──────────────────────────────────────────────────────────────────
const messagesEl   = document.getElementById('messages');
const inputEl      = document.getElementById('input');
const sendBtn      = document.getElementById('send-btn');
const badgeEl      = document.getElementById('course-badge');
const difficultyEl = document.getElementById('difficulty');

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

// ── Text formatting ────────────────────────────────────────────────────────────
function _esc(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function _formatBot(text) {
  // 1. Extraer segmentos LaTeX antes de procesar (placeholders \x00B<n>\x00)
  const blocks = [];
  const stash = m => { blocks.push(m); return `\x00B${blocks.length - 1}\x00`; };
  let t = text
    .replace(/\$\$[\s\S]*?\$\$/g,   stash)
    .replace(/\\\[[\s\S]*?\\\]/g,   stash)
    .replace(/\$[^$\n]+?\$/g,       stash)
    .replace(/\\\([^)\n]*?\\\)/g,   stash);

  // 2. Neutralizar HTML del modelo y aplicar markdown
  t = _esc(t);
  let html;
  if (window.marked) {
    html = marked.parse(t, { breaks: true });
  } else {
    html = t
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g,     '<em>$1</em>')
      .replace(/`([^`\n]+)`/g,   '<code>$1</code>')
      .replace(/\n/g, '<br>');
  }

  // 3. Restaurar LaTeX (escapado: KaTeX lee el textContent)
  html = html.replace(/\x00B(\d+)\x00/g, (_, i) => _esc(blocks[+i]));

  // 4. Dólares literales escapados por el modelo
  html = html.replace(/\\\$/g, '$');

  return html;
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
    bubble.innerHTML = `<span class="greeting-title">${_esc(text)}</span>`;
  } else {
    bubble.innerHTML = _formatBot(text);
    renderMath(bubble);
  }

  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  if (_recording && (role === 'user' || role === 'assistant') && text) {
    _transcript.push({ role, text });
    _persistState();
  }
  return wrap;
}

function _updateMessage(wrap, text, record = true) {
  const bubble = wrap.querySelector('.bubble');
  bubble.innerHTML = _formatBot(text);
  renderMath(bubble);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  if (_recording && record && text) {
    _transcript.push({ role: 'assistant', text });
    _persistState();
  }
}

// Nota discreta bajo la burbuja cuando la respuesta vino de un modelo/proveedor
// de reserva (cupo del principal agotado). No se guarda en el transcript: es
// informativa para esta sesión en vivo, no parte de la conversación repintada.
function _appendDegradedNote(wrap, provider) {
  const note = document.createElement('div');
  note.className = 'degraded-note';
  note.textContent = provider === 'ollama'
    ? '⚠️ Respuesta en modo reducido (se agotó el cupo del modelo principal) · modo local'
    : '⚠️ Respuesta en modo reducido (se agotó el cupo del modelo principal)';
  wrap.appendChild(note);
}

// Quick-reply fija que emite el agente cuando detecta un anuncio de evaluación
// ("tengo control de X en una semana más"). En vez de enviarse como mensaje,
// navega a Organización con el mensaje original precargado en 'Dile a Atenea'.
const AGENDA_QUICK_REPLY = '📅 Abrir Organización';

function _lastUserMessage() {
  for (let i = _transcript.length - 1; i >= 0; i--) {
    if (_transcript[i].role === 'user') return _transcript[i].text;
  }
  return '';
}

function appendOptions(options, handler = null) {
  clearOptions();
  if (!options || !options.length) return;
  if (!handler) { _lastOptions = options; _persistState(); }
  const row = document.createElement('div');
  row.className = 'quick-replies';
  options.forEach((opt, i) => {
    const btn = document.createElement('button');
    btn.className = 'quick-reply-btn';
    btn.style.animationDelay = `${i * 0.05}s`;
    btn.textContent = opt;
    btn.onclick = () => {
      clearOptions();
      _lastOptions = null;
      if (!handler && opt === AGENDA_QUICK_REPLY) {
        const original = _lastUserMessage();
        window.location.href = '/organizacion?text=' + encodeURIComponent(original);
        return;
      }
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

// ── State persistence (sobrevive recargas de página) ───────────────────────────
function _stateKey() { return 'atenea_chat_' + sessionId; }

function _persistState() {
  if (_flowState !== 'chatting') return;
  try {
    localStorage.setItem(_stateKey(), JSON.stringify({
      transcript: _transcript.slice(-60),
      course: _activeCourse,
      courseLabel: _activeCourseLabel,
      unit: _activeUnit,
      mode: _selectedMode,
      method: _selectedMethod,
      methodLabel: _methodLabel,
      difficulty: _activeDifficulty,
      options: _lastOptions,
    }));
  } catch {}
}

function _restoreState() {
  let saved;
  try { saved = JSON.parse(localStorage.getItem(_stateKey())); } catch {}
  if (!saved || !saved.course || !saved.transcript || !saved.transcript.length) return false;

  _activeCourse      = saved.course;
  _activeCourseLabel = saved.courseLabel;
  _activeUnit        = saved.unit || null;
  _selectedMode      = saved.mode || null;
  _selectedMethod    = saved.method || null;
  _methodLabel       = saved.methodLabel || null;
  _activeDifficulty  = saved.difficulty || 'practicando';
  _transcript        = saved.transcript;
  _lastOptions       = saved.options || null;
  _flowState         = 'chatting';

  if (difficultyEl) difficultyEl.value = _activeDifficulty;
  _updateBadge();

  _recording = false;
  messagesEl.innerHTML = '';
  _transcript.forEach(m => appendMessage(m.role, m.text));
  if (_lastOptions) appendOptions(_lastOptions);
  _recording = true;

  _setInputEnabled(true);
  return true;
}

function _updateBadge() {
  if (!badgeEl) return;
  if (_activeCourseLabel) {
    let txt = _activeUnit ? `${_activeCourseLabel} · ${_activeUnit}` : _activeCourseLabel;
    if (_methodLabel) txt += ` · 🧠 ${_methodLabel}`;
    badgeEl.textContent = txt;
    badgeEl.classList.add('visible');
  } else {
    badgeEl.textContent = '';
    badgeEl.classList.remove('visible');
  }
}

// ── Greeting flow ──────────────────────────────────────────────────────────────
function showGreeting() {
  _flowState    = 'greeting';
  _activeCourse = null;
  _activeCourseLabel = null;
  _activeUnit   = null;
  _selectedMode = null;
  _selectedMethod = null;
  _methodLabel  = null;
  _transcript   = [];
  _lastOptions  = null;
  messagesEl.innerHTML = '';

  _updateBadge();
  _setInputEnabled(false);

  appendMessage('greeting', '¿Qué haremos hoy?');
  appendOptions(
    ['Estudiar 📚', 'Ejercitar ✏️', 'Preguntar 💬'],
    _handleModeSelection
  );
}

function _modeKey(label) {
  if (label.includes('Estudiar'))  return 'estudiar';
  if (label.includes('Ejercitar')) return 'ejercitar';
  return 'preguntar';
}

async function _handleModeSelection(modeLabel) {
  _selectedMode = _modeKey(modeLabel);
  appendMessage('user', modeLabel);
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
  _activeCourse      = course.safe_name;
  _activeCourseLabel = label;
  _updateBadge();

  if (!course.indexed) {
    await _autoFetchCourse(course);
    course.indexed = true;
  }

  // Leer la calendarización del curso y rescatar sus unidades
  const unitsLoading = appendMessage('assistant loading', 'Leyendo la calendarización del curso…');
  let units = [];
  let unitsData = null;
  try {
    unitsData = await fetch(`/api/units/${course.safe_name}`).then(r => r.json());
    if (unitsData.source === 'units') {
      units = unitsData.units || [];
    }
  } catch {}
  unitsLoading.remove();

  // No material: source is 'files' (no _units.json) AND the files list is also empty
  const rawFiles = (unitsData && unitsData.source === 'files') ? (unitsData.units || []) : null;
  const noMaterial = rawFiles !== null && rawFiles.length === 0;
  if (noMaterial) {
    appendMessage('assistant', 'Este ramo aún no tiene material indexado. ¿Cómo quieres continuar?');
    appendOptions(
      ['Subir material', 'Estudiar sin material (conocimiento general)'],
      async opt => {
        if (opt === 'Subir material') {
          await _handleUploadMaterial(course, async () => {
            // Re-check units after upload
            let newUnits = [];
            try {
              const d2 = await fetch(`/api/units/${course.safe_name}`).then(r => r.json());
              if (d2.source === 'units') newUnits = d2.units || [];
            } catch {}
            if (newUnits.length && newUnits.length <= 25) {
              _flowState = 'choosing_unit';
              appendMessage('assistant', '¿Qué unidad quieres preparar?');
              appendOptions(
                [...newUnits, 'Toda la asignatura'],
                u => {
                  appendMessage('user', u);
                  _activeUnit = (u === 'Toda la asignatura') ? null : u;
                  _startChatting();
                }
              );
            } else {
              _startChatting();
            }
          });
        } else {
          // Generic knowledge path — ask for topic
          appendMessage('user', opt);
          appendMessage('assistant', '¿Qué unidad o tema quieres trabajar? (Puedes escribir libremente)');
          _setInputEnabled(true);
          _flowState = 'choosing_unit';
          _pendingTopicCapture = async (topicText) => {
            appendMessage('user', topicText);
            _activeUnit = topicText;
            _setInputEnabled(false);
            _startChatting();
          };
        }
      }
    );
    return;
  }

  if (units.length && units.length <= 25) {
    _flowState = 'choosing_unit';
    appendMessage('assistant', '¿Qué unidad quieres preparar?');
    appendOptions(
      [...units, 'Toda la asignatura'],
      u => {
        appendMessage('user', u);
        _activeUnit = (u === 'Toda la asignatura') ? null : u;
        _startChatting();
      }
    );
  } else {
    _startChatting();
  }
}

async function _handleUploadMaterial(course, onDone) {
  appendMessage('user', 'Subir material');
  return new Promise(resolve => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = '.pdf,.docx,.pptx,.txt,.md,.zip';
    input.style.display = 'none';
    document.body.appendChild(input);

    input.onchange = async () => {
      const files = Array.from(input.files || []);
      input.remove();
      if (!files.length) {
        appendMessage('assistant', 'No seleccionaste ningún archivo.');
        resolve();
        return;
      }

      const uploadMsg = appendMessage('assistant', `Subiendo ${files.length} archivo(s)…`);
      const bubble = uploadMsg.querySelector('.bubble');

      const formData = new FormData();
      files.forEach(f => formData.append('files', f));

      try {
        const res = await fetch(`/api/upload/${course.safe_name}`, {
          method: 'POST',
          body: formData,
        });
        const data = await res.json();
        if (data.indexed !== undefined) {
          bubble.innerHTML = _formatBot(
            `✅ ${data.indexed} archivo(s) indexado(s) correctamente.` +
            (data.failures && data.failures.length ? `\n⚠️ ${data.failures.length} archivo(s) no se pudieron procesar.` : '')
          );
        } else {
          bubble.innerHTML = _formatBot('✅ Material subido.');
        }
      } catch {
        bubble.innerHTML = _formatBot('⚠️ Error al subir los archivos. Puedes intentarlo de nuevo desde el Dashboard.');
      }

      resolve();
      if (onDone) await onDone();
    };

    input.oncancel = () => { input.remove(); resolve(); };
    input.click();
  });
}

// ── Método de estudio (recomendar + elegir antes de empezar) ───────────────────
async function _loadMethods() {
  if (_allMethods) return _allMethods;
  try {
    const d = await fetch('/api/methods').then(r => r.json());
    _allMethods = d.methods || [];
  } catch { _allMethods = []; }
  return _allMethods;
}

async function _chooseMethod() {
  let rec = null;
  try {
    const d = await fetch(`/api/methods/recommend?course=${encodeURIComponent(_activeCourseLabel || '')}`)
      .then(r => r.json());
    rec = (d.recommended && d.recommended[0]) || null;
  } catch {}

  const methods = await _loadMethods();
  const m = rec ? methods.find(x => x.key === rec) : null;
  if (!m) { _selectedMethod = '__none__'; _startChatting(); return; }

  appendMessage('assistant', `🧠 Para **${_activeCourseLabel}** te recomiendo **${m.name}** ${m.emoji || ''}: ${m.short}`);
  appendOptions([`Usar ${m.name}`, 'Elegir otro método'], opt => {
    appendMessage('user', opt);
    if (opt === 'Elegir otro método') {
      _showMethodPicker(methods);
    } else {
      _selectedMethod = m.key;
      _methodLabel    = m.name;
      _startChatting();
    }
  });
}

function _showMethodPicker(methods) {
  appendMessage('assistant', '¿Con qué método quieres estudiar?');
  appendOptions(
    methods.map(m => `${m.emoji || ''} ${m.name}`.trim()),
    label => {
      const m = methods.find(x => label.includes(x.name));
      appendMessage('user', label);
      _selectedMethod = m ? m.key : '__none__';
      _methodLabel    = m ? m.name : null;
      _startChatting();
    }
  );
}

function _startChatting() {
  // Antes de empezar, elegir el método de estudio (recomendado + cambiable).
  if (!_selectedMethod) { _chooseMethod(); return; }

  _flowState = 'chatting';
  _updateBadge();
  _setInputEnabled(true);

  const modeVerb = _selectedMode === 'estudiar' ? 'estudiar'
    : _selectedMode === 'ejercitar' ? 'practicar ejercicios en'
    : 'resolver dudas sobre';

  const methodNote = _methodLabel ? ` Usaremos **${_methodLabel}** como método de estudio.` : '';
  appendMessage('assistant', `¡Listo! Estamos para ${modeVerb} **${_activeCourseLabel}**${_activeUnit ? ` (${_activeUnit})` : ''}.${methodNote} ¿Por dónde empezamos?`);

  if (_selectedMode === 'ejercitar') {
    appendOptions(['Dame un ejercicio', 'Quiero el ejercicio más difícil']);
  } else if (_selectedMode === 'estudiar') {
    appendOptions(['Explícame el tema central', 'Quiero un resumen de la unidad']);
  } else {
    appendOptions(['Tengo una duda específica', 'Explícame desde el principio']);
  }
  _persistState();
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

  for (let attempt = 0; attempt < 200; attempt++) {
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
      let msg = '✅ ¡Material cargado! Ya tengo tus documentos listos.';
      if (status.failures && status.failures.length) {
        msg += `\n\n⚠️ ${status.failures.length} archivo(s) no se pudieron procesar.`;
      }
      bubble.innerHTML = _formatBot(msg);
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

function _chatPayload(text) {
  return JSON.stringify({
    session_id: sessionId,
    message:    text,
    course:     _activeCourse,
    unit:       _activeUnit,
    difficulty: _activeDifficulty,
    mode:       _selectedMode,
    method:     (_selectedMethod && _selectedMethod !== '__none__') ? _selectedMethod : null,
  });
}

async function doSend(text) {
  // Feature E: intercept for topic capture during "no material" flow
  if (_pendingTopicCapture) {
    const fn = _pendingTopicCapture;
    _pendingTopicCapture = null;
    await fn(text);
    return;
  }
  if (_flowState !== 'chatting') return;
  clearOptions();
  _lastOptions = null;
  appendMessage('user', text);
  _setInputEnabled(false);

  try {
    await _sendStreaming(text);
  } catch {
    // Fallback al endpoint sin streaming
    try {
      await _sendBlocking(text);
    } catch {
      appendMessage('assistant', '❌ Error al conectar con el servidor.');
    }
  } finally {
    _setInputEnabled(true);
    inputEl.focus();
  }
}

async function _sendStreaming(text) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: _chatPayload(text),
  });

  if (!res.ok || !res.body) throw new Error('stream no disponible');

  const wrap   = appendMessage('assistant streaming', '');
  const bubble = wrap.querySelector('.bubble');
  const reader = res.body.getReader();
  const dec    = new TextDecoder();

  let buf = '', acc = '', finalData = null, renderQueued = false, finished = false;

  const renderPartial = () => {
    renderQueued = false;
    if (finished) return; // el render final (con KaTeX) ya corrió; no lo pises
    bubble.innerHTML = _formatBot(acc) + '<span class="stream-cursor"></span>';
    messagesEl.scrollTop = messagesEl.scrollHeight;
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });

    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line) continue;
      let ev;
      try { ev = JSON.parse(line); } catch { continue; }
      if (ev.error) { wrap.remove(); throw new Error(ev.error); }
      if (ev.delta) {
        acc += ev.delta;
        if (!renderQueued) { renderQueued = true; requestAnimationFrame(renderPartial); }
      }
      if (ev.done) finalData = ev;
    }
  }

  finished = true;
  wrap.classList.remove('streaming');
  if (finalData) {
    _updateMessage(wrap, finalData.text);
    if (finalData.degraded) _appendDegradedNote(wrap, finalData.provider);
    if (finalData.options) appendOptions(finalData.options);
  } else if (acc) {
    _updateMessage(wrap, acc);
  } else {
    wrap.remove();
    throw new Error('respuesta vacía');
  }
}

async function _sendBlocking(text) {
  const loading = appendMessage('assistant loading', 'Pensando...');
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: _chatPayload(text),
  });
  const data = await res.json();
  loading.remove();
  const wrap = appendMessage('assistant', data.error ? `❌ Error: ${data.error}` : data.response);
  if (!data.error && data.degraded) _appendDegradedNote(wrap, data.provider);
  if (data.options) appendOptions(data.options);
}

// ── New chat ───────────────────────────────────────────────────────────────────
async function newChat() {
  try { await fetch(`/api/session/${sessionId}`, { method: 'DELETE' }); } catch {}
  try { localStorage.removeItem(_stateKey()); } catch {}
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

if (difficultyEl) {
  difficultyEl.addEventListener('change', () => {
    _activeDifficulty = difficultyEl.value;
    _persistState();
  });
}

// ── "Probar este método" desde /metodos (?method=<key>&label=<nombre>) ────────
// Arranca una sesión nueva y enfocada con el método ya preseleccionado, saltando
// _chooseMethod (que solo se dispara cuando _selectedMethod es falsy).
async function _initFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const methodParam = (params.get('method') || '').trim();
  if (!methodParam) return false;

  const labelParam = params.get('label');

  // Limpia el query param sin recargar la página.
  window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);

  // Sesión nueva y limpia (equivalente al botón "Nueva sesión") para que el
  // método aplique desde cero.
  try { await fetch(`/api/session/${sessionId}`, { method: 'DELETE' }); } catch {}
  try { localStorage.removeItem(_stateKey()); } catch {}
  sessionId = _mkSessionId();
  localStorage.setItem(SESSION_KEY, sessionId);

  showGreeting(); // resetea _selectedMethod/_methodLabel — se fijan justo después

  let label = labelParam || null;
  if (!label) {
    const methods = await _loadMethods();
    const m = methods.find(x => x.key === methodParam);
    if (m) label = m.name;
  }
  _selectedMethod = methodParam;
  _methodLabel    = label || methodParam;

  appendMessage('assistant', `🧠 Vamos a estudiar con el método **${_methodLabel}**. En cuanto elijamos el ramo y la unidad, lo aplicamos directamente.`);
  return true;
}

// ── Historial de sesiones ────────────────────────────────────────────────────
// Panel lateral (overlay derecha) con las sesiones guardadas en el servidor
// (logs/web_sessions/<sid>.json vía GET/PUT/DELETE /api/sessions), agrupadas
// por curso. Reutiliza el mismo patrón visual que archivos.html (modal-overlay
// + preview-panel).

function _relativeDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const diffMin = Math.floor((now - d) / 60000);
  if (diffMin < 1) return 'justo ahora';
  if (diffMin < 60) return `hace ${diffMin} min`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `hace ${diffH} h`;
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfD = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const diffDays = Math.round((startOfToday - startOfD) / 86400000);
  if (diffDays === 1) return 'ayer';
  if (diffDays >= 0 && diffDays < 7) return `hace ${diffDays} días`;
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return d.getFullYear() === now.getFullYear() ? `${dd}/${mm}` : `${dd}/${mm}/${d.getFullYear()}`;
}

async function openHistory() {
  document.getElementById('history-overlay').style.display = 'flex';
  await _loadHistoryPanel();
}

function closeHistory() {
  document.getElementById('history-overlay').style.display = 'none';
}

async function _loadHistoryPanel() {
  const body = document.getElementById('history-body');
  body.innerHTML = '<p class="units-hint">Cargando historial…</p>';

  let sessions = [];
  try {
    const data = await fetch('/api/sessions').then(r => r.json());
    sessions = data.sessions || [];
  } catch {
    body.innerHTML = '<p class="units-hint">No se pudo cargar el historial.</p>';
    return;
  }

  if (!sessions.length) {
    body.innerHTML = '<p class="units-hint">Todavía no tienes sesiones guardadas. Aparecerán aquí después de tu primer mensaje.</p>';
    return;
  }

  const groups = new Map();
  sessions.forEach(s => {
    const key = s.course_label || s.course || 'Sin curso';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(s);
  });

  body.innerHTML = '';
  let i = 0;
  for (const [courseName, list] of groups.entries()) {
    const det = document.createElement('details');
    det.className = 'history-group';
    det.open = i < 3; // primeros grupos abiertos por defecto
    i++;

    const sum = document.createElement('summary');
    sum.className = 'history-group-title';
    sum.textContent = `${courseName} (${list.length})`;
    det.appendChild(sum);

    const listEl = document.createElement('div');
    listEl.className = 'history-list';
    list.forEach(s => listEl.appendChild(_renderSessionItem(s)));
    det.appendChild(listEl);

    body.appendChild(det);
  }
}

function _renderSessionItem(s) {
  const row = document.createElement('div');
  row.className = 'history-item' + (s.sid === sessionId ? ' active' : '');

  const main = document.createElement('div');
  main.className = 'history-item-main';
  main.onclick = () => openHistorySession(s.sid);

  const title = document.createElement('div');
  title.className = 'history-item-title';
  title.textContent = s.title;
  main.appendChild(title);

  const meta = document.createElement('div');
  meta.className = 'history-item-meta';
  const bits = [];
  if (s.unit) bits.push(s.unit);
  if (s.method) bits.push(s.method);
  const rel = _relativeDate(s.updated_at);
  if (rel) bits.push(rel);
  meta.textContent = bits.join(' · ');
  main.appendChild(meta);

  row.appendChild(main);

  const actions = document.createElement('div');
  actions.className = 'history-item-actions';

  const renameBtn = document.createElement('button');
  renameBtn.className = 'history-item-btn';
  renameBtn.title = 'Renombrar';
  renameBtn.textContent = '✏️';
  renameBtn.onclick = e => { e.stopPropagation(); _renameSession(s); };
  actions.appendChild(renameBtn);

  const delBtn = document.createElement('button');
  delBtn.className = 'history-item-btn';
  delBtn.title = 'Eliminar';
  delBtn.textContent = '🗑️';
  delBtn.onclick = e => { e.stopPropagation(); _deleteSession(s); };
  actions.appendChild(delBtn);

  row.appendChild(actions);
  return row;
}

async function _renameSession(s) {
  const newTitle = prompt('Nuevo nombre para la sesión:', s.title);
  if (newTitle === null) return;
  const trimmed = newTitle.trim();
  if (!trimmed) return;
  try {
    await fetch(`/api/sessions/${s.sid}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: trimmed }),
    });
  } catch {}
  _loadHistoryPanel();
}

async function _deleteSession(s) {
  if (!confirm(`¿Eliminar la sesión "${s.title}"? Esta acción no se puede deshacer.`)) return;
  try {
    await fetch(`/api/sessions/${s.sid}`, { method: 'DELETE' });
  } catch {}
  try { localStorage.removeItem('atenea_chat_' + s.sid); } catch {}
  _loadHistoryPanel();
}

async function openHistorySession(sid) {
  if (sid === sessionId && _flowState === 'chatting') { closeHistory(); return; }

  let data;
  try {
    data = await fetch(`/api/sessions/${sid}`).then(r => r.json());
  } catch {
    alert('No se pudo abrir la sesión.');
    return;
  }
  if (data.error || !data.transcript || !data.transcript.length) {
    alert('Esa sesión ya no tiene mensajes para mostrar.');
    return;
  }

  sessionId = sid;
  localStorage.setItem(SESSION_KEY, sessionId);

  _activeCourse      = data.course || null;
  _activeCourseLabel = data.course_label || data.course || null;
  _activeUnit        = data.unit || null;
  _selectedMode      = data.mode || null;
  _selectedMethod    = data.method || null;
  _methodLabel       = null;
  if (_selectedMethod) {
    try {
      const methods = await _loadMethods();
      const m = methods.find(x => x.key === _selectedMethod);
      _methodLabel = m ? m.name : null;
    } catch {}
  }
  _activeDifficulty  = 'practicando';
  _transcript        = data.transcript;
  _lastOptions       = data.options || null;
  _flowState         = 'chatting';

  closeHistory();
  if (difficultyEl) difficultyEl.value = _activeDifficulty;
  _updateBadge();

  _recording = false;
  messagesEl.innerHTML = '';
  _transcript.forEach(m => appendMessage(m.role, m.text));
  if (_lastOptions) appendOptions(_lastOptions);
  _recording = true;

  _setInputEnabled(true);
  _persistState();
}

// ── Init ───────────────────────────────────────────────────────────────────────
(async () => {
  const handledQuery = await _initFromQuery();
  if (!handledQuery && !_restoreState()) showGreeting();
})();
