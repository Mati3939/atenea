import os
import re
import json as _json
import time
import uuid
import zipfile
import threading
import hashlib
import shutil
import mimetypes
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path, PurePosixPath
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse, FileResponse
from typing import List
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    """Same logic as VectorStore._safe_name — kept here to avoid circular import."""
    s = re.sub(r"[^a-zA-Z0-9_\-.]", "_", name)[:63]
    s = re.sub(r"^[^a-zA-Z0-9]+", "", s)
    s = re.sub(r"[^a-zA-Z0-9]+$", "", s)
    return s.ljust(3, "0") if s else "col"


def _safe_folder(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def _find_course_dir(course_safe: str) -> Path | None:
    """Busca la carpeta de curso en data/: primero por coincidencia exacta de
    nombre de carpeta (así el gestor de archivos puede pasar el nombre real
    directamente), y si no, por _safe_name (así el chat, que usa safe_name,
    también funciona)."""
    data_dir = Path("data")
    if not data_dir.exists() or not course_safe:
        return None
    # Nunca tratar course_safe como ruta compuesta (evita escapar de data/).
    if "/" in course_safe or "\\" in course_safe or ".." in course_safe:
        return None
    exact = data_dir / course_safe
    if exact.is_dir():
        return exact
    for d in data_dir.iterdir():
        if d.is_dir() and _safe_name(d.name) == course_safe:
            return d
    return None


# Cursos de Canvas que NO son ramos académicos (institucionales, genéricos, programas).
# Se filtran del selector de cursos y del sync. Ajustable con EXCLUDED_COURSES en .env
# (lista separada por comas de subcadenas que se buscan en nombre + course_code).
_DEFAULT_EXCLUDED = [
    "concepcion_gen", "viveudd", "generico",
    "estudiantes destacados", "programa de estudiantes",
]


def _excluded_patterns() -> list[str]:
    env = os.environ.get("EXCLUDED_COURSES", "").strip()
    if env:
        return [p.strip().lower() for p in env.split(",") if p.strip()]
    return _DEFAULT_EXCLUDED


def _is_academic_course(course: dict) -> bool:
    """True si el curso parece un ramo real (no institucional/genérico)."""
    hay = (str(course.get("name", "")) + " " + str(course.get("course_code", ""))).lower()
    return not any(p in hay for p in _excluded_patterns())


def _get_store():
    from ingestion.vectorstore import VectorStore
    return VectorStore()


# ── Login / auth (single-user, sesión vía cookie) ───────────────────────────────

_AUTH_COOKIE = "atenea_auth"
_ENV_PATH = Path(".env")
_GATED_PATHS = {"/", "/chat", "/organizacion", "/metodos", "/dashboard", "/archivos"}


def _token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:32]


def _is_authed(request: Request) -> bool:
    token = os.environ.get("CANVAS_TOKEN", "").strip()
    if not token:
        return False
    cookie_val = request.cookies.get(_AUTH_COOKIE, "")
    return bool(cookie_val) and cookie_val == _token_hash(token)


def _write_env_keys(updates: dict) -> None:
    """Actualiza/crea claves en .env preservando el resto de líneas y comentarios."""
    lines: list[str] = []
    if _ENV_PATH.exists():
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()

    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        new_lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")

    _ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value


def _validate_canvas_token(canvas_url: str, token: str) -> dict:
    """Valida el token contra Canvas. Devuelve {'ok':True,'name':...} o {'ok':False,'error':...}."""
    if not canvas_url or not token:
        return {"ok": False, "error": "Falta la URL de Canvas o el token."}
    url = canvas_url.rstrip("/") + "/api/v1/users/self"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    except requests.RequestException:
        return {"ok": False, "error": "No se pudo conectar con Canvas. Verifica la URL."}
    if r.status_code == 401:
        return {"ok": False, "error": "Token inválido o expirado."}
    if not r.ok:
        return {"ok": False, "error": f"Canvas respondió con un error ({r.status_code})."}
    try:
        data = r.json()
    except Exception:
        return {"ok": False, "error": "Respuesta inesperada de Canvas."}
    name = data.get("name") or data.get("short_name") or "Usuario"
    return {"ok": True, "name": name}


# ── Ollama warmup (loads models into RAM so first prompt is faster) ────────────

def _warmup_ollama():
    # Precalienta el modelo de chat solo si el proveedor activo es Ollama
    # (Groq no necesita warmup). El embedding siempre es Ollama.
    try:
        from chatbot import llm
        llm.warmup()
    except Exception:
        pass
    try:
        import ollama
        from ingestion.vectorstore import DEFAULT_EMBEDDING_MODEL
        emb = os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
        if emb and emb.lower() != "default":
            ollama.embed(model=emb, input="hola")
    except Exception:
        pass


_AUTOSYNC_STAMP = Path("logs") / ".autosync_done"
_AUTOSYNC_MIN_INTERVAL = 3600  # s — no relanzar en cada reinicio por reload


def _should_autosync() -> bool:
    if os.environ.get("AUTO_SYNC", "1").strip().lower() in ("0", "false", "no", ""):
        return False
    try:
        if _AUTOSYNC_STAMP.exists() and (time.time() - _AUTOSYNC_STAMP.stat().st_mtime) < _AUTOSYNC_MIN_INTERVAL:
            return False
    except Exception:
        pass
    return True


def _auto_sync():
    """Sincroniza Canvas e indexa en segundo plano al arrancar. No bloquea la app."""
    try:
        _AUTOSYNC_STAMP.parent.mkdir(parents=True, exist_ok=True)
        _AUTOSYNC_STAMP.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass
    try:
        if not _status["sync"]["running"]:
            _status["sync"] = _sync_state(running=True)
            _run_sync()
    except Exception:
        pass
    try:
        if not _status["ingest"]["running"]:
            _status["ingest"] = _ingest_state(running=True)
            _run_ingest()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warmup_ollama, daemon=True).start()
    if _should_autosync():
        threading.Thread(target=_auto_sync, daemon=True).start()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Atenea", lifespan=lifespan)
templates = Jinja2Templates(directory="web/templates")
app.mount("/static", StaticFiles(directory="web/static"), name="static")

chat_sessions: dict = {}

_fetch_status: dict[int, dict] = {}  # canvas_id → status


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    """Exige sesión (cookie atenea_auth) para las páginas HTML principales.
    /demo, /login y /api/* (además de /static) quedan siempre accesibles."""
    if request.method == "GET" and request.url.path in _GATED_PATHS and not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# ── Login / onboarding ───────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    canvas_url = os.environ.get("CANVAS_URL", "").strip() or "https://udd.instructure.com"
    configured = bool(os.environ.get("CANVAS_TOKEN", "").strip())
    return templates.TemplateResponse(request, "login.html", {
        "canvas_url": canvas_url,
        "configured": configured,
    })


@app.get("/api/login/status")
async def api_login_status():
    token = os.environ.get("CANVAS_TOKEN", "").strip()
    canvas_url = os.environ.get("CANVAS_URL", "").strip()
    if not token or not canvas_url:
        return {"configured": False, "name": None}
    result = _validate_canvas_token(canvas_url, token)
    if result.get("ok"):
        return {"configured": True, "name": result["name"]}
    return {"configured": False, "name": None}


