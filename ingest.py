from pathlib import Path
from ingestion.parsers import parse, SUPPORTED
from ingestion.chunker import chunk
from ingestion.vectorstore import VectorStore

DATA_DIR = Path("data")
store = VectorStore()


def ingest_course(course_dir: Path) -> None:
    course_name = course_dir.name
    # Ignorar archivos AppleDouble de macOS ("._foo.pdf") y carpetas __MACOSX que
    # vienen dentro de ZIPs: no son documentos reales y fallan al parsear.
    files = [f for f in course_dir.rglob("*")
             if f.suffix.lower() in SUPPORTED
             and not f.name.startswith("._")
             and "__MACOSX" not in f.parts]

    if not files:
        print(f"  {course_name}: sin archivos soportados, omitido")
        return

    total_chunks = 0
    for file in files:
        try:
            text = parse(file)
            if not text.strip():
                continue
            chunks = chunk(text)
            metadata = [
                {"course": course_name, "file": file.name, "path": str(file)}
                for _ in chunks
            ]
            store.add_chunks(course_name, chunks, metadata)
            total_chunks += len(chunks)
        except Exception as e:
            print(f"    [ERROR] {file.name}: {e}")

    print(f"  {course_name}: {len(files)} archivos → {total_chunks} chunks indexados")


if __name__ == "__main__":
    if not DATA_DIR.exists():
        raise SystemExit(f"No existe la carpeta '{DATA_DIR}'. Ejecuta primero: python sync.py")

    print("Iniciando ingesta de documentos...\n")
    course_dirs = sorted(d for d in DATA_DIR.iterdir() if d.is_dir())

    if not course_dirs:
        raise SystemExit("La carpeta 'data/' está vacía. Ejecuta primero: python sync.py")

    for course_dir in course_dirs:
        ingest_course(course_dir)

    print(f"\nIngesta completa. Colecciones en ChromaDB: {store.list_collections()}")
