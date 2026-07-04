"""Parse calendarización PDFs and classify course files into units.

Recorre TODO el árbol de `data/<curso>/` (no solo la raíz) porque desde la
descarga por Módulos casi todo el material vive en `Modulos/<nombre>/...`.
"""
import json
import re
from pathlib import Path
from chatbot import llm

# Ranking de candidatos a calendarización: el primer tier que matchea gana.
# Dentro de un tier, gana el archivo más cercano a la raíz del curso.
_CALENDAR_TIERS = [
    ("calendariz",),
    ("cronograma",),
    ("programa", "plan_docente", "guia_docente"),
    ("syllabus", "calendario", "schedule"),
]
_CALENDAR_EXTS = {".pdf", ".docx"}
_BATCH_SIZE = 12

# Carpetas que no contienen material clasificable (contenido de Canvas ya
# persistido aparte, o basura de macOS).
_SKIP_DIR_NAMES = {"paginas", "anuncios", "__macosx"}

_ACCENTS = str.maketrans("áéíóúÁÉÍÓÚñÑüÜ", "aeiouAEIOUnNuU")

# "Unidad N" con número — cubre tanto carpetas de Canvas ("Unidad 2 - Cinemática")
# como calendarizaciones que numeran en romano ("Unidad II: Cinemática").
_UNIT_TOKEN_RE = re.compile(r"unidad\s+([a-z]+|\d+)")
_ROMAN_NUMS = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8,
    "ix": 9, "x": 10, "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15,
}


def _normalize(s: str) -> str:
    s = (s or "").translate(_ACCENTS).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def _unit_numbers(text_norm: str) -> set[int]:
    """Extrae los números de 'unidad N' presentes en un texto ya normalizado,
    aceptando arábigos (2) y romanos (ii)."""
    nums: set[int] = set()
    for tok in _UNIT_TOKEN_RE.findall(text_norm):
        if tok.isdigit():
            nums.add(int(tok))
        elif tok in _ROMAN_NUMS:
            nums.add(_ROMAN_NUMS[tok])
    return nums


def _iter_files(course_dir: Path, exts: set[str] | None = None):
    """Recorre course_dir RECURSIVAMENTE. Ignora AppleDouble (._*), metadata
    (_*.json y similares), __MACOSX, y las carpetas Paginas/Anuncios (contenido
    de Canvas ya persistido, no material clasificable en unidades)."""
    for f in course_dir.rglob("*"):
        if not f.is_file():
            continue
        if exts is not None and f.suffix.lower() not in exts:
            continue
        if f.name.startswith("._") or f.name.startswith("_"):
            continue
        try:
            rel_parts = f.relative_to(course_dir).parts
        except ValueError:
            continue
        if any(p.lower() in _SKIP_DIR_NAMES for p in rel_parts[:-1]):
            continue
        yield f, rel_parts


def _manual_calendar_override(course_dir: Path) -> Path | None:
    """Lee `calendar_file` (ruta relativa al curso) desde _units.json, si existe."""
    units_file = course_dir / "_units.json"
    if not units_file.exists():
        return None
    try:
        data = json.loads(units_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    rel = data.get("calendar_file")
    if not rel:
        return None
    try:
        candidate = (course_dir / rel).resolve()
        candidate.relative_to(course_dir.resolve())
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


def find_calendar_file(course_dir: Path) -> Path | None:
    """Busca el archivo de calendarización en TODO el árbol del curso (incluye
    `Modulos/<nombre>/...`). Si hay un override manual válido en `_units.json`
    (clave `calendar_file`), ese manda y no se busca nada más."""
    override = _manual_calendar_override(course_dir)
    if override is not None:
        return override

    best: tuple[int, int] | None = None
    best_file: Path | None = None
    for f, rel_parts in _iter_files(course_dir, _CALENDAR_EXTS):
        stem = f.stem.lower().replace(" ", "_").replace("-", "_")
        for tier_idx, patterns in enumerate(_CALENDAR_TIERS):
            if any(p in stem for p in patterns):
                key = (tier_idx, len(rel_parts))
                if best is None or key < best:
                    best, best_file = key, f
                break
    return best_file


def set_calendar_override(course_dir: Path, rel_path: str) -> None:
    """Guarda `calendar_file` en _units.json (override manual)."""
    units_file = course_dir / "_units.json"
    try:
        existing = json.loads(units_file.read_text(encoding="utf-8")) if units_file.exists() else {}
    except Exception:
        existing = {}
    existing["calendar_file"] = rel_path
    units_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def save_calendar_override(course_dir: Path, rel_path: str, model: str = "") -> list[dict]:
    """Aplica un override manual de calendarización, redetecta unidades y las
    cachea (limpia el file_map viejo, quedó obsoleto). Devuelve las unidades."""
    set_calendar_override(course_dir, rel_path)
    units = detect_units(course_dir, model)

    units_file = course_dir / "_units.json"
    try:
        existing = json.loads(units_file.read_text(encoding="utf-8")) if units_file.exists() else {}
    except Exception:
        existing = {}
    existing["units"] = units
    existing["file_map"] = {}
    existing["calendar_file"] = rel_path
    units_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return units


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
        "Identifica TODAS las UNIDADES o MÓDULOS principales, de principio a fin del semestre "
        "(agrupa semanas por tema, no listes cada semana). NO omitas ninguna unidad, "
        "incluidas las últimas del documento. "
        "Responde ÚNICAMENTE con JSON, sin texto adicional:\n"
        '[{"name": "Unidad 1 — Nombre", "topics": ["tema a", "tema b"]}, ...]\n\n'
        f"Documento completo:\n{text[:12000]}"
    )

    try:
        content = llm.complete([{"role": "user", "content": prompt}], temperature=0.2)
        result = _extract_json_list(content)
        if result and isinstance(result, list):
            return [u for u in result if isinstance(u, dict) and "name" in u]
    except Exception:
        pass
    return []


