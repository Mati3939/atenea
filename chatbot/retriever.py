from ingestion.vectorstore import VectorStore


class Retriever:
    def __init__(self, store: VectorStore):
        self.store = store

    def get_context(self, query: str, collection_name: str, n: int = 4) -> tuple[str, list[dict]]:
        """Devuelve (texto_contexto, lista_de_metadatos)."""
        results = self.store.query(collection_name, query, n_results=n)
        if not results:
            return "", []
        context = "\n\n---\n\n".join(r["text"] for r in results)
        sources = [r["metadata"] for r in results]
        return context, sources

    def get_context_all(self, query: str, n_per_collection: int = 2) -> tuple[str, list[dict]]:
        """Busca en todas las colecciones."""
        all_results = []
        for col in self.store.list_collections():
            all_results.extend(self.store.query(col, query, n_results=n_per_collection))
        if not all_results:
            return "", []
        context = "\n\n---\n\n".join(r["text"] for r in all_results)
        sources = [r["metadata"] for r in all_results]
        return context, sources
