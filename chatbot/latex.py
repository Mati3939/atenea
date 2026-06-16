"""Normalización de LaTeX en respuestas del modelo.

Los modelos pequeños generan LaTeX con delimitadores inconsistentes
(\\[..\\], \\(..\\), [ .. ] o comandos sueltos sin delimitador). Este módulo
convierte todo a delimitadores $ / $$ que KaTeX renderiza en el frontend.
"""
import re

_CMD = re.compile(r"\\[a-zA-Z]+")

# Segmentos ya delimitados correctamente — no se tocan
_MATH_SEGMENT = re.compile(r"\$\$.*?\$\$|\$[^$\n]+?\$", re.DOTALL)

# Token "matemático": comando LaTeX con argumentos, número, letra suelta,
# diferencial/función común u operador. Se excluyen las letras sueltas que son
# palabras en español (a, e, o, u, y) para no absorber prosa.
_MATH_TOKEN = (
    r"\\[a-zA-Z]+(?:\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})*"  # \frac{..}{..}, 1 nivel anidado
    r"|\d+(?:[.,]\d+)?"
    r"|(?<![a-zA-Z])(?:d[xytuvrs]|ln|log|sin|cos|tan|sen|exp)(?![a-zA-Z])"
    r"|(?<![a-zA-Z])[b-df-hi-np-tv-xzB-DF-HI-NP-TV-XZ](?![a-zA-Z])"
    r"|[(){}|^_+\-=/<>]"
)
_BARE_RUN = re.compile(rf"(?:{_MATH_TOKEN})(?:[ \t]*(?:{_MATH_TOKEN}))*")


def normalize_latex(text: str) -> str:
    if "\\" not in text and "$" not in text:
        return text

    # \[..\] → $$..$$ ; \(..\) → $..$
    text = re.sub(r"\\\[(.*?)\\\]", r"$$\1$$", text, flags=re.DOTALL)
    text = re.sub(r"\\\((.*?)\\\)", r"$\1$", text)

    # Línea que es solo [ ... ] con LaTeX dentro → bloque $$
    text = re.sub(
        r"^[ \t]*\[[ \t]*(.+?)[ \t]*\][ \t]*$",
        lambda m: f"$${m.group(1)}$$" if _CMD.search(m.group(1)) else m.group(0),
        text,
        flags=re.MULTILINE,
    )

    # Cerrar bloque $$ huérfano
    if text.count("$$") % 2 == 1:
        text = text.rstrip() + "$$"

    # Envolver comandos LaTeX sueltos fuera de segmentos matemáticos
    parts = _MATH_SEGMENT.split(text)
    segments = _MATH_SEGMENT.findall(text)
    out = []
    for i, part in enumerate(parts):
        out.append(_wrap_bare_math(part))
        if i < len(segments):
            out.append(segments[i])
    return "".join(out)


def _wrap_bare_math(text: str) -> str:
    def repl(m: re.Match) -> str:
        run = m.group(0)
        if not _CMD.search(run):
            return run
        # No envolver puntuación final de la oración
        stripped = run.rstrip(" \t.,;:")
        trailing = run[len(stripped):]
        if not stripped:
            return run
        return f"${stripped}${trailing}"

    return _BARE_RUN.sub(repl, text)
