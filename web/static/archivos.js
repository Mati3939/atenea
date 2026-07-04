// ── Gestor de archivos: navegación por carpetas, drag & drop, previsualización ──

let _curPath = '';          // ruta relativa actual (rel a data/), "" = raíz
let _renameTarget = null;   // ruta del item a renombrar

const _ICONS = {
  pdf: '📄',
  doc: '📝', docx: '📝', txt: '📝', md: '📝',
  ppt: '📊', pptx: '📊', xls: '📊', xlsx: '📊', csv: '📊',
  py: '💻', js: '💻', ts: '💻', tsx: '💻', jsx: '💻', c: '💻', cpp: '💻',
  h: '💻', hpp: '💻', java: '💻', html: '💻', css: '💻', json: '💻',
  ipynb: '💻', sh: '💻', yml: '💻', yaml: '💻', xml: '💻',
  png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️', bmp: '🖼️',
};
const _IMG_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'];

function iconFor(ext) {
  return _ICONS[(ext || '').toLowerCase()] || '📄';
}

function _esc(t) {
  return String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

document.addEventListener('DOMContentLoaded', () => {
  loadFiles('');
  setupDropzone();
  setupBackNavigation();
});

// ── Carga / navegación ───────────────────────────────────────────────────────

async function loadFiles(path) {
  try {
    const res = await fetch(`/api/files?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'No se pudo listar la carpeta'); return; }
    _curPath = path;
    renderBreadcrumb(path);
    renderGrid(path, data.dirs || [], data.files || []);
    updateCourseButtons();
    updateBackButton();
  } catch (e) {
    alert('Error cargando archivos: ' + e);
  }
}

// Curso actual = primer segmento de la ruta (nombre real de carpeta bajo data/).
function currentCourse() {
  return _curPath ? _curPath.split('/')[0] : '';
}

function updateCourseButtons() {
  const course = currentCourse();
  const unitsBtn = document.getElementById('units-btn');
  if (unitsBtn) unitsBtn.style.display = course ? '' : 'none';
  const organizeBtn = document.getElementById('organize-btn');
  if (organizeBtn) organizeBtn.style.display = course ? '' : 'none';
  refreshUndoButton();
}

function updateBackButton() {
  const btn = document.getElementById('files-back-btn');
  if (btn) btn.style.display = _curPath ? '' : 'none';
}

function goUp() {
  if (!_curPath) return;
  const parent = _curPath.split('/').slice(0, -1).join('/');
  loadFiles(parent);
}

// Backspace = subir un nivel, salvo que el foco esté en un input/textarea o
// haya un modal abierto (para no interferir con edición de texto).
function setupBackNavigation() {
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Backspace') return;
    const tag = (document.activeElement && document.activeElement.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (document.activeElement && document.activeElement.isContentEditable) return;
    const anyModalOpen = Array.from(document.querySelectorAll('.modal-overlay'))
      .some(el => el.style.display === 'flex');
    if (anyModalOpen) return;
    if (!_curPath) return;
    e.preventDefault();
    goUp();
  });
}

function renderBreadcrumb(path) {
  const el = document.getElementById('files-breadcrumb');
  el.innerHTML = '';

  const home = document.createElement('span');
  home.className = 'crumb';
  home.textContent = '🎓 Cursos';
  home.addEventListener('click', () => loadFiles(''));
  addDropTarget(home, '');
  el.appendChild(home);

  const parts = path ? path.split('/') : [];
  let acc = '';
  parts.forEach(p => {
    const sep = document.createElement('span');
    sep.className = 'crumb-sep';
    sep.textContent = '›';
    el.appendChild(sep);

    acc = acc ? `${acc}/${p}` : p;
    const target = acc;
    const crumb = document.createElement('span');
    crumb.className = 'crumb';
    crumb.textContent = p;
    crumb.addEventListener('click', () => loadFiles(target));
    addDropTarget(crumb, target);
    el.appendChild(crumb);
  });
}

function renderGrid(path, dirs, files) {
  const grid = document.getElementById('files-grid');
  const empty = document.getElementById('files-empty');
  grid.innerHTML = '';
  grid.classList.toggle('files-grid-root', path === '');

  const isEmpty = !dirs.length && !files.length;
  empty.style.display = isEmpty ? 'block' : 'none';

  if (path !== '') {
    const parent = path.split('/').slice(0, -1).join('/');
    const up = document.createElement('div');
    up.className = 'file-item file-item-up';
    up.innerHTML = '<div class="file-icon">⬆️</div><div class="file-name">.. (subir)</div>';
    up.addEventListener('click', () => loadFiles(parent));
    addDropTarget(up, parent);
    grid.appendChild(up);
  }

  dirs.forEach(d => grid.appendChild(buildDirItem(path, d)));
  files.forEach(f => grid.appendChild(buildFileItem(path, f)));
}

// ── Construcción de items ────────────────────────────────────────────────────

function buildDirItem(path, d) {
  const isRoot = path === '';
  const full = path ? `${path}/${d.name}` : d.name;

  const el = document.createElement('div');
  el.className = 'file-item file-item-dir' + (isRoot ? ' course-card' : '');
  el.draggable = true;

  const icon = document.createElement('div');
  icon.className = 'file-icon';
  icon.textContent = isRoot ? '🎓' : '📁';
  const name = document.createElement('div');
  name.className = 'file-name';
  name.textContent = d.name;
  const meta = document.createElement('div');
  meta.className = 'file-meta';
  meta.textContent = `${d.count} archivo${d.count === 1 ? '' : 's'}`;

  el.appendChild(icon);
  el.appendChild(name);
  el.appendChild(meta);

  const actions = document.createElement('div');
  actions.className = 'file-actions';
  actions.appendChild(actionBtn('✏️', 'Renombrar', () => openRenameModal(full, d.name)));
  actions.appendChild(actionBtn('🗑️', 'Eliminar', () => deleteItem(full, true)));
  el.appendChild(actions);

  el.addEventListener('click', (e) => {
    if (e.target.closest('.file-actions')) return;
    loadFiles(full);
  });

  el.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData('text/plain', full);
    e.dataTransfer.effectAllowed = 'move';
  });
  addDropTarget(el, full);

  return el;
}

function buildFileItem(path, f) {
  const full = path ? `${path}/${f.name}` : f.name;

  const el = document.createElement('div');
  el.className = 'file-item file-item-file';
  el.draggable = true;

  const icon = document.createElement('div');
  icon.className = 'file-icon';
  icon.textContent = iconFor(f.ext);
  const name = document.createElement('div');
  name.className = 'file-name';
  name.textContent = f.name;
  const meta = document.createElement('div');
  meta.className = 'file-meta';
  meta.textContent = `${formatSize(f.size)} · ${formatDate(f.mtime)}`;

  const actions = document.createElement('div');
  actions.className = 'file-actions';
  actions.appendChild(actionBtn('👁️', 'Vista previa', () => openPreview(full, f.name, f.ext)));
  if (path !== '' && ['pdf', 'docx'].includes((f.ext || '').toLowerCase())) {
    actions.appendChild(actionBtn('📘', 'Usar como calendarización', () => useAsCalendar(full)));
  }
  actions.appendChild(actionBtn('✏️', 'Renombrar', () => openRenameModal(full, f.name)));
  actions.appendChild(actionBtn('⬇️', 'Descargar', () => downloadFile(full)));
  actions.appendChild(actionBtn('🗑️', 'Eliminar', () => deleteItem(full, false)));

  el.appendChild(icon);
  el.appendChild(name);
  el.appendChild(meta);
  el.appendChild(actions);

  el.addEventListener('click', (e) => {
    if (e.target.closest('.file-actions')) return;
    openPreview(full, f.name, f.ext);
  });

  el.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData('text/plain', full);
    e.dataTransfer.effectAllowed = 'move';
  });

  return el;
}

function actionBtn(icon, title, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'file-action-btn';
  b.title = title;
  b.textContent = icon;
  b.addEventListener('click', (e) => { e.stopPropagation(); onClick(); });
  return b;
}

function formatSize(bytes) {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(epochSeconds) {
  if (!epochSeconds) return '';
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleDateString('es-CL', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

// ── Drag & drop (mover archivos internamente) ────────────────────────────────

function addDropTarget(el, destPath) {
  el.addEventListener('dragover', (e) => {
    if (!e.dataTransfer.types.includes('text/plain')) return;
    e.preventDefault();
    e.stopPropagation();
    el.classList.add('drop-target');
  });
  el.addEventListener('dragleave', (e) => {
    e.stopPropagation();
    el.classList.remove('drop-target');
  });
  el.addEventListener('drop', async (e) => {
    if (!e.dataTransfer.types.includes('text/plain')) return;
    e.preventDefault();
    e.stopPropagation();
    el.classList.remove('drop-target');
    const src = e.dataTransfer.getData('text/plain');
    if (!src) return;
    await moveItem(src, destPath);
  });
}

async function moveItem(src, destDir) {
  try {
    const res = await fetch('/api/files/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ src, dst_dir: destDir }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'No se pudo mover'); return; }
    loadFiles(_curPath);
  } catch (e) {
    alert('Error moviendo: ' + e);
  }
}

// ── Subida (input y drag&drop desde el escritorio) ───────────────────────────

function setupDropzone() {
  const dz = document.getElementById('files-dropzone');
  let dragCounter = 0;

  dz.addEventListener('dragover', (e) => {
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
  });
  dz.addEventListener('dragenter', (e) => {
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    dragCounter++;
    dz.classList.add('dropzone-active');
  });
  dz.addEventListener('dragleave', () => {
    dragCounter = Math.max(0, dragCounter - 1);
    if (dragCounter === 0) dz.classList.remove('dropzone-active');
  });
  dz.addEventListener('drop', async (e) => {
    dragCounter = 0;
    dz.classList.remove('dropzone-active');
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      await uploadFiles(e.dataTransfer.files);
    }
  });
}

async function onUploadInputChange(e) {
  const fileList = e.target.files;
  if (fileList && fileList.length) await uploadFiles(fileList);
  e.target.value = '';
}

async function uploadFiles(fileList) {
  const fd = new FormData();
  for (const f of fileList) fd.append('files', f);
  try {
    const res = await fetch(`/api/files/upload?path=${encodeURIComponent(_curPath)}`, {
      method: 'POST',
      body: fd,
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'No se pudo subir'); return; }
    if (data.failures && data.failures.length) alert('Con errores:\n' + data.failures.join('\n'));
    loadFiles(_curPath);
  } catch (e) {
    alert('Error subiendo: ' + e);
  }
}

// ── Nueva carpeta ─────────────────────────────────────────────────────────────

function createFolder() {
  document.getElementById('mkdir-name').value = '';
  document.getElementById('mkdir-modal').style.display = 'flex';
  document.getElementById('mkdir-name').focus();
}
function closeMkdirModal() {
  document.getElementById('mkdir-modal').style.display = 'none';
}
async function confirmMkdir() {
  const name = document.getElementById('mkdir-name').value.trim();
  if (!name) return;
  try {
    const res = await fetch('/api/files/mkdir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: _curPath, name }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'No se pudo crear la carpeta'); return; }
    closeMkdirModal();
    loadFiles(_curPath);
  } catch (e) {
    alert('Error creando carpeta: ' + e);
  }
}

// ── Renombrar ─────────────────────────────────────────────────────────────────

function openRenameModal(path, currentName) {
  _renameTarget = path;
  document.getElementById('rename-name').value = currentName;
  document.getElementById('rename-modal').style.display = 'flex';
  document.getElementById('rename-name').focus();
}
function closeRenameModal() {
  document.getElementById('rename-modal').style.display = 'none';
  _renameTarget = null;
}
async function confirmRename() {
  const newName = document.getElementById('rename-name').value.trim();
  if (!newName || !_renameTarget) return;
  try {
    const res = await fetch('/api/files/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: _renameTarget, new_name: newName }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'No se pudo renombrar'); return; }
    closeRenameModal();
    loadFiles(_curPath);
  } catch (e) {
    alert('Error renombrando: ' + e);
  }
}

// ── Eliminar ──────────────────────────────────────────────────────────────────

async function deleteItem(path, isDir) {
  const label = path.split('/').pop();
  const kind = isDir ? 'la carpeta' : 'el archivo';
  if (!confirm(`¿Eliminar ${kind} "${label}"? Esta acción no se puede deshacer.`)) return;
  try {
    await _deleteFilePath(path, false);
    loadFiles(_curPath);
  } catch (e) {
    if (e && e.needsRecursive) {
      const n = e.count != null ? e.count : 'varios';
      if (!confirm(
        `La carpeta "${label}" contiene ${n} elemento(s). ¿Eliminar TODO su contenido? `
        + 'Esta acción no se puede deshacer.'
      )) return;
      try {
        await _deleteFilePath(path, true);
        loadFiles(_curPath);
      } catch (e2) {
        alert((e2 && e2.message) || 'No se pudo eliminar');
      }
      return;
    }
    alert((e && e.message) || 'Error eliminando: ' + e);
  }
}

// Lanza DELETE /api/files; si el backend responde 409 needs_recursive, rechaza
// con { needsRecursive: true, count } para que el caller confirme y reintente
// con recursive:true.
async function _deleteFilePath(path, recursive) {
  const res = await fetch('/api/files', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, recursive }),
  });
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 409 && data.needs_recursive) {
      throw { needsRecursive: true, count: data.count };
    }
    throw new Error(data.error || 'No se pudo eliminar');
  }
  return data;
}

// ── Descarga ──────────────────────────────────────────────────────────────────

function downloadFile(path) {
  window.open(`/api/files/raw?path=${encodeURIComponent(path)}`, '_blank');
}

// ── Previsualización ──────────────────────────────────────────────────────────

async function openPreview(path, name, ext) {
  document.getElementById('preview-title').textContent = name;
  document.getElementById('preview-download').href = `/api/files/raw?path=${encodeURIComponent(path)}`;
  const body = document.getElementById('preview-body');
  body.innerHTML = '<p class="preview-loading">Cargando…</p>';
  document.getElementById('preview-overlay').style.display = 'flex';

  try {
    const res = await fetch(`/api/files/preview?path=${encodeURIComponent(path)}`);
    const data = await res.json();
    if (!res.ok || data.error) {
      body.innerHTML = `<p class="preview-error">${_esc(data.error || 'No se pudo previsualizar')}</p>`;
      return;
    }

    if (data.kind === 'raw') {
      const lowerExt = (ext || '').toLowerCase();
      if (_IMG_EXTS.includes(lowerExt)) {
        body.innerHTML = `<img class="preview-img" src="/api/files/raw?path=${encodeURIComponent(path)}" alt="${_esc(name)}">`;
      } else if (lowerExt === 'pdf') {
        body.innerHTML = `<iframe class="preview-iframe" src="/api/files/raw?path=${encodeURIComponent(path)}"></iframe>`;
      } else {
        body.innerHTML = '<p class="preview-note">No hay vista previa disponible para este tipo de archivo. Usa Descargar.</p>';
      }
    } else if (data.kind === 'doc') {
      body.innerHTML =
        '<p class="preview-note">Vista de texto extraído (el formato original no se conserva).</p>' +
        `<pre class="preview-pre"><code>${_esc(data.content || '')}</code></pre>`;
    } else if (data.kind === 'text') {
      body.innerHTML = `<pre class="preview-pre"><code>${_esc(data.content || '')}</code></pre>`;
    } else {
      body.innerHTML = '<p class="preview-note">No hay vista previa disponible para este tipo de archivo. Usa Descargar.</p>';
    }
  } catch (e) {
    body.innerHTML = `<p class="preview-error">Error: ${_esc(String(e))}</p>`;
  }
}

function closePreview() {
  document.getElementById('preview-overlay').style.display = 'none';
  document.getElementById('preview-body').innerHTML = '';
}

// ── Calendarización manual ───────────────────────────────────────────────────

async function useAsCalendar(fullPath) {
  const course = fullPath.split('/')[0];
  const relToCourse = fullPath.slice(course.length + 1);
  if (!relToCourse) return;
  if (!confirm(
    `¿Usar "${fullPath.split('/').pop()}" como calendarización del curso? ` +
    'Se redetectarán las unidades a partir de este archivo.'
  )) return;

  try {
    const res = await fetch(`/api/units/${encodeURIComponent(course)}/calendar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: relToCourse }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'No se pudo aplicar la calendarización'); return; }
    const units = data.units || [];
    alert(
      units.length
        ? `Calendarización aplicada. Unidades detectadas (${units.length}):\n` +
          units.map(u => `• ${u}`).join('\n')
        : 'Calendarización aplicada, pero no se detectaron unidades en el documento.'
    );
  } catch (e) {
    alert('Error aplicando calendarización: ' + e);
  }
}

