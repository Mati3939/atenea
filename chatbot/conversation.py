import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Generator
from chatbot import llm
from chatbot.prompts import build_system_prompt
from chatbot.latex import normalize_latex
from chatbot.retriever import Retriever
from ingestion.vectorstore import VectorStore

# Máximo de mensajes (user+assistant) enviados al modelo. Acotado también para no
# gastar el cupo de tokens-por-minuto de Groq (free tier: 12k TPM en el 70B).
MAX_HISTORY_MESSAGES = 8

WEB_SESSIONS_DIR = Path("logs") / "web_sessions"

# ── Detección de intención (regex con límites de palabra) ─────────────────────

_EXERCISE_RE = re.compile(
    r"(?:dame|quiero|hazme|haz|gener\w+|plante\w+|propon\w+|p[oó]nme|mu[eé]str\w+|muestr\w+"
    r"|ens[eé]ñ\w+|empecemos con|otro|nuevo|ver)"
    r"\s+(?:un[ao]?\s+)?(?:ejercicio|problema|pr[áa]ctica|desaf[íi]o|ejemplo|pregunta de pr[áa]ctica)"
    r"|^\s*(?:ejercicio|otro|ejemplo)\s*[.!]?\s*$"
    r"|ejercicio m[áa]s dif[íi]cil",
    re.IGNORECASE,
)
_HINT_RE = re.compile(
    r"\bpista\b"
    r"|no s[ée] (?:por d[óo]nde|c[óo]mo) (?:empezar|partir|seguir|resolver)"
    r"|no (?:conozco|entiendo|s[ée]|manejo|domino) el tema",
    re.IGNORECASE,
)
_SOLUTION_RE = re.compile(
    r"(?:ver|dame|mu[ée]str\w+|mostrar|ens[ée]ñ\w+|quiero ver)\s+(?:la\s+)?soluci[óo]n"
    r"|soluci[óo]n paso|paso a paso|me rindo",
    re.IGNORECASE,
)
_TOPIC_RE = re.compile(
    r"(?:ejercicio|problema|pr[áa]ctica|desaf[íi]o)\s+(?:de|sobre|del|acerca de)\s+(.{3,100})",
    re.IGNORECASE,
)
# Palabras que no son un tema real (aparecen tras 'ejercicio de ...')
_JUNK_TOPICS = {"ejemplo", "ejemplos", "práctica", "practica", "eso", "esto", "aplicación", "aplicacion"}

# ── Detección de respuesta correcta ───────────────────────────────────────────

_CORRECT_MARKER = re.compile(r"\[\[\s*CORRECTO\s*\]\]", re.IGNORECASE)
# Solo felicitaciones inequívocas: frases como "la respuesta correcta es..."
# aparecen también cuando el estudiante FALLA, así que no sirven de señal.
_CORRECT_INDICATORS = [
    "¡correcto", "¡exacto", "¡muy bien", "¡excelente", "¡perfecto",
    "¡bien hecho", "¡eso es", "estás en lo correcto", "lo lograste",
    "tu respuesta es correcta", "tu resultado es correcto",
]


# ── Inyección de prompt (patrones claros de override) ─────────────────────────
# Defensa determinista: ante un intento evidente de cambiar el rol/reglas, se
# responde con un redireccionamiento fijo sin llamar al LLM. El prompt de sistema
# cubre los casos novedosos; esto garantiza los patrones comunes.
_INJECTION_RE = re.compile(
    r"ignora(?:r)?\s+(?:de\s+)?(?:(?:tus|las|mis|estas)\s+)?(?:instrucciones|reglas|lo\s+anterior|el\s+prompt|todo)"
    r"|olv[íi]da(?:r|te|se)?\s+(?:de\s+)?(?:(?:tus|las|mis)\s+)?(?:instrucciones|reglas|lo\s+anterior|todo\s+lo\s+anterior)"
    r"|responde\s+(?:solo|s[óo]lo|[úu]nicamente|nada\s+m[áa]s)\s+(?:con|la\s+palabra|el\s+texto|\"|')"
    r"|\bsystem\s*:|\bnuevas?\s+instrucciones\b|\bdeveloper\s+mode\b",
    re.IGNORECASE,
)


