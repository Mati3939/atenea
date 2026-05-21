import os
import json
from datetime import datetime
from pathlib import Path
import ollama
from chatbot.prompts import SYSTEM_PROMPT
from chatbot.retriever import Retriever
from ingestion.vectorstore import VectorStore


class AteneoChat:
    def __init__(self):
        self.model = os.environ.get("OLLAMA_MODEL", "llama3.2")
        self.store = VectorStore()
        self.retriever = Retriever(self.store)
        self.history: list[dict] = []
        self.session_log: list[dict] = []
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def chat(self, user_message: str, course: str = None) -> str:
        if course:
            context, sources = self.retriever.get_context(user_message, course)
        else:
            context, sources = self.retriever.get_context_all(user_message)

        if context:
            api_user_content = (
                f"[Material del curso]\n{context}\n\n"
                f"[Pregunta del estudiante]\n{user_message}"
            )
        else:
            api_user_content = user_message

        messages_for_api = (
            [{"role": "system", "content": SYSTEM_PROMPT}]
            + self.history
            + [{"role": "user", "content": api_user_content}]
        )

        response = ollama.chat(model=self.model, messages=messages_for_api)
        assistant_message = response["message"]["content"]

        # Historial limpio (sin el contexto RAG) para que las siguientes búsquedas sean relevantes
        self.history.append({"role": "user", "content": user_message})
        self.history.append({"role": "assistant", "content": assistant_message})

        self.session_log.append({
            "turn": len(self.session_log) + 1,
            "user": user_message,
            "sources": sources,
            "assistant": assistant_message,
        })

        return assistant_message

    def save_session(self, log_dir: Path = Path("logs")) -> Path:
        log_dir.mkdir(exist_ok=True)
        path = log_dir / f"session_{self.session_id}.json"
        path.write_text(json.dumps(self.session_log, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def list_courses(self) -> list[str]:
        return self.store.list_collections()
