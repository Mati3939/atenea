// ── Pomodoro global ─────────────────────────────────────────────────────────
// Temporizador Pomodoro persistido en localStorage para que sobreviva la
// navegación entre páginas. Se carga en TODAS las páginas (base.html, defer):
// - Si hay un timer activo/pausado, muestra un widget flotante (esquina
//   inferior derecha) con controles básicos.
// - Si estamos en /metodos, además alimenta el panel fijo con controles
//   completos (duración, iniciar/pausar/reanudar/reiniciar, fase, contador).
// - Si el método de estudio elegido en el chat es "pomodoro" y no hay timer
//   activo, ofrece un botón "Iniciar pomodoro" en el widget flotante.
(function () {
  const STORAGE_KEY   = 'atenea_pomodoro';
  const DEFAULT_FOCUS = 25;
  const DEFAULT_BREAK = 5;

  // Claves usadas por chat.js — deben coincidir exactamente para poder leer
  // qué método de estudio está activo en la sesión de chat actual.
  const CHAT_SESSION_KEY = 'atenea_session_id';
  const chatStateKey = sid => 'atenea_chat_' + sid;

  function _loadState() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  }

  function _saveState(state) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch {}
  }

  function _clearState() {
    try { localStorage.removeItem(STORAGE_KEY); } catch {}
  }

  function _fmt(ms) {
    const total = Math.max(0, Math.ceil(ms / 1000));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  function _beep() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx  = new Ctx();
      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = 800;
      gain.gain.setValueAtTime(0.16, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.3);
      osc.onended = () => ctx.close();
    } catch {}
  }

  // ── API pública (Iniciar/Pausar/Reanudar/Reiniciar/Detener) ────────────────
  const Pomodoro = {
    start(focusMin, breakMin) {
      const prev = _loadState();
      focusMin = Number(focusMin) || DEFAULT_FOCUS;
      breakMin = Number(breakMin) || DEFAULT_BREAK;
      _saveState({
        phase: 'focus',
        endsAt: Date.now() + focusMin * 60000,
        paused: false,
        remaining: focusMin * 60000,
        focusMin, breakMin,
        count: (prev && prev.count) || 0,
      });
      _dismissedPrompt = false;
      _tick();
    },
    pause() {
      const st = _loadState();
      if (!st || st.paused) return;
      st.remaining = Math.max(0, st.endsAt - Date.now());
      st.paused = true;
      _saveState(st);
      _tick();
    },
    resume() {
      const st = _loadState();
      if (!st || !st.paused) return;
      st.endsAt = Date.now() + st.remaining;
      st.paused = false;
      _saveState(st);
      _tick();
    },
    reset() {
      const st = _loadState();
      const focusMin = st ? st.focusMin : DEFAULT_FOCUS;
      const breakMin = st ? st.breakMin : DEFAULT_BREAK;
      const count    = st ? (st.count || 0) : 0;
      _saveState({
        phase: 'focus',
        endsAt: Date.now() + focusMin * 60000,
        paused: false,
        remaining: focusMin * 60000,
        focusMin, breakMin, count,
      });
      _tick();
    },
    stop() {
      _clearState();
      _tick();
    },
    getState: _loadState,
  };
  window.AteneaPomodoro = Pomodoro;

  // ── ¿El chat activo tiene el método Pomodoro seleccionado? ─────────────────
  let _dismissedPrompt = false;

  function _chatWantsPomodoro() {
    try {
      const sid = localStorage.getItem(CHAT_SESSION_KEY);
      if (!sid) return false;
      const raw = localStorage.getItem(chatStateKey(sid));
      if (!raw) return false;
      const chatState = JSON.parse(raw);
      return !!(chatState && chatState.method === 'pomodoro');
    } catch { return false; }
  }

  // ── Widget flotante (todas las páginas) ─────────────────────────────────────
  let _widgetEl   = null;
  let _widgetMode = null; // 'timer' | 'prompt' | null

  function _buildWidget(mode) {
    const el = document.createElement('div');
    el.id = 'pomodoro-widget';
    el.className = 'pomodoro-widget';

    if (mode === 'prompt') {
      el.innerHTML =
        '<div class="pomodoro-widget-head">' +
          '<span class="pomodoro-widget-phase">🍅 Pomodoro</span>' +
          '<button class="pomodoro-widget-close" title="Ocultar">✕</button>' +
        '</div>' +
        '<div class="pomodoro-widget-hint">Tu método de estudio es Pomodoro.</div>' +
        '<button type="button" class="pomodoro-widget-start-btn">▶ Iniciar pomodoro</button>';

      el.querySelector('.pomodoro-widget-close').addEventListener('click', () => {
        _dismissedPrompt = true;
        _removeWidget();
      });
      el.querySelector('.pomodoro-widget-start-btn').addEventListener('click', () => {
        Pomodoro.start(DEFAULT_FOCUS, DEFAULT_BREAK);
      });
    } else {
      el.innerHTML =
        '<div class="pomodoro-widget-head">' +
          '<span class="pomodoro-widget-phase"></span>' +
          '<button class="pomodoro-widget-close" title="Detener">✕</button>' +
        '</div>' +
        '<div class="pomodoro-widget-time"></div>' +
        '<div class="pomodoro-widget-actions">' +
          '<button type="button" class="pomodoro-widget-btn" data-action="toggle"></button>' +
          '<button type="button" class="pomodoro-widget-btn" data-action="reset" title="Reiniciar">↺</button>' +
        '</div>' +
        '<div class="pomodoro-widget-count"></div>';

      el.querySelector('.pomodoro-widget-close').addEventListener('click', () => Pomodoro.stop());
      el.querySelector('[data-action="toggle"]').addEventListener('click', () => {
        const st = _loadState();
        if (!st) return;
        if (st.paused) Pomodoro.resume(); else Pomodoro.pause();
      });
      el.querySelector('[data-action="reset"]').addEventListener('click', () => Pomodoro.reset());
    }

    document.body.appendChild(el);
    return el;
  }

  function _removeWidget() {
    if (_widgetEl) _widgetEl.remove();
    _widgetEl = null;
    _widgetMode = null;
  }

  function _ensureWidget(mode) {
    if (_widgetEl && _widgetMode === mode) return _widgetEl;
    _removeWidget();
    _widgetEl = _buildWidget(mode);
    _widgetMode = mode;
    return _widgetEl;
  }

  function _flashWidget() {
    if (_widgetEl) {
      _widgetEl.classList.remove('pomodoro-widget--flash');
      // Forzar reflow para reiniciar la animación si ya estaba aplicada.
      void _widgetEl.offsetWidth;
      _widgetEl.classList.add('pomodoro-widget--flash');
      setTimeout(() => { if (_widgetEl) _widgetEl.classList.remove('pomodoro-widget--flash'); }, 1600);
    }
    const fixedPanel = document.getElementById('pomodoro-fixed-panel');
    if (fixedPanel) {
      fixedPanel.classList.remove('pomodoro-fixed-panel--flash');
      void fixedPanel.offsetWidth;
      fixedPanel.classList.add('pomodoro-fixed-panel--flash');
      setTimeout(() => fixedPanel.classList.remove('pomodoro-fixed-panel--flash'), 1600);
    }
  }

  // ── Título del documento (notificación de cambio de fase) ──────────────────
  const _origTitle = document.title;
  let _titleFlashTimer = null;

  function _flashTitle(msg) {
    if (_titleFlashTimer) { clearInterval(_titleFlashTimer); _titleFlashTimer = null; }
    let on = true;
    let n = 0;
    document.title = msg;
    _titleFlashTimer = setInterval(() => {
      document.title = on ? _origTitle : msg;
      on = !on;
      n++;
      if (n >= 6) {
        clearInterval(_titleFlashTimer);
        _titleFlashTimer = null;
        document.title = _origTitle;
      }
    }, 900);
  }

  // ── Render ──────────────────────────────────────────────────────────────────
  function _renderWidget(st) {
    if (st) {
      const el = _ensureWidget('timer');
      const remaining = st.paused ? st.remaining : Math.max(0, st.endsAt - Date.now());
      el.querySelector('.pomodoro-widget-phase').textContent = st.phase === 'focus' ? '🍅 Foco' : '☕ Descanso';
      el.querySelector('.pomodoro-widget-time').textContent  = _fmt(remaining);
      el.querySelector('[data-action="toggle"]').textContent = st.paused ? '▶' : '⏸';
      el.querySelector('.pomodoro-widget-count').textContent = `Pomodoros completados: ${st.count || 0}`;
      el.classList.toggle('pomodoro-widget--break', st.phase === 'break');
      return;
    }
    if (!_dismissedPrompt && _chatWantsPomodoro()) {
      _ensureWidget('prompt');
      return;
    }
    _removeWidget();
  }

  function _renderFixedPanel(st) {
    const panel = document.getElementById('pomodoro-fixed-panel');
    if (!panel) return;

    const focusInput = document.getElementById('pomodoro-focus-min');
    const breakInput = document.getElementById('pomodoro-break-min');
    const display     = document.getElementById('pomodoro-display');
    const phaseEl     = document.getElementById('pomodoro-phase');
    const countEl     = document.getElementById('pomodoro-count');
    const startBtn    = document.getElementById('pomodoro-start-btn');
    const pauseBtn    = document.getElementById('pomodoro-pause-btn');
    const resetBtn    = document.getElementById('pomodoro-reset-btn');

    if (!st) {
      const focusMin = Number(focusInput.value) || DEFAULT_FOCUS;
      display.textContent  = _fmt(focusMin * 60000);
      phaseEl.textContent  = '🍅 Foco';
      countEl.textContent  = 'Pomodoros completados: 0';
      startBtn.style.display = '';
      pauseBtn.style.display = 'none';
      resetBtn.disabled = true;
      focusInput.disabled = false;
      breakInput.disabled = false;
      return;
    }

    const remaining = st.paused ? st.remaining : Math.max(0, st.endsAt - Date.now());
    display.textContent = _fmt(remaining);
    phaseEl.textContent  = st.phase === 'focus' ? '🍅 Foco' : '☕ Descanso';
    countEl.textContent  = `Pomodoros completados: ${st.count || 0}`;
    startBtn.style.display = 'none';
    pauseBtn.style.display = '';
    pauseBtn.textContent = st.paused ? '▶ Reanudar' : '⏸ Pausar';
    resetBtn.disabled = false;
    focusInput.value = st.focusMin;
    breakInput.value = st.breakMin;
    focusInput.disabled = true;
    breakInput.disabled = true;
  }

  // ── Tick principal (cada segundo) ───────────────────────────────────────────
  function _tick() {
    let st = _loadState();

    if (st && !st.paused && st.endsAt - Date.now() <= 0) {
      if (st.phase === 'focus') {
        st.count  = (st.count || 0) + 1;
        st.phase  = 'break';
        st.endsAt = Date.now() + st.breakMin * 60000;
        _saveState(st);
        _beep();
        _flashTitle('☕ ¡Descanso!');
      } else {
        st.phase  = 'focus';
        st.endsAt = Date.now() + st.focusMin * 60000;
        _saveState(st);
        _beep();
        _flashTitle('🍅 ¡A estudiar!');
      }
      _flashWidget();
    }

    _renderWidget(st);
    _renderFixedPanel(st);
  }

  // ── Controles del panel fijo (/metodos) ─────────────────────────────────────
  function _wireFixedPanelControls() {
    const startBtn = document.getElementById('pomodoro-start-btn');
    const pauseBtn = document.getElementById('pomodoro-pause-btn');
    const resetBtn = document.getElementById('pomodoro-reset-btn');
    if (!startBtn) return; // no estamos en /metodos

    startBtn.addEventListener('click', () => {
      const focusMin = Number(document.getElementById('pomodoro-focus-min').value) || DEFAULT_FOCUS;
      const breakMin = Number(document.getElementById('pomodoro-break-min').value) || DEFAULT_BREAK;
      Pomodoro.start(focusMin, breakMin);
    });
    pauseBtn.addEventListener('click', () => {
      const st = _loadState();
      if (!st) return;
      if (st.paused) Pomodoro.resume(); else Pomodoro.pause();
    });
    resetBtn.addEventListener('click', () => Pomodoro.reset());
  }

  function _init() {
    _wireFixedPanelControls();
    _tick();
    setInterval(_tick, 1000);
    window.addEventListener('storage', e => {
      if (e.key === STORAGE_KEY || (e.key && e.key.startsWith('atenea_chat_'))) _tick();
    });
  }

  // El script se carga con `defer`, así que el DOM ya está listo; por robustez
  // cubrimos también el caso en que corriera antes de tiempo.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }
})();
