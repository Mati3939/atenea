import os
import re
import chromadb

# Modelo de embeddings servido por Ollama. 'paraphrase-multilingual' entiende
# español mucho mejor que el default de ChromaDB (all-MiniLM, anglocéntrico).
# Usar EMBEDDING_MODEL=default en .env para volver al embedding de ChromaDB.
DEFAULT_EMBEDDING_MODEL = "paraphrase-multilingual"


class VectorStore:
    def __init__(self, persist_dir: str = "chroma_db"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedding_model = os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
        self._ef = None
        if self.embedding_model and self.embedding_model.lower() != "default":
            from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
            self._ef = OllamaEmbeddingFunction(
                url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
                model_name=self.embedding_model,
                timeout=300,  # la primera llamada carga el modelo en frío
            )

    def _safe_name(self, name: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_\-.]", "_", name)[:63]
        # ChromaDB exige que empiece y termine con alfanumérico
        safe = re.sub(r"^[^a-zA-Z0-9]+", "", safe)
        safe = re.sub(r"[^a-zA-Z0-9]+$", "", safe)
        if not safe:
            safe = "col"
        return safe.ljust(3, "0")

    def get_or_create_collection(self, name: str):
        kwargs = {"metadata": {"embedding_model": self.embedding_model or "default"}}
        if self._ef is not None:
            kwargs["embedding_function"] = self._ef
        return self.client.get_or_create_collection(self._safe_name(name), **kwargs)

    def embedding_compatible(self, name: str) -> bool:
        """True si la colección no existe o fue creada con el embedding actual."""
        try:
            col = self.client.get_collection(self._safe_name(name))
        except Exception:
            return True
        stored = (col.metadata or {}).get("embedding_model", "default")
        return stored == (self.embedding_model or "default")

    def delete_collection(self, name: str) -> None:
        try:
            self.client.delete_collection(self._safe_name(name))
        except Exception:
            pass

    def add_chunks(self, collection_name: str, chunks: list[str], metadata: list[dict]) -> None:
        collection = self.get_or_create_collection(collection_name)
        base = collection.count()
        ids = [f"chunk_{base + i}" for i in range(len(chunks))]
        collection.add(documents=chunks, metadatas=metadata, ids=ids)

    def query(self, collection_name: str, query: str, n_results: int = 5) -> list[dict]:
        # Una colección creada con otro embedding falla al consultar; se degrada
        # a "sin contexto" hasta que el re-indexado automático la regenere.
        try:
            collection = self.get_or_create_collection(collection_name)
            count = collection.count()
            if count == 0:
                return []
            results = collection.query(
                query_texts=[query],
                n_results=min(n_results, count),
            )
            return [
                {"text": doc, "metadata": meta}
                for doc, meta in zip(results["documents"][0], results["metadatas"][0])
            ]
        except Exception:
            return []

    def is_file_indexed(self, collection_name: str, file_path: str) -> bool:
        try:
            col = self.client.get_collection(self._safe_name(collection_name))
            result = col.get(where={"path": file_path}, limit=1)
            return len(result["ids"]) > 0
        except Exception:
            return False

    def list_collections(self) -> list[str]:
        return [c.name for c in self.client.list_collections()]

    def list_files_in_collection(self, collection_name: str) -> list[str]:
        try:
            col = self.client.get_collection(self._safe_name(collection_name))
            result = col.get(include=["metadatas"])
            files: set[str] = set()
            for meta in (result["metadatas"] or []):
                if meta and "file" in meta:
                    files.add(meta["file"])
            return sorted(files)
        except Exception:
            return []

    def query_by_unit(
        self, collection_name: str, query: str, unit_name: str, n_results: int = 5
    ) -> list[dict]:
        """Query only chunks that belong to a specific unit (metadata field 'unit')."""
        collection = self.get_or_create_collection(collection_name)
        count = collection.count()
        if count == 0:
            return []
        try:
            results = collection.query(
                query_texts=[query],
                n_results=min(n_results, count),
                where={"unit": unit_name},
            )
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            return [{"text": doc, "metadata": meta} for doc, meta in zip(docs, metas)]
        except Exception:
            return []

    def query_with_filter(
        self, collection_name: str, query: str, file_filter: str, n_results: int = 5
    ) -> list[dict]:
        collection = self.get_or_create_collection(collection_name)
        count = collection.count()
        if count == 0:
            return []
        try:
            results = collection.query(
                query_texts=[query],
                n_results=min(n_results, count),
                where={"file": file_filter},
            )
            return [
                {"text": doc, "metadata": meta}
                for doc, meta in zip(results["documents"][0], results["metadatas"][0])
            ]
        except Exception:
            return self.query(collection_name, query, n_results)
