import re

from ingestion.vectorstore import VectorStore

# Archivos de solucionario: traen soluciones (no queremos filtrarlas al estudiante)
# y suelen tener la peor matemática mal extraída del PDF (provoca que el modelo las
# copie). Se excluyen del contexto y se prefieren guías/apuntes.
_SOLUTION_FILE_RE = re.compile(
    r"pauta|soluci[oó]n|control|certamen|prueba|examen|resuelt", re.IGNORECASE
)


def _is_solution_file(meta: dict) -> bool:
    return bool(_SOLUTION_FILE_RE.search(str(meta.get("file", ""))))


def _filter_solutions(results: list[dict]) -> list[dict]:
    """Deja fuera los solucionarios; si solo había solucionarios, conserva todo."""
    teaching = [r for r in results if not _is_solution_file(r["metadata"])]
    return teaching or results


# Tope de caracteres por chunk al armar el contexto: acota los tokens enviados a
# Groq (free tier: 12k tokens/minuto) sin perder lo esencial del fragmento.
_CHUNK_CHARS = 900


def _build(results: list[dict], n: int) -> tuple[str, list[dict]]:
    results = results[:n]
    if not results:
        return "", []
    context = "\n\n---\n\n".join(r["text"][:_CHUNK_CHARS] for r in results)
    return context, [r["metadata"] for r in results]


class Retriever:
    def __init__(self, store: VectorStore):
        self.store = store

    def get_context(self, query: str, collection_name: str, n: int = 3) -> tuple[str, list[dict]]:
        """Devuelve (texto_contexto, lista_de_metadatos)."""
        return _build(_filter_solutions(self.store.query(collection_name, query, n_results=n * 2)), n)

    def get_context_for_unit(
        self, query: str, collection_name: str, unit_name: str, n: int = 3
    ) -> tuple[str, list[dict]]:
        """Contexto priorizando la unidad activa; completa con material general si la
        unidad tiene poco indexado.

        `query_by_unit` y `query` usan el MISMO texto de query, así que el embedding se
        calcula una sola vez (cache en VectorStore); la segunda búsqueda solo cuesta el
        lookup vectorial local (sin round-trip a Ollama)."""
        unit_res = _filter_solutions(
            self.store.query_by_unit(collection_name, query, unit_name, n_results=n * 2))
        if len(unit_res) >= n:
            return _build(unit_res, n)
        # Unidad escasa (o sin metadata 'unit'): completar con material general del curso.
        general = _filter_solutions(self.store.query(collection_name, query, n_results=n * 2))
        seen = {r["text"] for r in unit_res}
        merged = unit_res + [r for r in general if r["text"] not in seen]
        return _build(merged, n)

    def get_context_all(self, query: str, n_per_collection: int = 1) -> tuple[str, list[dict]]:
        """Busca en todas las colecciones (acotado a ~4 fragmentos por tokens)."""
        all_results = []
        for col in self.store.list_collections():
            all_results.extend(self.store.query(col, query, n_results=n_per_collection))
        return _build(_filter_solutions(all_results), 4)
