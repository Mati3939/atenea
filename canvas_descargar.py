#!/usr/bin/env python3
"""
Descargador de archivos del Canvas LMS.
Baja archivos de tus cursos preservando la estructura, saltando lo ya descargado.
Funciona aunque el curso NO tenga pestaña "Archivos": también recorre los MÓDULOS
(items de tipo File y archivos embebidos en Páginas). Pensado para alimentar el
banco de conocimiento (p. ej. Física, cuyo material vive en Módulos).

USO:
  1. Generá un token en Canvas:
       Cuenta -> Configuración -> Tokens de acceso aprobados -> "+ Nuevo token de acceso"
  2. Configurá (env vars o canvas_config.json en esta carpeta):
       CANVAS_BASE_URL = https://TU-INSTITUCION.instructure.com   (sin / final)
       CANVAS_TOKEN    = tu_token
  3. Listar cursos:        python canvas_descargar.py --listar
     Descargar uno:        python canvas_descargar.py --curso 12345
     Descargar varios:     python canvas_descargar.py --curso 12345 67890
     Descargar todos:      python canvas_descargar.py --todos
     Filtrar por nombre:   python canvas_descargar.py --todos --filtro fisica
     Solo módulos:         python canvas_descargar.py --curso 12345 --solo-modulos
     Solo archivos:        python canvas_descargar.py --curso 12345 --solo-archivos

Por defecto descarga de AMBAS fuentes (archivos + módulos) sin duplicar.
Salida: ../Archivos oficiales/Canvas (descarga)/<Curso>/<carpetas>/...
"""
import os, sys, json, time, argparse, re
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Falta 'requests'. Instalá con:  pip install requests")

# La consola de Windows (cp1252) no puede imprimir acentos ni símbolos:
# forzamos UTF-8 en la salida para evitar UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "canvas_config.json"
DEFAULT_DEST = HERE.parent / "Archivos oficiales" / "Canvas (descarga)"


def cargar_config():
    base = os.environ.get("CANVAS_BASE_URL")
    token = os.environ.get("CANVAS_TOKEN")
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        base = base or cfg.get("base_url")
        token = token or cfg.get("token")
    if not base or not token:
        sys.exit(
            "Falta configuración. Definí CANVAS_BASE_URL y CANVAS_TOKEN como variables\n"
            f"de entorno, o creá {CONFIG_FILE} con:\n"
            '  {"base_url": "https://tu-institucion.instructure.com", "token": "xxxx"}'
        )
    return base.rstrip("/"), token


def limpiar(nombre):
    return re.sub(r'[<>:"/\\|?*]', "_", str(nombre)).strip() or "sin_nombre"


class Canvas:
    def __init__(self, base, token):
        self.base = base
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    # ---- HTTP con paginación y reintento por throttling ----
    def _paginar(self, url, params=None):
        params = dict(params or {})
        params.setdefault("per_page", 100)
        items = []
        while url:
            for intento in range(4):
                r = self.s.get(url, params=params)
                if r.status_code == 403 and "throttle" in r.text.lower():
                    time.sleep(2 * (intento + 1)); continue
                r.raise_for_status(); break
            else:
                r.raise_for_status()
            data = r.json()
            items.extend(data if isinstance(data, list) else [data])
            url, params = self._siguiente(r), None
        return items

    @staticmethod
    def _siguiente(r):
        for parte in r.headers.get("Link", "").split(","):
            m = re.search(r'<([^>]+)>;\s*rel="next"', parte)
            if m:
                return m.group(1)
        return None

    def _get_json(self, path):
        r = self.s.get(f"{self.base}{path}")
        r.raise_for_status()
        return r.json()

    # ---- Endpoints ----
    def cursos(self):
        return self._paginar(f"{self.base}/api/v1/courses",
                             {"enrollment_state": "active", "include[]": "term"})

    def carpetas(self, cid):
        fs = self._paginar(f"{self.base}/api/v1/courses/{cid}/folders")
        return {f["id"]: f.get("full_name", "files") for f in fs}

    def archivos(self, cid):
        return self._paginar(f"{self.base}/api/v1/courses/{cid}/files")

    def modulos(self, cid):
        return self._paginar(f"{self.base}/api/v1/courses/{cid}/modules")

    def items_modulo(self, cid, mid):
        return self._paginar(f"{self.base}/api/v1/courses/{cid}/modules/{mid}/items",
                             {"include[]": "content_details"})

    def archivo(self, cid, fid):
        return self._get_json(f"/api/v1/courses/{cid}/files/{fid}")

    def pagina(self, cid, page_url):
        return self._get_json(f"/api/v1/courses/{cid}/pages/{page_url}")


def bajar(api, url, destino, size=-1):
    """Descarga una URL a destino. Devuelve True si bajó, False si ya existía."""
    if destino.exists() and size >= 0 and destino.stat().st_size == size:
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    with api.s.get(url, stream=True) as r:
        r.raise_for_status()
        with open(destino, "wb") as fh:
            for chunk in r.iter_content(8192):
                fh.write(chunk)
    return True


