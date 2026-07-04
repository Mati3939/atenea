// ── Organización: calendario mensual, eventos usuario, plan IA ──────────────

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
  return String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Markdown + LaTeX → HTML (mismo patrón que chat.js)
function _formatRich(text) {
  const blocks = [];
  const stash = m => { blocks.push(m); return `\x00B${blocks.length - 1}\x00`; };
  let t = text
    .replace(/\$\$[\s\S]*?\$\$/g, stash)
    .replace(/\\\[[\s\S]*?\\\]/g, stash)
    .replace(/\$[^$\n]+?\$/g,     stash)
    .replace(/\\\([^)\n]*?\\\)/g, stash);

  t = _esc(t);
  let html = window.marked ? marked.parse(t, { breaks: true }) : t.replace(/\n/g, '<br>');
  html = html.replace(/\x00B(\d+)\x00/g, (_, i) => _esc(blocks[+i]));
  return html.replace(/\\\$/g, '$');
}

function _renderInto(el, text) {
  el.innerHTML = _formatRich(text);
  if (window.renderMathInElement) renderMathInElement(el, KATEX_OPTS);
}

// ── Month names ──────────────────────────────────────────────────────────────
const MONTHS_ES = [
  'Enero','Febrero','Marzo','Abril','Mayo','Junio',
  'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre',
];

// ── State ─────────────────────────────────────────────────────────────────────
let _curYear  = new Date().getFullYear();
let _curMonth = new Date().getMonth(); // 0-indexed
let _canvasEvents  = [];   // [{name, due_at, course, url, points}]
let _userEvents    = [];   // [{id, date, title, type, syllabus}]
let _pendingDate   = null; // date string "YYYY-MM-DD" for modal
let _highlightDate = null; // date string recién agendado por NL (resalte temporal)

