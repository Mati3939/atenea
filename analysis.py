import os
import json
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv
import ollama

load_dotenv()

LOGS_DIR = Path("logs")


def load_all_interactions() -> list[dict]:
    interactions = []
    for f in sorted(LOGS_DIR.glob("session_*.json")):
        interactions.extend(json.loads(f.read_text(encoding="utf-8")))
    return interactions


def build_summary(interactions: list[dict]) -> str:
    questions = [i["user"] for i in interactions]
    topic_counter: Counter = Counter()
    for i in interactions:
        for source in i.get("sources", []):
            topic_counter[source.get("file", "desconocido")] += 1

    lines = ["Preguntas realizadas por el estudiante:"]
    for q in questions:
        lines.append(f"  - {q}")

    if topic_counter:
        lines.append("\nArchivos del curso más consultados:")
        for fname, count in topic_counter.most_common(10):
            lines.append(f"  - {fname}: {count} veces")

    return "\n".join(lines)


def generate_report() -> str:
    """Genera y devuelve el reporte de debilidades de estudio como texto."""
    sessions = list(LOGS_DIR.glob("session_*.json")) if LOGS_DIR.exists() else []
    if not sessions:
        raise ValueError("No hay sesiones guardadas. Usa el chat primero.")

    interactions = load_all_interactions()
    summary = build_summary(interactions)

    model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    prompt = f"""Eres un experto en pedagogía y métodos de estudio. Analiza el siguiente registro de un estudiante universitario:

{summary}

Con base en este registro:
1. Identifica los temas o conceptos donde el estudiante muestra más dudas recurrentes.
2. Detecta posibles debilidades en su método de estudio (ej: salta conceptos base, repite las mismas preguntas, estudia siempre los mismos archivos ignorando otros, etc.).
3. Proporciona 3 a 5 sugerencias concretas y personalizadas para mejorar su método de estudio.

Sé específico, constructivo y breve."""

    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    report = response["message"]["content"]
    Path("reporte_estudio.txt").write_text(report, encoding="utf-8")
    return report


def analyze():
    report = generate_report()
    interactions = load_all_interactions()
    print("=" * 45)
    print("  Reporte de Metodo de Estudio - Atenea")
    print("=" * 45)
    print(f"\nSesiones analizadas: {len(list(LOGS_DIR.glob('session_*.json')))}")
    print(f"Preguntas totales: {len(interactions)}\n")
    print(report)
    print(f"\nReporte guardado en: reporte_estudio.txt")


if __name__ == "__main__":
    analyze()
