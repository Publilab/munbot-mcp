import unicodedata

def normalize_text(text: str) -> str:
    """Lowercase, remove accents and collapse punctuation into single spaces."""
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    normalized_chars = []
    for char in text:
        if char.isalnum():
            normalized_chars.append(char)
        elif char.isspace():
            normalized_chars.append(" ")
        else:
            # Preserve separators so phrases like "sí,ver" don't merge.
            normalized_chars.append(" ")
    collapsed = " ".join("".join(normalized_chars).split())
    return collapsed
