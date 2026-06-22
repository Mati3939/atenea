def chunk(text: str, size: int = 400, overlap: int = 50, max_chars: int = 2400) -> list[str]:
    """Divide texto en chunks de `size` palabras con `overlap` palabras de solapamiento.

    Además acota cada chunk a `max_chars` caracteres: un PDF mal extraído puede producir
    'palabras' larguísimas (texto sin espacios) que generan un chunk que excede el límite
    de contexto del modelo de embeddings (error 400) y aborta el archivo completo. El tope
    solo trocea los segmentos excesivos; los chunks normales no cambian."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        segment = " ".join(words[i : i + size])
        if segment.strip():
            chunks.extend(_split_oversized(segment, max_chars))
        i += size - overlap
    return chunks


def _split_oversized(segment: str, max_chars: int) -> list[str]:
    if len(segment) <= max_chars:
        return [segment]
    return [segment[j : j + max_chars] for j in range(0, len(segment), max_chars)]
