import os
import re
import json as _json
import time
import uuid
import zipfile
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
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

# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "chat.html", {
        "active_page": "chat",
    })


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return RedirectResponse("/")


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
                               difficulty=p["difficulty"], mode=p["mode"])
        chat_obj.save_session()
        chat_obj.save_state(p["session_id"])
        return {"response": result["text"], "options": result.get("options")}
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
            for event in chat_obj.chat_stream(p["message"], course=p["course"], unit=p["unit"],
                                              difficulty=p["difficulty"], mode=p["mode"]):
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
        from canvas_api import CanvasClient
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
        files = client.get_course_files(canvas_id)
        to_download = [
            f for f in files
            if not f.get("locked_for_user") and not (course_dir / f["filename"]).exists()
        ]

        s["phase"] = f"Descargando {len(to_download)} archivo(s)..."
        s["percentage"] = 10

        def _on_dl(done, total):
            s["phase"] = f"Descargando {done}/{total} archivo(s)..."
            s["percentage"] = 10 + int(done / max(total, 1) * 35)

        dl_items = [(f["url"], course_dir / f["filename"]) for f in to_download]
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
                     if f.suffix.lower() in SUPPORTED and not f.name.startswith("._")]
        to_index  = [f for f in all_files if not store.is_file_indexed(course_name_safe, str(f))]

        s["phase"] = f"Indexando {len(to_index)} documento(s)..."
        s["percentage"] = 55

        for i, f in enumerate(to_index):
            s["phase"] = f"Indexando: {f.name}"
            s["percentage"] = 55 + int(i / max(len(to_index), 1) * 43)
            try:
                text = parse(f)
                if text.strip():
                    chunks = chunk(text)
                    meta = [{"course": course_name_safe, "file": f.name, "path": str(f)} for _ in chunks]
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

@app.get("/api/units/{course}")
async def get_units(course: str):
    data_dir = Path("data")
    course_dir = None
    if data_dir.exists():
        for d in data_dir.iterdir():
            if d.is_dir() and _safe_name(d.name) == course:
                course_dir = d
                break

    if course_dir:
        units_file = course_dir / "_units.json"
        existing = {}
        if units_file.exists():
            try:
                existing = _json.loads(units_file.read_text(encoding="utf-8"))
                names = [u["name"] for u in existing.get("units", []) if "name" in u]
                if names:
                    return {"units": names, "source": "units"}
            except Exception:
                existing = {}

        # No hay unidades cacheadas: detectarlas leyendo la calendarización (on-demand).
        try:
            from ingestion.calendar_parser import detect_units
            units = detect_units(course_dir)
        except Exception:
            units = []
        if units:
            try:
                units_file.write_text(_json.dumps(
                    {"units": units, "file_map": existing.get("file_map", {})},
                    ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            return {"units": [u["name"] for u in units if "name" in u], "source": "units"}

    return {"units": _get_store().list_files_in_collection(course), "source": "files"}


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
        from canvas_api import CanvasClient
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
            files = client.get_course_files(course["id"])
            course_dir = data_dir / _safe_folder(name)
            for f in files:
                if f.get("locked_for_user"):
                    skipped += 1
                    continue
                dest = course_dir / f["filename"]
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

        dl_items = [(f["url"], course_dir / f["filename"]) for course_dir, f in to_download]
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
                    unit_maps[course_dir.name] = _json.loads(units_file.read_text(encoding="utf-8"))
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
                    unit_name = file_map.get(f.name)
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
            from_d = datetime.fromisoformat(from_str)
            to_d   = datetime.fromisoformat(to_str)
        except Exception:
            return JSONResponse({"error": "Fechas inválidas"}, status_code=400)

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
        events_txt = ""
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

        prompt = (
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
        try:
            plan = llm.complete([{"role": "user", "content": prompt}], temperature=0.5)
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


# ── Study methods API ─────────────────────────────────────────────────────────

@app.get("/api/methods")
async def get_methods():
    from chatbot.study_methods import METHODS
    return {"methods": METHODS}


@app.get("/api/methods/recommend")
async def get_methods_recommend():
    from chatbot.study_methods import recommend
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
    data_dir = Path("data")
    course_dir: Path | None = None
    if data_dir.exists():
        for d in data_dir.iterdir():
            if d.is_dir() and _safe_name(d.name) == course:
                course_dir = d
                break
    if course_dir is None:
        course_dir = data_dir / course
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