// ── Panel de unidades (ver / editar / redetectar) ────────────────────────────

let _unitsCourse = null;

function openUnitsModal() {
  _unitsCourse = currentCourse();
  if (!_unitsCourse) return;
  document.getElementById('units-title').textContent = `🧩 Unidades — ${_unitsCourse}`;
  document.getElementById('units-status').textContent = '';
  document.getElementById('units-list').innerHTML = '<p class="preview-loading">Cargando…</p>';
  document.getElementById('units-overlay').style.display = 'flex';
  loadUnitsInto(false);
}

function closeUnitsModal() {
  document.getElementById('units-overlay').style.display = 'none';
  _unitsCourse = null;
}

async function loadUnitsInto(refresh) {
  if (!_unitsCourse) return;
  const status = document.getElementById('units-status');
  status.textContent = refresh ? 'Redetectando…' : 'Cargando…';
  try {
    const url = `/api/units/${encodeURIComponent(_unitsCourse)}${refresh ? '?refresh=1' : ''}`;
    const res = await fetch(url);
    const data = await res.json();
    status.textContent = '';
    const names = (data.units || []).map(n => (typeof n === 'string' ? { name: n, topics: [] } : n));
    renderUnitsList(names);
  } catch (e) {
    status.textContent = '';
    document.getElementById('units-list').innerHTML =
      `<p class="preview-error">Error cargando unidades: ${_esc(String(e))}</p>`;
  }
}

