from typing import List, Dict, Optional


def rewrite_query(history: List[Dict[str, str]], user_input: str, selected_doc: Optional[str] = None) -> str:
    """Generate a search query combining the latest user input with session context."""
    query = user_input.strip()
    if selected_doc and len(query.split()) < 6:
        query = f"{query} del trámite {selected_doc}"
    return query