def infer_units_from_filenames(course_dir: Path, model: str) -> list[dict]:
    """Infer unit structure from file names when no calendar PDF is found."""
    from ingestion.parsers import SUPPORTED

    filenames = sorted(
        "/".join(rel_parts) for _, rel_parts in _iter_files(course_dir, SUPPORTED)
    )
    if not filenames:
        return []

    prompt = (
        "Tengo los siguientes archivos de un curso universitario. "
        "Agrúpalos en unidades temáticas coherentes basándote en sus nombres "
        "(y en la carpeta de módulo si aparece en la ruta).\n"
        + "\n".join(f"- {n}" for n in filenames[:30])
        + "\n\nResponde ÚNICAMENTE con JSON:\n"
        '[{"name": "Unidad 1 — Nombre", "topics": ["tema"]}, ...]\n'
        "Si no hay estructura clara, devuelve []"
    )

    try:
        content = llm.complete([{"role": "user", "content": prompt}], temperature=0.2)
        result = _extract_json_list(content)
        if result and isinstance(result, list):
            return [u for u in result if isinstance(u, dict) and "name" in u]
    except Exception:
        pass
    return []


def _classify_batch(
    batch: list[tuple[str, str, str]], units: list[dict], model: str
) -> dict[str, str | None]:
    """Classify a batch of (id, label, content_sample) → {id: unit_name|None}.

    `id` es un identificador corto y opaco: se le pide al LLM que lo devuelva
    tal cual como clave del JSON, en vez de la ruta completa (nombres con
    acentos/espacios/rutas largas son propensos a errores de transcripción del
    modelo). `label` (ruta relativa) es solo contexto humano en el prompt. Si
    `content_sample` viene prefijado con '[Carpeta del módulo: ...]', es una
    pista fuerte del módulo Canvas donde vive el archivo — úsala para decidir
    la unidad."""
    units_desc = "\n".join(
        f'{i + 1}. {u["name"]}: {", ".join(u.get("topics", []))}'
        for i, u in enumerate(units)
    )
    files_desc = "\n".join(f'{fid} ({label}): {sample[:250]}' for fid, label, sample in batch)

    prompt = (
        "Clasifica cada archivo en la unidad más apropiada.\n\n"
        f"UNIDADES:\n{units_desc}\n\n"
        "ARCHIVOS (id (ruta): muestra). Si la muestra empieza con "
        "'[Carpeta del módulo: X]', usa X como pista fuerte de la unidad:\n"
        f"{files_desc}\n\n"
        "Responde ÚNICAMENTE con JSON, usando el id EXACTO (el que va antes del "
        "paréntesis, ej. f0) como clave:\n"
        '{"f0": 1, "f1": 2, "f2": null}\n'
        "Usa el número de la unidad o null."
    )

    try:
        content = llm.complete([{"role": "user", "content": prompt}], temperature=0.2)
        raw = _extract_json_object(content)
        if not raw:
            return {}
        result: dict[str, str | None] = {}
        for fid, idx in raw.items():
            if idx is None:
                result[fid] = None
            elif isinstance(idx, (int, float)) and 1 <= int(idx) <= len(units):
                result[fid] = units[int(idx) - 1]["name"]
            else:
                result[fid] = None
        return result
    except Exception:
        return {}


