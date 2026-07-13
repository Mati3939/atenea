# Atenea

Atenea es una plataforma web de estudio asistido por IA para estudiantes universitarios. Se conecta a **Canvas LMS**, descarga y organiza el material real de cada curso, lo indexa en una base de conocimiento vectorial y ofrece un **chatbot con pedagogía socrática**: en vez de dar la respuesta, guía al estudiante con preguntas hasta que la encuentra por sí mismo. Además ayuda a organizar el semestre (agenda, planes de estudio generados por IA) y recomienda métodos de estudio personalizados.

**Objetivo:** que el estudiante llegue a las respuestas por sí mismo, fortaleciendo la comprensión profunda en lugar de memorizar soluciones.

---

## Funcionalidades

### Integración con Canvas LMS
- Login con URL institucional + token de Canvas (validado contra la API, persistido en `.env`).
- Sincronización automática en segundo plano al arrancar: cursos, archivos (tanto la pestaña **Archivos** como los **Módulos** y los archivos embebidos en Páginas), Páginas, Anuncios y **fechas de evaluaciones** (Assignments).
- Descargas paralelas, descompresión automática de ZIPs y filtro de cursos no académicos (institucionales/genéricos).

### Chatbot socrático con RAG
- Responde **con base en el material real del curso** (ChromaDB + embeddings multilingües), citando y respetando el enfoque de cada ramo.
- Pedagogía socrática: hace preguntas de guía, da pistas graduales y solo explica desde cero cuando el estudiante genuinamente no tiene punto de partida. No revela soluciones de ejercicios activos.
- Flujo guiado: modo (estudiar / ejercitar / preguntar) → curso → **unidad** (detectada automáticamente desde la calendarización del ramo) → método de estudio → conversación.
- Streaming de respuestas, LaTeX perfecto (KaTeX), markdown, selector de dificultad y quick-replies contextuales en cada turno.
- **Historial de sesiones por curso**: retomar, renombrar o eliminar conversaciones anteriores; el estado completo sobrevive reinicios del servidor.
- Defensas integradas: guard determinista contra inyección de prompts y anti-eco de solucionarios (el RAG excluye pautas/controles al recuperar material).

### Organización y agenda
- **Calendario mensual** con las fechas de evaluaciones de Canvas + eventos propios (crear, editar, eliminar).
- **Agendado en lenguaje natural**: escribe *"tengo control de integrales en una semana más"* y Atenea crea el evento **y** genera un plan de estudio día a día como eventos editables. También funciona desde el chat: si le cuentas al tutor que tienes una prueba, te ofrece agendarla.
- **Plan de estudio IA** por rango de fechas, basado en el material del curso y el método de estudio elegido.
- **Análisis de debilidades**: reporte generado por IA a partir de las sesiones de estudio, con sugerencias de mejora.

### Métodos de estudio
- Catálogo de 8 métodos (Pomodoro, Feynman, práctica espaciada, etc.) con recomendación por curso.
- El método elegido adapta la pedagogía del tutor dentro de la sesión de chat.
- **Temporizador Pomodoro global**: widget flotante que sobrevive la navegación entre páginas, con fases automáticas, aviso sonoro y duraciones configurables.

### Gestor de archivos
- Explorador estilo Canvas del material descargado: navegación con breadcrumb, drag & drop (mover entre carpetas y subir desde el escritorio), renombrar, eliminar, descargar, crear carpetas.
- **Previsualización** de PDF, imágenes, texto/código y extracción de texto para PPTX/DOCX.
- **Ordenar curso**: propone una reorganización automática (evaluaciones, material por unidad, otros) que se revisa antes de aplicar y se puede deshacer. Los archivos movidos **no** se re-indexan: la metadata de los chunks se actualiza en el momento.
- Panel de **unidades**: ver, renombrar, añadir, eliminar o re-detectar las unidades de un curso, y elegir manualmente qué archivo es la calendarización.

### Otras vistas
- **Dashboard** (`/dashboard`): sincronizar Canvas e indexar documentos manualmente, con barras de progreso y reporte de errores por archivo.
- **Modo Demo** (`/demo`): tour interactivo de 7 slides que muestra todo el flujo sin necesitar backend ni LLM (útil para presentaciones).

---

## Arquitectura y stack

| Capa | Tecnología |
|------|-----------|
| Lenguaje | Python 3.14 |
| Web | FastAPI + Jinja2 + JavaScript vanilla |
| Vector DB | ChromaDB (`PersistentClient`) |
| Embeddings | Ollama `paraphrase-multilingual` (entiende español) |
| LLM principal | **Groq** (`llama-3.3-70b-versatile`, nube, gratis, ~1-3 s) |
| LLM de respaldo | Modelo secundario en Groq → **Ollama local** (offline) |
| Canvas | Canvas REST API (token Bearer) |
| Render matemático | KaTeX + marked.js, servidos localmente (funciona sin internet) |
| Config | `.env` |

El motor de IA es **conmutable** (`chatbot/llm.py`): con `GROQ_API_KEY` usa Groq; si el modelo agota su cupo cae al modelo de reserva de Groq y, en último término, a Ollama local. Los embeddings siempre son Ollama, independientemente del proveedor de chat.

### Flujo de datos

```
Canvas LMS ──sync──▶ data/<curso>/ ──parse+chunk──▶ ChromaDB
                                                        │
Estudiante ◀──streaming + LaTeX── Chatbot socrático ◀──RAG (por unidad)
```

---

## Instalación

