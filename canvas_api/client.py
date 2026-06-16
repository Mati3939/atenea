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
        files = self.get_course_files(course["id"])

        for f in files:
            if f.get("locked_for_user"):
                stats["skipped"] += 1
                continue
            dest = course_dir / f["filename"]
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
