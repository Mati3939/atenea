import os
import re
import json as _json
import uuid
import threading
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
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


def _resolve_courses() -> list[dict]:
    """
    Return [{value: safe_collection_name, label: human_readable_name}].
    Maps ChromaDB safe names back to the original directory names in data/.
    """
    from ingestion.vectorstore import VectorStore
    safe_names = VectorStore().list_collections()

    data_dir = Path("data")
    safe_to_label: dict[str, str] = {}
    if data_dir.exists():
        for d in data_dir.iterdir():
            if d.is_dir():
                safe_to_label[_safe_name(d.name)] = d.name

    return [
        {"value": s, "label": safe_to_label.get(s, s.replace("_", " ").strip())}
        for s in safe_names
    ]


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


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    courses = _resolve_courses()
    return templates.TemplateResponse(request, "index.html", {
        "active_page": "home",
        "courses": courses,
        "sync_status": _status["sync"],
        "ingest_status": _status["ingest"],
    })


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    courses = _resolve_courses()
    return templates.TemplateResponse(request, "chat.html", {
        "active_page": "chat",
        "courses": courses,
    })


@app.get("/analysis", response_class=HTMLResponse)
async def analysis_page(request: Request):
    return templates.TemplateResponse(request, "analysis.html", {
        "active_page": "analysis",
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

    # Fallback: file names
    return {"units": _get_store().list_files_in_collection(course)}


@app.delete("/api/units/{course}/reset")
async def reset_units(course: str):
    """Delete _units.json so the next ingest re-analyzes the course."""
    data_dir = Path("data")
    if data_dir.exists():
        for d in data_dir.iterdir():
            if d.is_dir() and _safe_name(d.name) == course:
                f = d / "_units.json"
                if f.exists():
                    f.unlink()
                return {"reset": True, "course": d.name}
    return {"reset": False}


# ── Sync API ──────────────────────────────────────────────────────────────────

@app.post("/api/sync")
async def start_sync(background_tasks: BackgroundTasks):
    if _status["sync"]["running"]:
        return {"status": "already_running"}
    _status["sync"] = _sync_state(running=True)
    background_tasks.add_task(_run_sync)
    return {"status": "started"}


def _safe_folder(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


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


# ── Ingest API ────────────────────────────────────────────────────────────────

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
            raise FileNotFoundError("Carpeta 'data/' no encontrada. Sincroniza Canvas primero.")

        course_dirs = sorted(d for d in data_dir.iterdir() if d.is_dir())

        # Fase 0: calendarizaciones — solo cursos sin _units.json
        unit_maps: dict[str, dict] = {}
        for i, course_dir in enumerate(course_dirs):
            units_file = course_dir / "_units.json"

            if units_file.exists():
                try:
                    unit_maps[course_dir.name] = _json.loads(
                        units_file.read_text(encoding="utf-8")
                    )
                    continue
                except Exception:
                    pass

            s["phase"]      = f"Analizando unidades: {course_dir.name} (solo primera vez)"
            s["percentage"] = int(i / max(len(course_dirs), 1) * 30)

            try:
                unit_data = build_unit_map(course_dir, model)
            except Exception:
                unit_data = {"units": [], "file_map": {}}

            # Always write — prevents retrying on every subsequent ingest
            try:
                units_file.write_text(
                    _json.dumps(unit_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

            unit_maps[course_dir.name] = unit_data

        # Fase 1: escanear archivos
        s["phase"]      = "Escaneando documentos..."
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

        # Fase 2: indexar con metadata de unidad
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
                        {
                            "course": course_name,
                            "file":   f.name,
                            "path":   str(f),
                            **({"unit": unit_name} if unit_name else {}),
                        }
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