Requisitos: Python 3.11+ y (para embeddings y fallback offline) [Ollama](https://ollama.com).

```bash
git clone <repo>
cd Atenea
pip install -r requirements.txt

ollama pull paraphrase-multilingual  # embeddings (siempre necesario para el RAG)
ollama pull llama3.2                 # solo si no usarás Groq (fallback local)

cp .env.example .env                 # completar CANVAS_URL, CANVAS_TOKEN y GROQ_API_KEY
python run.py                        # http://127.0.0.1:8080
```

Al abrir la app por primera vez se pide la URL de Canvas y el token (Canvas → Cuenta → Configuración → Integraciones aprobadas → Nuevo token). No hace falta editar el `.env` a mano: el login lo escribe.

### Configuración (`.env`)

| Variable | Descripción |
|----------|-------------|
| `CANVAS_URL` | URL base del Canvas institucional |
| `CANVAS_TOKEN` | Token de acceso de Canvas |
| `GROQ_API_KEY` | Key gratuita de [console.groq.com](https://console.groq.com/keys); si está, el chat usa Groq |
| `GROQ_MODEL` | Modelo principal en Groq (default `llama-3.3-70b-versatile`) |
| `GROQ_FALLBACK_MODEL` | Modelo de reserva en Groq ante rate limits |
| `LLM_PROVIDER` | Forzar `groq` u `ollama` (vacío = automático) |
| `OLLAMA_MODEL` | Modelo local de fallback |
| `EMBEDDING_MODEL` | Modelo de embeddings (default `paraphrase-multilingual`) |
| `AUTO_SYNC` | `1` = sincronizar Canvas en segundo plano al arrancar |
| `EXCLUDED_COURSES` | Cursos de Canvas a excluir del selector (no académicos) |

Todas las variables están documentadas con más detalle en [`.env.example`](.env.example).

---

## Uso

1. **Chat** (página de inicio): elige modo → ramo → unidad → método de estudio y conversa. El material se descarga e indexa solo la primera vez.
2. **Organización** (`/organizacion`): calendario con fechas de Canvas, eventos propios, agendado en lenguaje natural y planes de estudio IA.
3. **Métodos** (`/metodos`): explora los métodos de estudio, pruébalos en el chat y usa el Pomodoro.
4. **Archivos** (`/archivos`): gestiona y reorganiza el material descargado.
5. **Dashboard** (`/dashboard`): sincronización e indexado manual.

### CLIs equivalentes

Para uso sin interfaz web:

```bash
python sync.py       # sincronizar Canvas
python ingest.py     # indexar documentos en ChromaDB
python chat.py       # chatear por consola
python analysis.py   # generar reporte de análisis
```

---

## Estructura del proyecto

```
canvas_api/           # Cliente de la API de Canvas (cursos, archivos, módulos, páginas, fechas)
ingestion/
  parsers.py          # Extracción de texto: PDF / DOCX / PPTX / TXT / MD (con reparación de acentos)
  chunker.py          # Chunking por ventana deslizante (400 palabras, 50 de overlap)
  vectorstore.py      # ChromaDB + embeddings Ollama (con cache de queries)
  calendar_parser.py  # Detección de unidades desde la calendarización del ramo
chatbot/
  llm.py              # Abstracción del motor IA: Groq ⇄ Ollama, streaming, reintentos
  prompts.py          # System prompt socrático (curso, unidad, método, dificultad)
  conversation.py     # Máquina de estados del tutor: RAG, intenciones, persistencia
  retriever.py        # Recuperación de contexto (por unidad, filtrando solucionarios)
  latex.py            # Normalizador de LaTeX (red de seguridad)
  study_methods.py    # Catálogo de métodos de estudio
web/
  main.py             # FastAPI: páginas, chat NDJSON, agenda, archivos, unidades, login
  templates/          # Jinja2: chat, organización, métodos, archivos, dashboard, demo, login
  static/             # JS vanilla + CSS + KaTeX/marked locales (vendor/)
sync.py · ingest.py · chat.py · analysis.py   # CLIs equivalentes a la web
run.py                # Entry point (uvicorn, puerto 8080)
test_agent_eval.py    # Batería de regresión del agente (deterministas + casos en vivo)
```

Los datos viven fuera del control de versiones: `data/<curso>/` (material descargado y metadata de unidades), `chroma/` (índice vectorial) y `logs/` (sesiones y eventos).

---

## Solución de problemas

| Problema | Solución |
|----------|----------|
| 401 de Canvas | Token incorrecto o expirado → regenerar en Canvas y volver a hacer login |
| Groq 429 (rate limit) | El free tier limita tokens/min y tokens/día; Atenea reintenta, cambia al modelo de reserva y cae a Ollama solo si todo falla |
| "Failed to connect to Ollama" | El RAG necesita Ollama corriendo con `paraphrase-multilingual`; sin él, el chat responde sin material del curso |
| PDFs escaneados (solo imagen) | No hay OCR: se omiten al indexar (esperado) |
| Acentos rotos en consola Windows | Solo afecta a los CLIs: correr con `PYTHONIOENCODING=utf-8` o usar la web |
| Puerto ocupado (WinError 10013) | La app usa el puerto 8080; cambiarlo en `run.py` si está tomado |

---

## Tests

```bash
python test_agent_eval.py
```

Batería de regresión del agente: detección de intención, máquina de estados, formato LaTeX, adherencia al método, fidelidad al ramo e inyección de prompts. Incluye casos deterministas (sin LLM) y casos en vivo contra Groq.

---

## Proyecto académico

Atenea es el proyecto final del curso **"Taller de diseño de servicios y prototipado"** de la carrera **Ingeniería Civil en Informática e Innovación Tecnológica**, Universidad del Desarrollo (Concepción). El informe completo del proceso (investigación, Design Thinking, prototipado e iteraciones) está en [`informe/main.pdf`](informe/main.pdf).
