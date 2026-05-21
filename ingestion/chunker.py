def chunk(text: str, size: int = 400, overlap: int = 50) -> list[str]:
    """Divide texto en chunks de `size` palabras con `overlap` palabras de solapamiento."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        segment = " ".join(words[i : i + size])
        if segment.strip():
            chunks.append(segment)
        i += size - overlap
    return chunks
