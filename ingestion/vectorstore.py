import re
import chromadb


class VectorStore:
    def __init__(self, persist_dir: str = "chroma_db"):
        self.client = chromadb.PersistentClient(path=persist_dir)

    def _safe_name(self, name: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_\-.]", "_", name)[:63]
        # ChromaDB exige que empiece y termine con alfanumérico
        safe = re.sub(r"^[^a-zA-Z0-9]+", "", safe)
        safe = re.sub(r"[^a-zA-Z0-9]+$", "", safe)
        if not safe:
            safe = "col"
        return safe.ljust(3, "0")

    def get_or_create_collection(self, name: str):
        return self.client.get_or_create_collection(self._safe_name(name))

    def add_chunks(self, collection_name: str, chunks: list[str], metadata: list[dict]) -> None:
        collection = self.get_or_create_collection(collection_name)
        base = collection.count()
        ids = [f"chunk_{base + i}" for i in range(len(chunks))]
        collection.add(documents=chunks, metadatas=metadata, ids=ids)

    def query(self, collection_name: str, query: str, n_results: int = 5) -> list[dict]:
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

    def is_file_indexed(self, collection_name: str, file_path: str) -> bool:
        try:
            col = self.client.get_collection(self._safe_name(collection_name))
            result = col.get(where={"path": file_path}, limit=1)
            return len(result["ids"]) > 0
        except Exception:
            return False

    def list_collections(self) -> list[str]:
        return [c.name for c in self.client.list_collections()]
