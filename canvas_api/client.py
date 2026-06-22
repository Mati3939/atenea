import html
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def html_to_text(html_str: str) -> str:
    """Convierte el HTML de páginas/anuncios de Canvas a texto plano legible."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", html_str or "")
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|table|ul|ol)>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _safe_part(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", str(name)).strip() or "sin_nombre"


def file_dest(course_dir: Path, f: dict) -> Path:
    """Ruta destino de un archivo bajo la carpeta del curso, respetando 'subdir'
    (vacío = raíz del curso; "Modulos/<nombre>" para archivos de módulos)."""
    sub = f.get("subdir") or ""
    parent = course_dir
    for part in sub.split("/"):
        if part:
            parent = parent / _safe_part(part)
    return parent / f["filename"]


class CanvasClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _get_paginated(self, url: str, params: dict = None) -> list:
        """Fetch all pages of a Canvas API endpoint."""
        results = []
        while url:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            results.extend(response.json())
            params = None
            url = self._next_page(response.headers.get("Link", ""))
        return results

    def _next_page(self, link_header: str) -> str | None:
        for part in link_header.split(","):
            if 'rel="next"' in part:
                match = re.search(r"<([^>]+)>", part)
                if match:
                    return match.group(1)
        return None

    def get_courses(self) -> list[dict]:
        url = f"{self.base_url}/api/v1/courses"
        return self._get_paginated(url, params={"enrollment_state": "active", "per_page": 50})

    def get_course_files(self, course_id: int) -> list[dict]:
        url = f"{self.base_url}/api/v1/courses/{course_id}/files"
        try:
            return self._get_paginated(url, params={"per_page": 100})
        except requests.HTTPError as e:
            # Algunos cursos no tienen módulo de archivos habilitado (401/404)
            if e.response.status_code in (401, 403, 404):
                return []
            raise

    def _get_file_meta(self, course_id: int, file_id) -> dict | None:
        """Metadata de un archivo por id (para items de módulo sin URL embebida)."""
        try:
            r = self.session.get(f"{self.base_url}/api/v1/courses/{course_id}/files/{file_id}")
            r.raise_for_status()
            return r.json()
        except requests.HTTPError:
            return None

    def get_module_files(self, course_id: int) -> list[dict]:
        """Archivos accesibles vía MÓDULOS: items de tipo File y archivos embebidos
        en el cuerpo de las Páginas. Imprescindible para cursos sin pestaña "Archivos"
        (p. ej. material que solo vive en Módulos).

        Cada dict trae los campos habituales de archivo de Canvas (id, url, filename,
        display_name, size) más 'subdir' = carpeta relativa sugerida bajo el curso.
        Deduplicado por id de archivo dentro de esta llamada.
        """
        try:
            modules = self._get_paginated(
                f"{self.base_url}/api/v1/courses/{course_id}/modules",
                params={"per_page": 100})
        except requests.HTTPError as e:
            if e.response.status_code in (401, 403, 404):
                return []
            raise

        out: list[dict] = []
        seen: set = set()

        def _add(meta: dict, file_id, subdir: str):
            if file_id in seen or not meta or not meta.get("url"):
                return
            seen.add(file_id)
            out.append({
                "id": file_id,
                "url": meta.get("url"),
                "filename": meta.get("filename") or meta.get("display_name") or f"file_{file_id}",
                "display_name": meta.get("display_name") or meta.get("filename"),
                "size": meta.get("size", -1),
                "subdir": subdir,
            })

        for m in modules:
            mod_name = re.sub(r'[<>:"/\\|?*]', "_", str(m.get("name") or "modulo")).strip() or "modulo"
            subdir = f"Modulos/{mod_name}"
            try:
                items = self._get_paginated(
                    f"{self.base_url}/api/v1/courses/{course_id}/modules/{m['id']}/items",
                    params={"per_page": 100, "include[]": "content_details"})
            except requests.HTTPError:
                continue
            for it in items:
                tipo = it.get("type")
                if tipo == "File":
                    fid = it.get("content_id")
                    if not fid or fid in seen:
                        continue
                    cd = it.get("content_details") or {}
                    if not cd.get("url"):
                        cd = self._get_file_meta(course_id, fid) or {}
                    _add(cd, fid, subdir)
                elif tipo == "Page":
                    page_url = it.get("page_url")
                    if not page_url:
                        continue
                    body = self.get_page_body(course_id, page_url)
                    for fid in {int(x) for x in re.findall(r"/files/(\d+)", body or "")}:
                        if fid in seen:
                            continue
                        _add(self._get_file_meta(course_id, fid) or {}, fid, subdir)
        return out

    def get_all_course_files(self, course_id: int) -> list[dict]:
        """Pestaña "Archivos" + Módulos (+ archivos embebidos en Páginas), deduplicado
        por id de archivo. Los archivos de la pestaña llevan subdir="" (raíz del curso),
        los de módulos llevan subdir="Modulos/<nombre>"."""
        files = self.get_course_files(course_id)
        for f in files:
            f.setdefault("subdir", "")
        seen = {f.get("id") for f in files}
        for mf in self.get_module_files(course_id):
            if mf.get("id") not in seen:
                seen.add(mf.get("id"))
                files.append(mf)
        return files

    def get_pages(self, course_id: int) -> list[dict]:
        """Lista las páginas de un curso (sin cuerpo; usar get_page_body)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/pages"
        try:
            return self._get_paginated(url, params={"per_page": 100})
        except requests.HTTPError as e:
            if e.response.status_code in (401, 403, 404):
                return []
            raise

    def get_page_body(self, course_id: int, page_url: str) -> str:
        url = f"{self.base_url}/api/v1/courses/{course_id}/pages/{page_url}"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json().get("body") or ""
        except requests.HTTPError:
            return ""

    def get_assignments(self, course_id: int) -> list[dict]:
        """Entregas/evaluaciones del curso con fecha (due_at, name, html_url)."""
        url = f"{self.base_url}/api/v1/courses/{course_id}/assignments"
        try:
            return self._get_paginated(url, params={"per_page": 100, "order_by": "due_at"})
        except requests.HTTPError as e:
            if e.response.status_code in (401, 403, 404):
                return []
            raise

    def get_announcements(self, course_id: int) -> list[dict]:
        """Anuncios del curso (incluyen el cuerpo en 'message')."""
        url = f"{self.base_url}/api/v1/announcements"
        try:
            return self._get_paginated(url, params={
                "context_codes[]": f"course_{course_id}",
                "start_date": "2020-01-01",
                "per_page": 100,
            })
        except requests.HTTPError as e:
            if e.response.status_code in (401, 403, 404):
                return []
            raise

    def download_file(self, file_url: str, dest_path: Path) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.session.get(file_url, stream=True) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

    def download_many(self, items: list[tuple[str, Path]], max_workers: int = 8,
                      on_done=None) -> list[tuple[Path, Exception]]:
        """Descarga (url, dest) en paralelo. Devuelve [(dest, excepción)] de los fallos.

        `on_done(done, total)` se llama tras cada descarga para reportar progreso.
        """
        failures: list[tuple[Path, Exception]] = []
        total = len(items)
        if not total:
            return failures
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(self.download_file, url, dest): dest for url, dest in items}
            for fut in as_completed(futures):
                dest = futures[fut]
                try:
                    fut.result()
                except Exception as e:
                    failures.append((dest, e))
                done += 1
                if on_done:
                    try:
                        on_done(done, total)
                    except Exception:
                        pass
        return failures

    def sync_course(self, course: dict, dest_dir: Path) -> dict:
        """Descarga todos los archivos de un curso. Devuelve resumen {downloaded, skipped, errors}."""
        course_name = course.get("name", f"course_{course['id']}")
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", course_name).strip()
        course_dir = dest_dir / safe_name

        stats = {"downloaded": 0, "skipped": 0, "errors": 0}
        files = self.get_all_course_files(course["id"])

        for f in files:
            if f.get("locked_for_user"):
                stats["skipped"] += 1
                continue
            dest = file_dest(course_dir, f)
            if dest.exists():
                stats["skipped"] += 1
                continue
            try:
                self.download_file(f["url"], dest)
                stats["downloaded"] += 1
            except Exception:
                stats["errors"] += 1

        return stats

    def sync_all(self, dest_dir: Path) -> dict:
        """Sincroniza todos los cursos activos. Devuelve {nombre_curso: stats}."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        courses = self.get_courses()
        results = {}
        for course in courses:
            name = course.get("name", str(course["id"]))
            try:
                results[name] = self.sync_course(course, dest_dir)
            except Exception as e:
                results[name] = {"error": str(e)}
        return results