@app.post("/api/login")
async def api_login(request: Request):
    data = await request.json()
    use_existing = bool(data.get("use_existing"))

    if use_existing:
        canvas_url = os.environ.get("CANVAS_URL", "").strip()
        token = os.environ.get("CANVAS_TOKEN", "").strip()
        if not token or not canvas_url:
            return JSONResponse(
                {"ok": False, "error": "No hay una sesión previa configurada."}, status_code=400
            )
    else:
        canvas_url = (data.get("canvas_url") or "").strip().rstrip("/")
        token = (data.get("token") or "").strip()
        if not canvas_url or not token:
            return JSONResponse(
                {"ok": False, "error": "Completa la URL de Canvas y el token."}, status_code=400
            )

    result = _validate_canvas_token(canvas_url, token)
    if not result.get("ok"):
        return JSONResponse(result, status_code=401)

    if not use_existing:
        _write_env_keys({"CANVAS_URL": canvas_url, "CANVAS_TOKEN": token})

    resp = JSONResponse({"ok": True, "name": result["name"]})
    resp.set_cookie(
        key=_AUTH_COOKIE,
        value=_token_hash(token),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return resp


@app.post("/api/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_AUTH_COOKIE)
    return resp


@app.get("/logout")
async def logout_redirect():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(_AUTH_COOKIE)
    return resp


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "chat.html", {
        "active_page": "chat",
    })


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return RedirectResponse("/")


@app.get("/archivos", response_class=HTMLResponse)
async def archivos_page(request: Request):
    return templates.TemplateResponse(request, "archivos.html", {
        "active_page": "archivos",
    })


@app.get("/organizacion", response_class=HTMLResponse)
async def organizacion_page(request: Request):
    return templates.TemplateResponse(request, "organizacion.html", {
        "active_page": "organizacion",
    })


@app.get("/metodos", response_class=HTMLResponse)
async def metodos_page(request: Request):
    return templates.TemplateResponse(request, "metodos.html", {
        "active_page": "metodos",
    })


@app.get("/analysis", response_class=HTMLResponse)
async def analysis_page():
    # La revisión de métodos de estudio vive ahora dentro de Organización.
    return RedirectResponse("/organizacion")


@app.get("/demo", response_class=HTMLResponse)
async def demo_page(request: Request):
    return templates.TemplateResponse(request, "demo.html", {"active_page": "demo"})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Página de administración (sync/ingest manual). No aparece en el nav principal."""
    from ingestion.vectorstore import VectorStore
    safe_names = VectorStore().list_collections()
    data_dir = Path("data")
    safe_to_label: dict[str, str] = {}
    if data_dir.exists():
        for d in data_dir.iterdir():
            if d.is_dir():
                safe_to_label[_safe_name(d.name)] = d.name
    courses = [
        {"value": s, "label": safe_to_label.get(s, s.replace("_", " ").strip())}
        for s in safe_names
    ]
    return templates.TemplateResponse(request, "index.html", {
        "active_page": "home",
        "courses": courses,
        "sync_status": _status["sync"],
        "ingest_status": _status["ingest"],
    })


# ── Chat API ──────────────────────────────────────────────────────────────────

MAX_ACTIVE_SESSIONS = 30


def _safe_session_id(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "", raw or "")[:48] or "default"


def _get_session(session_id: str):
    """Devuelve la sesión en memoria, restaurándola de disco si el servidor
    se reinició. Expulsa (guardando) las sesiones menos usadas."""
    from chatbot.conversation import AteneoChat

    if session_id not in chat_sessions:
        chat_sessions[session_id] = AteneoChat.restore(session_id) or AteneoChat()

    if len(chat_sessions) > MAX_ACTIVE_SESSIONS:
        oldest = min(chat_sessions, key=lambda k: getattr(chat_sessions[k], "last_used", 0))
        if oldest != session_id:
            try:
                chat_sessions[oldest].save_state(oldest)
            except Exception:
                pass
            del chat_sessions[oldest]

    chat_obj = chat_sessions[session_id]
    chat_obj.last_used = time.time()
    return chat_obj


def _parse_chat_payload(data: dict):
    return {
        "session_id": _safe_session_id(data.get("session_id", "default")),
        "message":    (data.get("message") or "").strip(),
        "course":     data.get("course") or None,
        "unit":       data.get("unit") or None,
        "difficulty": data.get("difficulty") or "practicando",
        "mode":       data.get("mode") or None,
        "method":     data.get("method") or None,
    }


@app.post("/api/session/new")
async def new_session():
    return {"session_id": str(uuid.uuid4())}


@app.post("/api/chat")
async def api_chat(request: Request):
    p = _parse_chat_payload(await request.json())
    if not p["message"]:
        return JSONResponse({"error": "Mensaje vacío"}, status_code=400)

    chat_obj = _get_session(p["session_id"])
    try:
        result = chat_obj.chat(p["message"], course=p["course"], unit=p["unit"],
                               difficulty=p["difficulty"], mode=p["mode"], method=p["method"])
        chat_obj.save_session()
        chat_obj.save_state(p["session_id"])
        return {
            "response": result["text"], "options": result.get("options"),
            "degraded": result.get("degraded", False), "provider": result.get("provider"),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/chat/stream")
async def api_chat_stream(request: Request):
    p = _parse_chat_payload(await request.json())
    if not p["message"]:
        return JSONResponse({"error": "Mensaje vacío"}, status_code=400)

    chat_obj = _get_session(p["session_id"])

    def gen():
        try:
            # chat_stream() ya incluye degraded/provider en el evento "done"
            # (calculados en conversation.py inmediatamente tras llm.stream(),
            # en el mismo hilo — así no dependemos de una consulta aparte a
            # llm.last_served() que podría leer un valor de otro turno).
            for event in chat_obj.chat_stream(p["message"], course=p["course"], unit=p["unit"],
                                              difficulty=p["difficulty"], mode=p["mode"],
                                              method=p["method"]):
                yield _json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:
            yield _json.dumps({"error": str(e)}, ensure_ascii=False) + "\n"
        finally:
            try:
                chat_obj.save_session()
                chat_obj.save_state(p["session_id"])
            except Exception:
                pass

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.delete("/api/session/{session_id}")
async def clear_session(session_id: str):
    from chatbot.conversation import AteneoChat
    session_id = _safe_session_id(session_id)
    if session_id in chat_sessions:
        chat_sessions[session_id].save_session()
        del chat_sessions[session_id]
    AteneoChat.delete_state(session_id)
    return {"cleared": True}


# ── Chat sessions history API ────────────────────────────────────────────────
# Cada turno de chat persiste su estado completo en logs/web_sessions/<sid>.json
# (AteneoChat.save_state); estos endpoints exponen ese historial agrupado por
# curso para la UI (botón "Historial" en el chat).

_SESSIONS_DIR = Path("logs") / "web_sessions"
SESSIONS_MAX_PER_COURSE = 20


def _safe_name_to_label_map() -> dict[str, str]:
    """safe_name (el valor de 'course' que usa el chat) -> nombre legible de carpeta."""
    mapping: dict[str, str] = {}
    data_dir = Path("data")
    if data_dir.exists():
        for d in data_dir.iterdir():
            if d.is_dir():
                mapping[_safe_name(d.name)] = d.name
    return mapping


def _read_session_file(path: Path) -> dict | None:
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _session_title(data: dict, transcript: list, unit: str | None) -> str:
    custom = (data.get("title") or "").strip()
    if custom:
        return custom
    first_user = next((t.get("text", "") for t in transcript if t.get("role") == "user"), "")
    first_user = (first_user or "").strip()
    if first_user:
        return first_user[:60] + ("…" if len(first_user) > 60 else "")
    if unit:
        return unit
    return "Sesión sin título"


def _session_summary(sid: str, data: dict, label_map: dict[str, str]) -> dict | None:
    """None si el archivo no tiene forma de sesión mostrable (formato viejo/de
    prueba sin curso NI transcript, p. ej. verif_*.json)."""
    transcript = data.get("transcript") or []
    course = data.get("course") or None
    if not course and not transcript:
        return None

    unit = data.get("unit") or None
    return {
        "sid": sid,
        "course": course,
        "course_label": (label_map.get(course, course) if course else None),
        "unit": unit,
        "method": data.get("method") or None,
        "mode": data.get("last_mode") or None,
        "updated_at": data.get("updated_at") or "",
        "title": _session_title(data, transcript, unit),
        "n_messages": len(transcript),
    }


def _session_sort_key(item: dict) -> datetime:
    try:
        return datetime.fromisoformat(item["updated_at"])
    except Exception:
        return datetime.min


@app.get("/api/sessions")
async def list_sessions():
    """Historial de sesiones de chat. Ignora archivos que no parseen o que no
    tengan curso NI transcript (sesiones de formatos anteriores/de prueba).
    Poda a SESSIONS_MAX_PER_COURSE las sesiones más antiguas de cada curso
    (las sesiones sin curso no se podan)."""
    if not _SESSIONS_DIR.exists():
        return {"sessions": []}

    label_map = _safe_name_to_label_map()
    items: list[dict] = []
    for path in _SESSIONS_DIR.glob("*.json"):
        data = _read_session_file(path)
        if data is None:
            continue
        summary = _session_summary(path.stem, data, label_map)
        if summary is not None:
            items.append(summary)

    items.sort(key=_session_sort_key, reverse=True)

    by_course: dict[str, list[dict]] = {}
    for it in items:
        if it["course"]:
            by_course.setdefault(it["course"], []).append(it)

    to_remove: set[str] = set()
    for group in by_course.values():
        for it in group[SESSIONS_MAX_PER_COURSE:]:
            to_remove.add(it["sid"])

    if to_remove:
        from chatbot.conversation import AteneoChat
        for sid in to_remove:
            AteneoChat.delete_state(sid)
            chat_sessions.pop(sid, None)
        items = [it for it in items if it["sid"] not in to_remove]

    return {"sessions": items}


@app.get("/api/sessions/{sid}")
async def get_session_detail(sid: str):
    sid = _safe_session_id(sid)
    data = _read_session_file(_SESSIONS_DIR / f"{sid}.json")
    if data is None:
        return JSONResponse({"error": "Sesión no encontrada"}, status_code=404)

    label_map = _safe_name_to_label_map()
    course = data.get("course") or None
    return {
        "sid": sid,
        "course": course,
        "course_label": (label_map.get(course, course) if course else None),
        "unit": data.get("unit") or None,
        "method": data.get("method") or None,
        "mode": data.get("last_mode") or None,
        "transcript": data.get("transcript", []),
        "options": data.get("options"),
    }


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    from chatbot.conversation import AteneoChat
    sid = _safe_session_id(sid)
    path = _SESSIONS_DIR / f"{sid}.json"
    if not path.exists():
        return JSONResponse({"error": "Sesión no encontrada"}, status_code=404)
    AteneoChat.delete_state(sid)
    chat_sessions.pop(sid, None)
    return {"deleted": True}


@app.put("/api/sessions/{sid}")
async def rename_session(sid: str, request: Request):
    sid = _safe_session_id(sid)
    path = _SESSIONS_DIR / f"{sid}.json"
    data = _read_session_file(path)
    if data is None:
        return JSONResponse({"error": "Sesión no encontrada"}, status_code=404)

    body = await request.json()
    title = str(body.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "Título vacío"}, status_code=400)

    data["title"] = title[:120]
    try:
        path.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"ok": True, "title": data["title"]}


# ── Canvas courses API ────────────────────────────────────────────────────────

@app.get("/api/canvas/courses")
async def get_canvas_courses():
    """Devuelve todos los cursos activos de Canvas con estado de indexación."""
    try:
        from canvas_api import CanvasClient
        from ingestion.vectorstore import VectorStore

        client = CanvasClient(os.environ["CANVAS_URL"], os.environ["CANVAS_TOKEN"])
        courses = client.get_courses()

        store = VectorStore()
        indexed_set = set(store.list_collections())

        result = []
        for c in courses:
            if not _is_academic_course(c):
                continue
            name = c.get("name", str(c["id"]))
            safe = _safe_name(_safe_folder(name))
            # Un curso indexado con otro embedding se reporta como no indexado
            # para que el flujo de chat dispare el re-indexado automático.
            result.append({
                "canvas_id": c["id"],
                "label":     name,
                "safe_name": safe,
                "indexed":   safe in indexed_set and store.embedding_compatible(safe),
            })

        return {"courses": result}
    except KeyError:
        return JSONResponse({"error": "Canvas no configurado. Define CANVAS_URL y CANVAS_TOKEN en .env"}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Per-course fetch + index API ──────────────────────────────────────────────

@app.post("/api/fetch/course/{canvas_id}")
async def fetch_course(canvas_id: int, background_tasks: BackgroundTasks, name: str = ""):
    """Descarga e indexa un solo curso de Canvas bajo demanda."""
    if _fetch_status.get(canvas_id, {}).get("running"):
        return {"status": "already_running"}

    course_name = name or str(canvas_id)
    _fetch_status[canvas_id] = {
        "running": True, "done": False, "error": None,
        "phase": "Iniciando...", "percentage": 0,
    }
    background_tasks.add_task(_run_fetch_course, canvas_id, course_name)
    return {"status": "started"}


def _extract_zip(zip_path: Path, target_dir: Path) -> list[str]:
    """Extrae solo los archivos soportados de un ZIP. Devuelve errores."""
    from ingestion.parsers import SUPPORTED
    errors = []
    try:
        with zipfile.ZipFile(zip_path) as z:
            for member in z.namelist():
                if ".." in member or member.startswith(("/", "\\")):
                    continue
                # Archivos AppleDouble de macOS ("._foo.pdf") no son documentos reales
                if Path(member).name.startswith("._"):
                    continue
                if Path(member).suffix.lower() in SUPPORTED:
                    try:
                        z.extract(member, target_dir)
                    except Exception as e:
                        errors.append(f"{zip_path.name}/{member}: {e}")
    except Exception as e:
        errors.append(f"{zip_path.name}: {e}")
    return errors


def _extract_pending_zips(course_dir: Path) -> list[str]:
    """Extrae los ZIP del curso que aún no tienen carpeta de extracción."""
    errors = []
    for zp in course_dir.rglob("*.zip"):
        target = zp.parent / f"{zp.stem}_zip"
        if not target.exists():
            errors.extend(_extract_zip(zp, target))
    return errors


def _save_canvas_pages(client, canvas_id: int, course_dir: Path) -> list[str]:
    """Guarda Pages y Announcements del curso como .md para indexarlos."""
    from canvas_api.client import html_to_text
    errors = []

    for p in client.get_pages(canvas_id):
        title = p.get("title") or p.get("url", "pagina")
        dest = course_dir / "Paginas" / (_safe_folder(title)[:120] + ".md")
        if dest.exists():
            continue
        try:
            body = client.get_page_body(canvas_id, p["url"])
            text = html_to_text(body)
            if text:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(f"# {title}\n\n{text}", encoding="utf-8")
        except Exception as e:
            errors.append(f"Página '{title}': {e}")

    for a in client.get_announcements(canvas_id):
        title = a.get("title") or "anuncio"
        dest = course_dir / "Anuncios" / (_safe_folder(title)[:120] + ".md")
        if dest.exists():
            continue
        try:
            text = html_to_text(a.get("message") or "")
            if text:
                dest.parent.mkdir(parents=True, exist_ok=True)
                posted = (a.get("posted_at") or "")[:10]
                dest.write_text(f"# Anuncio: {title}\n\nFecha: {posted}\n\n{text}", encoding="utf-8")
        except Exception as e:
            errors.append(f"Anuncio '{title}': {e}")

    return errors


def _save_assignments(client, canvas_id: int, course_name: str, course_dir: Path) -> None:
    """Guarda las entregas/evaluaciones del curso en _assignments.json para la Agenda."""
    try:
        assignments = client.get_assignments(canvas_id)
    except Exception:
        return
    items = []
    for a in assignments:
        if not a.get("due_at"):
            continue
        items.append({
            "name":   a.get("name") or "Sin título",
            "due_at": a.get("due_at"),
            "url":    a.get("html_url") or "",
            "points": a.get("points_possible"),
            "course": course_name,
        })
    try:
        course_dir.mkdir(parents=True, exist_ok=True)
        (course_dir / "_assignments.json").write_text(
            _json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _run_fetch_course(canvas_id: int, course_name: str):
    s = _fetch_status[canvas_id]
    failures: list[str] = []
    try:
        from canvas_api import CanvasClient, file_dest
        from ingestion.parsers import parse, SUPPORTED
        from ingestion.chunker import chunk
        from ingestion.vectorstore import VectorStore

        client = CanvasClient(os.environ["CANVAS_URL"], os.environ["CANVAS_TOKEN"])
        data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)

        course_dir = data_dir / _safe_folder(course_name)
        course_dir.mkdir(parents=True, exist_ok=True)

        # Fase 1: Descargar archivos nuevos
        s["phase"] = "Conectando con Canvas..."
        s["percentage"] = 5
        files = client.get_all_course_files(canvas_id)
        to_download = [
            f for f in files
            if not f.get("locked_for_user") and not file_dest(course_dir, f).exists()
        ]

        s["phase"] = f"Descargando {len(to_download)} archivo(s)..."
        s["percentage"] = 10

        def _on_dl(done, total):
            s["phase"] = f"Descargando {done}/{total} archivo(s)..."
            s["percentage"] = 10 + int(done / max(total, 1) * 35)

        dl_items = [(f["url"], file_dest(course_dir, f)) for f in to_download]
        for dest, e in client.download_many(dl_items, on_done=_on_dl):
            failures.append(f"{dest.name}: {e}")

        # Fase 2: Páginas, anuncios y fechas de Canvas
        s["phase"] = "Buscando páginas, anuncios y fechas..."
        s["percentage"] = 45
        failures.extend(_save_canvas_pages(client, canvas_id, course_dir))
        _save_assignments(client, canvas_id, course_name, course_dir)

        # Fase 3: Descomprimir ZIPs nuevos
        failures.extend(_extract_pending_zips(course_dir))

        # Fase 4: Indexar documentos nuevos
        store = VectorStore()
        course_name_safe = _safe_folder(course_name)

        # Si la colección quedó con otro embedding, se regenera completa
        if not store.embedding_compatible(course_name_safe):
            s["phase"] = "Cambio de embeddings: re-indexando todo el curso..."
            store.delete_collection(course_name_safe)

        all_files = [f for f in course_dir.rglob("*")
                     if f.suffix.lower() in SUPPORTED and not f.name.startswith("._")
                     and "__MACOSX" not in f.parts]
        to_index  = [f for f in all_files if not store.is_file_indexed(course_name_safe, str(f))]

        # Reusar el mapa archivo→unidad si ya existe (lo crea el ingest masivo). Permite
        # etiquetar 'unit' en la metadata para que el filtrado por unidad del RAG sea real.
        file_map = {}
        units_file = course_dir / "_units.json"
        if units_file.exists():
            try:
                file_map = (_json.loads(units_file.read_text(encoding="utf-8")) or {}).get("file_map", {})
            except Exception:
                file_map = {}

        s["phase"] = f"Indexando {len(to_index)} documento(s)..."
        s["percentage"] = 55

        for i, f in enumerate(to_index):
            s["phase"] = f"Indexando: {f.name}"
            s["percentage"] = 55 + int(i / max(len(to_index), 1) * 43)
            try:
                text = parse(f)
                if text.strip():
                    chunks = chunk(text)
                    try:
                        relpath = str(f.relative_to(course_dir)).replace("\\", "/")
                    except ValueError:
                        relpath = f.name
                    unit_name = file_map.get(relpath) or file_map.get(f.name)
                    meta = [
                        {"course": course_name_safe, "file": f.name, "path": str(f),
                         **({"unit": unit_name} if unit_name else {})}
                        for _ in chunks
                    ]
                    store.add_chunks(course_name_safe, chunks, meta)
            except Exception as e:
                failures.append(f"{f.name}: {e}")

        phase = f"Listo: {len(to_download)} descargados, {len(to_index)} indexados"
        if failures:
            phase += f" — {len(failures)} con error"
        s.update({
            "percentage": 100,
            "done": True,
            "phase": phase,
            "failures": failures,
        })
    except Exception as e:
        s["error"] = str(e)
        s["failures"] = failures
    finally:
        s["running"] = False


@app.get("/api/fetch/course/{canvas_id}/status")
async def get_fetch_status(canvas_id: int):
    return _fetch_status.get(canvas_id, {"done": False, "error": "Sin trabajo activo"})


# ── Units API ─────────────────────────────────────────────────────────────────

_FILE_TOPIC_RE = re.compile(r"\.(pdf|pptx|docx|txt|md)\s*$", re.IGNORECASE)


def _units_cache_is_stale(existing: dict) -> bool:
    """True si el cache de _units.json es del FORMATO VIEJO: unidades inferidas
    desde nombres de archivo, con 'topics' que son nombres de PDF (p. ej.
    "(A1) Lineal.pdf"). Criterio: la mayoría de los topics terminan en extensión
    de archivo. Ese cache producía prompts con temas basura y nunca se
    regeneraba solo porque `units` no estaba vacío."""
    topics = [t for u in (existing or {}).get("units", [])
              for t in (u.get("topics") or []) if isinstance(t, str) and t.strip()]
    if not topics:
        return False
    file_like = sum(1 for t in topics if _FILE_TOPIC_RE.search(t.strip()))
    return file_like * 2 > len(topics)


@app.get("/api/units/{course}")
async def get_units(course: str, refresh: int = 0):
    course_dir = _find_course_dir(course)

    if course_dir:
        units_file = course_dir / "_units.json"
        existing = {}
        if units_file.exists():
            try:
                existing = _json.loads(units_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
            # Cache en formato viejo (topics = nombres de archivo) => inválido:
            # redetectar como si fuera refresh=1 (el override manual de
            # calendarización se respeta porque detect_units lo mira primero).
            if not refresh and _units_cache_is_stale(existing):
                refresh = 1
            if not refresh:
                names = [u["name"] for u in existing.get("units", []) if "name" in u]
                if names:
                    return {"units": names, "source": "units"}

        # No hay unidades cacheadas (o se pidió refresh): detectar leyendo la
        # calendarización, recursivo en todo el árbol del curso (respeta un
        # override manual de calendarización si existe en _units.json).
        try:
            from ingestion.calendar_parser import detect_units
            model = os.environ.get("OLLAMA_MODEL", "llama3.2")
            units = detect_units(course_dir, model)
        except Exception:
            units = []
        if units:
            try:
                # Si se refrescó (calendarización/unidades pudieron cambiar), el
                # file_map viejo queda obsoleto y se limpia; se reclasifica en el
                # próximo ingest o al editar unidades manualmente.
                file_map = {} if refresh else existing.get("file_map", {})
                units_file.write_text(_json.dumps(
                    {**existing, "units": units, "file_map": file_map},
                    ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            return {"units": [u["name"] for u in units if "name" in u], "source": "units"}

    return {"units": _get_store().list_files_in_collection(course), "source": "files"}


@app.post("/api/units/{course}/calendar")
async def set_units_calendar(course: str, request: Request):
    """Override manual: usa un PDF/DOCX concreto como calendarización del curso
    (para cursos donde la detección automática elige el archivo equivocado o no
    encuentra ninguno). Redetecta las unidades a partir de ese archivo."""
    data = await request.json()
    rel_path = str(data.get("path") or "").strip()
    if not rel_path:
        return JSONResponse({"error": "Ruta requerida"}, status_code=400)

    course_dir = _find_course_dir(course)
    if not course_dir:
        return JSONResponse({"error": "Curso no encontrado"}, status_code=404)

    try:
        target = (course_dir / rel_path).resolve()
        target.relative_to(course_dir.resolve())
    except (ValueError, OSError):
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)

    if not target.is_file() or target.suffix.lower() not in (".pdf", ".docx"):
        return JSONResponse(
            {"error": "Archivo no encontrado o tipo no soportado (usa PDF o DOCX)"}, status_code=404
        )

    from ingestion.calendar_parser import save_calendar_override
    rel_norm = str(target.relative_to(course_dir.resolve())).replace("\\", "/")
    model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    try:
        units = save_calendar_override(course_dir, rel_norm, model)
    except Exception as e:
        return JSONResponse({"error": f"No se pudo redetectar: {e}"}, status_code=500)

    return {"ok": True, "units": [u["name"] for u in units if "name" in u]}


@app.put("/api/units/{course}")
async def put_units(course: str, request: Request):
    """Guarda una lista de unidades editada a mano (renombrar/añadir/eliminar) y
    reclasifica los archivos del curso en esas unidades (pase determinista +
    LLM solo para lo ambiguo — barato gracias al pase determinista)."""
    data = await request.json()
    units_in = data.get("units")
    if not isinstance(units_in, list) or not units_in:
        return JSONResponse({"error": "Lista de unidades inválida"}, status_code=400)

    clean_units: list[dict] = []
    for u in units_in:
        if not isinstance(u, dict):
            continue
        name = str(u.get("name") or "").strip()
        if not name:
            continue
        topics = [str(t).strip() for t in (u.get("topics") or []) if str(t).strip()]
        clean_units.append({"name": name, "topics": topics})
    if not clean_units:
        return JSONResponse({"error": "Lista de unidades inválida"}, status_code=400)

    course_dir = _find_course_dir(course)
    if not course_dir:
        return JSONResponse({"error": "Curso no encontrado"}, status_code=404)

    from ingestion.calendar_parser import classify_files
    model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    try:
        file_map = classify_files(course_dir, clean_units, model)
    except Exception:
        file_map = {}

    units_file = course_dir / "_units.json"
    try:
        existing = _json.loads(units_file.read_text(encoding="utf-8")) if units_file.exists() else {}
    except Exception:
        existing = {}
    existing["units"] = clean_units
    existing["file_map"] = file_map
    try:
        units_file.write_text(_json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": f"No se pudo guardar: {e}"}, status_code=500)

    return {"ok": True, "units": [u["name"] for u in clean_units]}


# ── Sync API (dashboard manual) ───────────────────────────────────────────────

def _sync_state(running=False):
    return {
        "running": running, "done": False, "error": None,
        "phase": "Iniciando...", "percentage": 0,
        "total_new": 0, "downloaded": 0, "skipped": 0, "errors": 0,
        "current": None,
    }


def _ingest_state(running=False):
    return {
        "running": running, "done": False, "error": None,
        "phase": "Iniciando...", "percentage": 0,
        "total_files": 0, "processed": 0, "skipped": 0, "current": None,
        "summary": None,
    }


_status = {
    "sync":   _sync_state(),
    "ingest": _ingest_state(),
}


@app.post("/api/sync")
async def start_sync(background_tasks: BackgroundTasks):
    if _status["sync"]["running"]:
        return {"status": "already_running"}
    _status["sync"] = _sync_state(running=True)
    background_tasks.add_task(_run_sync)
    return {"status": "started"}


def _run_sync():
    s = _status["sync"]
    try:
        from canvas_api import CanvasClient, file_dest
        client   = CanvasClient(os.environ["CANVAS_URL"], os.environ["CANVAS_TOKEN"])
        data_dir = Path("data")
        data_dir.mkdir(exist_ok=True)

        s["phase"] = "Obteniendo cursos de Canvas..."
        courses = [c for c in client.get_courses() if _is_academic_course(c)]

        to_download = []
        skipped = 0
        for i, course in enumerate(courses):
            name = course.get("name", str(course["id"]))
            s["phase"] = f"Comparando: {name}"
            s["percentage"] = int(i / len(courses) * 30)
            files = client.get_all_course_files(course["id"])
            course_dir = data_dir / _safe_folder(name)
            for f in files:
                if f.get("locked_for_user"):
                    skipped += 1
                    continue
                dest = file_dest(course_dir, f)
                if dest.exists():
                    skipped += 1
                else:
                    to_download.append((course_dir, f))

        s["skipped"]   = skipped
        s["total_new"] = len(to_download)
        s["phase"]     = f"{len(to_download)} archivos nuevos. Descargando..."

        failures: list[str] = []

        def _on_dl(done, total):
            s["current"]    = f"{done}/{total}"
            s["downloaded"] = done
            s["percentage"] = 30 + int(done / max(total, 1) * 50)

        dl_items = [(f["url"], file_dest(course_dir, f)) for course_dir, f in to_download]
        for dest, e in client.download_many(dl_items, on_done=_on_dl):
            failures.append(f"{dest.name}: {e}")
        s["errors"] = len(failures)

        # Páginas, anuncios, fechas y ZIPs por curso
        for i, course in enumerate(courses):
            name = course.get("name", str(course["id"]))
            s["phase"] = f"Páginas, anuncios y fechas: {name}"
            s["percentage"] = 80 + int(i / max(len(courses), 1) * 20)
            course_dir = data_dir / _safe_folder(name)
            course_dir.mkdir(parents=True, exist_ok=True)
            try:
                failures.extend(_save_canvas_pages(client, course["id"], course_dir))
            except Exception as e:
                failures.append(f"Páginas de {name}: {e}")
            _save_assignments(client, course["id"], name, course_dir)
            failures.extend(_extract_pending_zips(course_dir))
        s["errors"] = len(failures)

        downloaded = len(to_download) - len(failures)
        phase = f"Listo: {downloaded} descargados, {skipped} ya existian"
        if failures:
            phase += f" — {len(failures)} con error"
        s.update({
            "percentage": 100, "done": True, "current": None,
            "phase": phase, "failures": failures,
        })
    except Exception as e:
        s["error"] = str(e)
    finally:
        s["running"] = False


@app.get("/api/sync/status")
async def get_sync_status():
    return _status["sync"]


# ── Ingest API (dashboard manual) ─────────────────────────────────────────────

@app.post("/api/ingest")
async def start_ingest(background_tasks: BackgroundTasks):
    if _status["ingest"]["running"]:
        return {"status": "already_running"}
    _status["ingest"] = _ingest_state(running=True)
    background_tasks.add_task(_run_ingest)
    return {"status": "started"}


def _run_ingest():
    from ingestion.parsers import parse, SUPPORTED
    from ingestion.chunker import chunk
    from ingestion.vectorstore import VectorStore
    from ingestion.calendar_parser import build_unit_map

    s        = _status["ingest"]
    store    = VectorStore()
    data_dir = Path("data")
    model    = os.environ.get("OLLAMA_MODEL", "llama3.2")

    try:
        if not data_dir.exists():
            raise FileNotFoundError("Carpeta 'data/' no encontrada.")

        course_dirs = sorted(d for d in data_dir.iterdir() if d.is_dir())

        unit_maps: dict[str, dict] = {}
        for i, course_dir in enumerate(course_dirs):
            units_file = course_dir / "_units.json"
            if units_file.exists():
                try:
                    existing_units = _json.loads(units_file.read_text(encoding="utf-8"))
                    # Cache en formato viejo (topics = nombres de archivo): rehacer.
                    if not _units_cache_is_stale(existing_units):
                        unit_maps[course_dir.name] = existing_units
                        continue
                except Exception:
                    pass
            s["phase"]      = f"Analizando unidades: {course_dir.name}"
            s["percentage"] = int(i / max(len(course_dirs), 1) * 30)
            try:
                unit_data = build_unit_map(course_dir, model)
            except Exception:
                unit_data = {"units": [], "file_map": {}}
            try:
                units_file.write_text(_json.dumps(unit_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            unit_maps[course_dir.name] = unit_data

        s["phase"] = "Escaneando documentos..."
        s["percentage"] = 30
        failures: list[str] = []
        all_files: list[tuple[str, Path]] = []
        for course_dir in course_dirs:
            failures.extend(_extract_pending_zips(course_dir))
            # Colecciones creadas con otro embedding se regeneran completas
            if not store.embedding_compatible(course_dir.name):
                s["phase"] = f"Cambio de embeddings: re-indexando {course_dir.name}"
                store.delete_collection(course_dir.name)
            for f in course_dir.rglob("*"):
                if f.suffix.lower() in SUPPORTED and not f.name.startswith("._"):
                    all_files.append((course_dir.name, f))

        to_index, skipped = [], 0
        for course_name, f in all_files:
            if store.is_file_indexed(course_name, str(f)):
                skipped += 1
            else:
                to_index.append((course_name, f))

        s["total_files"] = len(to_index)
        s["skipped"]     = skipped
        s["phase"]       = f"{len(to_index)} nuevos, {skipped} ya indexados. Indexando..."

        totals: dict = {}
        for i, (course_name, f) in enumerate(to_index):
            s["current"]    = f.name
            s["percentage"] = 30 + int(i / len(to_index) * 70) if to_index else 100
            try:
                text = parse(f)
                if text.strip():
                    chunks    = chunk(text)
                    file_map  = unit_maps.get(course_name, {}).get("file_map", {})
                    try:
                        relpath = str(f.relative_to(data_dir / course_name)).replace("\\", "/")
                    except ValueError:
                        relpath = f.name
                    unit_name = file_map.get(relpath) or file_map.get(f.name)
                    meta = [
                        {"course": course_name, "file": f.name, "path": str(f),
                         **({"unit": unit_name} if unit_name else {})}
                        for _ in chunks
                    ]
                    store.add_chunks(course_name, chunks, meta)
                    totals[course_name] = totals.get(course_name, 0) + len(chunks)
            except Exception as e:
                failures.append(f"{f.name}: {e}")
            s["processed"] = i + 1

        phase = f"Listo: {len(to_index)} indexados, {skipped} ya existian"
        if failures:
            phase += f" — {len(failures)} con error"
        s.update({
            "percentage": 100, "done": True, "current": None,
            "phase":   phase,
            "summary": totals,
            "failures": failures,
        })
    except Exception as e:
        s["error"] = str(e)
    finally:
        s["running"] = False


@app.get("/api/ingest/status")
async def get_ingest_status():
    return _status["ingest"]


# ── Agenda / Organización API ──────────────────────────────────────────────────

def _parse_due(due_at: str | None):
    if not due_at:
        return None
    try:
        return datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    except Exception:
        return None


@app.get("/api/agenda")
async def get_agenda():
    """Agrega las entregas/evaluaciones de todos los cursos (desde _assignments.json).

    Si hay fechas próximas, devuelve esas (orden ascendente). Si no quedan eventos
    futuros (p. ej. fin de semestre), cae a las entregas más recientes para no
    mostrar una agenda vacía (`fallback: true`)."""
    data_dir = Path("data")
    if not data_dir.exists():
        return {"events": [], "fallback": False}

    now = datetime.now(timezone.utc)
    soon_cutoff = now - timedelta(days=3)
    all_events = []
    for course_dir in data_dir.iterdir():
        if not _is_academic_course({"name": course_dir.name}):
            continue
        af = course_dir / "_assignments.json"
        if not af.exists():
            continue
        try:
            items = _json.loads(af.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in items:
            due = _parse_due(it.get("due_at"))
            if due is None:
                continue
            all_events.append({
                "name":   it.get("name", "Sin título"),
                "due_at": it.get("due_at"),
                "url":    it.get("url", ""),
                "points": it.get("points"),
                "course": it.get("course", course_dir.name),
                "_due":   due,
            })

    upcoming = sorted((e for e in all_events if e["_due"] >= soon_cutoff), key=lambda e: e["_due"])
    if upcoming:
        events, fallback = upcoming, False
    else:
        # Sin fechas futuras: mostrar las más recientes (descendente)
        events = sorted(all_events, key=lambda e: e["_due"], reverse=True)[:12]
        fallback = True

    for e in events:
        e.pop("_due", None)
    return {"events": events, "fallback": fallback}


def _build_range_plan_prompt(from_str: str, to_str: str, events: list[dict], method: str) -> str:
    """Arma el prompt del plan de estudio día a día para un rango de fechas.
    Compartido entre /api/agenda/plan (modo nuevo) y /api/agenda/nl."""
    from chatbot.retriever import Retriever

    from_d = datetime.fromisoformat(from_str)
    to_d   = datetime.fromisoformat(to_str)
    total_days = max((to_d - from_d).days + 1, 1)

    # Method description
    method_hint = ""
    if method and method != "auto":
        try:
            from chatbot.study_methods import METHODS
            m_obj = next((m for m in METHODS if m["key"] == method), None)
            if m_obj:
                method_hint = f"Aplica el método '{m_obj['name']}': {m_obj['short']} "
        except Exception:
            pass
    if not method_hint:
        method_hint = "Elige el método de estudio más adecuado para el contenido. "

    # Build events summary
    if events:
        lines = []
        for ev in events:
            syl = f" (temario: {ev.get('syllabus','')})" if ev.get("syllabus") else ""
            crs = f" [{ev.get('course','')}]" if ev.get("course") else ""
            lines.append(f"- {ev.get('date','?')} — {ev.get('title','?')}{crs}{syl}")
        events_txt = "Evaluaciones en el período:\n" + "\n".join(lines) + "\n\n"
    else:
        events_txt = "No hay evaluaciones registradas en el período.\n\n"

    # RAG context for unique courses mentioned
    context_parts = []
    seen_courses = set()
    for ev in events[:3]:
        crs = (ev.get("course") or "").strip()
        if crs and crs not in seen_courses:
            seen_courses.add(crs)
            try:
                safe = _safe_name(_safe_folder(crs))
                ctx, _ = Retriever(_get_store()).get_context(
                    ev.get("title", crs), safe, n=2
                )
                if ctx:
                    context_parts.append(f"[{crs}]\n{ctx}")
            except Exception:
                pass
    material = ("\n\nMaterial de los cursos:\n" + "\n---\n".join(context_parts[:2])) \
               if context_parts else ""

    return (
        f"Eres un tutor experto. {method_hint}\n"
        f"Arma un plan de estudio día a día del {from_str} al {to_str} "
        f"({total_days} días en total).\n"
        f"{events_txt}"
        f"Distribuye el estudio considerando las fechas de evaluación: "
        f"más intensidad los días previos a cada evaluación.{material}\n"
        f"Formato: una sección Markdown por día (### Día YYYY-MM-DD), "
        f"con qué estudiar, qué practicar y una meta concreta. "
        f"Fórmulas entre $...$. Máximo ~300 palabras."
    )


def _generate_plan_text(from_str: str, to_str: str, events: list[dict], method: str) -> str:
    """Genera el texto del plan de estudio (día a día) vía el LLM. Compartido entre
    /api/agenda/plan (modo nuevo) y /api/agenda/nl."""
    from chatbot import llm
    prompt = _build_range_plan_prompt(from_str, to_str, events, method)
    return llm.complete([{"role": "user", "content": prompt}], temperature=0.5)


_DAY_HEADING_RE = re.compile(
    r"^#{1,4}\s*d[ií]a\s+(\d{4}-\d{2}-\d{2})\s*:?\s*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)


def _split_plan_by_day(plan_text: str) -> list[dict]:
    """Parsea el markdown '### Día YYYY-MM-DD' del plan en bloques por día:
    [{date, summary, detail}]. Usado para guardar cada día como evento editable."""
    matches = list(_DAY_HEADING_RE.finditer(plan_text or ""))
    days = []
    for i, m in enumerate(matches):
        date = m.group(1)
        inline_title = (m.group(2) or "").strip(" -—:")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(plan_text)
        body = plan_text[start:end].strip()
        if inline_title:
            summary = inline_title
        else:
            first_line = next((l.strip(" -*#") for l in body.splitlines() if l.strip()), "")
            summary = first_line
        summary = (summary or "Repaso")[:70]
        days.append({"date": date, "summary": summary, "detail": body})
    return days


@app.post("/api/agenda/plan")
async def generate_study_plan(request: Request):
    """Genera un plan de estudio.

    Acepta dos formas:
    - Legado: {title, course, due_at}
    - Nueva:  {from:"YYYY-MM-DD", to:"YYYY-MM-DD", events:[...], method:"key|auto"}
    """
    from chatbot import llm
    from chatbot.retriever import Retriever

    data = await request.json()

    # ── Modo nuevo: rango de fechas ───────────────────────────────────────────
    if data.get("from") and data.get("to"):
        from_str = data["from"]
        to_str   = data["to"]
        events   = data.get("events") or []
        method   = (data.get("method") or "auto").strip()

        try:
            datetime.fromisoformat(from_str)
            datetime.fromisoformat(to_str)
        except Exception:
            return JSONResponse({"error": "Fechas inválidas"}, status_code=400)

        try:
            plan = _generate_plan_text(from_str, to_str, events, method)
            return {"plan": plan}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── Modo legado: título único ─────────────────────────────────────────────
    title  = (data.get("title") or "").strip()
    course = (data.get("course") or "").strip()
    due_at = data.get("due_at")
    if not title:
        return JSONResponse({"error": "Falta el título de la actividad"}, status_code=400)

    due = _parse_due(due_at)
    today = datetime.now(timezone.utc)
    days = max((due - today).days, 1) if due else 7
    due_txt = due.strftime("%d/%m/%Y") if due else "la fecha de entrega"

    context = ""
    if course:
        try:
            context, _ = Retriever(_get_store()).get_context(title, course, n=4)
        except Exception:
            context = ""

    material = f"\n\nMaterial relevante del curso:\n{context}\n" if context else ""
    prompt = (
        f"Eres un tutor que arma planes de estudio realistas. La actividad es: "
        f"\"{title}\" del curso \"{course or 'general'}\", con fecha {due_txt} "
        f"(faltan {days} días).{material}\n"
        f"Crea un plan de estudio repartido en esos {days} días (agrupa si son muchos). "
        f"Para cada bloque indica: qué tema repasar, qué practicar y una meta concreta. "
        f"Sé específico al material si lo hay. Usa Markdown con una sección por día o bloque. "
        f"Las fórmulas van entre signos de dólar ($...$). Máximo ~250 palabras."
    )
    try:
        plan = llm.complete([{"role": "user", "content": prompt}], temperature=0.5)
        return {"plan": plan, "days": days}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Analysis API ──────────────────────────────────────────────────────────────

@app.post("/api/analysis/generate")
async def generate_analysis():
    from analysis import generate_report
    try:
        report = generate_report()
        return {"report": report}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── User events API ───────────────────────────────────────────────────────────

_EVENTS_FILE = Path("logs") / "user_events.json"


def _load_user_events() -> list[dict]:
    try:
        if _EVENTS_FILE.exists():
            return _json.loads(_EVENTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_user_events(events: list[dict]) -> None:
    _EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _EVENTS_FILE.write_text(_json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/events")
async def get_user_events():
    return {"events": _load_user_events()}


@app.post("/api/events")
async def create_user_event(request: Request):
    data = await request.json()
    title    = (data.get("title") or "").strip()
    date     = (data.get("date") or "").strip()
    ev_type  = (data.get("type") or "Otro").strip()
    syllabus = (data.get("syllabus") or "").strip()
    if not title or not date:
        return JSONResponse({"error": "Faltan título o fecha"}, status_code=400)

    new_ev = {"id": str(uuid.uuid4()), "date": date, "title": title,
              "type": ev_type, "syllabus": syllabus}
    events = _load_user_events()
    events.append(new_ev)
    _save_user_events(events)
    return new_ev


@app.delete("/api/events/{event_id}")
async def delete_user_event(event_id: str):
    events = _load_user_events()
    new_list = [e for e in events if e.get("id") != event_id]
    if len(new_list) == len(events):
        return JSONResponse({"error": "Evento no encontrado"}, status_code=404)
    _save_user_events(new_list)
    return {"deleted": True}


@app.put("/api/events/{event_id}")
async def update_user_event(event_id: str, request: Request):
    data = await request.json()
    events = _load_user_events()
    ev = next((e for e in events if e.get("id") == event_id), None)
    if ev is None:
        return JSONResponse({"error": "Evento no encontrado"}, status_code=404)

    if "title" in data and data["title"] is not None:
        title = str(data["title"]).strip()
        if not title:
            return JSONResponse({"error": "El nombre no puede quedar vacío"}, status_code=400)
        ev["title"] = title
    if "date" in data and data["date"] is not None:
        date = str(data["date"]).strip()
        if not date:
            return JSONResponse({"error": "La fecha no puede quedar vacía"}, status_code=400)
        ev["date"] = date
    if "type" in data and data["type"] is not None:
        ev["type"] = str(data["type"]).strip() or ev.get("type", "Otro")
    if "syllabus" in data and data["syllabus"] is not None:
        ev["syllabus"] = str(data["syllabus"])
    if "notes" in data and data["notes"] is not None:
        # Alias de 'syllabus': el frontend usa 'notes' para el detalle de eventos de estudio.
        ev["syllabus"] = str(data["notes"])

    _save_user_events(events)
    return ev


@app.delete("/api/events/plan/{plan_group}")
async def delete_plan_group(plan_group: str):
    events = _load_user_events()
    new_list = [e for e in events if e.get("plan_group") != plan_group]
    removed = len(events) - len(new_list)
    if removed == 0:
        return JSONResponse({"error": "Plan no encontrado"}, status_code=404)
    _save_user_events(new_list)
    return {"deleted": True, "count": removed}


# ── Agenda por lenguaje natural ────────────────────────────────────────────────

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_EVENT_TYPE_LABELS = {
    "control": "Control", "certamen": "Certamen", "examen": "Examen",
    "prueba": "Prueba", "tarea": "Tarea", "otro": "Otro",
}


def _list_course_names() -> list[str]:
    data_dir = Path("data")
    if not data_dir.exists():
        return []
    return [d.name for d in data_dir.iterdir()
            if d.is_dir() and _is_academic_course({"name": d.name})]


@app.post("/api/agenda/nl")
async def agenda_from_natural_language(request: Request):
    """Interpreta un pedido en lenguaje natural ("tengo control de X en una semana
    más"), crea el evento principal y genera + guarda un plan de estudio día a día
    como eventos de tipo 'estudio' ligados por `plan_group`."""
    from chatbot import llm

    data = await request.json()
    text = (data.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "Escribe qué quieres agendar."}, status_code=400)

    now = datetime.now()
    weekday_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][now.weekday()]
    course_names = _list_course_names()
    courses_txt = ", ".join(course_names) if course_names else "(sin cursos registrados)"

    extract_prompt = (
        f"Hoy es {now.strftime('%Y-%m-%d')} ({weekday_es}). Extrae de este mensaje de un "
        f"estudiante los datos de una evaluación o tarea que quiere agendar:\n"
        f"\"{text}\"\n\n"
        f"Cursos existentes (usa el nombre EXACTO de esta lista si el mensaje se refiere a uno "
        f"de ellos; si no aplica ninguno usa null): {courses_txt}\n\n"
        "Responde SOLO con un JSON, sin texto antes ni después, con esta forma exacta:\n"
        '{"titulo": "...", "tipo": "control|certamen|examen|prueba|tarea|otro", '
        '"fecha": "YYYY-MM-DD", "curso": "nombre exacto de la lista o null", "tema": "..."}\n'
        "La fecha se resuelve en relación a HOY (ej.: 'en una semana más' = hoy + 7 días; "
        "'el próximo lunes' = el lunes que viene; 'mañana' = hoy + 1 día)."
    )

    try:
        raw = llm.complete([{"role": "user", "content": extract_prompt}], temperature=0.2)
    except Exception as e:
        return JSONResponse({"error": f"No pude interpretar el mensaje: {e}"}, status_code=500)

    m = _JSON_OBJ_RE.search(raw)
    if not m:
        return JSONResponse({"error": "No pude interpretar un evento en ese mensaje."}, status_code=422)
    try:
        parsed = _json.loads(m.group(0))
    except Exception:
        return JSONResponse({"error": "No pude interpretar el JSON del evento."}, status_code=422)

    titulo = (parsed.get("titulo") or "Evaluación").strip() or "Evaluación"
    tipo_raw = (parsed.get("tipo") or "otro").strip().lower()
    fecha = (parsed.get("fecha") or "").strip()
    tema = (parsed.get("tema") or "").strip()
    curso = parsed.get("curso")
    curso = curso.strip() if isinstance(curso, str) else None
    if curso and curso not in course_names:
        # Aceptar coincidencia case-insensitive; si no matchea con nada, descartar.
        match = next((c for c in course_names if c.lower() == curso.lower()), None)
        curso = match

    try:
        datetime.fromisoformat(fecha)
    except Exception:
        return JSONResponse({"error": "No pude determinar una fecha válida para ese evento."}, status_code=422)

    ev_type_label = _EVENT_TYPE_LABELS.get(tipo_raw, "Otro")

    events_all = _load_user_events()
    main_ev = {
        "id": str(uuid.uuid4()), "date": fecha, "title": titulo,
        "type": ev_type_label, "syllabus": tema,
    }
    events_all.append(main_ev)

    plan_events: list[dict] = []
    today_d = now.date()
    tomorrow_d = today_d + timedelta(days=1)
    target_d = datetime.fromisoformat(fecha).date()

    if target_d >= tomorrow_d:
        plan_from = tomorrow_d.isoformat()
        plan_ctx = [{"title": titulo, "date": fecha, "course": curso or "", "syllabus": tema}]
        try:
            plan_text = _generate_plan_text(plan_from, fecha, plan_ctx, "auto")
        except Exception:
            plan_text = ""
        days = _split_plan_by_day(plan_text) if plan_text else []
        plan_group = str(uuid.uuid4())
        for d in days:
            ev = {
                "id": str(uuid.uuid4()), "date": d["date"],
                "title": f"Estudio: {d['summary']}", "type": "estudio",
                "syllabus": d["detail"], "plan_group": plan_group,
            }
            events_all.append(ev)
            plan_events.append(ev)

    _save_user_events(events_all)

    resumen = f"📅 {ev_type_label} de {titulo} agendado para el {fecha}."
    if plan_events:
        resumen += f" Creé un plan de {len(plan_events)} día(s) — puedes editarlo tocando cada día."
    elif target_d < tomorrow_d:
        resumen += " Es muy pronto para armar un plan de estudio."

    return {"ok": True, "event": main_ev, "plan_events": plan_events, "resumen": resumen}


# ── Study methods API ─────────────────────────────────────────────────────────

@app.get("/api/methods")
async def get_methods():
    from chatbot.study_methods import METHODS
    return {"methods": METHODS}


@app.get("/api/methods/recommend")
async def get_methods_recommend(course: str = ""):
    """Métodos recomendados. Con ?course=<nombre> recomienda para ese ramo en concreto;
    sin parámetro, agrega sobre todos los ramos académicos descargados."""
    from chatbot.study_methods import recommend
    if course.strip():
        return {"recommended": recommend([course.strip()])}
    data_dir = Path("data")
    labels: list[str] = []
    if data_dir.exists():
        for d in data_dir.iterdir():
            if d.is_dir() and _is_academic_course({"name": d.name}):
                labels.append(d.name)
    return {"recommended": recommend(labels)}


# ── Manual upload API ─────────────────────────────────────────────────────────

@app.post("/api/upload/{course}")
async def upload_course_files(course: str, files: List[UploadFile] = File(...)):
    """Acepta uno o más archivos vía multipart, los indexa en el curso dado."""
    from ingestion.parsers import parse, SUPPORTED
    from ingestion.chunker import chunk

    # Resolve data dir (match by safe_name)
    course_dir = _find_course_dir(course)
    if course_dir is None:
        course_dir = Path("data") / course
        course_dir.mkdir(parents=True, exist_ok=True)

    store = _get_store()
    indexed = 0
    failures: list[str] = []

    for upload in files:
        fname = upload.filename or "file"
        if Path(fname).name.startswith("._"):
            continue
        suffix = Path(fname).suffix.lower()
        if suffix not in SUPPORTED:
            failures.append(f"{fname}: tipo no soportado ({suffix})")
            continue

        dest = course_dir / _safe_folder(fname)
        try:
            content = await upload.read()
            dest.write_bytes(content)
            text = parse(dest)
            if text.strip():
                chunks = chunk(text)
                meta = [{"course": course, "file": dest.name, "path": str(dest)} for _ in chunks]
                store.add_chunks(course, chunks, meta)
                indexed += len(chunks)
        except Exception as e:
            failures.append(f"{fname}: {e}")

    return {"indexed": indexed, "failures": failures}


# ===== Gestor de archivos =====

_DATA_ROOT = Path("data").resolve()

# Extensiones que el navegador puede mostrar inline (PDF e imágenes)
_INLINE_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
# Extensiones de texto plano / código previsualizables directamente
_TEXT_PREVIEW_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".c", ".cpp", ".h", ".hpp",
    ".java", ".html", ".css", ".json", ".ipynb", ".sh", ".yml", ".yaml", ".xml", ".csv",
}
# Documentos que se previsualizan extrayendo el texto (sin conservar formato)
_DOC_PREVIEW_EXTS = {".pptx", ".docx"}
_PREVIEW_MAX_CHARS = 200_000


def _safe_data_path(rel: str) -> Path:
    """Resuelve `rel` contra data/ rechazando cualquier intento de escape
    (rutas absolutas, letras de unidad de Windows, `..`). Lanza ValueError
    si la ruta no es segura. Úsalo en TODOS los endpoints de /api/files*."""
    rel = (rel or "").strip()
    if not rel:
        return _DATA_ROOT
    rel = rel.replace("\\", "/").strip("/")
    if not rel:
        return _DATA_ROOT
    # Letra de unidad de Windows (C:, D:, etc.) en cualquier posición
    if re.search(r"[a-zA-Z]:", rel):
        raise ValueError("Ruta inválida")
    parts = PurePosixPath(rel).parts
    if any(part in ("..", "") or part == "/" for part in parts):
        raise ValueError("Ruta inválida")
    candidate = _DATA_ROOT.joinpath(*parts).resolve()
    try:
        candidate.relative_to(_DATA_ROOT)
    except ValueError:
        raise ValueError("Ruta inválida")
    return candidate


def _count_files(dir_path: Path) -> int:
    """Cuenta archivos visibles (no ocultos, no metadata `_*`) bajo un directorio, recursivo."""
    count = 0
    try:
        for entry in dir_path.rglob("*"):
            if entry.is_file() and not entry.name.startswith((".", "_")):
                count += 1
    except Exception:
        pass
    return count


@app.get("/api/files")
async def api_list_files(path: str = ""):
    try:
        target = _safe_data_path(path)
    except ValueError:
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)

    if not target.exists():
        if target == _DATA_ROOT:
            return {"dirs": [], "files": []}
        return JSONResponse({"error": "Carpeta no encontrada"}, status_code=404)
    if not target.is_dir():
        return JSONResponse({"error": "No es una carpeta"}, status_code=400)

    dirs, files = [], []
    for entry in sorted(target.iterdir(), key=lambda e: e.name.lower()):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir():
            dirs.append({"name": entry.name, "count": _count_files(entry)})
        else:
            try:
                st = entry.stat()
                size, mtime = st.st_size, st.st_mtime
            except Exception:
                size, mtime = 0, 0
            files.append({
                "name": entry.name,
                "size": size,
                "mtime": mtime,
                "ext": entry.suffix.lower().lstrip("."),
            })

    return {"dirs": dirs, "files": files}


@app.post("/api/files/move")
async def api_move_file(request: Request):
    data = await request.json()
    try:
        src = _safe_data_path(data.get("src", ""))
        dst_dir = _safe_data_path(data.get("dst_dir", ""))
    except ValueError:
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)

    if src == _DATA_ROOT:
        return JSONResponse({"error": "No se puede mover la carpeta raíz"}, status_code=400)
    if not src.exists():
        return JSONResponse({"error": "Origen no encontrado"}, status_code=404)
    if not dst_dir.exists() or not dst_dir.is_dir():
        return JSONResponse({"error": "Carpeta destino no encontrada"}, status_code=404)

    if src.is_dir():
        try:
            dst_dir.relative_to(src)
            return JSONResponse(
                {"error": "No se puede mover una carpeta dentro de sí misma"}, status_code=400
            )
        except ValueError:
            pass

    if src.parent == dst_dir:
        return {"moved": True}  # ya está ahí, no-op

    dest = dst_dir / src.name
    if dest.exists():
        return JSONResponse(
            {"error": "Ya existe un archivo o carpeta con ese nombre en el destino"}, status_code=409
        )

    try:
        shutil.move(str(src), str(dest))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"moved": True}


@app.post("/api/files/rename")
async def api_rename_file(request: Request):
    data = await request.json()
    try:
        target = _safe_data_path(data.get("path", ""))
    except ValueError:
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)

    if target == _DATA_ROOT:
        return JSONResponse({"error": "No se puede renombrar la carpeta raíz"}, status_code=400)
    new_name = _safe_folder((data.get("new_name") or "").strip())
    if not new_name:
        return JSONResponse({"error": "Nombre inválido"}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": "No encontrado"}, status_code=404)

    dest = target.parent / new_name
    if dest.exists():
        return JSONResponse(
            {"error": "Ya existe un archivo o carpeta con ese nombre"}, status_code=409
        )
    try:
        target.rename(dest)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"renamed": True, "name": new_name}


@app.delete("/api/files")
async def api_delete_file(request: Request, path: str = ""):
    rel = path
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    if not rel:
        rel = body.get("path", "")
    recursive = bool(body.get("recursive", False))

    try:
        target = _safe_data_path(rel)
    except ValueError:
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)

    if target == _DATA_ROOT:
        return JSONResponse({"error": "No se puede borrar la carpeta raíz"}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": "No encontrado"}, status_code=404)

    # Curso de primer nivel (data/<curso>/): si se borra, también su colección
    # de ChromaDB (si existe) para no dejar una colección huérfana.
    is_course_root = target.parent == _DATA_ROOT and target.is_dir()
    course_name = target.name

    try:
        if target.is_dir():
            has_content = any(target.iterdir())
            if has_content and not recursive:
                count = _count_files(target)
                return JSONResponse(
                    {"error": "La carpeta no está vacía", "needs_recursive": True, "count": count},
                    status_code=409,
                )
            if has_content:
                shutil.rmtree(target)
            else:
                target.rmdir()
        else:
            target.unlink()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    if is_course_root:
        try:
            _get_store().drop_collection(course_name)
        except Exception:
            pass

    return {"deleted": True}


@app.post("/api/files/mkdir")
async def api_mkdir_file(request: Request):
    data = await request.json()
    try:
        parent = _safe_data_path(data.get("path", ""))
    except ValueError:
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)

    name = _safe_folder((data.get("name") or "").strip())
    if not name:
        return JSONResponse({"error": "Nombre inválido"}, status_code=400)
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    if not parent.is_dir():
        return JSONResponse({"error": "La ruta padre no es una carpeta"}, status_code=400)

    dest = parent / name
    if dest.exists():
        return JSONResponse(
            {"error": "Ya existe un archivo o carpeta con ese nombre"}, status_code=409
        )
    try:
        dest.mkdir(parents=True)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"created": True, "name": name}


@app.post("/api/files/upload")
async def api_upload_to_folder(path: str = "", files: List[UploadFile] = File(...)):
    try:
        target_dir = _safe_data_path(path)
    except ValueError:
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)

    target_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    failures: list[str] = []

    for upload in files:
        fname = _safe_folder(Path(upload.filename or "archivo").name)
        if not fname or fname.startswith("._"):
            continue
        dest = target_dir / fname
        try:
            content = await upload.read()
            dest.write_bytes(content)
            saved.append(fname)
        except Exception as e:
            failures.append(f"{fname}: {e}")

    return {"saved": saved, "failures": failures}


@app.get("/api/files/raw")
async def api_raw_file(path: str = ""):
    try:
        target = _safe_data_path(path)
    except ValueError:
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": "No encontrado"}, status_code=404)

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    disposition = "inline" if target.suffix.lower() in _INLINE_EXTS else "attachment"
    return FileResponse(
        target, media_type=media_type, filename=target.name,
        content_disposition_type=disposition,
    )


@app.get("/api/files/preview")
async def api_preview_file(path: str = ""):
    try:
        target = _safe_data_path(path)
    except ValueError:
        return JSONResponse({"error": "Ruta inválida"}, status_code=400)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": "No encontrado"}, status_code=404)

    ext = target.suffix.lower()

    if ext in _DOC_PREVIEW_EXTS:
        try:
            from ingestion.parsers import parse
            text = parse(target)
            return {"kind": "doc", "content": text[:_PREVIEW_MAX_CHARS]}
        except Exception as e:
            return JSONResponse({"error": f"No se pudo extraer el texto: {e}"}, status_code=500)

    if ext in _TEXT_PREVIEW_EXTS:
        try:
            content = target.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return {"kind": "text", "content": content[:_PREVIEW_MAX_CHARS]}

    # PDF, imágenes y cualquier otro tipo: el frontend usa /api/files/raw directamente
    return {"kind": "raw"}


# ===== Ordenar curso (organización automática por unidades/evaluaciones) =====

_EVAL_NAME_RE = re.compile(r"\b(pauta|solucion(ario)?|control|certamen|examen|prueba|test)\b")
_OLD_HINT_RE = re.compile(r"anterior|pasado|2023|2024|2025")
_YEAR_TOKEN_RE = re.compile(r"20\d{2}")


def _dest_with_suffix(dest: Path) -> Path:
    """Si `dest` ya existe, devuelve una variante 'nombre (2).ext', 'nombre (3).ext'..."""
    if not dest.exists():
        return dest
    stem, suffix, parent = dest.stem, dest.suffix, dest.parent
    i = 2
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _reorganize_reindex(course_name: str, pairs: list[tuple[str, str, str | None]]) -> None:
    """Corrige la metadata (path/file/unit) de los chunks ya indexados de un curso
    tras aplicar o deshacer un orden, en un hilo de fondo. No re-embebe."""
    try:
        from ingestion.vectorstore import VectorStore
        VectorStore().update_chunk_paths(course_name, pairs)
    except Exception:
        pass


@app.post("/api/files/organize/plan")
async def organize_plan(request: Request):
    """Genera un plan de reorganización del curso en Unidad N / Evaluaciones / Otros
    SIN mover nada. El frontend lo muestra en un modal para confirmar."""
    data = await request.json()
    course = str(data.get("course") or "").strip()
    course_dir = _find_course_dir(course)
    if not course_dir:
        return JSONResponse({"error": "Curso no encontrado"}, status_code=404)

    from ingestion.calendar_parser import (
        build_unit_map, classify_files, find_calendar_file, _iter_files, _normalize,
    )

    model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    units_file = course_dir / "_units.json"
    try:
        existing = _json.loads(units_file.read_text(encoding="utf-8")) if units_file.exists() else {}
    except Exception:
        existing = {}
    units = existing.get("units") or []

    if units:
        try:
            file_map = classify_files(course_dir, units, model)
        except Exception:
            file_map = existing.get("file_map") or {}
    else:
        try:
            unit_data = build_unit_map(course_dir, model)
        except Exception:
            unit_data = {"units": [], "file_map": {}}
        units = unit_data.get("units") or []
        file_map = unit_data.get("file_map") or {}

    calendar_file = find_calendar_file(course_dir)
    calendar_resolved = calendar_file.resolve() if calendar_file else None

    current_year = datetime.now().year
    plan: list[dict] = []
    resumen: dict[str, int] = {}

    for f, rel_parts in _iter_files(course_dir):
        if calendar_resolved is not None and f.resolve() == calendar_resolved:
            continue
        relpath = "/".join(rel_parts)
        name = f.name
        name_norm = _normalize(name)

        if _EVAL_NAME_RE.search(name_norm):
            years = [int(y) for y in _YEAR_TOKEN_RE.findall(name)]
            is_old = any(y != current_year for y in years) or bool(_OLD_HINT_RE.search(name_norm))
            folder = "Evaluaciones/Pautas anteriores" if is_old else "Evaluaciones/Semestre actual"
        else:
            unit_name = file_map.get(relpath) or file_map.get(name)
            folder = _safe_folder(unit_name) if unit_name else "Otros"

        dst_rel = f"{folder}/{name}"
        resumen[folder] = resumen.get(folder, 0) + 1
        if relpath != dst_rel:
            plan.append({"src": relpath, "dst": dst_rel})

    return {"plan": plan, "resumen": {"por_carpeta": resumen}}


@app.post("/api/files/organize/apply")
async def organize_apply(request: Request):
    """Aplica (mueve físicamente) un plan de reorganización ya confirmado por el
    usuario. Actualiza _units.json (file_map) y dispara la corrección de metadata
    de la colección vectorial en segundo plano."""
    data = await request.json()
    course = str(data.get("course") or "").strip()
    moves_in = data.get("moves")

    course_dir = _find_course_dir(course)
    if not course_dir:
        return JSONResponse({"error": "Curso no encontrado"}, status_code=404)
    course_dir = course_dir.resolve()
    if not isinstance(moves_in, list) or not moves_in:
        return JSONResponse({"error": "Lista de movimientos vacía"}, status_code=400)

    resolved: list[tuple[str, Path, str, Path]] = []
    for m in moves_in:
        if not isinstance(m, dict):
            return JSONResponse({"error": "Movimiento inválido"}, status_code=400)
        src_rel = str(m.get("src") or "").strip()
        dst_rel = str(m.get("dst") or "").strip()
        if not src_rel or not dst_rel:
            return JSONResponse({"error": "src/dst requeridos"}, status_code=400)
        try:
            src_path = _safe_data_path(f"{course_dir.name}/{src_rel}")
            dst_path = _safe_data_path(f"{course_dir.name}/{dst_rel}")
        except ValueError:
            return JSONResponse({"error": "Ruta inválida"}, status_code=400)
        resolved.append((src_rel, src_path, dst_rel, dst_path))

    units_file = course_dir / "_units.json"
    try:
        existing = _json.loads(units_file.read_text(encoding="utf-8")) if units_file.exists() else {}
    except Exception:
        existing = {}
    file_map = dict(existing.get("file_map") or {})

    applied_moves: list[dict] = []
    metadata_pairs: list[tuple[str, str, str | None]] = []

    for src_rel, src_path, dst_rel, dst_path in resolved:
        if not src_path.exists() or not src_path.is_file():
            continue  # ya no está ahí (movido/borrado mientras tanto) -> se ignora
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        final_dst = dst_path if not dst_path.exists() else _dest_with_suffix(dst_path)
        try:
            shutil.move(str(src_path), str(final_dst))
        except Exception:
            continue

        final_rel = "/".join(final_dst.relative_to(course_dir).parts)
        applied_moves.append({"src": src_rel, "dst_final": final_rel})

        unit_val = file_map.pop(src_rel, None)
        if unit_val is not None:
            file_map[final_rel] = unit_val
            file_map[final_dst.name] = unit_val

        metadata_pairs.append((str(src_path), str(final_dst), unit_val))

    existing["file_map"] = file_map
    try:
        units_file.write_text(_json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    try:
        (course_dir / "_orden_log.json").write_text(_json.dumps(
            {"applied_at": datetime.now(timezone.utc).isoformat(), "moves": applied_moves},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    if metadata_pairs:
        threading.Thread(
            target=_reorganize_reindex, args=(course_dir.name, metadata_pairs), daemon=True
        ).start()

    return {"ok": True, "moved": len(applied_moves), "reindexing": bool(metadata_pairs)}


@app.get("/api/files/organize/status")
async def organize_status(course: str = ""):
    """Le dice al frontend si hay un orden aplicado para poder mostrar '↩ Deshacer'."""
    course_dir = _find_course_dir(course)
    if not course_dir:
        return {"has_log": False}
    return {"has_log": (course_dir / "_orden_log.json").is_file()}


@app.post("/api/files/organize/undo")
async def organize_undo(request: Request):
    """Revierte el último orden aplicado (dst -> src), actualiza file_map y
    borra el log. Ignora movimientos cuyo archivo ya no está donde se dejó, o
    cuyo destino original está ocupado (no sobreescribe)."""
    data = await request.json()
    course = str(data.get("course") or "").strip()
    course_dir = _find_course_dir(course)
    if not course_dir:
        return JSONResponse({"error": "Curso no encontrado"}, status_code=404)

    log_path = course_dir / "_orden_log.json"
    if not log_path.is_file():
        return JSONResponse({"error": "No hay una reorganización para deshacer"}, status_code=404)

    try:
        log = _json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:
        return JSONResponse({"error": "Registro de orden corrupto"}, status_code=500)
    moves = log.get("moves") or []

    units_file = course_dir / "_units.json"
    try:
        existing = _json.loads(units_file.read_text(encoding="utf-8")) if units_file.exists() else {}
    except Exception:
        existing = {}
    file_map = dict(existing.get("file_map") or {})

    restored: list[str] = []
    metadata_pairs: list[tuple[str, str, str | None]] = []

    for mv in reversed(moves):
        src_rel = str(mv.get("src") or "").strip()
        dst_rel = str(mv.get("dst_final") or "").strip()
        if not src_rel or not dst_rel:
            continue
        try:
            dst_path = _safe_data_path(f"{course_dir.name}/{dst_rel}")
            src_path = _safe_data_path(f"{course_dir.name}/{src_rel}")
        except ValueError:
            continue
        if not dst_path.exists() or not dst_path.is_file():
            continue  # ya no existe donde el log dice -> se ignora
        if src_path.exists():
            continue  # el destino original está ocupado -> no se sobreescribe

        try:
            src_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst_path), str(src_path))
        except Exception:
            continue

        restored.append(src_rel)
        unit_val = file_map.pop(dst_rel, None)
        if unit_val is not None:
            file_map[src_rel] = unit_val
            file_map[src_path.name] = unit_val
        metadata_pairs.append((str(dst_path), str(src_path), unit_val))

    existing["file_map"] = file_map
    try:
        units_file.write_text(_json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    try:
        log_path.unlink()
    except Exception:
        pass

    if metadata_pairs:
        threading.Thread(
            target=_reorganize_reindex, args=(course_dir.name, metadata_pairs), daemon=True
        ).start()

    return {"ok": True, "restored": len(restored)}
