import os
import re
import json as _json
import uuid
import threading
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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


def _get_store():
    from ingestion.vectorstore import VectorStore
    return VectorStore()


# ── Ollama warmup (loads model into RAM so first prompt is faster) ─────────────

def _warmup_ollama():
    try:
        import ollama
        model = os.environ.get("OLLAMA_MODEL", "llama3.2")
        ollama.chat(model=model, messages=[{"role": "user", "content": "hola"}])
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warmup_ollama, daemon=True).start()
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


@app.get("/analysis", response_class=HTMLResponse)
async def analysis_page(request: Request):
    return templates.TemplateResponse(request, "analysis.html", {
        "active_page": "analysis",
    })


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

@app.post("/api/session/new")
async def new_session():
    return {"session_id": str(uuid.uuid4())}


@app.post("/api/chat")
async def api_chat(request: Request):
    data = await request.json()
    session_id = data.get("session_id", "default")
    message    = (data.get("message") or "").strip()
    course     = data.get("course") or None
    unit       = data.get("unit") or None
    difficulty = data.get("difficulty") or "practicando"

    if not message:
        return JSONResponse({"error": "Mensaje vacío"}, status_code=400)

    if session_id not in chat_sessions:
        from chatbot.conversation import AteneoChat
        chat_sessions[session_id] = AteneoChat()

    chat_obj = chat_sessions[session_id]
    try:
        result = chat_obj.chat(message, course=course, unit=unit, difficulty=difficulty)
        chat_obj.save_session()
        return {"response": result["text"], "options": result.get("options")}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/api/session/{session_id}")
async def clear_session(session_id: str):
    if session_id in chat_sessions:
        chat_sessions[session_id].save_session()
        del chat_sessions[session_id]
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

        indexed_set = set(VectorStore().list_collections())

        result = []
        for c in courses:
            name = c.get("name", str(c["id"]))
            safe = _safe_name(_safe_folder(name))
            result.append({
                "canvas_id": c["id"],
                "label":     name,
                "safe_name": safe,
                "indexed":   safe in indexed_set,
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


def _run_fetch_course(canvas_id: int, course_name: str):
    s = _fetch_status[canvas_id]
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

        for i, f in enumerate(to_download):
            s["phase"] = f"Descargando: {f['filename']}"
            s["percentage"] = 10 + int(i / max(len(to_download), 1) * 45)
            try:
                client.download_file(f["url"], course_dir / f["filename"])
            except Exception:
                pass

        # Fase 2: Indexar documentos nuevos
        store = VectorStore()
        course_name_safe = _safe_folder(course_name)

        all_files = [f for f in course_dir.rglob("*") if f.suffix.lower() in SUPPORTED]
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
            except Exception:
                pass

        s.update({
            "percentage": 100,
            "done": True,
            "phase": f"Listo: {len(to_download)} descargados, {len(to_index)} indexados",
        })
    except Exception as e:
        s["error"] = str(e)
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
        if units_file.exists():
            try:
                data = _json.loads(units_file.read_text(encoding="utf-8"))
                names = [u["name"] for u in data.get("units", []) if "name" in u]
                if names:
                    return {"units": names}
            except Exception:
                pass

    return {"units": _get_store().list_files_in_collection(course)}


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
        courses = client.get_courses()

        to_download = []
        skipped = 0
        for i, course in enumerate(courses):
            name = course.get("name", str(course["id"]))
            s["phase"] = f"Comparando: {name}"
            s["percentage"] = int(i / len(courses) * 40)
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

        downloaded = errors = 0
        for i, (course_dir, f) in enumerate(to_download):
            s["current"]    = f["filename"]
            s["percentage"] = 40 + int(i / len(to_download) * 60) if to_download else 100
            dest = course_dir / f["filename"]
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                client.download_file(f["url"], dest)
                downloaded += 1
                s["downloaded"] = downloaded
            except Exception:
                errors += 1
                s["errors"] = errors

        s.update({
            "percentage": 100, "done": True, "current": None,
            "phase": f"Listo: {downloaded} descargados, {skipped} ya existian",
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
        all_files: list[tuple[str, Path]] = []
        for course_dir in course_dirs:
            for f in course_dir.rglob("*"):
                if f.suffix.lower() in SUPPORTED:
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
            except Exception:
                pass
            s["processed"] = i + 1

        s.update({
            "percentage": 100, "done": True, "current": None,
            "phase":   f"Listo: {len(to_index)} indexados, {skipped} ya existian",
            "summary": totals,
        })
    except Exception as e:
        s["error"] = str(e)
    finally:
        s["running"] = False


@app.get("/api/ingest/status")
async def get_ingest_status():
    return _status["ingest"]


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
