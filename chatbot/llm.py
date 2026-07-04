"""Capa de abstracción del modelo de lenguaje (Groq | Ollama).

Centraliza TODAS las llamadas al LLM para que el resto del código no sepa qué
proveedor está activo. El proveedor se elige por variables de entorno:

- Si hay `GROQ_API_KEY` y `LLM_PROVIDER` != "ollama"  → Groq (nube, rápido).
- En cualquier otro caso                              → Ollama (local).

Así la demo usa Groq (1-3s, LaTeX limpio) y, quitando la key del `.env`, todo
vuelve a Ollama sin tocar código.
"""
import os
import json
import threading
import time
from typing import Generator

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
GROQ_DEFAULT_FALLBACK_MODEL = "llama-3.1-8b-instant"
_TIMEOUT = 120
_MAX_RETRIES = 3
_MAX_TOKENS = 1024  # acota la respuesta (cuenta para el cupo TPM de Groq)

# ── Proveedor/modelo que realmente respondió al último turno (thread-local) ────
# Permite que web/main.py exponga al frontend cuándo la respuesta vino de un
# modelo/proveedor de reserva (degraded=True) en vez del primario configurado.
_served_local = threading.local()


def _set_served(provider_name: str, model: str, degraded: bool) -> None:
    _served_local.provider = provider_name
    _served_local.model = model
    _served_local.degraded = degraded


def last_served() -> dict:
    """Info del proveedor/modelo que sirvió la última llamada a complete()/stream()
    en este hilo. `degraded=True` si no fue el modelo primario configurado."""
    return {
        "provider": getattr(_served_local, "provider", provider()),
        "model": getattr(_served_local, "model", model_name()),
        "degraded": getattr(_served_local, "degraded", False),
    }


def provider() -> str:
    """Devuelve 'groq' u 'ollama' según la configuración."""
    forced = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if forced in ("groq", "ollama"):
        # 'groq' forzado solo tiene sentido si hay key
        if forced == "groq" and not os.environ.get("GROQ_API_KEY", "").strip():
            return "ollama"
        return forced
    return "groq" if os.environ.get("GROQ_API_KEY", "").strip() else "ollama"


def model_name() -> str:
    if provider() == "groq":
        return os.environ.get("GROQ_MODEL", GROQ_DEFAULT_MODEL)
    return os.environ.get("OLLAMA_MODEL", "llama3.2")


def _groq_fallback_model() -> str:
    """Segundo modelo de Groq a intentar si el primario agota su cupo (TPD/TPM).
    Tiene cupo propio en el free tier, así que sirve de colchón antes de caer a
    Ollama local (mucho más lento)."""
    return os.environ.get("GROQ_FALLBACK_MODEL", GROQ_DEFAULT_FALLBACK_MODEL).strip()


def _ollama_fallback_enabled() -> bool:
    """Último eslabón de la cadena: Ollama local. Habilitado por defecto;
    OLLAMA_FALLBACK=0 lo desactiva (p. ej. si no hay Ollama corriendo)."""
    return os.environ.get("OLLAMA_FALLBACK", "1").strip() != "0"


# ── Groq ───────────────────────────────────────────────────────────────────────

def _groq_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['GROQ_API_KEY'].strip()}",
        "Content-Type": "application/json",
    }


def _groq_post(messages: list[dict], temperature: float, stream: bool, model: str | None = None):
    """POST a Groq con reintentos ante 429 (rate limit) y errores 5xx."""
    payload = {"model": model or model_name(), "messages": messages, "temperature": temperature,
               "max_tokens": _MAX_TOKENS}
    if stream:
        payload["stream"] = True
    resp = None
    for attempt in range(_MAX_RETRIES):
        resp = requests.post(GROQ_URL, headers=_groq_headers(), json=payload,
                             stream=stream, timeout=_TIMEOUT)
        if resp.status_code == 429 or resp.status_code >= 500:
            try:
                wait = float(resp.headers.get("retry-after", 1.5 * (attempt + 1)))
            except (TypeError, ValueError):
                wait = 1.5 * (attempt + 1)
            if attempt < _MAX_RETRIES - 1:
                time.sleep(min(wait, 8))
                continue
        break
    resp.raise_for_status()
    return resp


