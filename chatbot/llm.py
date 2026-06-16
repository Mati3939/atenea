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
import time
from typing import Generator

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
_TIMEOUT = 120
_MAX_RETRIES = 3
_MAX_TOKENS = 1024  # acota la respuesta (cuenta para el cupo TPM de Groq)


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


# ── Groq ───────────────────────────────────────────────────────────────────────

def _groq_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['GROQ_API_KEY'].strip()}",
        "Content-Type": "application/json",
    }


def _groq_post(messages: list[dict], temperature: float, stream: bool):
    """POST a Groq con reintentos ante 429 (rate limit) y errores 5xx."""
    payload = {"model": model_name(), "messages": messages, "temperature": temperature,
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


def _groq_complete(messages: list[dict], temperature: float) -> str:
    resp = _groq_post(messages, temperature, stream=False)
    # Decodificar como UTF-8 explícitamente: requests asume ISO-8859-1 cuando la
    # respuesta no declara charset, lo que rompe los acentos (mojibake).
    data = json.loads(resp.content.decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _groq_stream(messages: list[dict], temperature: float) -> Generator[str, None, None]:
    with _groq_post(messages, temperature, stream=True) as resp:
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
    """Respuesta completa del modelo (sin streaming)."""
    if provider() == "groq":
        try:
            return _groq_complete(messages, temperature)
        except requests.HTTPError as e:
            if _is_rate_limit(e):
                return _ollama_complete(messages, temperature)  # fallback local
            raise
    return _ollama_complete(messages, temperature)


def stream(messages: list[dict], temperature: float = 0.4) -> Generator[str, None, None]:
    """Genera los deltas de texto a medida que el modelo responde."""
    if provider() != "groq":
        yield from _ollama_stream(messages, temperature)
        return
    yielded = False
    try:
        for delta in _groq_stream(messages, temperature):
            yielded = True
            yield delta
    except requests.HTTPError as e:
        # Si Groq agota el cupo (429) y aún no emitió nada, responder con Ollama local
        if not yielded and _is_rate_limit(e):
            yield from _ollama_stream(messages, temperature)
        else:
            raise


def _is_rate_limit(e: requests.HTTPError) -> bool:
    return e.response is not None and e.response.status_code == 429


def warmup() -> None:
    """Precalienta el proveedor local (no-op para Groq)."""
    if provider() != "ollama":
        return
    try:
        _ollama_complete([{"role": "user", "content": "hola"}], 0.0)
    except Exception:
        pass