// ── Helpers ───────────────────────────────────────────────────────────────────
function _isoDate(date) {
  // Returns "YYYY-MM-DD" in local time
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function _canvasDate(isoStr) {
  // Canvas ISO strings are UTC; we want the date in local time
  if (!isoStr) return null;
  const d = new Date(isoStr);
  if (isNaN(d)) return null;
  return _isoDate(d);
}

// ── Load data ─────────────────────────────────────────────────────────────────
async function loadCalendarData() {
  const [agendaRes, userRes] = await Promise.all([
    fetch('/api/agenda').then(r => r.json()).catch(() => ({ events: [] })),
    fetch('/api/events').then(r => r.json()).catch(() => ({ events: [] })),
  ]);
  _canvasEvents = agendaRes.events || [];
  _userEvents   = userRes.events   || [];
}

async function loadCalendar() {
  await loadCalendarData();
  renderCalendar();
  loadMethodOptions();
}

// ── Render calendar ───────────────────────────────────────────────────────────
function renderCalendar() {
  document.getElementById('cal-title').textContent =
    `${MONTHS_ES[_curMonth]} ${_curYear}`;

  const grid = document.getElementById('cal-grid');
  // Remove old cells (keep first 7 DOW headers)
  const headers = Array.from(grid.children).slice(0, 7);
  grid.innerHTML = '';
  headers.forEach(h => grid.appendChild(h));

  const today = _isoDate(new Date());

  // First day of month (Monday = 0)
  const firstDay = new Date(_curYear, _curMonth, 1);
  // getDay(): 0=Sun,1=Mon..6=Sat  →  convert to Mon-based (0=Mon)
  let startOffset = (firstDay.getDay() + 6) % 7;

  // Days in current and previous month
  const daysInMonth = new Date(_curYear, _curMonth + 1, 0).getDate();
  const daysInPrev  = new Date(_curYear, _curMonth, 0).getDate();

  // Build index of events by date
  const evByDate = {};
  _canvasEvents.forEach(ev => {
    const d = _canvasDate(ev.due_at);
    if (!d) return;
    if (!evByDate[d]) evByDate[d] = [];
    evByDate[d].push({ label: ev.name, type: 'canvas', ref: ev });
  });
  _userEvents.forEach(ev => {
    const d = ev.date;
    if (!d) return;
    if (!evByDate[d]) evByDate[d] = [];
    const cls = ev.type === 'estudio' ? 'estudio' : 'user';
    evByDate[d].push({ label: ev.title, type: cls, ref: ev });
  });

  const totalCells = Math.ceil((startOffset + daysInMonth) / 7) * 7;

  for (let i = 0; i < totalCells; i++) {
    const cell = document.createElement('div');

    let dateStr, dayNum, otherMonth;
    if (i < startOffset) {
      dayNum = daysInPrev - startOffset + 1 + i;
      const d = new Date(_curYear, _curMonth - 1, dayNum);
      dateStr = _isoDate(d);
      otherMonth = true;
    } else if (i >= startOffset + daysInMonth) {
      dayNum = i - startOffset - daysInMonth + 1;
      const d = new Date(_curYear, _curMonth + 1, dayNum);
      dateStr = _isoDate(d);
      otherMonth = true;
    } else {
      dayNum = i - startOffset + 1;
      dateStr = _isoDate(new Date(_curYear, _curMonth, dayNum));
      otherMonth = false;
    }

    cell.className = 'cal-cell' +
      (otherMonth ? ' other-month' : '') +
      (dateStr === today ? ' today' : '') +
      (dateStr === _highlightDate ? ' nl-highlight' : '');

    cell.innerHTML = `<span class="cal-day">${dayNum}</span><span class="cal-plus">+</span>`;

    // Add events
    const evs = evByDate[dateStr] || [];
    evs.slice(0, 3).forEach(ev => {
      const span = document.createElement('span');
      span.className = `cal-event ${ev.type}`;
      span.title = ev.label;
      span.textContent = ev.label;
      span.addEventListener('click', (e) => {
        e.stopPropagation();
        openDetailModal(ev.ref, ev.type);
      });
      cell.appendChild(span);
    });
    if (evs.length > 3) {
      const more = document.createElement('span');
      more.style.cssText = 'font-size:.6rem;color:var(--text-muted);padding:0 2px';
      more.textContent = `+${evs.length - 3} más`;
      cell.appendChild(more);
    }

    cell.addEventListener('click', () => openModal(dateStr));
    grid.appendChild(cell);
  }
}

function changeMonth(delta) {
  _curMonth += delta;
  if (_curMonth < 0)  { _curMonth = 11; _curYear--; }
  if (_curMonth > 11) { _curMonth = 0;  _curYear++; }
  renderCalendar();
}

function goToday() {
  const now = new Date();
  _curYear  = now.getFullYear();
  _curMonth = now.getMonth();
  renderCalendar();
}

// ── Event modal ───────────────────────────────────────────────────────────────
function openModal(dateStr) {
  _pendingDate = dateStr;
  document.getElementById('ev-date').value  = dateStr;
  document.getElementById('ev-title').value = '';
  document.getElementById('ev-type').value  = 'Control';
  document.getElementById('ev-syllabus').value = '';

  // Show existing user events on this day
  const dayEvs = _userEvents.filter(e => e.date === dateStr);
  const existEl  = document.getElementById('ev-existing');
  const listEl   = document.getElementById('ev-existing-list');
  if (dayEvs.length) {
    listEl.innerHTML = '';
    dayEvs.forEach(ev => {
      const row = document.createElement('div');
      row.className = 'event-list-item';
      row.innerHTML = `
        <span class="ev-title">${_esc(ev.title)}</span>
        <span class="ev-type">${_esc(ev.type)}</span>
        <button class="ev-del" title="Eliminar" data-id="${_esc(ev.id)}">✕</button>
      `;
      row.querySelector('.ev-del').addEventListener('click', (e) => {
        e.stopPropagation();
        deleteEvent(ev.id);
      });
      listEl.appendChild(row);
    });
    existEl.style.display = 'block';
  } else {
    existEl.style.display = 'none';
  }

  document.getElementById('event-modal').style.display = 'flex';
  setTimeout(() => document.getElementById('ev-title').focus(), 50);
}

function closeModal() {
  document.getElementById('event-modal').style.display = 'none';
}

async function saveEvent() {
  const title    = document.getElementById('ev-title').value.trim();
  const type     = document.getElementById('ev-type').value;
  const date     = document.getElementById('ev-date').value;
  const syllabus = document.getElementById('ev-syllabus').value.trim();
  if (!title || !date) return;

  try {
    const res = await fetch('/api/events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, type, date, syllabus }),
    });
    const ev = await res.json();
    _userEvents.push(ev);
    closeModal();
    renderCalendar();
  } catch {
    alert('No se pudo guardar el evento.');
  }
}

