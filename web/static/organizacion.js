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
    evByDate[d].push({ label: ev.name, type: 'canvas' });
  });
  _userEvents.forEach(ev => {
    const d = ev.date;
    if (!d) return;
    if (!evByDate[d]) evByDate[d] = [];
    evByDate[d].push({ label: ev.title, type: 'user' });
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
      (dateStr === today ? ' today' : '');

    cell.innerHTML = `<span class="cal-day">${dayNum}</span><span class="cal-plus">+</span>`;

    // Add events
    const evs = evByDate[dateStr] || [];
    evs.slice(0, 3).forEach(ev => {
      const span = document.createElement('span');
      span.className = `cal-event ${ev.type}`;
      span.title = ev.label;
      span.textContent = ev.label;
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

// ── Init ───────────────────────────────────────────────────────────────────────
loadCalendar();
