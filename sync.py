import os
from pathlib import Path
from dotenv import load_dotenv
from canvas_api import CanvasClient

load_dotenv()

CANVAS_URL = os.environ.get("CANVAS_URL")
CANVAS_TOKEN = os.environ.get("CANVAS_TOKEN")

if not CANVAS_URL or not CANVAS_TOKEN:
    raise SystemExit("Falta CANVAS_URL o CANVAS_TOKEN en el archivo .env")

DATA_DIR = Path("data")
client = CanvasClient(CANVAS_URL, CANVAS_TOKEN)

print("Obteniendo cursos activos...")
results = client.sync_all(DATA_DIR)

print()
total_downloaded = 0
for course_name, stats in results.items():
    if "error" in stats:
        print(f"  [ERROR] {course_name}: {stats['error']}")
    else:
        d, s, e = stats["downloaded"], stats["skipped"], stats["errors"]
        total_downloaded += d
        print(f"  {course_name}: {d} descargados, {s} omitidos" + (f", {e} errores" if e else ""))

print(f"\nTotal descargado: {total_downloaded} archivos → {DATA_DIR}/")
