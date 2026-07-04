import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Generator
from chatbot import llm
from chatbot.prompts import build_system_prompt, _is_review_unit
from chatbot.latex import normalize_latex
from chatbot.retriever import Retriever
from ingestion.vectorstore import VectorStore

# Máximo de mensajes (user+assistant) enviados al modelo. Acotado también para no
# gastar el cupo de tokens-por-minuto de Groq (free tier: 12k TPM en el 70B).
MAX_HISTORY_MESSAGES = 8

# Turnos (mensajes user/assistant) guardados en `transcript` para repintar el
# historial de sesiones en la UI (ver save_state).
TRANSCRIPT_MAX_TURNS = 60

WEB_SESSIONS_DIR = Path("logs") / "web_sessions"

DATA_DIR = Path("data")


def _safe_name(name: str) -> str:
    """Misma lógica que VectorStore._safe_name / web.main._safe_name (replicada
    para no crear un import circular con la web)."""
    s = re.sub(r"[^a-zA-Z0-9_\-.]", "_", name)[:63]
    s = re.sub(r"^[^a-zA-Z0-9]+", "", s)
    s = re.sub(r"[^a-zA-Z0-9]+$", "", s)
    return s.ljust(3, "0") if s else "col"


def _course_label(course_safe: str | None) -> str | None:
    """Nombre legible del curso: mapea el safe_name que usa el chat a la carpeta
    real en data/ (p. ej. 'LGEBRA_LINEAL' → 'ÁLGEBRA LINEAL')."""
    if not course_safe:
        return None
    if "/" in course_safe or "\\" in course_safe or ".." in course_safe:
        return None
    try:
        if (DATA_DIR / course_safe).is_dir():
            return course_safe
        for d in DATA_DIR.iterdir():
            if d.is_dir() and _safe_name(d.name) == course_safe:
                return d.name
    except OSError:
        pass
    return None


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


# ── Agendado por lenguaje natural (detección conservadora) ────────────────────
# Solo dispara si hay un anuncio de evaluación PROPIO ("tengo...") Y una mención
# temporal explícita; así "no entiendo el control de flujo" o "¿qué es un examen
# de hipótesis?" (sin "tengo" ni fecha) nunca matchean.
_AGENDA_RE = re.compile(
    r"tengo\s+(?:un[ao]?\s+)?(?:control|certamen|examen|prueba|test|tarea)\b.{0,60}?"
    r"(?:"
    r"en\s+\d+\s+(?:d[ií]as?|semanas?)"
    r"|en\s+un[ao]\s+(?:d[ií]a|semana|mes)(?:\s+m[áa]s)?"
    r"|la\s+pr[oó]xima\s+semana|el\s+pr[oó]ximo\s+mes"
    r"|pr[oó]xim\w+\s+(?:semana|lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo|mes)"
    r"|este\s+(?:lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo|fin\s+de\s+semana)"
    r"|ma[ñn]ana|pasado\s+ma[ñn]ana|dentro\s+de\s+\d+\s+(?:d[ií]as?|semanas?)"
    # Menciones temporales coloquiales de "pronto/se acerca" (sin fecha exacta):
    # "pronto", "ya viene", "se acerca", "esta semana", "el finde"... (feedback:
    # "tengo certamen pronto" no matcheaba porque 'pronto' no estaba cubierto).
    r"|\bpront\w*\b|\bluego\b|ya\s+viene|se\s+acerca|se\s+viene|est[áa]\s+cerca"
    r"|\bencima\b|esta\s+semana|este\s+mes|en\s+unos\s+d[ií]as|en\s+unas\s+semanas"
    r"|en\s+pocos\s+d[ií]as|este\s+finde|el\s+finde"
    r")",
    re.IGNORECASE,
)