def _groq_complete(messages: list[dict], temperature: float, model: str | None = None) -> str:
    resp = _groq_post(messages, temperature, stream=False, model=model)
    # Decodificar como UTF-8 explícitamente: requests asume ISO-8859-1 cuando la
    # respuesta no declara charset, lo que rompe los acentos (mojibake).
    data = json.loads(resp.content.decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _groq_stream(messages: list[dict], temperature: float,
                  model: str | None = None) -> Generator[str, None, None]:
    with _groq_post(messages, temperature, stream=True, model=model) as resp:
        # Iterar bytes y decodificar UTF-8 a mano: con decode_unicode=True requests
        # usa ISO-8859-1 cuando el stream no declara charset y rompe los acentos.
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace")
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = obj["choices"][0]["delta"].get("content")
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            if delta:
                yield delta


# ── Ollama ─────────────────────────────────────────────────────────────────────

_OLLAMA_OPTS = {"keep_alive": "30m"}


def _ollama_model() -> str:
    # Siempre el modelo local, aunque el proveedor activo sea Groq (caso fallback).
    return os.environ.get("OLLAMA_MODEL", "llama3.2")


def _ollama_complete(messages: list[dict], temperature: float) -> str:
    import ollama
    resp = ollama.chat(
        model=_ollama_model(),
        messages=messages,
        options={"temperature": temperature, **_OLLAMA_OPTS},
        keep_alive="30m",
    )
    return resp["message"]["content"]


def _ollama_stream(messages: list[dict], temperature: float) -> Generator[str, None, None]:
    import ollama
    for chunk in ollama.chat(
        model=_ollama_model(),
        messages=messages,
        options={"temperature": temperature, **_OLLAMA_OPTS},
        keep_alive="30m",
        stream=True,
    ):
        delta = chunk.get("message", {}).get("content", "")
        if delta:
            yield delta


# ── API pública ────────────────────────────────────────────────────────────────

def complete(messages: list[dict], temperature: float = 0.4) -> str:
    """Respuesta completa del modelo (sin streaming).

    Cadena de fallback si el proveedor activo es Groq: modelo primario
    (`GROQ_MODEL`) → si falla (429/cupo agotado, modelo inválido, 5xx...) →
    modelo de reserva de Groq (`GROQ_FALLBACK_MODEL`, cupo propio) → si también
    falla → Ollama local (salvo `OLLAMA_FALLBACK=0`). Si todo falla, se propaga
    el último error. `llm.last_served()` expone qué proveedor/modelo respondió
    realmente.
    """
    if provider() != "groq":
        text = _ollama_complete(messages, temperature)
        _set_served("ollama", _ollama_model(), False)
        return text

    primary = model_name()
    last_err: Exception | None = None
    try:
        text = _groq_complete(messages, temperature, primary)
        _set_served("groq", primary, False)
        return text
    except requests.HTTPError as e:
        last_err = e

    fallback = _groq_fallback_model()
    if fallback and fallback != primary:
        try:
            text = _groq_complete(messages, temperature, fallback)
            _set_served("groq", fallback, True)
            return text
        except requests.HTTPError as e:
            last_err = e

    if _ollama_fallback_enabled():
        text = _ollama_complete(messages, temperature)
        _set_served("ollama", _ollama_model(), True)
        return text

    raise last_err


def stream(messages: list[dict], temperature: float = 0.4) -> Generator[str, None, None]:
    """Genera los deltas de texto a medida que el modelo responde.

    Misma cadena de fallback que `complete()`. Si un hop ya emitió texto antes
    de fallar, no se reintenta (evitaría duplicar/perder lo ya mostrado) y el
    error se propaga tal cual.
    """
    if provider() != "groq":
        _set_served("ollama", _ollama_model(), False)
        yield from _ollama_stream(messages, temperature)
        return

    primary = model_name()
    last_err: Exception | None = None
    yielded = False
    try:
        for delta in _groq_stream(messages, temperature, primary):
            yielded = True
            yield delta
        _set_served("groq", primary, False)
        return
    except requests.HTTPError as e:
        # Si ya emitió texto, no reintentar (duplicaría/perdería lo mostrado).
        if yielded:
            raise
        last_err = e

    fallback = _groq_fallback_model()
    if fallback and fallback != primary:
        yielded = False
        try:
            for delta in _groq_stream(messages, temperature, fallback):
                yielded = True
                yield delta
            _set_served("groq", fallback, True)
            return
        except requests.HTTPError as e:
            if yielded:
                raise
            last_err = e

    if _ollama_fallback_enabled():
        _set_served("ollama", _ollama_model(), True)
        yield from _ollama_stream(messages, temperature)
        return

    raise last_err


def warmup() -> None:
    """Precalienta el proveedor local (no-op para Groq)."""
    if provider() != "ollama":
        return
    try:
        _ollama_complete([{"role": "user", "content": "hola"}], 0.0)
    except Exception:
        pass
