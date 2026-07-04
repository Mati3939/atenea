// ── Métodos de estudio ────────────────────────────────────────────────────────

function _esc(t) {
  return String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

let _allMethods = [];
let _recommendedKeys = [];

// Navega al chat con el método preseleccionado (feature "Probar este método").
function _tryMethod(key, name) {
  window.location.href = '/?method=' + encodeURIComponent(key) + '&label=' + encodeURIComponent(name);
}

function _methodCard(m, highlighted) {
  const div = document.createElement('div');
  div.className = 'method-card' + (highlighted ? ' method-card--highlight' : '');
  div.innerHTML = `
    <div class="method-card-emoji">${_esc(m.emoji)}</div>
    <div class="method-card-name">${_esc(m.name)}</div>
    <div class="method-card-short">${_esc(m.short)}</div>
    <div class="method-card-actions">
      <button type="button" class="btn btn-secondary btn-sm method-try-btn">▶ Probar este método</button>
    </div>
  `;
  div.addEventListener('click', () => showDetail(m.key));
  div.querySelector('.method-try-btn').addEventListener('click', e => {
    e.stopPropagation();
    _tryMethod(m.key, m.name);
  });
  return div;
}

function showDetail(key) {
  const m = _allMethods.find(x => x.key === key);
  if (!m) return;

  document.getElementById('detail-title').textContent = m.emoji + ' ' + m.name;
  document.getElementById('detail-what').textContent = m.what;

  const stepsEl = document.getElementById('detail-steps');
  stepsEl.innerHTML = '';
  (m.how || []).forEach(step => {
    const li = document.createElement('li');
    li.textContent = step;
    stepsEl.appendChild(li);
  });

  const tagsEl = document.getElementById('detail-tags');
  tagsEl.innerHTML = (m.best_for || []).map(t =>
    `<span class="method-tag">${_esc(t)}</span>`
  ).join('');

  const tryBtn = document.getElementById('detail-try-btn');
  if (tryBtn) tryBtn.onclick = () => _tryMethod(m.key, m.name);

  // El método Pomodoro tiene su propio panel fijo y siempre visible en esta
  // página (más abajo); desde el detalle ofrecemos un atajo directo a él.
  const pomodoroLink = document.getElementById('detail-pomodoro-link');
  if (pomodoroLink) pomodoroLink.style.display = (m.key === 'pomodoro') ? 'block' : 'none';

  const panel = document.getElementById('method-detail');
  panel.style.display = 'flex';
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function closeDetail() {
  document.getElementById('method-detail').style.display = 'none';
}

async function loadMethods() {
  try {
    const [methodsRes, recRes] = await Promise.all([
      fetch('/api/methods').then(r => r.json()),
      fetch('/api/methods/recommend').then(r => r.json()).catch(() => ({ recommended: [] })),
    ]);

    _allMethods = methodsRes.methods || [];
    _recommendedKeys = recRes.recommended || [];

    // Render all methods grid
    const grid = document.getElementById('methods-grid');
    grid.innerHTML = '';
    _allMethods.forEach(m => {
      const highlighted = _recommendedKeys.includes(m.key);
      grid.appendChild(_methodCard(m, highlighted));
    });

    // Render recommended section
    if (_recommendedKeys.length > 0) {
      const recGrid = document.getElementById('recommended-grid');
      recGrid.innerHTML = '';
      _recommendedKeys.forEach(key => {
        const m = _allMethods.find(x => x.key === key);
        if (m) recGrid.appendChild(_methodCard(m, true));
      });
      document.getElementById('recommended-section').style.display = 'flex';
    }
  } catch (e) {
    document.getElementById('methods-grid').innerHTML =
      '<div class="empty">No pude cargar los métodos.</div>';
  }
}

loadMethods();
