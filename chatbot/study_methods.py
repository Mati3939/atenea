# chatbot/study_methods.py
# Métodos de estudio bien conocidos + recomendación heurística por tipo de curso.

METHODS = [
    {
        "key": "pomodoro",
        "name": "Técnica Pomodoro",
        "emoji": "🍅",
        "short": "Trabaja 25 min, descansa 5 min. Mantiene el foco y evita la fatiga.",
        "what": (
            "La Técnica Pomodoro divide el trabajo en intervalos de 25 minutos "
            "(llamados 'pomodoros') separados por descansos cortos de 5 minutos. "
            "Cada 4 pomodoros se toma un descanso largo de 15-30 minutos. "
            "Fue desarrollada por Francesco Cirillo a finales de los 80. "
            "Su principal ventaja es que hace el tiempo de estudio más manejable "
            "y reduce la procrastinación al comprometerse solo con bloques cortos."
        ),
        "how": [
            "Elige la tarea que vas a estudiar y tenla clara.",
            "Pon un temporizador en 25 minutos.",
            "Trabaja en la tarea sin interrupciones hasta que suene el temporizador.",
            "Marca un pomodoro completado y toma 5 minutos de descanso real.",
            "Repite. Cada 4 pomodoros, toma un descanso largo de 15-30 min.",
            "Si surge una distracción, anótala para después y vuelve al foco.",
        ],
        "best_for": ["cualquier materia", "concentración", "procrastinación", "gestión del tiempo"],
    },
    {
        "key": "spaced_repetition",
        "name": "Repetición espaciada",
        "emoji": "📆",
        "short": "Repasa el material en intervalos crecientes para fijar la memoria a largo plazo.",
        "what": (
            "La repetición espaciada (Spaced Repetition) se basa en la curva del olvido "
            "de Ebbinghaus: repasas el material justo antes de olvidarlo, en intervalos "
            "que van creciendo. Así el recuerdo se consolida con menos esfuerzo total. "
            "Es ideal para vocabulario, fórmulas, definiciones y cualquier contenido "
            "que requiera memorización. Herramientas como Anki automatizan el calendario."
        ),
        "how": [
            "Crea tarjetas o resúmenes de los conceptos clave (una idea por tarjeta).",
            "Repasa el mismo día que estudias el material (intervalo 1).",
            "Vuelve a repasar al día siguiente (intervalo 2).",
            "Luego a los 3 días, 7 días, 14 días, etc. (duplica el intervalo cada vez).",
            "Si fallas una tarjeta, reinicia su intervalo desde el principio.",
            "Usa Anki o una planilla para gestionar los intervalos automáticamente.",
        ],
        "best_for": ["memorización", "vocabulario", "fórmulas", "definiciones", "idiomas"],
    },
    {
        "key": "active_recall",
        "name": "Active Recall",
        "emoji": "🧠",
        "short": "Practica recuperar información de memoria en lugar de releerla.",
        "what": (
            "El Active Recall (recuperación activa) consiste en esforzarte por recordar "
            "la información sin mirar las notas, en lugar de releer pasivamente. "
            "Cada vez que intentas recordar algo, refuerzas esa conexión neuronal. "
            "Es más efectivo que releer y subrayar: genera mayor retención con menos tiempo. "
            "Puede combinarse con flashcards, cuestionarios, explicar en voz alta o escribir de memoria."
        ),
        "how": [
            "Estudia el material una vez con atención.",
            "Cierra el libro/apunte y escribe o di en voz alta todo lo que recuerdas.",
            "Compara con el original y marca lo que olvidaste.",
            "Repite el proceso enfocándote en los puntos débiles.",
            "Usa preguntas de práctica o crea las tuyas propias antes del examen.",
            "No releas pasivamente: siempre intenta recordar primero.",
        ],
        "best_for": ["matemáticas", "física", "química", "exámenes con preguntas", "comprensión profunda"],
    },
    {
        "key": "feynman",
        "name": "Técnica Feynman",
        "emoji": "🧑‍🏫",
        "short": "Explica el concepto como si se lo enseñaras a alguien sin conocimiento previo.",
        "what": (
            "La Técnica Feynman (del físico Richard Feynman) tiene 4 pasos: "
            "estudia el concepto, explícalo con palabras simples, identifica los "
            "vacíos donde te trabaste, y vuelve al material para rellenarlos. "
            "Al tener que explicarlo en lenguaje simple, revelas exactamente qué "
            "NO entiendes. Es especialmente poderosa para conceptos abstractos o "
            "mecánicos que crees entender pero no puedes articular."
        ),
        "how": [
            "Escribe el nombre del concepto o tema en la parte superior de una hoja.",
            "Explícalo en lenguaje simple, como si se lo enseñaras a alguien de 12 años.",
            "Identifica los puntos donde te atascas o donde no puedes explicar con claridad.",
            "Vuelve al material original y estudia específicamente esas brechas.",
            "Simplifica aún más la explicación: elimina jerga, usa analogías.",
            "Repite hasta poder explicarlo de corrido sin dudar.",
        ],
        "best_for": ["conceptos abstractos", "física", "economía", "filosofía", "programación"],
    },
    {
        "key": "mind_maps",
        "name": "Mapas mentales",
        "emoji": "🗺️",
        "short": "Organiza el conocimiento visualmente para ver relaciones y estructura global.",
        "what": (
            "Un mapa mental es un diagrama que parte de un concepto central y se ramifica "
            "en ideas relacionadas. Aprovecha la capacidad visual del cerebro para ver "
            "patrones y conexiones entre ideas. Son útiles para organizar un tema grande "
            "antes de estudiarlo, para repasar la estructura de un curso o para sintetizar "
            "apuntes. Herramientas: papel, Miro, XMind, MindMeister o draw.io."
        ),
        "how": [
            "Escribe el tema central en el medio de la hoja o pantalla.",
            "Dibuja ramas principales para cada subtema o sección.",
            "Agrega ramas secundarias con detalles, ejemplos o fórmulas clave.",
            "Usa colores, íconos y palabras cortas (no frases largas).",
            "Conecta ramas que tengan relación cruzada con una línea punteada.",
            "Usa el mapa para repasar: cúbrelo y trata de reconstruirlo de memoria.",
        ],
        "best_for": ["cursos con mucho contenido", "historia", "biología", "diseño", "síntesis"],
    },
    {
        "key": "cornell",
        "name": "Método Cornell",
        "emoji": "📝",
        "short": "Sistema de toma de apuntes dividido en notas, preguntas clave y resumen.",
        "what": (
            "El Método Cornell divide la hoja en tres zonas: una columna derecha ancha "
            "(notas durante la clase), una columna izquierda estrecha (preguntas clave y "
            "palabras clave, completadas después de clase) y una franja inferior (resumen "
            "con tus propias palabras). Esta estructura fuerza la revisión activa y "
            "convierte los apuntes en una herramienta de autoevaluación."
        ),
        "how": [
            "Divide tu hoja: ~70% derecha (notas), ~30% izquierda (preguntas), franja inferior (resumen).",
            "Durante la clase/lectura: toma apuntes en la columna derecha.",
            "Poco después: lee los apuntes y escribe preguntas en la columna izquierda que "
            "la columna derecha responde.",
            "Cubre la columna derecha y responde las preguntas de izquierda de memoria.",
            "Escribe un resumen de 3-5 oraciones en la franja inferior.",
            "Repasa solo cubriendo la columna de notas y respondiendo las preguntas.",
        ],
        "best_for": ["clases magistrales", "lecturas de libros", "derecho", "historia", "toma de notas"],
    },
    {
        "key": "interleaving",
        "name": "Interleaving (entrelazado)",
        "emoji": "🔀",
        "short": "Alterna entre distintos temas o tipos de problemas en una misma sesión.",
        "what": (
            "El interleaving (práctica entrelazada) consiste en mezclar distintos temas "
            "o tipos de problemas en una misma sesión de estudio, en lugar de hacer todo "
            "el 'bloque' de un solo tema (llamado blocking). Aunque se siente más difícil "
            "y confuso, genera mejor transferencia del aprendizaje y rendimiento real en "
            "exámenes donde los problemas vienen mezclados. Contrarrestar la falsa "
            "sensación de dominio es uno de sus beneficios más importantes."
        ),
        "how": [
            "En vez de estudiar 2 horas de un solo tema, divide la sesión en bloques de 20-30 min.",
            "Alterna entre 2-4 temas o tipos de problemas distintos.",
            "Por ejemplo: 25 min derivadas → 25 min integrales → 25 min límites → vuelta al principio.",
            "Usa el mismo enfoque en los ejercicios: no hagas todos los problemas del mismo tipo juntos.",
            "Acepta que se sentirá más difícil: eso es señal de que el cerebro está trabajando más.",
            "Revisa las conexiones entre temas al final de la sesión.",
        ],
        "best_for": ["matemáticas", "física", "química", "problemas mixtos", "preparación de exámenes"],
    },
    {
        "key": "sq3r",
        "name": "Método SQ3R",
        "emoji": "📖",
        "short": "Survey, Question, Read, Recite, Review — lectura activa en 5 pasos.",
        "what": (
            "SQ3R es un método estructurado de lectura activa: Survey (inspeccionar), "
            "Question (formular preguntas), Read (leer), Recite (recitar) y Review (repasar). "
            "Transforma la lectura pasiva en un proceso de comprensión profunda. Es "
            "especialmente útil para capítulos de libros de texto densos donde es fácil "
            "leer sin retener nada. Obliga a comprometerse con el material antes, durante "
            "y después de la lectura."
        ),
        "how": [
            "Survey (Inspeccionar): lee títulos, subtítulos, imágenes y resumen del capítulo (2-3 min).",
            "Question (Preguntar): convierte cada título en una pregunta (ej. '¿Qué es la mitosis?').",
            "Read (Leer): lee buscando la respuesta a cada pregunta. No subralles aún.",
            "Recite (Recitar): cierra el libro y responde en voz alta o escrita cada pregunta.",
            "Review (Repasar): repasa todo el capítulo respondiendo las preguntas sin mirar las notas.",
            "Repite 'Recite + Review' en sesiones posteriores para consolidar.",
        ],
        "best_for": ["libros de texto", "cursos teóricos", "biología", "historia", "economía"],
    },
]

