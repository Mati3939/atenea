import os
import re
import functools
import chromadb

# Modelo de embeddings servido por Ollama. 'paraphrase-multilingual' entiende
# español mucho mejor que el default de ChromaDB (all-MiniLM, anglocéntrico).
# Usar EMBEDDING_MODEL=default en .env para volver al embedding de ChromaDB.
DEFAULT_EMBEDDING_MODEL = "paraphrase-multilingual"


@functools.lru_cache(maxsize=512)
def _embed_query_cached(model: str, url: str, text: str) -> tuple:
    """Embebe el texto de una query con Ollama y cachea el resultado.

    Las queries de chat se repiten mucho (quick-replies constantes como "Dame otro
    ejercicio", repreguntas idénticas), así que cachearlas evita un round-trip a
    Ollama por turno. Clave: (modelo, url, texto). Devuelve una tupla (hashable)."""
    import ollama
    resp = ollama.Client(host=url).embed(model=model, input=text)
    return tuple(resp["embeddings"][0])


class VectorStore:
    def __init__(self, persist_dir: str = "chroma_db"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedding_model = os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()
        self._ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        self._ef = None
        if self.embedding_model and self.embedding_model.lower() != "default":
            from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
            self._ef = OllamaEmbeddingFunction(
                url=self._ollama_url,
                model_name=self.embedding_model,
                timeout=300,  # la primera llamada carga el modelo en frío
            )

    def _query_embedding(self, text: str) -> list | None:
        """Embedding cacheado de una query (solo con Ollama EF). None → usar query_texts.

        Si algo falla (Ollama caído), devuelve None y la consulta cae al camino con
        query_texts, que el propio ChromaDB resolverá o degradará a 'sin contexto'."""
        if self._ef is None:
            return None
        try:
            return [list(_embed_query_cached(self.embedding_model, self._ollama_url, text))]
        except Exception:
            return None

    def _query_args(self, query: str) -> dict:
        """Args para collection.query: embedding cacheado si es posible, si no texto."""
        emb = self._query_embedding(query)
        return {"query_embeddings": emb} if emb is not None else {"query_texts": [query]}

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

    def drop_collection(self, course: str) -> None:
        """Alias de `delete_collection`, tolerante a colección inexistente.
        Se usa al borrar un curso completo desde el gestor de archivos: si el
        curso nunca se indexó (o ya se había borrado), simplemente no hace nada."""
        self.delete_collection(course)

    def add_chunks(self, collection_name: str, chunks: list[str], metadata: list[dict]) -> None:
        # Un nombre vacío/no alfanumérico degeneraría en la colección basura 'col'.
        if not (collection_name or "").strip() or not re.search(r"[a-zA-Z0-9]", collection_name):
            raise ValueError(f"Nombre de curso inválido para indexar: {collection_name!r}")
        if not chunks:
            return
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
            qargs = self._query_args(query)
            results = collection.query(n_results=min(n_results, count), **qargs)
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

    def update_chunk_paths(self, collection_name: str, pairs: list[tuple[str, str, str | None]]) -> int:
        """Corrige in-place la metadata (path/file/unit) de chunks ya indexados
        cuyo archivo físico se movió (p. ej. tras 'Ordenar curso' en el gestor de
        archivos). No re-embebe ni llama al LLM — es una corrección de metadata,
        mucho más barata y sin duplicar contenido en la colección.

        `pairs`: lista de (old_abs_path, new_abs_path, new_unit_o_None). Devuelve
        cuántos chunks se actualizaron."""
        try:
            col = self.client.get_collection(self._safe_name(collection_name))
        except Exception:
            return 0
        updated = 0
        for old_path, new_path, new_unit in pairs:
            try:
                result = col.get(where={"path": old_path}, include=["metadatas"])
            except Exception:
                continue
            ids = result.get("ids") or []
            if not ids:
                continue
            metas = result.get("metadatas") or []
            new_name = os.path.basename(new_path)
            new_metas = []
            for m in metas:
                m = dict(m or {})
                m["path"] = new_path
                m["file"] = new_name
                if new_unit:
                    m["unit"] = new_unit
                else:
                    m.pop("unit", None)
                new_metas.append(m)
            try:
                col.update(ids=ids, metadatas=new_metas)
                updated += len(ids)
            except Exception:
                continue
        return updated

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
                n_results=min(n_results, count),
                where={"unit": unit_name},
                **self._query_args(query),
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
                n_results=min(n_results, count),
                where={"file": file_filter},
                **self._query_args(query),
            )
            return [
                {"text": doc, "metadata": meta}
                for doc, meta in zip(results["documents"][0], results["metadatas"][0])
            ]
        except Exception:
            return self.query(collection_name, query, n_results)
