import os
import json
from datetime import datetime
from pathlib import Path
import ollama
from chatbot.prompts import build_system_prompt
from chatbot.retriever import Retriever
from ingestion.vectorstore import VectorStore

_EXERCISE_KEYWORDS = [
    "ejercicio", "practica", "práctica", "problema", "dame un", "dame otro",
    "nuevo ejercicio", "otro ejercicio", "plantéame", "planteame",
]
_HINT_KEYWORDS = [
    "pista", "no sé por dónde", "no se por donde",
    "no conozco el tema", "no entiendo el tema", "no sé el tema", "no conozco el tema base",
]
_SOLUTION_KEYWORDS = [
    "ver solución", "ver la solución", "dame la solución", "solución paso",
    "paso a paso", "muéstrame la solución", "ver solución paso a paso",
    "mostrar solución", "mostrar la solución",
]
_CORRECT_INDICATORS = [
    "¡correcto", "¡exacto", "¡muy bien", "¡excelente", "respuesta correcta",
    "has llegado", "es correcto", "estás en lo correcto", "está correcto",
    "¡bien hecho", "lograste", "perfectamente", "correcto,", "correcto.",
    "es la respuesta", "¡eso es", "eso es correcto",
]


def _detect_intent(text: str) -> str:
    t = text.lower()
    if any(k in t for k in _SOLUTION_KEYWORDS):
        return "wants_solution"
    if any(k in t for k in _HINT_KEYWORDS):
        return "wants_hint"
    if any(k in t for k in _EXERCISE_KEYWORDS):
        return "wants_exercise"
    return "answering"


def _llm_confirmed_correct(text: str) -> bool:
    t = text.lower()
    return any(ind in t for ind in _CORRECT_INDICATORS)


class AteneoChat:
    def __init__(self):
        self.model = os.environ.get("OLLAMA_MODEL", "llama3.2")
        self.store = VectorStore()
        self.retriever = Retriever(self.store)
        self.history: list[dict] = []
        self.session_log: list[dict] = []
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.exercise_state: str = "idle"
        self.exercise_attempts: int = 0

    def chat(self, user_message: str, course: str = None, unit: str = None, difficulty: str = "practicando") -> dict:
        intent = _detect_intent(user_message)

        # RAG retrieval — filtered by unit if provided
        if unit and course:
            context, sources = self.retriever.get_context_for_unit(user_message, course, unit)
        elif course:
            context, sources = self.retriever.get_context(user_message, course)
        else:
            context, sources = self.retriever.get_context_all(user_message)

        system_prompt = build_system_prompt(self.exercise_state, unit, difficulty)

        if context:
            api_user_content = (
                f"[Material del curso]\n{context}\n\n"
                f"[Mensaje del estudiante]\n{user_message}"
            )
        else:
            api_user_content = user_message

        messages_for_api = (
            [{"role": "system", "content": system_prompt}]
            + self.history
            + [{"role": "user", "content": api_user_content}]
        )

        response = ollama.chat(model=self.model, messages=messages_for_api)
        assistant_message = response["message"]["content"]

        # Store clean history (no RAG context so future queries stay relevant)
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": assistant_message})

        options = self._advance_state(intent, assistant_message)

        self.session_log.append({
            "turn": len(self.session_log) + 1,
            "user": user_message,
            "sources": sources,
            "assistant": assistant_message,
            "state": self.exercise_state,
        })

        return {"text": assistant_message, "options": options}

    def _advance_state(self, intent: str, assistant_message: str) -> list[str] | None:
        """Update exercise state and return quick-reply options for the new state."""
        if intent == "wants_exercise":
            self.exercise_state = "exercise"
            self.exercise_attempts = 0
            return ["Necesito una pista", "No conozco el tema base"]

        if intent == "wants_solution" and self.exercise_state in ("exercise", "guided", "hinted"):
            self.exercise_state = "solved"
            return ["Dame otro ejercicio"]

        if intent == "wants_hint" and self.exercise_state in ("exercise", "guided"):
            self.exercise_state = "hinted"
            return ["Ver solución paso a paso"]

        if self.exercise_state in ("exercise", "guided", "hinted") and intent == "answering":
            self.exercise_attempts += 1
            if _llm_confirmed_correct(assistant_message):
                self.exercise_state = "idle"
                return ["Dame otro ejercicio", "Quiero estudiar otro tema"]
            self.exercise_state = "guided"
            return ["Quiero una pista", "Ver solución paso a paso"]

        if self.exercise_state == "solved" and intent == "wants_exercise":
            self.exercise_state = "exercise"
            self.exercise_attempts = 0
            return ["Necesito una pista", "No conozco el tema base"]

        return None

    def save_session(self, log_dir: Path = Path("logs")) -> Path:
        log_dir.mkdir(exist_ok=True)
        path = log_dir / f"session_{self.session_id}.json"
        path.write_text(json.dumps(self.session_log, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def list_courses(self) -> list[str]:
        return self.store.list_collections()
