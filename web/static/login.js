document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("login-form");
  const canvasUrlInput = document.getElementById("canvas-url");
  const tokenInput = document.getElementById("token");
  const toggleBtn = document.getElementById("toggle-token");
  const errorBox = document.getElementById("login-error");
  const submitBtn = document.getElementById("login-submit");
  const existingBox = document.getElementById("existing-session");
  const existingBtn = document.getElementById("existing-btn");
  const existingName = document.getElementById("existing-name");

  toggleBtn.addEventListener("click", () => {
    const isPassword = tokenInput.type === "password";
    tokenInput.type = isPassword ? "text" : "password";
    toggleBtn.textContent = isPassword ? "🙈" : "👁";
  });

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.classList.add("visible");
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.classList.remove("visible");
  }

  async function doLogin(payload, btn, originalLabel) {
    clearError();
    btn.disabled = true;
    btn.textContent = "Conectando…";
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.ok) {
        window.location.href = "/";
      } else {
        showError(data.error || "No se pudo conectar. Intenta nuevamente.");
      }
    } catch (e) {
      showError("Error de red. Verifica tu conexión e inténtalo de nuevo.");
    } finally {
      btn.disabled = false;
      btn.textContent = originalLabel;
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const canvasUrl = canvasUrlInput.value.trim();
    const token = tokenInput.value.trim();
    if (!canvasUrl || !token) {
      showError("Completa la URL de Canvas y el token.");
      return;
    }
    doLogin({ canvas_url: canvasUrl, token }, submitBtn, "Conectar");
  });

  // Si ya hay una sesión configurada, ofrecer el atajo "Continuar como <nombre>"
  fetch("/api/login/status")
    .then((r) => r.json())
    .then((data) => {
      if (data.configured && data.name) {
        existingName.textContent = data.name;
        existingBox.classList.add("visible");
      }
    })
    .catch(() => {});

  existingBtn.addEventListener("click", () => {
    doLogin({ use_existing: true }, existingBtn, "Continuar como " + existingName.textContent);
  });
});