def _is_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text or ""))


def _detect_intent(text: str) -> str:
    if _SOLUTION_RE.search(text):
        return "wants_solution"
    if _HINT_RE.search(text):
        return "wants_hint"
    if _EXERCISE_RE.search(text):
        return "wants_exercise"
    return "answering"


def _is_correct(assistant_message: str) -> bool:
    if _CORRECT_MARKER.search(assistant_message):
        return True
    t = assistant_message.lower()
    return any(ind in t for ind in _CORRECT_INDICATORS)


class AteneoChat:
    def __init__(self):
        self.store = VectorStore()
        self.retriever = Retriever(self.store)
        self.history: list[dict] = []
        self.session_log: list[dict] = []
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.exercise_state: str = "idle"
        self.exercise_attempts: int = 0
        self.current_exercise: str = ""   # enunciado del último ejercicio planteado
        self.current_topic: str = ""      # tema activo extraído de los mensajes
        self._last_mode: str | None = None  # último modo enviado (estudiar/ejercitar/preguntar)
        self._method: str | None = None   # método de estudio activo (key de study_methods)

    # ── Flujo principal ───────────────────────────────────────────────────────

    def chat(self, user_message: str, course: str = None, unit: str = None,
             difficulty: str = "practicando", mode: str = None, method: str = None) -> dict:
        if _is_injection(user_message):
            return self._injection_reply(user_message)
        intent, sources, messages = self._prepare(user_message, course, unit, difficulty, mode, method)
        raw = llm.complete(messages)
        return self._finalize(user_message, intent, sources, raw)

    def chat_stream(self, user_message: str, course: str = None, unit: str = None,
                    difficulty: str = "practicando", mode: str = None,
                    method: str = None) -> Generator[dict, None, None]:
        """Genera eventos {'delta': str} durante el streaming y un evento final
        {'done': True, 'text': str, 'options': list|None} con el texto normalizado."""
        if _is_injection(user_message):
            result = self._injection_reply(user_message)
            yield {"delta": result["text"]}
            yield {"done": True, "text": result["text"], "options": result["options"]}
            return
        intent, sources, messages = self._prepare(user_message, course, unit, difficulty, mode, method)
        raw = ""
        for delta in llm.stream(messages):
            raw += delta
            yield {"delta": delta}
        result = self._finalize(user_message, intent, sources, raw)
        yield {"done": True, "text": result["text"], "options": result["options"]}

    def _injection_reply(self, user_message: str) -> dict:
        """Respuesta fija ante un intento de manipulación; no llama al LLM ni cambia el
        estado del ejercicio, pero registra el turno y ofrece opciones por modo."""
        text = ("Estoy aquí para ayudarte a estudiar como tu tutora 😊. Sigamos con el "
                "material del curso: ¿por dónde quieres continuar?")
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": text})
        options = self._advance_state("answering", False, text)
        self.session_log.append({
            "turn": len(self.session_log) + 1, "user": user_message,
            "sources": [], "assistant": text, "state": self.exercise_state,
        })
        return {"text": text, "options": options}

    # ── Preparación y cierre de cada turno ───────────────────────────────────

    def _prepare(self, user_message: str, course: str | None, unit: str | None,
                 difficulty: str, mode: str | None, method: str | None = None):
        if mode is not None:
            self._last_mode = mode
        if method is not None:
            self._method = method
        intent = _detect_intent(user_message)

        # No se adjunta material crudo en dos situaciones (y de paso se ahorra el embedding):
        #  1. Al PEDIR un ejercicio: el modelo genera mejor desde el tema y no copia pautas.
        #  2. Durante un ejercicio ACTIVO (intento / pista / solución): el tutor razona sobre
        #     el enunciado ya planteado; recuperar más material arriesga ecoar solucionarios
        #     con matemática rota del PDF (fuga de solución + LaTeX malo).
        in_active_exercise = self.exercise_state in ("exercise", "guided", "hinted")
        if intent == "wants_exercise" or in_active_exercise:
            # Aun así dejamos que _rag_query capture el tema del mensaje (efecto lateral).
            self._rag_query(user_message, intent, unit)
            context, sources = "", []
        else:
            rag_query = self._rag_query(user_message, intent, unit)
            if unit and course:
                context, sources = self.retriever.get_context_for_unit(rag_query, course, unit)
            elif course:
                context, sources = self.retriever.get_context(rag_query, course)
            else:
                context, sources = self.retriever.get_context_all(rag_query)

        system_prompt = build_system_prompt(self.exercise_state, unit, difficulty,
                                             mode, self._method)

        messages = [{"role": "system", "content": system_prompt}]
        # El material va como referencia de SISTEMA (no como parte del mensaje del
        # estudiante): así el modelo lo usa de fuente pero no lo "continúa" ni copia.
        # Para ejercicios NO se adjunta: el modelo tiende a copiar las pautas
        # (con soluciones y matemática rota del PDF). Genera mejor ejercicios desde
        # el tema de la unidad, que igual proviene del curso (calendarización).
        if context:
            messages.append({
                "role": "system",
                "content": (
                    "MATERIAL DEL CURSO (solo referencia interna). Úsalo para fundamentar "
                    "tu respuesta, pero NO lo copies ni lo repitas; reescribe con tu propio "
                    "LaTeX correcto. Puede tener matemática mal extraída del PDF.\n\n" + context
                ),
            })
        messages += self.history[-MAX_HISTORY_MESSAGES:]
        # Para ejercicios, una directiva explícita evita que el modelo copie/continúe
        # las pautas del material (que traen soluciones y matemática rota del PDF).
        if intent == "wants_exercise":
            topic = self.current_topic or unit or "el tema en estudio"
            api_user = (
                f"Plantéame UN ejercicio nuevo sobre {topic}, del mismo tipo y nivel que el "
                "material del curso. Escribe SOLO el enunciado, redactado por ti con LaTeX "
                "correcto entre signos de dólar. No copies texto del material, no incluyas "
                "solución ni pistas, y termina con '¿Cuál es tu enfoque para resolverlo?'."
            )
        else:
            api_user = user_message
        messages += [{"role": "user", "content": api_user}]
        return intent, sources, messages

    def _finalize(self, user_message: str, intent: str, sources: list, raw: str) -> dict:
        correct = _is_correct(raw)
        text = _CORRECT_MARKER.sub("", raw).strip()
        # Modelos pequeños a veces emiten entidades HTML (&#39;, &amp;...);
        # el frontend escapa todo de nuevo, así que desescapar aquí es seguro.
        text = html.unescape(text)
        text = normalize_latex(text)

        # Garantía determinista: un enunciado de ejercicio debe invitar a responder.
        # El modelo a veces omite la pregunta de cierre pedida en el prompt.
        if intent == "wants_exercise" and "?" not in text:
            text = text.rstrip() + "\n\n¿Cuál es tu enfoque para resolverlo?"

        # Historial limpio: sin contexto RAG ni etiqueta de control
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": text})

        options = self._advance_state(intent, correct, text)

        self.session_log.append({
            "turn": len(self.session_log) + 1,
            "user": user_message,
            "sources": sources,
            "assistant": text,
            "state": self.exercise_state,
        })

        return {"text": text, "options": options}

    # ── Query RAG inteligente ─────────────────────────────────────────────────

    def _rag_query(self, user_message: str, intent: str, unit: str | None) -> str:
        """Cuando el mensaje es una orden corta ('dame un ejercicio', 'pista'),
        buscar con el tema o el enunciado activo recupera material útil; la frase
        literal no."""
        if intent == "wants_exercise":
            m = _TOPIC_RE.search(user_message)
            if m:
                cand = m.group(1).strip(" ?¿!¡.,")
                # Evitar temas basura ('ejercicio de ejemplo' → 'ejemplo')
                if len(cand) > 3 and cand.lower() not in _JUNK_TOPICS:
                    self.current_topic = cand
            topic = self.current_topic or ""
            # Siempre anclar a la unidad activa para no salir de su tema
            if unit and unit.lower() not in topic.lower():
                topic = f"{topic} {unit}".strip()
            if topic:
                return f"ejercicios, ejemplos y fórmulas de {topic}"
            return self.current_exercise[:400] or user_message

        if intent in ("wants_hint", "wants_solution") and self.current_exercise:
            return self.current_exercise[:400]

        # Respuestas muy cortas ('no sé', '42') no sirven como query semántica
        if len(user_message.split()) <= 4 and self.current_exercise:
            return self.current_exercise[:400]

        # Con unidad activa, sesgar la búsqueda hacia ese tema para que el material
        # recuperado sea de la unidad elegida (y no genérico del curso).
        if unit:
            return f"{user_message} {unit}"

        return user_message

    # ── Máquina de estados del ejercicio ──────────────────────────────────────

    def _advance_state(self, intent: str, correct: bool, assistant_text: str) -> list[str] | None:
        if intent == "wants_exercise":
            self.exercise_state = "exercise"
            self.exercise_attempts = 0
            self.current_exercise = assistant_text
            return ["Necesito una pista", "No conozco el tema base"]

        if intent == "wants_solution" and self.exercise_state in ("exercise", "guided", "hinted"):
            self.exercise_state = "solved"
            return ["Dame otro ejercicio"]

        if intent == "wants_hint" and self.exercise_state in ("exercise", "guided"):
            self.exercise_state = "hinted"
            return ["Ver solución paso a paso"]

        if self.exercise_state in ("exercise", "guided", "hinted") and intent == "answering":
            self.exercise_attempts += 1
            if correct:
                self.exercise_state = "idle"
                return ["Dame otro ejercicio", "Quiero estudiar otro tema"]
            self.exercise_state = "guided"
            return ["Quiero una pista", "Ver solución paso a paso"]

        # Default options when no exercise state applies — cheap heuristic, no LLM call
        mode = getattr(self, "_last_mode", None)
        if mode == "ejercitar":
            return ["Dame otro ejercicio", "Necesito una pista", "Ver solución paso a paso"]
        if mode == "estudiar":
            return ["Dame un ejercicio", "Explícame con un ejemplo", "Profundiza en esto"]
        # preguntar or None
        return ["Tengo otra duda", "Explícame más simple", "Dame un ejemplo"]

    # ── Persistencia ──────────────────────────────────────────────────────────

    def save_session(self, log_dir: Path = Path("logs")) -> Path:
        log_dir.mkdir(exist_ok=True)
        path = log_dir / f"session_{self.session_id}.json"
        path.write_text(json.dumps(self.session_log, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_state(self, web_session_id: str) -> None:
        """Persiste el estado completo para sobrevivir reinicios del servidor."""
        WEB_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "session_id": self.session_id,
            "history": self.history,
            "session_log": self.session_log,
            "exercise_state": self.exercise_state,
            "exercise_attempts": self.exercise_attempts,
            "current_exercise": self.current_exercise,
            "current_topic": self.current_topic,
            "last_mode": self._last_mode,
            "method": self._method,
        }
        path = WEB_SESSIONS_DIR / f"{web_session_id}.json"
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def restore(cls, web_session_id: str) -> "AteneoChat | None":
        path = WEB_SESSIONS_DIR / f"{web_session_id}.json"
        if not path.exists():
            return None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            obj = cls()
            obj.session_id = state.get("session_id", obj.session_id)
            obj.history = state.get("history", [])
            obj.session_log = state.get("session_log", [])
            obj.exercise_state = state.get("exercise_state", "idle")
            obj.exercise_attempts = state.get("exercise_attempts", 0)
            obj.current_exercise = state.get("current_exercise", "")
            obj.current_topic = state.get("current_topic", "")
            obj._last_mode = state.get("last_mode", None)
            obj._method = state.get("method", None)
            return obj
        except Exception:
            return None

    @staticmethod
    def delete_state(web_session_id: str) -> None:
        path = WEB_SESSIONS_DIR / f"{web_session_id}.json"
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    def list_courses(self) -> list[str]:
        return self.store.list_collections()
