import unicodedata
import re

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\sáéíóúñü]")  # conserva letras y tildes básicas

def normalize_for_search(s: str) -> str:
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))  # quita diacríticos
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s
