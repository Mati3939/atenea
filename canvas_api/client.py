import re
import requests
from pathlib import Path


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

    def download_file(self, file_url: str, dest_path: Path) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.session.get(file_url, stream=True) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

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