def descargar_por_archivos(api, cid, cname, dest_root, vistos):
    bajados = saltados = 0
    try:
        rutas = api.carpetas(cid)
        archivos = api.archivos(cid)
    except requests.HTTPError:
        print("  (sin pestaña Archivos accesible)")
        return 0, 0
    for a in archivos:
        vistos.add(a["id"])
        carpeta = re.sub(r"^course files/?", "", rutas.get(a.get("folder_id"), "files"))
        subdir = dest_root / cname / Path(*[limpiar(p) for p in carpeta.split("/") if p])
        destino = subdir / limpiar(a.get("display_name", a["filename"]))
        try:
            if bajar(api, a["url"], destino, a.get("size", -1)):
                bajados += 1; print(f"  ↓ [archivos] {destino.relative_to(dest_root)}")
            else:
                saltados += 1
        except Exception as e:
            print(f"  ✗ {a.get('display_name')}: {e}")
    return bajados, saltados


def descargar_por_modulos(api, cid, cname, dest_root, vistos):
    bajados = saltados = 0
    try:
        mods = api.modulos(cid)
    except requests.HTTPError:
        print("  (sin Módulos accesibles)")
        return 0, 0
    for m in mods:
        mname = limpiar(m.get("name", "modulo"))
        base_dir = dest_root / cname / "Módulos" / mname
        try:
            items = api.items_modulo(cid, m["id"])
        except requests.HTTPError:
            continue
        for it in items:
            tipo = it.get("type")
            try:
                if tipo == "File":
                    fid = it.get("content_id")
                    if fid in vistos:
                        continue
                    cd = it.get("content_details") or {}
                    url = cd.get("url")
                    if not url:  # fallback: pedir metadata del archivo
                        meta = api.archivo(cid, fid); url = meta["url"]; cd = meta
                    name = limpiar(cd.get("display_name") or it.get("title"))
                    if bajar(api, url, base_dir / name, cd.get("size", -1)):
                        bajados += 1; print(f"  ↓ [módulo:{mname}] {name}")
                    else:
                        saltados += 1
                    vistos.add(fid)
                elif tipo == "Page":
                    # archivos embebidos dentro del cuerpo de la página
                    try:
                        body = api.pagina(cid, it["page_url"]).get("body") or ""
                    except (requests.HTTPError, KeyError):
                        continue
                    for fid in {int(x) for x in re.findall(r"/files/(\d+)", body)}:
                        if fid in vistos:
                            continue
                        try:
                            meta = api.archivo(cid, fid)
                        except requests.HTTPError:
                            continue
                        name = limpiar(meta.get("display_name", meta.get("filename")))
                        if bajar(api, meta["url"], base_dir / name, meta.get("size", -1)):
                            bajados += 1; print(f"  ↓ [página:{mname}] {name}")
                        else:
                            saltados += 1
                        vistos.add(fid)
            except Exception as e:
                print(f"  ✗ item '{it.get('title')}': {e}")
    return bajados, saltados


def descargar_curso(api, curso, dest_root, modo):
    cid = curso["id"]
    cname = limpiar(curso.get("name", f"curso_{cid}"))
    print(f"\n=== {cname} (id {cid}) ===")
    vistos = set()  # file ids ya descargados, para no duplicar entre fuentes
    n = s = 0
    if modo in ("ambos", "archivos"):
        a, b = descargar_por_archivos(api, cid, cname, dest_root, vistos); n += a; s += b
    if modo in ("ambos", "modulos"):
        a, b = descargar_por_modulos(api, cid, cname, dest_root, vistos); n += a; s += b
    print(f"  → {n} nuevos, {s} ya existían.")
    return n, s


def main():
    ap = argparse.ArgumentParser(description="Descargador de Canvas LMS (archivos + módulos)")
    ap.add_argument("--listar", action="store_true", help="lista tus cursos y sale")
    ap.add_argument("--curso", nargs="+", type=int, help="IDs de curso a descargar")
    ap.add_argument("--todos", action="store_true", help="descarga todos los cursos activos")
    ap.add_argument("--filtro", help="solo cursos cuyo nombre contenga este texto")
    ap.add_argument("--solo-modulos", action="store_true", help="bajar solo desde Módulos")
    ap.add_argument("--solo-archivos", action="store_true", help="bajar solo desde la pestaña Archivos")
    ap.add_argument("--dest", default=str(DEFAULT_DEST), help="carpeta destino")
    args = ap.parse_args()

    modo = "ambos"
    if args.solo_modulos:
        modo = "modulos"
    elif args.solo_archivos:
        modo = "archivos"

    base, token = cargar_config()
    api = Canvas(base, token)
    cursos = api.cursos()

    if args.filtro:
        f = args.filtro.lower()
        cursos = [c for c in cursos if f in c.get("name", "").lower()]

    if args.listar or not (args.curso or args.todos):
        print("Cursos disponibles:")
        for c in cursos:
            term = (c.get("term") or {}).get("name", "")
            print(f"  {c['id']:>8}  {c.get('name','(sin nombre)')}  [{term}]")
        if not (args.curso or args.todos):
            print("\nUsá --curso <id> o --todos para descargar.")
        return

    if args.curso:
        ids = set(args.curso)
        cursos = [c for c in cursos if c["id"] in ids]

    dest = Path(args.dest)
    tn = ts = 0
    for c in cursos:
        n, s = descargar_curso(api, c, dest, modo)
        tn += n; ts += s
    print(f"\nTERMINADO: {tn} archivos nuevos, {ts} ya estaban. Destino: {dest}")


if __name__ == "__main__":
    main()