function redetectUnits() {
  if (!_unitsCourse) return;
  loadUnitsInto(true);
}

function renderUnitsList(units) {
  const list = document.getElementById('units-list');
  list.innerHTML = '';
  if (!units.length) {
    list.innerHTML = '<p class="preview-note">No hay unidades detectadas todavía. Añade una manualmente o usa "Redetectar" tras subir una calendarización.</p>';
    return;
  }
  units.forEach(u => list.appendChild(buildUnitRow(u.name || '', (u.topics || []).join(', '))));
}

function buildUnitRow(name, topicsStr) {
  const row = document.createElement('div');
  row.className = 'unit-row';

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'unit-name-input';
  nameInput.placeholder = 'Nombre de la unidad';
  nameInput.value = name;

  const topicsInput = document.createElement('input');
  topicsInput.type = 'text';
  topicsInput.className = 'unit-topics-input';
  topicsInput.placeholder = 'Temas (separados por coma)';
  topicsInput.value = topicsStr;

  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'file-action-btn unit-remove-btn';
  removeBtn.title = 'Eliminar unidad';
  removeBtn.textContent = '🗑️';
  removeBtn.addEventListener('click', () => row.remove());

  row.appendChild(nameInput);
  row.appendChild(topicsInput);
  row.appendChild(removeBtn);
  return row;
}

