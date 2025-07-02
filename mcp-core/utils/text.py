import unicodedata

def normalize_text(text: str) -> str:
    """Lowercase, remove accents and keep only alphanumerics and spaces."""
    text = text.lower().strip()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    text = "".join(c for c in text if c.isalnum() or c.isspace())
    return text
