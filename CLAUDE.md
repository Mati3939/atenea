# Proyecto Atenea — Contexto y Progreso

## Propósito

Atenea es una aplicación de estudio asistida por IA para estudiantes universitarios. El flujo central es:

1. El estudiante conecta su cuenta de **Canvas LMS** via API y descarga todos los archivos de sus cursos.
2. Los archivos se organizan y se indexan en una base de conocimiento vectorial (**ChromaDB**).
3. Un **chatbot RAG** responde preguntas sobre el material con pedagogía socrática:
   - Hace preguntas de guía en lugar de dar respuestas directas.
   - Explica conceptos base cuando el estudiante genuinamente no sabe el punto de partida.
4. El sistema **detecta debilidades** en los métodos de estudio y sugiere mejoras personalizadas.

**Objetivo final:** que el estudiante llegue a las respuestas por sí mismo, fortaleciendo comprensión profunda.

---

## Stack Tecnológico

| Capa | Tecnología |
|------|-----------|
| Lenguaje | Python 3.14 (Windows) |
| Vector DB | ChromaDB (PersistentClient) |
| LLM / Chat | **Ollama** (local, gratuito) — modelo configurable vía `.env` |
| Canvas integration | Canvas REST API (token Bearer) |
| Web | FastAPI + Jinja2 + vanilla JS |
| Config / Secrets | `.env` |

**Decisión clave:** Se migró de Anthropic API a **Ollama** (local, gratis) porque el usuario tiene Claude Pro pero no quiere pagar API separada. Default model: `llama3.2`.

---

## Estructura de Archivos

```
canvas_api/
  __init__.py
  client.py           # CanvasClient: get_courses, get_course_files, download_file, sync_all
ingestion/
  __init__.py
  parsers.py          # parse() para PDF/DOCX/PPTX/TXT/MD
  chunker.py          # chunk() ventana deslizante 400 palabras, 50 overlap
  vectorstore.py      # VectorStore wrapper de ChromaDB
chatbot/
  __init__.py
  prompts.py          # SYSTEM_PROMPT socrático
  retriever.py        # Retriever: get_context() y get_context_all()
  conversation.py     # AteneoChat: RAG + Ollama + historial + auto-save logs
web/
  __init__.py
  main.py             # FastAPI app: páginas + APIs de chat/sync/ingest/analysis
  templates/
    base.html         # Layout con sidebar
    index.html        # Dashboard con barras de progreso para sync e ingest
    chat.html         # Interfaz de chat con selector de curso
    analysis.html     # Generación de reporte de debilidades
  static/
    style.css         # Estilos: sidebar azul oscuro, chat bubbles, progress bar
    chat.js           # Lógica de chat (sessionId en localStorage, markdown básico)
sync.py               # CLI para sincronizar Canvas (alternativa a web)
ingest.py             # CLI para indexar documentos (alternativa a web)
chat.py               # CLI para chatear (alternativa a web)
analysis.py           # CLI + función generate_report() usada por la web
run.py                # Entry point: uvicorn en puerto 8080
requirements.txt
.env.example
```

---

## Estado Actual — TODO COMPLETADO ✅

### Fase 1 — Canvas Integration ✅
- `canvas_api/client.py`: autenticación Bearer, paginación automática, manejo de 401/403/404
- `sync.py`: CLI entry point

### Fase 2 — Ingestion Pipeline ✅
- `ingestion/parsers.py`: PDF (PyMuPDF), DOCX, PPTX, TXT/MD
- `ingestion/chunker.py`: ventana deslizante
- `ingestion/vectorstore.py`: ChromaDB wrapper
- `ingest.py`: CLI entry point

### Fase 3 — Chatbot RAG Socrático ✅
- `chatbot/prompts.py`: nunca respuesta directa, explica base si es necesario
- `chatbot/retriever.py`: busca en colección específica o en todas
- `chatbot/conversation.py`: usa Ollama, historial limpio (contexto RAG no contamina history), auto-save a `logs/`
- `chat.py`: CLI entry point

### Fase 4 — Análisis de Debilidades ✅
- Logs en `logs/session_YYYYMMDD_HHMMSS.json`
- `analysis.py`: `generate_report()` analiza todos los logs con Ollama → `reporte_estudio.txt`

### Fase 5 — Web App ✅
- FastAPI + Jinja2 + vanilla JS en puerto **8080**
- Dashboard con **barras de progreso** para sync e ingest (polling cada 1s)
- Sync inteligente: escanea Canvas, compara con `data/`, descarga solo archivos nuevos
- Chat con selector de curso, sesiones persistidas en localStorage
- Página de análisis de debilidades

---

## Variables de Entorno (.env)

```
CANVAS_URL=https://udd.instructure.com
CANVAS_TOKEN=<token de Canvas>
OLLAMA_MODEL=llama3.2
```

---

## Cómo Correr la App

```bash
pip install -r requirements.txt
ollama pull llama3.2      # solo la primera vez
python run.py             # levanta en http://127.0.0.1:8080
```

Flujo en la web:
1. Dashboard → "Sincronizar Canvas" (descarga archivos nuevos con barra de progreso)
2. Dashboard → "Indexar Documentos" (indexa en ChromaDB con barra de progreso)
3. Chat → selecciona curso → estudia con el asistente socrático
4. Análisis → genera reporte de debilidades

---

## Problemas Conocidos / Resueltos

- **WinError 10013**: puerto ocupado → usar puerto 8080 en `run.py`
- **TypeError en TemplateResponse**: Starlette nuevo requiere `(request, name, context)` no `(name, {"request":...})`
- **401 Canvas**: token incorrecto o expirado → regenerar en Canvas → Configuración → Integraciones aprobadas

---

## Por Hacer (mejoras opcionales)

- [ ] Streaming de respuestas del chat (Ollama soporta streaming)
- [ ] Historial de reportes anteriores en la página de Análisis
- [ ] Autenticación multi-usuario
- [ ] Soporte para archivos ZIP en Canvas (descomprimir automáticamente)

---

## Cómo Retomar en una Nueva Sesión

1. Leer este archivo completo.
2. El proyecto está **funcional end-to-end** — todas las fases están implementadas.
3. Preguntar al usuario qué quiere mejorar o si encontró algún error.
4. Al terminar, actualizar este archivo con los cambios realizados.