# Índice rápido por key
_METHOD_MAP = {m["key"]: m for m in METHODS}


def recommend(course_labels: list[str]) -> list[str]:
    """Heurística: devuelve 2-3 keys de métodos recomendados según los nombres de cursos."""
    joined = " ".join(course_labels).lower()

    # Patrones de detección
    is_math = any(w in joined for w in [
        "cálculo", "calculo", "álgebra", "algebra", "matemática", "matematica",
        "física", "fisica", "estadística", "estadistica", "probabilidad",
        "ecuaciones", "análisis", "analisis numérico", "numerico",
    ])
    is_memorization = any(w in joined for w in [
        "biología", "biologia", "anatomía", "anatomia", "histología", "histologia",
        "química", "quimica", "farmacología", "farmacologia", "derecho", "historia",
        "geografía", "geografia", "sociología", "sociologia",
    ])
    is_design = any(w in joined for w in [
        "taller", "diseño", "diseno", "proyecto", "arquitectura", "arte",
        "comunicación", "comunicacion", "media", "audiovisual", "fotografía",
    ])
    is_reading = any(w in joined for w in [
        "literatura", "economía", "economia", "sociología", "sociologia",
        "derecho", "filosofía", "filosofia", "psicología", "psicologia",
        "historia", "política", "politica",
    ])

    if is_math:
        return ["active_recall", "spaced_repetition", "pomodoro"]
    if is_design:
        return ["feynman", "mind_maps", "pomodoro"]
    if is_memorization:
        return ["spaced_repetition", "active_recall", "cornell"]
    if is_reading:
        return ["sq3r", "cornell", "feynman"]
    # Default genérico
    return ["pomodoro", "active_recall", "spaced_repetition"]