def detect_units(course_dir: Path, model: str = "") -> list[dict]:
    """Solo la LISTA de unidades (rápido), priorizando la calendarización.

    No clasifica archivos (eso lo hace classify_files/build_unit_map en el ingest).
    Pensado para llamarse al seleccionar un curso: leer la calendarización → unidades → preguntar.
    """
    calendar_file = find_calendar_file(course_dir)
    if calendar_file:
        units = parse_calendar_units(calendar_file, model)
        if units:
            return units
    return infer_units_from_filenames(course_dir, model)


def _module_name_from_rel(dir_parts: tuple[str, ...]) -> str | None:
    """Si la ruta pasa por Modulos/<nombre>/..., devuelve <nombre> — suele SER
    la unidad (así es como Canvas organiza el material por módulo)."""
    for i, p in enumerate(dir_parts):
        if p.lower() == "modulos" and i + 1 < len(dir_parts):
            return dir_parts[i + 1]
    return None


def _deterministic_unit(module_name: str | None, units: list[dict]) -> str | None:
    """Intenta asignar el archivo a una unidad SIN llamar al LLM, usando el
    nombre de la carpeta de módulo como señal. Solo asigna si la señal es
    inequívoca (un solo número de unidad, o un único match textual)."""
    if not module_name:
        return None
    mod_norm = _normalize(module_name)
    if not mod_norm:
        return None

    mod_nums = _unit_numbers(mod_norm)
    if len(mod_nums) == 1:
        num = next(iter(mod_nums))
        matches = [
            u["name"] for u in units
            if _unit_numbers(_normalize(u["name"])) == {num}
        ]
        if len(matches) == 1:
            return matches[0]
        return None  # número ambiguo o sin match único -> deja que decida el LLM

    if mod_nums:
        return None  # la carpeta menciona varias unidades ("Unidad 5 y Unidad 6") -> ambiguo

    matched: set[str] = set()
    for u in units:
        u_title = _UNIT_TOKEN_RE.sub("", _normalize(u["name"]), count=1).strip()
        candidates = [c for c in [u_title, *[_normalize(t) for t in u.get("topics", [])]] if c]
        for c in candidates:
            if len(c) >= 6 and (c in mod_norm or mod_norm in c):
                matched.add(u["name"])
                break
    if len(matched) == 1:
        return matched.pop()
    return None


def classify_files(course_dir: Path, units: list[dict], model: str) -> dict[str, str | None]:
    """Clasifica los archivos indexables del curso en `units`, recorriendo TODO
    el árbol (incluye Modulos/<nombre>/...). Pase determinista primero usando la
    carpeta de módulo como pista fuerte; solo lo ambiguo se manda al LLM, en lotes.

    Devuelve un file_map con DOS claves por archivo (compatibilidad con quien
    consuma el mapa): la ruta relativa posix (única, precisa) y el nombre plano
    (compat con el formato viejo; si dos archivos de distinta unidad comparten
    nombre, la ruta relativa es la fuente de verdad y el nombre queda con el
    último que se procesó — igual que el comportamiento previo)."""
    if not units:
        return {}

    from ingestion.parsers import parse, SUPPORTED

    calendar_file = find_calendar_file(course_dir)
    calendar_resolved = calendar_file.resolve() if calendar_file else None
    file_map: dict[str, str | None] = {}
    to_classify: list[tuple[str, str, str, str]] = []  # (id, relpath, sample, name)

    for f, rel_parts in _iter_files(course_dir, SUPPORTED):
        if calendar_resolved is not None and f.resolve() == calendar_resolved:
            continue
        relpath = "/".join(rel_parts)
        module_name = _module_name_from_rel(rel_parts[:-1])

        det = _deterministic_unit(module_name, units)
        if det is not None:
            file_map[relpath] = det
            file_map[f.name] = det
            continue

        try:
            text = parse(f)
            sample = text[:250] if text else ""
        except Exception:
            sample = ""
        if module_name:
            sample = f"[Carpeta del módulo: {module_name}] {sample}"
        to_classify.append((f"f{len(to_classify)}", relpath, sample, f.name))

    for i in range(0, len(to_classify), _BATCH_SIZE):
        batch = to_classify[i: i + _BATCH_SIZE]
        result = _classify_batch(
            [(fid, relpath, sample) for fid, relpath, sample, _ in batch], units, model
        )
        for fid, relpath, _sample, name in batch:
            unit_name = result.get(fid)
            file_map[relpath] = unit_name
            file_map[name] = unit_name

    return file_map


def build_unit_map(course_dir: Path, model: str) -> dict:
    """
    Parse calendarización (or infer from filenames) and classify files into units.
    Always returns a dict — never raises. Empty units → {"units": [], "file_map": {}}.
    """
    units = detect_units(course_dir, model)
    if not units:
        return {"units": [], "file_map": {}}

    file_map = classify_files(course_dir, units, model)
    return {"units": units, "file_map": file_map}
