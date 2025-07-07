
def classify_reclamo_response(text: str) -> str:
    t = text.lower().strip()
    if any(word in t for word in ['?', 'saber']):
        return 'question'
    if t.startswith('si') or t.startswith('sí') or ' sí' in t:
        return 'affirmative'
    if t.startswith('no') or ' no' in t:
        return 'negative'
    return 'unknown'