function addUnitRow() {
  document.getElementById('units-list').appendChild(buildUnitRow('', ''));
}

async function saveUnits() {
  if (!_unitsCourse) return;
  const rows = document.querySelectorAll('#units-list .unit-row');
  const units = [];
  rows.forEach(row => {
    const name = row.querySelector('.unit-name-input').value.trim();
    if (!name) return;
    const topics = row.querySelector('.unit-topics-input').value
      .split(',').map(t => t.trim()).filter(Boolean);
    units.push({ name, topics });
  });
  if (!units.length) { alert('Añade al menos una unidad con nombre.'); return; }

  const status = document.getElementById('units-status');
  status.textContent = 'Guardando y reclasificando archivos…';
  try {
    const res = await fetch(`/api/units/${encodeURIComponent(_unitsCourse)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ units }),
    });
    const data = await res.json();
    if (!res.ok) { status.textContent = ''; alert(data.error || 'No se pudo guardar'); return; }
    status.textContent = 'Guardado.';
  } catch (e) {
    status.textContent = '';
    alert('Error guardando: ' + e);
  }
}

// ── Ordenar curso ─────────────────────────────────────────────────────────────

let _organizeCourse = null;
let _organizePlan = [];

async function refreshUndoButton() {
  const btn = document.getElementById('organize-undo-btn');
  if (!btn) return;
  const course = currentCourse();
  if (!course) { btn.style.display = 'none'; return; }
  try {
    const res = await fetch(`/api/files/organize/status?course=${encodeURIComponent(course)}`);
    const data = await res.json();
    btn.style.display = data.has_log ? '' : 'none';
  } catch (e) {
    btn.style.display = 'none';
  }
}

async function openOrganizeModal() {
  _organizeCourse = currentCourse();
  if (!_organizeCourse) return;
  document.getElementById('organize-title').textContent = `🪄 Ordenar curso — ${_organizeCourse}`;
  document.getElementById('organize-status').textContent = 'Generando plan…';
  document.getElementById('organize-summary').innerHTML = '';
  document.getElementById('organize-list').innerHTML = '<p class="preview-loading">Analizando archivos y unidades…</p>';
  document.getElementById('organize-apply-btn').disabled = true;
  document.getElementById('organize-overlay').style.display = 'flex';

  try {
    const res = await fetch('/api/files/organize/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ course: _organizeCourse }),
    });
    const data = await res.json();
    document.getElementById('organize-status').textContent = '';
    if (!res.ok) {
      document.getElementById('organize-list').innerHTML =
        `<p class="preview-error">${_esc(data.error || 'No se pudo generar el plan')}</p>`;
      return;
    }
    _organizePlan = data.plan || [];
    renderOrganizeSummary(data.resumen || {});
    renderOrganizeList(_organizePlan);
    document.getElementById('organize-apply-btn').disabled = _organizePlan.length === 0;
  } catch (e) {
    document.getElementById('organize-status').textContent = '';
    document.getElementById('organize-list').innerHTML =
      `<p class="preview-error">Error generando el plan: ${_esc(String(e))}</p>`;
  }
}

function closeOrganizeModal() {
  document.getElementById('organize-overlay').style.display = 'none';
  _organizeCourse = null;
  _organizePlan = [];
}

function renderOrganizeSummary(resumen) {
  const el = document.getElementById('organize-summary');
  const porCarpeta = resumen.por_carpeta || {};
  const entries = Object.entries(porCarpeta);
  if (!entries.length) { el.innerHTML = ''; return; }
  el.innerHTML = entries
    .map(([folder, count]) => `<span class="organize-chip">📁 ${_esc(folder)} · ${count}</span>`)
    .join('');
}

function renderOrganizeList(plan) {
  const list = document.getElementById('organize-list');
  list.innerHTML = '';
  if (!plan.length) {
    list.innerHTML = '<p class="preview-note">Todo el material ya está organizado — no hay movimientos que hacer.</p>';
    return;
  }
  plan.forEach((mv, idx) => {
    const row = document.createElement('div');
    row.className = 'organize-row';

    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = true;
    check.dataset.idx = String(idx);

    const src = document.createElement('span');
    src.className = 'organize-path organize-src';
    src.textContent = mv.src;

    const arrow = document.createElement('span');
    arrow.className = 'organize-arrow';
    arrow.textContent = '→';

    const dst = document.createElement('span');
    dst.className = 'organize-path organize-dst';
    dst.textContent = mv.dst;

    row.appendChild(check);
    row.appendChild(src);
    row.appendChild(arrow);
    row.appendChild(dst);
    list.appendChild(row);
  });
}

async function applyOrganize() {
  if (!_organizeCourse || !_organizePlan.length) return;
  const checks = document.querySelectorAll('#organize-list .organize-row input[type="checkbox"]');
  const moves = [];
  checks.forEach(chk => {
    if (chk.checked) {
      const mv = _organizePlan[Number(chk.dataset.idx)];
      if (mv) moves.push({ src: mv.src, dst: mv.dst });
    }
  });
  if (!moves.length) { alert('No hay movimientos seleccionados.'); return; }

  const status = document.getElementById('organize-status');
  const applyBtn = document.getElementById('organize-apply-btn');
  status.textContent = `Moviendo ${moves.length} archivo(s)…`;
  applyBtn.disabled = true;
  try {
    const res = await fetch('/api/files/organize/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ course: _organizeCourse, moves }),
    });
    const data = await res.json();
    if (!res.ok) { status.textContent = ''; applyBtn.disabled = false; alert(data.error || 'No se pudo aplicar el orden'); return; }
    status.textContent = `Listo: ${data.moved} movido(s). Reindexando material en segundo plano…`;
    loadFiles(_curPath);
    setTimeout(() => { closeOrganizeModal(); }, 1200);
  } catch (e) {
    status.textContent = '';
    applyBtn.disabled = false;
    alert('Error aplicando el orden: ' + e);
  }
}

async function undoOrganize() {
  const course = currentCourse();
  if (!course) return;
  if (!confirm('¿Deshacer la última reorganización de este curso? Los archivos volverán a su ubicación anterior.')) return;
  try {
    const res = await fetch('/api/files/organize/undo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ course }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'No se pudo deshacer'); return; }
    alert(`Se restauraron ${data.restored} archivo(s).`);
    loadFiles(_curPath);
  } catch (e) {
    alert('Error deshaciendo el orden: ' + e);
  }
}

