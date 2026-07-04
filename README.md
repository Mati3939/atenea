# Atenea

Atenea es una plataforma web de estudio asistido por IA para estudiantes universitarios. Se conecta a **Canvas LMS**, organiza e indexa el material de los cursos, y ofrece un **chatbot con pedagogía socrática**: en vez de dar la respuesta, guía al estudiante con preguntas hasta que la encuentra por sí mismo. Además detecta debilidades académicas y recomienda métodos de estudio personalizados.

## Qué hace

- **Integración con Canvas**: sincroniza automáticamente cursos, archivos (pestaña de Archivos y Módulos), páginas, anuncios y fechas de evaluaciones.
- **Chatbot socrático (RAG)**: responde con base en el material real de cada curso, con streaming y soporte de LaTeX.
- **Detección de debilidades y métodos de estudio**: recomienda técnicas de estudio según el curso y el desempeño del estudiante.
- **Organización**: agenda con fechas de Canvas y eventos propios, agendamiento en lenguaje natural (p. ej. "tengo control de integrales en una semana más") con plan de estudio día a día generado automáticamente.
- **Gestor de archivos**: navegación, reorganización automática y previsualización del material descargado.

## Stack

Python · FastAPI · ChromaDB (embeddings con Ollama, `paraphrase-multilingual`) · Groq (`llama-3.3-70b-versatile`) como LLM principal, con Ollama local como respaldo sin conexión.

## Cómo correr la app

```bash
pip install -r requirements.txt
ollama pull llama3.2:1b              # solo si no hay GROQ_API_KEY (fallback local)
ollama pull paraphrase-multilingual  # embeddings, siempre necesario
cp .env.example .env                 # completar CANVAS_URL, CANVAS_TOKEN y GROQ_API_KEY
python run.py                        # http://127.0.0.1:8080
```

Variables de entorno relevantes están documentadas en [`.env.example`](.env.example).

## Estructura del proyecto

```
canvas_api/     # Cliente de la API de Canvas
ingestion/      # Parseo, chunking e indexado del material (ChromaDB)
chatbot/        # Motor conversacional: LLM, prompts, estados, RAG
web/            # FastAPI: rutas, plantillas y estáticos
sync.py / ingest.py / chat.py / analysis.py  # CLIs equivalentes a la web
run.py          # Entry point (uvicorn)
```

## Proyecto académico

Atenea es el proyecto final del curso "Taller de diseño de servicios y prototipado" de la carrera **Ingeniería Civil en Informática e Innovación Tecnológica**, Universidad del Desarrollo (Concepción). El informe completo del proceso (investigación, Design Thinking, prototipado e iteraciones) está en [`informe/main.pdf`](informe/main.pdf).