def _is_agenda_request(text: str) -> bool:
    return bool(_AGENDA_RE.search(text or ""))


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
        self._last_course: str | None = None    # último curso (safe_name) usado en este chat
        self._last_unit: str | None = None      # última unidad activa
        self._last_difficulty: str = "practicando"
        self._last_options: list[str] | None = None  # últimas quick-replies emitidas
        self._units_meta_cache: dict[str, dict] = {}  # course_safe -> _units.json parseado

    # ── Metadatos del curso (nombre legible + unidades de _units.json) ────────

    def _load_units_meta(self, course_safe: str | None) -> dict:
        """Lee data/<curso>/_units.json (tolerante a formato viejo o ausente) y
        cachea por sesión. Devuelve {"units": [{"name","topics"}...]} o {}."""
        if not course_safe:
            return {}
        if course_safe in self._units_meta_cache:
            return self._units_meta_cache[course_safe]
        meta: dict = {}
        label = _course_label(course_safe)
        if label:
            try:
                raw = json.loads((DATA_DIR / label / "_units.json").read_text(encoding="utf-8"))
                units = []
                for u in (raw or {}).get("units", []):
                    if isinstance(u, dict) and u.get("name"):
                        topics = [str(t) for t in (u.get("topics") or []) if str(t).strip()]
                        units.append({"name": str(u["name"]), "topics": topics})
                if units:
                    meta = {"units": units}
            except Exception:
                meta = {}
        self._units_meta_cache[course_safe] = meta
        return meta

    def _unit_topics_and_others(self, course_safe: str | None,
                                unit: str | None) -> tuple[list[str], list[str]]:
        """(topics de la unidad activa, nombres de las demás unidades)."""
        meta = self._load_units_meta(course_safe)
        units = meta.get("units", [])
        if not units:
            return [], []
        topics: list[str] = []
        others: list[str] = []
        unit_l = (unit or "").strip().lower()
        for u in units:
            if unit_l and u["name"].strip().lower() == unit_l:
                topics = u.get("topics", [])
            else:
                others.append(u["name"])
        return topics, others

    # ── Flujo principal ───────────────────────────────────────────────────────

    def chat(self, user_message: str, course: str = None, unit: str = None,
             difficulty: str = "practicando", mode: str = None, method: str = None) -> dict:
        if _is_injection(user_message):
            result = self._injection_reply(user_message)
        elif _is_agenda_request(user_message):
            result = self._agenda_reply(user_message)
        else:
            intent, sources, messages = self._prepare(user_message, course, unit, difficulty, mode, method)
            raw = llm.complete(messages)
            result = self._finalize(user_message, intent, sources, raw)
            # Info del proveedor/modelo que realmente respondió (thread-local,
            # se lee justo tras llm.complete() en este mismo hilo — así el
            # frontend puede avisar si la respuesta vino de un modelo de
            # reserva en vez del primario configurado).
            served = llm.last_served()
            result["degraded"] = served.get("degraded", False)
            result["provider"] = served.get("provider")
        self._last_options = result.get("options")
        return result

    def chat_stream(self, user_message: str, course: str = None, unit: str = None,
                    difficulty: str = "practicando", mode: str = None,
                    method: str = None) -> Generator[dict, None, None]:
        """Genera eventos {'delta': str} durante el streaming y un evento final
        {'done': True, 'text': str, 'options': list|None} con el texto normalizado."""
        if _is_injection(user_message):
            result = self._injection_reply(user_message)
            self._last_options = result.get("options")
            yield {"delta": result["text"]}
            yield {"done": True, "text": result["text"], "options": result["options"]}
            return
        if _is_agenda_request(user_message):
            result = self._agenda_reply(user_message)
            self._last_options = result.get("options")
            yield {"delta": result["text"]}
            yield {"done": True, "text": result["text"], "options": result["options"]}
            return
        intent, sources, messages = self._prepare(user_message, course, unit, difficulty, mode, method)
        raw = ""
        for delta in llm.stream(messages):
            raw += delta
            yield {"delta": delta}
        result = self._finalize(user_message, intent, sources, raw)
        served = llm.last_served()
        self._last_options = result.get("options")
        yield {"done": True, "text": result["text"], "options": result["options"],
               "degraded": served.get("degraded", False), "provider": served.get("provider")}

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

    def _agenda_reply(self, user_message: str) -> dict:
        """Respuesta fija cuando el estudiante anuncia una evaluación con fecha
        ("tengo control de X en una semana más"): no llama al LLM, redirige a
        Organización donde puede agendarla y generar un plan de estudio."""
        text = ("¡Eso es importante! 📅 Puedo agendarlo y armarte un plan de estudio. "
                "Ve a **Organización** y escríbelo en 'Dile a Atenea', o sigue estudiando aquí.")
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": text})
        options = ["📅 Abrir Organización", "Seguir estudiando"]
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
        self._last_course = course
        self._last_unit = unit
        self._last_difficulty = difficulty
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

        # Anclar la disciplina: nombre legible del ramo + temas de la unidad activa
        # (fix del bug "ejercicio de integrales en Álgebra Lineal": el prompt nunca
        # recibía el curso y el modelo adivinaba la disciplina desde sus ejemplos).
        course_label = _course_label(course)
        topics, other_units = self._unit_topics_and_others(course, unit)
        system_prompt = build_system_prompt(self.exercise_state, unit, difficulty,
                                             mode, self._method,
                                             course=course_label, topics=topics,
                                             other_units=other_units)

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
            de_curso = f" de {course_label}" if course_label else ""
            if unit and _is_review_unit(unit) and not self.current_topic:
                # Unidad de repaso: "repaso" no es un tema — pedir cobertura del curso,
                # anclando con temas CONCRETOS (nombres de las otras unidades).
                alcance = "que cubra alguno de los temas principales del curso"
                ejemplos = ", ".join(u for u in other_units if u)[:300]
                if ejemplos:
                    alcance += f" (por ejemplo: {ejemplos})"
            else:
                topic = self.current_topic or unit or "el tema en estudio"
                alcance = f"sobre {topic}"
            # La restricción de disciplina se repite en el ÚLTIMO mensaje (recencia):
            # es la señal más fuerte para que el modelo no derive a otro ramo.
            disciplina = (
                f" El ejercicio DEBE pertenecer a la disciplina de {course_label}; "
                "no propongas ejercicios de otra materia." if course_label else ""
            )
            api_user = (
                f"Plantéame UN ejercicio nuevo{de_curso} {alcance}, del mismo tipo y nivel "
                "que el material del curso. Escribe SOLO el enunciado, redactado por ti con "
                "LaTeX correcto entre signos de dólar. No copies texto del material, no "
                "incluyas solución ni pistas, y termina con "
                f"'¿Cuál es tu enfoque para resolverlo?'.{disciplina}"
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
        """Persiste el estado completo para sobrevivir reinicios del servidor.

        Incluye un `transcript` (últimos TRANSCRIPT_MAX_TURNS turnos, roles user/assistant
        con texto plano) apto para repintar la UI del historial de sesiones sin depender
        del formato interno de `history`, y `updated_at` para ordenar/mostrar el historial.
        Preserva un `title` custom (puesto vía PUT /api/sessions/{sid}) si ya existía.
        """
        WEB_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = WEB_SESSIONS_DIR / f"{web_session_id}.json"

        custom_title = None
        if path.exists():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                custom_title = old.get("title")
            except Exception:
                custom_title = None

        transcript = [
            {"role": h.get("role"), "text": h.get("content", "")}
            for h in self.history[-TRANSCRIPT_MAX_TURNS:]
            if isinstance(h, dict) and h.get("role") in ("user", "assistant")
        ]

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
            "course": self._last_course,
            "unit": self._last_unit,
            "difficulty": self._last_difficulty,
            "options": self._last_options,
            "transcript": transcript,
            "updated_at": datetime.now().isoformat(),
        }
        if custom_title:
            state["title"] = custom_title
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
            obj._last_course = state.get("course", None)
            obj._last_unit = state.get("unit", None)
            obj._last_difficulty = state.get("difficulty", "practicando")
            obj._last_options = state.get("options", None)
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