async function deleteEvent(id) {
  try {
    await fetch(`/api/events/${id}`, { method: 'DELETE' });
    _userEvents = _userEvents.filter(e => e.id !== id);
    renderCalendar();
    // Re-open modal to refresh existing list
    if (_pendingDate) openModal(_pendingDate);
  } catch {
    alert('No se pudo eliminar el evento.');
  }
}

// ── Methods dropdown ──────────────────────────────────────────────────────────
async function loadMethodOptions() {
  try {
    const data = await fetch('/api/methods').then(r => r.json());
    const sel  = document.getElementById('plan-method');
    // Keep the first "auto" option
    const existing = Array.from(sel.options).map(o => o.value);
    (data.methods || []).forEach(m => {
      if (!existing.includes(m.key)) {
        const opt = document.createElement('option');
        opt.value = m.key;
        opt.textContent = m.emoji + ' ' + m.name;
        sel.appendChild(opt);
      }
    });
  } catch { /* ignore */ }
}

// ── Plan generation ───────────────────────────────────────────────────────────
async function generatePlan() {
  const from   = document.getElementById('plan-from').value;
  const to     = document.getElementById('plan-to').value;
  const method = document.getElementById('plan-method').value;

  if (!from || !to) {
    alert('Selecciona las fechas "Desde" y "Hasta".');
    return;
  }

  // Collect events in range
  const fromD = new Date(from);
  const toD   = new Date(to);
  const rangeEvents = [];

  _canvasEvents.forEach(ev => {
    const d = _canvasDate(ev.due_at);
    if (!d) return;
    const evD = new Date(d);
    if (evD >= fromD && evD <= toD) {
      rangeEvents.push({ title: ev.name, date: d, course: ev.course, syllabus: '' });
    }
  });
  _userEvents.forEach(ev => {
    const evD = new Date(ev.date);
    if (evD >= fromD && evD <= toD) {
      rangeEvents.push({ title: ev.title, date: ev.date, course: '', syllabus: ev.syllabus || '' });
    }
  });

  const btn     = document.getElementById('plan-btn');
  const content = document.getElementById('plan-content');
  btn.disabled = true;
  btn.textContent = 'Generando…';
  content.style.display = 'block';
  content.innerHTML = '<span class="muted">Armando plan de estudio…</span>';

  try {
    const res = await fetch('/api/agenda/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from, to, events: rangeEvents, method }),
    });
    const data = await res.json();
    if (data.error) {
      content.innerHTML = `<span style="color:#ef4444">Error: ${_esc(data.error)}</span>`;
    } else {
      _renderInto(content, data.plan);
    }
  } catch {
    content.innerHTML = '<span style="color:#ef4444">No pude generar el plan.</span>';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generar plan';
  }
}

// ── Detalle / edición de evento ────────────────────────────────────────────────
let _detailEvent = null;  // objeto del evento (usuario o canvas) actualmente abierto
let _detailType  = null;  // 'canvas' | 'user' | 'estudio'

function openDetailModal(ev, type) {
  _detailEvent = ev;
  _detailType  = type;
  const readonly = type === 'canvas';

  document.getElementById('detail-title-head').textContent =
    readonly ? '📌 Evento de Canvas' : (type === 'estudio' ? '📖 Día de estudio' : '📌 Evento');

  const titleEl = document.getElementById('detail-title');
  titleEl.value = readonly ? (ev.name || '') : (ev.title || '');
  titleEl.disabled = readonly;

  const dateEl = document.getElementById('detail-date');
  dateEl.value = readonly ? (_canvasDate(ev.due_at) || '') : (ev.date || '');
  dateEl.disabled = readonly;

  const notesField = document.getElementById('detail-notes-field');
  const notesEl    = document.getElementById('detail-notes');
  const notes = readonly ? '' : (ev.syllabus || '');
  if (notes) {
    notesField.style.display = 'flex';
    _renderInto(notesEl, notes);
  } else {
    notesField.style.display = 'none';
  }

  document.getElementById('detail-readonly-note').style.display = readonly ? 'block' : 'none';
  document.getElementById('detail-save-btn').style.display = readonly ? 'none' : 'inline-flex';
  document.getElementById('detail-del-btn').style.display  = readonly ? 'none' : 'inline-flex';
  document.getElementById('detail-del-plan').style.display =
    (!readonly && ev.plan_group) ? 'inline-flex' : 'none';

  document.getElementById('detail-modal').style.display = 'flex';
}

