import os
import json
from pathlib import Path
from dotenv import load_dotenv
import ollama

load_dotenv()

LOGS_DIR = Path("logs")

# Quick-reply buttons are meta-actions, not real study content
_QUICK_REPLIES = {
    "Necesito una pista", "No conozco el tema base", "Ver solución paso a paso",
    "Quiero una pista", "Dame otro ejercicio", "Quiero estudiar otro tema",
    "Dame un ejercicio", "Explícame el tema", "Lo intento con la pista",
    "Lo intento de nuevo",
}


def load_all_interactions() -> list[dict]:
    interactions = []
    for f in sorted(LOGS_DIR.glob("session_*.json")):
        interactions.extend(json.loads(f.read_text(encoding="utf-8")))
    return interactions


def build_summary(interactions: list[dict]) -> str:
    """Build a structured summary focused on actual learning performance."""

    # Filter real content turns (exclude quick-reply button presses)
    content_turns = [t for t in interactions if t["user"].strip() not in _QUICK_REPLIES]

    # Reconstruct exercise sessions from state transitions
    sessions = []
    current = None

    for turn in interactions:
        state = turn.get("state", "idle")
        user = turn["user"].strip()

        if state == "exercise":
            if current:
                sessions.append(current)
            topic = user if user not in _QUICK_REPLIES else "(ejercicio solicitado con botón)"
            current = {"topic": topic, "attempts": 0, "hint": False, "solution": False}

        elif state == "guided" and current:
            current["attempts"] += 1

        elif state == "hinted" and current:
            current["hint"] = True

        elif state == "solved" and current:
            current["solution"] = True
            sessions.append(current)
            current = None

        elif state == "idle" and current:
            sessions.append(current)
            current = None

    if current:
        sessions.append(current)

    lines = []

    if content_turns:
        lines.append("MENSAJES REALES DEL ESTUDIANTE (excluyendo botones de acción):")
        for t in content_turns[:25]:
            lines.append(f"  [{t.get('state', '?')}] {t['user'][:200]}")

    if sessions:
        lines.append("\nRENDIMIENTO POR EJERCICIO:")
        for s in sessions:
            if s["solution"]:
                outcome = f"necesitó ver la solución completa — intentos fallidos: {s['attempts']}"
            elif s["hint"]:
                outcome = f"necesitó una pista, luego llegó solo — intentos: {s['attempts']}"
            elif s["attempts"] > 0:
                outcome = f"se equivocó {s['attempts']} vez(ces) pero lo resolvió solo"
            else:
                outcome = "resolvió al primer intento ✓"
            lines.append(f"  - Tema: {s['topic'][:120]}")
            lines.append(f"    Resultado: {outcome}")

    guided = sum(1 for t in interactions if t.get("state") == "guided")
    solved = sum(1 for t in interactions if t.get("state") == "solved")

    lines.append("\nESTADÍSTICAS GLOBALES:")
    lines.append(f"  - Intercambios totales: {len(interactions)}")
    lines.append(f"  - Ejercicios registrados: {len(sessions)}")
    lines.append(f"  - Turnos atascado (necesitó guía): {guided}")
    lines.append(f"  - Ejercicios donde pidió la solución: {solved}")

    return "\n".join(lines)


def generate_report() -> str:
    sessions = list(LOGS_DIR.glob("session_*.json")) if LOGS_DIR.exists() else []
    if not sessions:
        raise ValueError("No hay sesiones guardadas. Usa el chat primero.")

    interactions = load_all_interactions()
    summary = build_summary(interactions)

    model = os.environ.get("OLLAMA_MODEL", "llama3.2")

    prompt = f"""Eres un tutor universitario analizando el progreso de un estudiante.

Datos de sus sesiones de estudio:

{summary}

Genera un informe ESPECÍFICO Y CONCRETO. Habla directamente al estudiante usando "tú". NO uses lenguaje vago como "podría beneficiarse de...".

Estructura tu respuesta EXACTAMENTE con estas secciones:

**DONDE TIENES DIFICULTADES**
Lista los temas concretos donde te equivocaste, necesitaste pistas o la solución. Si no hay suficientes datos, dilo honestamente.

**PATRON DE ESTUDIO DETECTADO**
¿Abandonas rápido o insistes? ¿Intentas antes de pedir ayuda? ¿Hay temas que evitas? Sé directo.

**RECOMENDACIONES**
Máximo 4 acciones concretas que puedes hacer hoy. Empieza cada una con un verbo de acción."""

    response = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
    report = response["message"]["content"]
    Path("reporte_estudio.txt").write_text(report, encoding="utf-8")
    return report


def analyze():
    report = generate_report()
    interactions = load_all_interactions()
    sessions = list(LOGS_DIR.glob("session_*.json"))
    print("=" * 45)
    print("  Reporte de Metodo de Estudio - Atenea")
    print("=" * 45)
    print(f"\nSesiones analizadas: {len(sessions)}")
    print(f"Intercambios totales: {len(interactions)}\n")
    print(report)
    print(f"\nReporte guardado en: reporte_estudio.txt")


if __name__ == "__main__":
    analyze()
