from typing import Optional, Dict

try:
    from utils.text import normalize_text
except (ModuleNotFoundError, ImportError):
    # Fallback para pruebas locales
    def normalize_text(text: str) -> str:
        import re
        import unicodedata
        text = text.lower()
        text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
        text = re.sub(r'[^\w\s]', '', text)
        return text

class DocumentRouter:
    """
    Identifica el documento o tema principal de una consulta de usuario
    basándose en un mapa de palabras clave.
    """
    def __init__(self, topic_map: Dict[str, str]):
        # Normalizamos las claves del mapa para una comparación robusta
        self.topic_map = {normalize_text(k): v for k, v in topic_map.items()}
        # Creamos una lista ordenada por longitud para evitar coincidencias parciales (ej: "patente" vs "patente de alcoholes")
        self.sorted_keys = sorted(self.topic_map.keys(), key=len, reverse=True)

    def get_document_topic(self, user_input: str) -> Optional[str]:
        """
        Busca una coincidencia de tema en la entrada del usuario.
        Devuelve el nombre del documento asociado si lo encuentra.
        """
        normalized_input = normalize_text(user_input)

        for keyword in self.sorted_keys:
            if keyword in normalized_input:
                return self.topic_map[keyword]
        
        return None
