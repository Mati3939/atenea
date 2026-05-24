"""Parse calendarización PDFs and classify course files into units."""
import json
import re
from pathlib import Path
import ollama

# Extended patterns — covers common naming in Spanish-language universities
_CALENDAR_PATTERNS = [
    "calendariz", "calendario", "programa", "syllabus",
    "schedule", "cronograma", "guia_docente", "plan_docente",
]
_BATCH_SIZE = 12


def find_calendar_file(course_dir: Path) -> Path | None:
    for f in course_dir.iterdir():
        if f.is_file() and f.suffix.lower() == ".pdf":
            stem = f.stem.lower().replace(" ", "_").replace("-", "_")
            if any(p in stem for p in _CALENDAR_PATTERNS):
                return f
    return None


def _extract_json_list(text: str) -> list | None:
    # Try the first JSON array we find (greedy won't work for nested, use first complete one)
    for m in re.finditer(r'\[', text):
        try:
            candidate = text[m.start():]
            depth = 0
            for i, ch in enumerate(candidate):
                if ch == '[': depth += 1
                elif ch == ']':
                    depth -= 1
                    if depth == 0:
                        return json.loads(candidate[:i + 1])
        except (json.JSONDecodeError, Exception):
            continue
    return None


def _extract_json_object(text: str) -> dict | None:
    for m in re.finditer(r'\{', text):
        try:
            candidate = text[m.start():]
            depth = 0
            for i, ch in enumerate(candidate):
                if ch == '{': depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return json.loads(candidate[:i + 1])
        except (json.JSONDecodeError, Exception):
            continue
    return None


def parse_calendar_units(pdf_path: Path, model: str) -> list[dict]:
    """Extract unit list from calendarización PDF."""
    from ingestion.parsers import parse

    try:
        text = parse(pdf_path)
    except Exception:
        return []

    if not text.strip():
        return []

    prompt = (
        "Analiza este documento de calendarización de un curso universitario. "
        "Identifica las UNIDADES o MÓDULOS principales (agrupa semanas por tema, no listes cada semana). "
        "Responde ÚNICAMENTE con JSON, sin texto adicional:\n"
        '[{"name": "Unidad 1 — Nombre", "topics": ["tema a", "tema b"]}, ...]\n\n'
        f"Documento (primeras 5000 palabras):\n{text[:5000]}"
    )

    try:
        resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
        result = _extract_json_list(resp["message"]["content"])
        if result and isinstance(result, list):
            return [u for u in result if isinstance(u, dict) and "name" in u]
    except Exception:
        pass
    return []


def infer_units_from_filenames(course_dir: Path, model: str) -> list[dict]:
    """Infer unit structure from file names when no calendar PDF is found."""
    from ingestion.parsers import SUPPORTED

    filenames = sorted(
        f.name for f in course_dir.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED and not f.name.startswith("_")
    )
    if not filenames:
        return []

    prompt = (
        "Tengo los siguientes archivos de un curso universitario. "
        "Agrúpalos en unidades temáticas coherentes basándote en sus nombres.\n"
        + "\n".join(f"- {n}" for n in filenames[:30])
        + "\n\nResponde ÚNICAMENTE con JSON:\n"
        '[{"name": "Unidad 1 — Nombre", "topics": ["tema"]}, ...]\n'
        "Si no hay estructura clara, devuelve []"
    )

    try:
        resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
        result = _extract_json_list(resp["message"]["content"])
        if result and isinstance(result, list):
            return [u for u in result if isinstance(u, dict) and "name" in u]
    except Exception:
        pass
    return []


def _classify_batch(
    batch: list[tuple[str, str]], units: list[dict], model: str
) -> dict[str, str | None]:
    """Classify a batch of (filename, content_sample) → {filename: unit_name|None}."""
    units_desc = "\n".join(
        f'{i + 1}. {u["name"]}: {", ".join(u.get("topics", []))}'
        for i, u in enumerate(units)
    )
    files_desc = "\n".join(f'"{name}": {sample[:250]}' for name, sample in batch)

    prompt = (
        "Clasifica cada archivo en la unidad más apropiada.\n\n"
        f"UNIDADES:\n{units_desc}\n\n"
        f"ARCHIVOS (nombre: muestra):\n{files_desc}\n\n"
        "Responde ÚNICAMENTE con JSON:\n"
        '{"nombre.pdf": 1, "otro.pptx": 2, "irrelevante.pdf": null}\n'
        "Usa el número de la unidad o null."
    )

    try:
        resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
        raw = _extract_json_object(resp["message"]["content"])
        if not raw:
            return {}
        result: dict[str, str | None] = {}
        for filename, idx in raw.items():
            if idx is None:
                result[filename] = None
            elif isinstance(idx, (int, float)) and 1 <= int(idx) <= len(units):
                result[filename] = units[int(idx) - 1]["name"]
            else:
                result[filename] = None
        return result
    except Exception:
        return {}


def build_unit_map(course_dir: Path, model: str) -> dict:
    """
    Parse calendarización (or infer from filenames) and classify files into units.
    Always returns a dict — never raises. Empty units → {"units": [], "file_map": {}}.
    """
    calendar_file = find_calendar_file(course_dir)

    if calendar_file:
        units = parse_calendar_units(calendar_file, model)
    else:
        units = infer_units_from_filenames(course_dir, model)

    if not units:
        return {"units": [], "file_map": {}}

    from ingestion.parsers import parse, SUPPORTED

    to_classify: list[tuple[str, str]] = []
    for f in course_dir.iterdir():
        if not f.is_file() or f == calendar_file or f.name.startswith("_"):
            continue
        if f.suffix.lower() not in SUPPORTED:
            continue
        try:
            text = parse(f)
            sample = text[:250] if text else ""
        except Exception:
            sample = ""
        to_classify.append((f.name, sample))

    file_map: dict[str, str | None] = {}
    for i in range(0, len(to_classify), _BATCH_SIZE):
        file_map.update(_classify_batch(to_classify[i : i + _BATCH_SIZE], units, model))

    return {"units": units, "file_map": file_map}