function closeDetailModal() {
  document.getElementById('detail-modal').style.display = 'none';
  _detailEvent = null;
  _detailType  = null;
}

async function saveDetailEvent() {
  if (!_detailEvent || _detailType === 'canvas') return;
  const title = document.getElementById('detail-title').value.trim();
  const date  = document.getElementById('detail-date').value;
  if (!title || !date) { alert('El nombre y la fecha son obligatorios.'); return; }

  try {
    const res = await fetch(`/api/events/${_detailEvent.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, date }),
    });
    if (!res.ok) throw new Error();
    const updated = await res.json();
    const idx = _userEvents.findIndex(e => e.id === updated.id);
    if (idx >= 0) _userEvents[idx] = updated;
    closeDetailModal();
    renderCalendar();
  } catch {
    alert('No se pudo guardar el evento.');
  }
}

async function deleteDetailEvent() {
  if (!_detailEvent || _detailType === 'canvas') return;
  try {
    await fetch(`/api/events/${_detailEvent.id}`, { method: 'DELETE' });
    _userEvents = _userEvents.filter(e => e.id !== _detailEvent.id);
    closeDetailModal();
    renderCalendar();
  } catch {
    alert('No se pudo eliminar el evento.');
  }
}

async function deleteDetailPlanGroup() {
  if (!_detailEvent || !_detailEvent.plan_group) return;
  if (!confirm('¿Eliminar todos los días de este plan de estudio?')) return;
  const group = _detailEvent.plan_group;
  try {
    await fetch(`/api/events/plan/${group}`, { method: 'DELETE' });
    _userEvents = _userEvents.filter(e => e.plan_group !== group);
    closeDetailModal();
    renderCalendar();
  } catch {
    alert('No se pudo eliminar el plan.');
  }
}

// ── Agenda por lenguaje natural ─────────────────────────────────────────────────
async function submitNL(prefillText) {
  const input = document.getElementById('nl-input');
  const text  = (prefillText !== undefined ? prefillText : input.value).trim();
  if (!text) return;
  input.value = text;

  const btn    = document.getElementById('nl-btn');
  const status = document.getElementById('nl-status');
  btn.disabled = true;
  input.disabled = true;
  status.style.display = 'block';
  status.innerHTML = '<span class="muted">🪄 Atenea está organizando tu plan…</span>';

  try {
    const res = await fetch('/api/agenda/nl', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      status.innerHTML = `<span style="color:#ef4444">⚠️ ${_esc(data.error || 'No pude interpretar el mensaje.')}</span>`;
    } else {
      status.innerHTML = `<span style="color:#15803d">${_esc(data.resumen || 'Listo.')}</span>`;
      input.value = '';
      await loadCalendarData();
      if (data.event && data.event.date) {
        const d = new Date(data.event.date);
        if (!isNaN(d)) { _curYear = d.getFullYear(); _curMonth = d.getMonth(); }
        _highlightDate = data.event.date;
        setTimeout(() => { _highlightDate = null; renderCalendar(); }, 5000);
      }
      renderCalendar();
    }
  } catch {
    status.innerHTML = '<span style="color:#ef4444">⚠️ No pude conectar con el servidor.</span>';
  } finally {
    btn.disabled = false;
    input.disabled = false;
  }
}

document.getElementById('nl-input')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); submitNL(); }
});

// Si llegamos con ?text=... (desde el chatbot principal), prellenar y ejecutar.
function _consumeNLFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const text = params.get('text');
  if (!text) return;
  document.getElementById('nl-input').value = text;
  submitNL(text);
  const url = new URL(window.location.href);
  url.searchParams.delete('text');
  window.history.replaceState({}, '', url.toString());
}

// ── Init ───────────────────────────────────────────────────────────────────────
loadCalendar();
_consumeNLFromQuery();
